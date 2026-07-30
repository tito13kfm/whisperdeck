# Topic grouping: topics as a first-class entity type, tagging retirement

> One-line status: Planned (design pass done via exploration + Plan-agent design + advisor review, including a direct measurement to settle the merge-comparator question rather than guessing). Part 3 of 5 on the meeting-knowledge-layer master tracker (#241). Blocked on parts 1 (#245) and 2 (#247) landing first.

## Motivation

From issue #241's topic-grouping comment: extract topics as an entity type alongside people, projects, decisions, and action items, so meetings cluster under the topics they touch — a browsable topic index ("all meetings about 'billing migration' in one place, even across different projects"), and topic pages become graph hubs for #242's retrieval work later (part 4's job, not this one).

This turned out to be small, and this doc says so plainly rather than padding it: adding a fifth entity type is almost entirely a set of amendments to parts 1 and 2's already-written, not-yet-implemented plans. The one genuinely new decision is what comparator to use for topic-name matching (resolved below with real measurement, not assumption) and what to do about the fact that a topic-shaped feature (`services/tagging.py`) already ships today.

**This doc does not restate part 1's or part 2's design.** Read `docs/plans/07-entity-extraction-core.md` and `docs/plans/08-entity-pages-ui.md` first — both have been edited in place to fold in the amendments below; this doc explains *why* those edits were made and covers the one piece of work that's genuinely new (tagging retirement).

## Amendments to part 1 (`docs/plans/07-entity-extraction-core.md` / #245)

- `Entity.type` gains `"topic"` as a fifth value. No schema change beyond that — `String(16)` already fits it, the `(user_id, type, normalized_name)` unique constraint needs no change, `voice_profile_id` stays null for topic exactly as it already does for project/decision/action_item.
- **No second, cross-transcript clustering pass.** "Meetings cluster under topics... across different projects" sounds like it needs new batch machinery, but it doesn't: it's the same emergent property `plan_merge`'s existing-entities-first incremental matching already gives person/project. A topic proposed in meeting #50 merges onto the topic entity created in meeting #10 through the ordinary per-transcript extraction-then-merge loop — no periodic job, no new `LlmJob` shape. (Confirmed there's no cross-transcript batching pattern in `services/llm_jobs.py` to reuse anyway — `llm_worker_loop` claims one job row at a time.)
- **Merge comparator: a third branch, resolved by direct measurement, not by reusing person/project's char-fuzzy metric.** Full reasoning and the measured pairs are now in part 1's doc §4 (the "topic" bullet under "Deterministic merge"). Summary: `difflib.SequenceMatcher` (person/project's metric) under-merges real topic paraphrases at the existing 0.82 threshold; token-set Jaccard at a separate `topic_dedup_threshold` (default `0.6`, provisional) does better, and — verified with an extended measurement covering asymmetric token lengths, not just the original sample — needs no separate token-count guard at that threshold, because the dangerous single-token-attractor case already scores at or below threshold on its own. The measurement also surfaced a real, accepted limitation: length-asymmetric genuine paraphrases (a 2-token topic vs. a 4-token elaboration of it) commonly score right at the edge too, so some genuine paraphrases won't fuzzy-merge and will land as duplicate rows — same "just noise" tradeoff part 1 already accepts for decisions/action-items, now also accepted for topic, stated explicitly rather than discovered later.
- Extraction prompt gains a fifth JSON key, `topics` (capped, styled to match `tagging.py`'s existing tag-phrasing guidance) — see part 1 doc's updated "Code touchpoints".
- `services/settings.py` gains `topic_dedup_threshold` alongside `entity_dedup_threshold`.
- The orphan-delete exemption (`status IS NULL OR status = 'open'`, from part 2) needs zero change for topic — topic's `status` is always null, so a zero-mention topic is correctly eligible for deletion with no new code.

## Amendments to part 2 (`docs/plans/08-entity-pages-ui.md` / #247)

Four small, precisely located edits, all applied in place:
- `GET /api/entities?type=` validates against five values instead of four.
- `GET /api/entities/{id}`'s co-occurrence branch (`decisions`/`action_items`) runs for `topic` too, not just `person`/`project` — directly justified by the issue's own "graph hub" framing.
- The list-page type-filter tab strip gains a `Topics` tab.
- The detail-page tab-eligibility check (`decisions`/`action-items` tabs) gains `topic`. Called out explicitly as a paired edit with the route-level change above — the same class of "touch both lockstep sites" risk part 1 already flagged for `enqueue_auto_tagging`'s two call sites.

Explicitly **not** done here: extending the co-occurrence query to also surface people/projects that share a meeting with a topic (the reverse direction). That's a genuinely different query shape plus new tabs/render branches, not a mechanical edit — deferred to part 4, which already owns "true graph, beyond same-meeting co-occurrence."

## New work: retire `tagging` in favor of topic entities

`services/tagging.py` + `database.TranscriptTag` (issue #171) already extracts 1–5 short topic-like labels per transcript via a separate `LlmJob(kind="tagging")` call — display-only pills, no browse page, no merge/identity, only incidental substring matching in the bank-list search box. Topic entities do the same conceptual job strictly better: merged identity, mention counts, a real browse page, click-through to meetings. Decision (confirmed with you directly, not assumed): **retire tagging, topics replace it.** Running both would mean two LLM calls per transcript producing near-identical output; retiring removes one.

Retirement scope:
- `services/llm_jobs.py`: remove `"tagging"` from `VALID_KINDS`/`IO_KINDS`/`AUTO_RETRY_KINDS`; remove `enqueue_auto_tagging` and its dispatch branch in `run_llm_job`.
- `app.py` + `services/queue.py`: remove both `enqueue_auto_tagging(...)` call sites, replaced by the (already-planned, part 1) `enqueue_auto_entities(...)` call that now also produces topics.
- `database/__init__.py`: `TranscriptTag` model and its `CREATE TABLE IF NOT EXISTS` block in `init_db()` — decide at implementation time whether to drop the table outright (clean, but destroys any tags a user has already accumulated) or leave it in place unused (simplest, no data loss, dead code until a future cleanup). **Recommend leaving the table in place and simply stopping all writes to it** — consistent with this codebase's general bias toward additive, non-destructive schema changes (no migration ever drops a table today), and it costs nothing to leave a handful of orphaned rows around versus a one-way data-loss decision made in passing during an unrelated feature's rollout. Revisit in part 5 (staleness curation) if it ever matters.
- `static/rack.js`: remove the tag-pill rendering in both the bank-list row (`~rack.js:2882-2885`) and the transcript-detail view (`~rack.js:4334-4340`), and the incidental tag substring-match branch in the bank-list free-text filter (`~rack.js:2842-2847`) — a user's tag-based free-text search UX is superseded by the new entities list page's own type+query filtering, not lost.
- `tests/test_tagging.py`: remove or repurpose (some of its normalization-function tests may be directly portable to the new topic-extraction tests, since both are short-phrase LLM extraction with a similar prompt shape — check before deleting outright).
- Existing user data: any `TranscriptTag` rows a user already has are not migrated into `Entity(type="topic")` rows automatically in this slice — tags and topics are different identity models (free-form label vs. merged entity), so a blind migration would just create unmerged, ungoverned topic rows that defeat the point of the new system. A user's existing tags simply stop being extracted going forward and stop rendering; a fresh topic index builds up from that point on, same as day one for a topic that's never existed on this account. State this plainly in the release notes for whoever ships this, not just in this doc.

## Open questions

None left unresolved for this slice — the design-review pass (comparator metric, tagging retirement) settled the two real questions this doc started with. The only forward-looking note: if `topic_dedup_threshold` is ever tuned down toward 0.5 based on real usage data, add the token-count guard described in part 1 doc's amendment before doing so (see that doc for the exact condition).

## Rough phasing / checklist

**Amendments landed in #245/#247's docs (this session, already done)**
- [x] Part 1 doc: topic comparator (Jaccard), `topic_dedup_threshold`, extraction-prompt `topics` key, type-list comment
- [x] Part 2 doc: five-value type validation, topic co-occurrence branch, `Topics` tab, detail-tab eligibility

**New work for part 3's implementation**
- [ ] `services/entities.py`: `jaccard_ratio`, topic branch in `plan_merge`'s comparator dispatch, topic entry in `normalize_name`'s per-type length clamp
- [ ] `services/settings.py`: `topic_dedup_threshold` default
- [ ] Extraction prompt: `topics` key, `_MAX_TOPICS` cap
- [ ] **Tagging retirement**: remove `"tagging"` kind + its two call sites + `enqueue_auto_tagging`/dispatch branch; leave `TranscriptTag` table in place unused (see decision above); remove tag-pill UI and the tag substring-match branch in `rack.js`; triage `tests/test_tagging.py`

**Tests**
- [ ] `services/entities.py` unit tests: `jaccard_ratio` on the measured pairs (both the original sample and the asymmetric-length extension), confirming no separate token-count guard is needed at the shipped threshold
- [ ] Regression: confirm removing the `tagging` kind doesn't break `test_io_cpu_pools_partition_valid_kinds` or any other kind-partition test (it should just shrink the valid set)
- [ ] A test asserting a topic proposed in a later transcript merges onto the topic entity created in an earlier one (proves the "no second pass needed" claim above with an actual multi-transcript fixture, not just an assertion in this doc)

## Testing considerations

Same tiering as parts 1 and 2: the comparator and merge logic are pure/unit-testable with zero DB/LLM involvement; the tagging-retirement removal touches shipped UI (tag pills), so re-run whatever e2e/browser check currently covers the bank list's tag rendering (if any) to confirm removing it doesn't leave a broken reference — grep `rack.js`/`tests/e2e/` for `tag` before removing, per this project's "changing user-visible text/control changes what e2e selects by" rule.
