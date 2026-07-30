# Staleness curation: review queue with user-verified purge

> One-line status: Planned (design pass via exploration + Plan-agent design + advisor review, which caught two real bugs — a silent-loss-of-user-intent rule and an unbounded recurring LLM cost — before either shipped as pseudocode). Part 5 of 5, the last part, on the meeting-knowledge-layer master tracker (#241). Hard-blocked on part 1's `Entity`/`EntityMention` (#245) and part 2's `Entity.status` (#247). Soft-depends on part 3's `topic` type (#248) for one signal's type filter — degrades gracefully (zero topic candidates) if topic isn't live yet. Independent of part 4 (#250). **Also depends on `docs/plans/04-scheduled-tasks.md`'s scheduler infrastructure — an unrelated, separately-unimplemented feature** — see "Scheduler integration" for the fallback if that plan never lands.

## Motivation

After entities exist (part 1) and are browsable (part 2), some go stale: action items nobody's mentioned in weeks, entities that quietly stopped coming up, decisions later contradicted. A periodic scan finds candidates with cheap heuristics, uses an LLM only for the one genuinely judgment-shaped call ("is this superseded"), and everything lands in a review queue the user disposes of by hand. Same division of labor as the rest of #241: the LLM proposes, deterministic code and the user dispose. **Nothing is ever auto-deleted by the scan itself** — only an explicit, user-confirmed purge deletes anything, and purge only touches the entity layer (`Transcript.full_text`/`segments` are never written).

## Three collisions this plan resolves explicitly

**1. Part 1's orphan-auto-delete is a different rule, not this part's job restated.** `delete_orphaned_entities` (#245) deletes any zero-mention `Entity` immediately. So "entities unreferenced for a long stretch" here can only mean entities that **still have ≥1 mention**, just an old one — part 1's own doc names this the disjoint case. The detection query below is an inner join `Entity → EntityMention → Transcript`, so a zero-mention entity structurally produces no row at all — resolved by the join shape, not an extra guard, and tested as a structural property (see Tests).

**2. Part 2's `Entity.status` gates one signal, and purge deliberately overrides part 2's orphan-delete exemption.** The stale-action-item detection query filters `Entity.status IS NULL OR Entity.status = 'open'` in SQL — a `done` item is never flagged as stale. But this plan's **confirmed-purge** action is a distinct, user-approved deletion path, and it **can** purge an old `done` action item. Not a contradiction: part 2's exemption exists to prevent a *silent* loss of the user's "done" mark; a purge the user explicitly clicked is the opposite of silent.

**3. This reuses `docs/plans/04-scheduled-tasks.md`'s scheduler; it does not invent a second one.** That plan (unimplemented but fully designed, on `master`, an unrelated Blinko-inspired backup/archive feature) already specifies a third asyncio loop (`scheduler_worker_loop`, `services/scheduler.py`, coarse tick, a `ScheduledTaskRun(id, kind, status, detail JSON, error, started_at, finished_at)` table, `kind` deliberately left open for future tasks) started from `app.py`'s `lifespan`. This plan adds a third `kind`, `"staleness_scan"`, to that same loop and table rather than building a parallel mechanism.

## Why not `LlmJob` for the scan/queue itself

`LlmJob.progress_done`/`progress_total` model one linear counter for one run, not N independently-actionable flagged items each needing its own `pending_review`/`snoozed`/`pinned`/`purged`/`resolved` life cycle. A dedicated table, one row per flagged item, is the right shape — closer to `TranscriptTag`/`RelabelHistory`'s per-item-row pattern than to `LlmJob`'s per-run pattern. The scan itself (heuristics a/b, pair-generation for c) is plain deterministic SQL with no LLM involved, so it runs directly in the scheduler tick, not inside `LlmJob`.

The one place an LLM call genuinely belongs is signal (c)'s "is this decision superseded" judgment. Checked directly (not assumed) whether cost-accounting argues for routing it through `LlmJob`: it doesn't — `services/cost.py` tracks only STT duration cost today, no chat-completion call is cost-tracked at all. The real reason to use `LlmJob` is provider-rate-limit pooling: a direct call from inside the scheduler tick would bypass `IO_KINDS`' existing 2-concurrent-call cap, and `LlmJob` gives never-raise/observability/auto-retry for free. `transcript_id=None` is already precedented by the existing `"assistant"` kind, which also demonstrates the input/output trick this plan reuses: `job.result_json` holds input at enqueue time, gets overwritten with output at completion.

## Schema

```python
class StaleCandidate(Base):
    """One flagged staleness signal, one row per (entity_id, signal_type).
    Reviewed via GET/PATCH/POST /api/stale-candidates*. Never auto-deleted
    by the scan; only a confirmed purge deletes the underlying Entity, and
    this row survives that as an audit tombstone (entity_id goes NULL,
    snapshot fields keep the record legible)."""
    __tablename__ = "stale_candidates"
    __table_args__ = (
        UniqueConstraint("entity_id", "signal_type", name="uq_stale_candidate_entity_signal"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # own column (not derived via Entity join) — needed because entity_id goes NULL after purge
    entity_id = Column(Integer, ForeignKey("entities.id"), nullable=True)  # NULL after purge; SQLite's foreign_keys pragma is never enabled, so this is never DB-enforced on delete — the app clears it explicitly
    entity_type = Column(String(16), nullable=False)  # snapshot at flag time — survives purge; entities are never renamed post-creation (part 2 ships no rename route) so this can't drift
    entity_name = Column(Text, nullable=False)  # snapshot at flag time, same reasoning
    signal_type = Column(String(32), nullable=False)  # stale_action_item | quiet_entity | superseded_decision
    related_entity_id = Column(Integer, ForeignKey("entities.id"), nullable=True)  # superseded_decision only: the newer decision
    status = Column(String(16), default="pending_review")  # pending_review | snoozed | pinned | purged | resolved
    last_mention_at = Column(DateTime, nullable=True)  # real column, not JSON — the list route orders by it, which is exactly "something reads it" per part 1's own denormalization test
    snoozed_until = Column(DateTime, nullable=True)
    evidence = Column(JSON, default=dict)  # {"linked_meetings": [...], "related_meetings": [...superseded_decision only...], "reasoning": "..."}
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
```

No relationship-level cascade on this table at all (per part 1's own warning about a second `delete-orphan` parent producing cascade behavior neither side implies) — queried by plain `entity_id` filter, same posture part 1 uses for `EntityMention`.

### Status transitions (amended during review — see below)

- `pending_review → snoozed` (sets `snoozed_until`), `pending_review → pinned`, `pending_review → purged` (real delete) — the three actions the issue names.
- `snoozed → pending_review`: automatic, at scan time, once `snoozed_until` has passed **and the signal still holds**.
- **`→ resolved` (amended during advisor review, not "delete the row" as an earlier draft had it): any non-terminal row (`pending_review` or `snoozed`) whose underlying signal no longer holds on a later scan becomes `resolved`, a terminal status, and is kept, not deleted.** The earlier draft deleted such rows outright as "queue bookkeeping, not the entity layer" — flagged in review as the same class of silent-loss-of-user-intent that made part 1 amend its own orphan rule for `status`: a user snoozes a candidate, the signal resolves, the row vanishes, and if the signal re-triggers next month it comes back as a *fresh* `pending_review` with no memory that they'd already looked at it once. `resolved` fixes this — the row (and the fact that a human once engaged with it) persists, visible via an explicit `?status=resolved` query, same as `purged`. The default `pending_review` list view naturally excludes both.
- `pinned → pending_review`: user-initiated only (explicit "unpin" PATCH), never automatic. A pinned row is skipped entirely by the scan (evidence not even refreshed) — pinning means "stop asking."
- `purged`/`resolved`: both terminal, no further PATCH allowed (400 on any attempt against either).
- **Idempotence, amended during review to also bound recurring LLM cost (see below)**: if a non-terminal-or-resolved row already exists for `(entity_id, signal_type)`, the scan updates evidence/`last_mention_at` in place for signals (a)/(b) rather than inserting a duplicate. For signal (c), the pre-LLM-call filter skips generating a judgment call at all if **any** row already exists for that older-decision `entity_id` under `signal_type="superseded_decision"`, in **any** status including `resolved` — not just non-purged as an earlier draft had it. See "Detection heuristics (c)" for why this needed tightening.

## Detection heuristics

**(a) Stale open action items** and **(b) entities gone quiet** share one query shape (`_find_quiet_entities`), differing only in `type` filter and threshold:

```sql
SELECT e.id, MAX(t.created_at) AS last_mention_at
FROM entities e
JOIN entity_mentions em ON em.entity_id = e.id
JOIN transcripts t ON t.id = em.transcript_id
WHERE e.user_id = :user_id AND e.type IN (:types)
  [AND (e.status IS NULL OR e.status = 'open')]   -- (a) only
GROUP BY e.id
HAVING MAX(t.created_at) < :cutoff
```

- **(a)**: `types = ("action_item",)`, plus the `status` filter, `cutoff = now - stale_action_item_days`.
- **(b)**: `types = ("person", "project", "topic")`, no status filter, `cutoff = now - stale_entity_quiet_days`. `decision` is deliberately excluded — a decision is resolved history, not something that needs continual remention to stay valid. `action_item` is excluded from (b) too, to avoid double-flagging under two signal types; a `done` item is covered by neither (a)'s status filter nor (b)'s type filter, correctly — finished isn't stale.
- The inner join is what makes collision #1 hold structurally: a zero-mention entity (already gone via part 1's orphan rule, or a rare `done`-zero-mention survivor via part 2's exemption) produces no row here at all.
- **Named limitation**: `Transcript` has no meeting-date column distinct from `created_at`. Bulk import (#231, already on `master`) can set `created_at` at import time rather than the historical meeting date, so a bulk-imported year-old meeting can look "recent" and mask real staleness for anything it mentions. Flagged for whoever implements this, not solved here.

**(c) Decisions potentially superseded** — a two-stage funnel, cheap narrowing before any LLM call, with a cost-boundedness fix made during review:

1. **Cheap narrowing** (pure Python/SQL over one user's `decision` entities, no LLM): for each pair `(A, B)`, anchor each to `decided_at = MIN(Transcript.created_at)` across its mentions, order `older`/`newer` by that anchor (skip equal-anchor pairs), require shared co-occurrence (at least one `person`/`project`/`topic` entity co-occurring with both, reusing part 2's same-meeting co-occurrence shape across each decision's own transcript set), require `jaccard_ratio(older.normalized_name, newer.normalized_name) >= _SUPERSEDE_JACCARD_FLOOR` (module constant, default `0.2`, **explicitly provisional/unmeasured**, unlike part 3's directly-measured threshold — flagged the same way, not silently presented as tuned). Jaccard over `SequenceMatcher`, same reasoning part 3 already established for sentence-shaped names.
   - **Skip any pair whose older-decision entity already has a row for `signal_type="superseded_decision"` in ANY status, not just non-purged.**
   - Sort surviving pairs by score descending, cap at `_MAX_SUPERSEDE_PAIRS` (module constant, default `20` **per user per tick**).
2. **LLM judgment**: one `LlmJob(kind="stale_decision_judge", transcript_id=None)` per surviving pair, `result_json = {"older_entity_id", "newer_entity_id"}` as input (same trick as the `"assistant"` kind). New `services/entities.py` function:

```python
async def judge_superseded(older_text, older_date, newer_text, newer_date,
                            api_key, provider_name, provider_config, model) -> dict:
    """One LLM call -> {"superseded": bool, "reasoning": str}. Never raises —
    any failure (RuntimeError, malformed JSON, non-JSON prose, wrong shape,
    None) degrades to {"superseded": False, "reasoning": ""}, same fail-closed
    contract as generate_tags/classify_intent."""
```

**Amendment made during review, fixing an unbounded recurring cost**: the dispatch branch in `run_llm_job` writes a `StaleCandidate` row for **every** judgment outcome, not only `True` ones. On `superseded=True`: `status="pending_review"`, `related_entity_id=newer_entity_id`, evidence includes the LLM's reasoning. On `superseded=False`: `status="resolved"` immediately (never enters the review queue, but the row's existence is what makes the idempotence check above actually bound the cost). **Why this matters**: an earlier draft wrote no row at all on a `False` verdict, which meant a pair judged "not superseded" this week was fully eligible to be re-submitted to the LLM next week, forever — for a user with 200 decision entities, the pair cross-product is on the order of 20,000 before narrowing, `_MAX_SUPERSEDE_PAIRS=20`/tick means the same handful of pairs (or new ones, depending on sort stability) get judged weekly with no guarantee the tail is ever reached and no memory of what's already been checked. Writing a `resolved` row on `False` bounds each decision entity to **at most one supersession judgment, ever**, at the cost of an accepted simplification stated plainly here: if a decision is judged "not superseded" against one candidate, it will never be re-checked again even against a completely different, later decision that might genuinely supersede it. This trades a small amount of recall for a hard cost ceiling — revisit only if it becomes a real complaint, not preemptively re-engineered.

`"stale_decision_judge"` is added to `services/llm_jobs.py`'s `VALID_KINDS`, `IO_KINDS` (same pool as `tagging`/`assistant`), and `AUTO_RETRY_KINDS`.

**Cost note, stated plainly rather than left for someone to discover**: unlike part 1's `entities` job (an LLM call per transcript, scaling with transcript volume), this signal's LLM cost scales with decision-entity count and is hard-capped at `_MAX_SUPERSEDE_PAIRS` per user per tick (default 20/week) — and now, per the fix above, each decision entity contributes to that cap at most once ever, not repeatedly. Signals (a)/(b) cost nothing (pure SQL). This is why `staleness_scan_enabled` defaulting to `True` (below) is a reasonable default despite the general "another always-on LLM-spending job" caution that applies to part 1 — the magnitude here is bounded and small by construction, not open-ended.

## Scheduler integration

- `ScheduledTaskRun.kind` gains `"staleness_scan"` (designed extensible for exactly this in `04`'s own doc).
- `services/scheduler.py`'s per-task due-check gains a third task: "has `staleness_scan_interval_days` passed since the last `staleness_scan` `ScheduledTaskRun`."
- New `services/settings.py` `DEFAULT_SETTINGS` keys: `staleness_scan_enabled` (default `True` — see cost note above), `staleness_scan_interval_days` (default `7`), `stale_action_item_days` (default `21`), `stale_entity_quiet_days` (default `60`).
- **A wrinkle `04` never had to solve**: backup/archive are installation-wide (no per-user concept); this scan is inherently per-user (thresholds, entity ownership). Resolution: the scheduler-global `staleness_scan_enabled`/`staleness_scan_interval_days` toggle lives wherever `04` lands its own config (the fallback/admin user's settings, per `04`'s own recommendation) and gates whether the tick runs at all; when it runs, it iterates every `User` row and reads `stale_action_item_days`/`stale_entity_quiet_days` from **that user's own** settings, same per-user model `entity_dedup_threshold`/`topic_dedup_threshold` already use. One `ScheduledTaskRun` row per tick covers all users: `detail = {"users_scanned": N, "stale_action_items_flagged": X, "quiet_entities_flagged": Y, "decision_pairs_enqueued": Z}` — note `detail` can only report pairs *enqueued* for signal (c), since the LLM verdict resolves asynchronously later via the ordinary `llm_worker_loop` drain, independent of the `ScheduledTaskRun` row that triggered the enqueue.
- `_MAX_SUPERSEDE_PAIRS`/`_SUPERSEDE_JACCARD_FLOOR` stay module constants in `services/entities.py`, not settings — same reasoning part 4 used for its own decay constant: no user-facing frame of reference to tune them against yet.

**Fallback if `04` is never implemented**: build only the minimal slice this part needs — `services/scheduler.py` with `scheduler_worker_tick`/`scheduler_worker_loop` (single-task version, no backup/archive branches), the `ScheduledTaskRun` model, and the three-line `app.py` `lifespan` addition mirroring the existing two loops exactly as `04`'s doc already specifies. Explicitly not built in the fallback: `run_backup`, `run_archive_and_prune`, `Transcript.archived`, `include_archived` filters — those stay `04`'s own unrelated scope.

## Review-queue routes + minimal UI

- `GET /api/stale-candidates?status=&signal_type=&limit=` — `status` defaults to `pending_review`; `snoozed`/`pinned` also listable (a transparency/undo view); `purged`/`resolved` only returned when explicitly requested (audit trail, not the default queue view). `signal_type` optional filter. Ordered by `last_mention_at ASC` (most-overdue first). Scoped via `StaleCandidate.user_id == current_user.id`.
- `PATCH /api/stale-candidates/{id}` body `{"status": "snoozed"|"pinned"|"pending_review", "snooze_days": int}` (`snooze_days` meaningful only with `status="snoozed"`, defaults to a new `stale_snooze_default_days` setting, default `14`). 400 on any other target status (purge isn't reachable here) or if current status is `purged`/`resolved` (both immutable). 404 scoped.
- `POST /api/stale-candidates/{id}/purge` — the destructive action, its own route by design, same "separate route for the dangerous action" posture `04` used for `prune_enabled` vs `archive_enabled`. 400 if already `purged`/`resolved`. 404 scoped.

**UI**: a seventh tab, `Stale`, added to part 2's existing entities-list type-filter tab strip (`All | People | Projects | Decisions | Action items | Topics | Stale`). **Explicitly a different data shape behind the same visual strip, not a filter predicate over the same cached array** — the other six tabs filter one cached `Entity[]` fetch; `Stale` is backed by its own fetch/cache (`staleCandidateListCache`, `GET /api/stale-candidates`) and its own card renderer, since `StaleCandidate` rows aren't `Entity` rows. Stating this explicitly so a future reader doesn't try to implement it as a client-side filter and wonder why it's empty. Each card shows entity name/type, `last_mention_at`, evidence's linked-meeting titles (click-through to `detail`, same precedent as part 2's meetings tab), and three actions: Snooze, Pin, Purge (Purge behind a confirm step). Carries forward part 2's two named traps verbatim: the logout/account-switch `S.*` reset block (issue #54) needs the new state/cache added, and `npm run build:js` is required before any of this is visible.

## The purge action itself

A confirmed purge is a real delete of the `Entity` row and all its `EntityMention` rows — not a soft flag, not scoped to one transcript. Sequence: `StaleCandidate.status = "purged"`, `entity_id = NULL` (snapshot columns already captured), flush, then delete the entity's `EntityMention` rows and the `Entity` row. Zero writes to `Transcript.full_text`/`segments` — recovery is re-extraction: rerunning the `entities` `LlmJob` via the Queue screen's existing rerun action (part 1 ships no manual "Extract entities" route, so rerun-from-Queue is the actual, not hypothetical, recovery path).

Confirmed different from part 1's automatic orphan-delete: that fires on zero mentions, silently, no queue, no evidence, no click. This purge fires on an entity the user explicitly reviewed and clicked purge on, which can have any number of mentions — the whole point of "gone quiet" is it still has ≥1. Per collision #2, this purge deliberately can target a `done` action item even though part 1/2's orphan-delete would never touch it; the two paths solve different problems (silent-loss prevention vs. explicit curation) and don't need to agree.

## Test plan

- **Heuristic detection, DB-fixture level**, stale + boundary "recently touched" fixture for each signal, mirroring `04`'s own boundary-condition insistence:
  - (a): `open` action item past threshold flagged; recent one not; `done` item past the same threshold **not** flagged (collision #2, checked in SQL).
  - (b): `person`/`project`/`topic` quiet past threshold flagged; recent one not; `decision`, however old, never flagged by this signal.
  - **Collision #1 as a structural property**: a zero-mention entity (constructed directly at the DB layer, bypassing part 1's orphan-delete, same technique part 1's own tests use) is absent from both (a) and (b)'s candidate sets — asserted, not just claimed by the join shape.
  - (c) pair-generation, zero-LLM: excludes same-transcript-anchor pairs, excludes pairs with no shared co-occurring person/project/topic, excludes pairs below `_SUPERSEDE_JACCARD_FLOOR`, respects `_MAX_SUPERSEDE_PAIRS`, skips a pair whose older-decision entity already has ANY row (including `resolved`) for `superseded_decision`.
- **`judge_superseded` never-raise contract**: RuntimeError, malformed JSON, non-JSON prose, `None`, wrong shape — all degrade to `{"superseded": False, "reasoning": ""}`, `httpx.AsyncClient` stubbing pattern per `tests/test_tagging.py`.
- **Cost-boundedness fix, its own test**: a decision entity judged `False` once must not be re-submitted to the LLM on a subsequent scan tick even against a different candidate newer decision — construct two ticks with a fresh candidate pair each time, assert only one `judge_superseded` call ever fires for that older entity.
- **Scan idempotence**: running the scan twice against unchanged data produces exactly one row per `(entity_id, signal_type)`.
- **Review-queue state transitions**: `pending_review → snoozed → pending_review` after `snoozed_until` passes and the signal still holds; a snoozed (or pending_review) row whose signal has resolved becomes `resolved` (not deleted — this is the amendment's own pinning test, constructed exactly as the bug scenario: snooze, resolve, re-trigger, assert the OLD row is `resolved` and no fresh duplicate `pending_review` row was created for the same entity/signal); `pinned` skipped entirely by the scan; `purged`/`resolved` both reject further PATCH (400).
- **Purge correctness**: deletes `Entity` + its `EntityMention` rows; leaves `Transcript.full_text`/`segments` byte-identical; leaves co-occurring entities on shared transcripts untouched; leaves the `StaleCandidate` row present with `status="purged"`, `entity_id=NULL`, snapshots intact.
- **Scheduler-tick level**: "fires only when due" tests at the same shape as `04`'s own backup/archive due-check tests — cite and mirror rather than re-derive; plus a smoke test that the third loop doesn't break lifespan startup/shutdown task-cancellation.

## Rough phasing / checklist

**Prerequisite (unrelated feature, blocking)**
- [ ] `docs/plans/04-scheduled-tasks.md`'s scheduler infrastructure, or this part's minimal fallback slice (see "Scheduler integration")

**Schema**
- [ ] `database/__init__.py`: `StaleCandidate` model, `__all__`

**Detection**
- [ ] `services/entities.py`: `_find_quiet_entities` (signals a/b), `_find_supersede_pairs` (signal c narrowing), `judge_superseded` (LLM call)
- [ ] `services/llm_jobs.py`: `"stale_decision_judge"` kind (`VALID_KINDS`/`IO_KINDS`/`AUTO_RETRY_KINDS`), dispatch branch writing `StaleCandidate` on both `True` and `False` outcomes

**Scheduler wiring**
- [ ] `"staleness_scan"` `ScheduledTaskRun.kind`, per-user iteration, settings keys

**Routes + UI**
- [ ] `app.py`: `GET /api/stale-candidates`, `PATCH /api/stale-candidates/{id}`, `POST /api/stale-candidates/{id}/purge`
- [ ] `rack.js`/`index.html`: `Stale` tab, its own fetch/cache/renderer, logout-reset block, `npm run build:js`

**Tests**
- [ ] Per the Test plan above, including the two review-fix pinning tests specifically (resolved-not-deleted, cost-bounded negative-verdict memory)

## Testing considerations

Detection heuristics and state transitions are DB-fixture-testable with zero LLM involvement except `judge_superseded` itself, which stubs the provider call same as every other LLM-calling function in this codebase. No browser e2e needed beyond a scripted check of the new `Stale` tab rendering and its three action buttons, per this project's tiering for a new-but-small UI surface (smaller than part 2's two full new pages).
