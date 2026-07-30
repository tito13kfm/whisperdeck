# Entity extraction core: schema, background job, deterministic merge, voice roster reconciliation

> One-line status: Planned (design pass done via exploration + Plan-agent review + advisor review). Part 1 of 5 on the meeting-knowledge-layer master tracker (#241). Backend-only — no rack.js changes in this slice.

## Motivation

Issue #241 is the master tracker for a "meeting knowledge layer": after a transcript finishes, extract structured entities (people, projects, decisions, action items) so they can eventually become browsable, cross-linked pages ("Project X: every meeting that touched it, decisions made, open action items"). That later payoff (parts 2–5: browse UI, topic grouping, cross-transcript graph-decay search, staleness curation) only works if the underlying entity rows are trustworthy — specifically, if the same person or project mentioned across ten meetings ends up as *one* row, not ten near-duplicates with slightly different spellings.

This plan covers only the foundation: the schema, the background LLM job that proposes candidate entities, and the deterministic (non-LLM) merge logic that decides identity. Concept credit: inspired by GrayBox (Aaryan Verma, MIT license) — specifically its principle that the LLM should only ever propose extractions, never decide merges. Ideas only; no code was read or reused from that project.

## Design principle (load-bearing)

The LLM only proposes candidate names as structured JSON per transcript. Deterministic Python code decides what merges with what, using fuzzy name matching with a tunable threshold. **The LLM never decides merges.** Extracted person entities must reconcile against the existing voice roster (`VoiceProfile`, populated by `services/voice_id.py`) rather than creating duplicate identities for people who are already enrolled voices.

## Proposed approach

**1. One `Entity` table with a `type` discriminator**, not four separate tables (Person/Project/Decision/ActionItem). This mirrors the existing `LlmJob.kind` polymorphic-table pattern already in this codebase (one table, many kinds of work) and gives parts 2–5 a single uniform query surface instead of `UNION ALL`s across four tables once cross-type browsing/ranking/search exist. Trade-off accepted: `name` means a short identity noun for `person`/`project` but a full sentence for `decision`/`action_item`, and `voice_profile_id` is only meaningful for `person` rows — the same shape of sparsity `LlmJob.provider`/`LlmJob.model` already tolerates for `CPU_KINDS`.

**2. A separate `EntityMention` join table**, one row per `(entity_id, transcript_id)` pair, mirroring `TranscriptTag`'s composite-primary-key, no-surrogate-id shape. A rerun of the extraction job for a transcript **replaces** that transcript's mention rows (delete-then-reinsert) — it never appends across reruns, matching `TranscriptTag`'s own rerun semantics. Granularity is transcript-level, not utterance-level: three mentions of "David Chen" within one transcript collapse into one `EntityMention` row with a count, not three rows. Deliberately coarse for part 1.

**3. New `LlmJob` kind, `"entities"`**, following `tagging`'s existing shape exactly: single LLM call, JSON-object response, a parsing function that **never raises** (degrades to an empty result on any failure, same contract as `services/tagging.py`'s `generate_tags`), then a deterministic Python merge step, then a delete-then-reinsert write. Auto-triggered only — no user-facing "Extract entities" button, matching both `tagging`'s existing precedent (no manual route exists for it either) and the issue's own framing ("after a transcript finishes... run a background LLM job," not a user action).

**4. Deterministic merge, in a new `services/entities.py`:**
- Normalize (lowercase, strip punctuation except apostrophe/hyphen, collapse whitespace).
- Fuzzy-match with stdlib `difflib.SequenceMatcher` — no new dependency. The codebase has zero existing fuzzy-string-matching code or libraries today (confirmed via exhaustive grep — no rapidfuzz/fuzzywuzzy/Levenshtein anywhere), and per-user entity counts are small enough that a faster third-party matcher buys nothing.
- Short-name guard: names under 5 normalized characters require an exact match — pure ratio-based fuzzy matching on short strings produces real false positives (`"Dan"` vs `"Dana"` scores ~0.86, comfortably above any workable threshold).
- Fuzzy matching applies only to `person` and `project` (default threshold 0.82, configurable per-user via a new `entity_dedup_threshold` settings key, no new route needed — it rides the existing settings PATCH endpoint). `decision`/`action_item` dedupe on **exact** normalized match only, because their "name" is a full sentence — fuzzy-collapsing two similar-but-distinct decisions would destroy information, where an occasional duplicate row is just noise.
- Person reconciliation order: check this user's **existing Person entities first**, then the **voice roster** (`VoiceProfile`) only when creating a new entity. An entity is created with the roster's canonical spelling whenever a roster match exists at creation time, so every later mention converges via the entities-first check without re-querying the roster. The gap this leaves — a `VoiceProfile` enrolled *after* an entity already exists won't retroactively link — is an accepted part-5 curation concern, not part 1's problem.
- The merge decision function itself, `plan_merge(...)`, takes plain dicts (no DB session, no LLM call) so it's fully unit-testable and independently auditable — this is what makes "the LLM never decides merges" a checkable property, not just a design intention.

**5. Orphan cleanup.** Replacing a transcript's mentions on rerun can leave an `Entity` with zero mentions (e.g. the model no longer extracts a person it extracted last time). Decision: auto-delete any `Entity` that hits zero mentions, both on rerun and on transcript delete. This is a data-integrity rule (a mention-less entity was never real to begin with), distinct from part 5's staleness-curation principle ("never auto-delete" for entities that are still mentioned somewhere but have simply gone quiet over time) — the two rules don't conflict because they cover disjoint cases.

**6. One narrow, verification-only read route**, `GET /api/transcripts/{id}/entities`, scoped and 404'd exactly like every other transcript route. This exists purely to confirm the feature works end-to-end without needing any frontend — it is explicitly *not* the part-2 browse UI, and the extracted entities are **not** embedded into the main transcript-detail payload (`_serialize_transcript`) in this slice, so this change touches zero existing frontend contracts or tests.

## Code touchpoints (files + symbols, no line numbers)

- `database/__init__.py`: new `Entity`, `EntityMention` model classes; a `Transcript.entity_mentions` cascade relationship (SQLite's `foreign_keys` pragma is never enabled in this app, so cascade-on-transcript-delete must be done at the ORM level, same reasoning as the existing `relabel_history` relationship); both classes added to `__all__`; `Index` added to the sqlalchemy import line.
- `services/entities.py` (new): `normalize_name`, `fuzzy_ratio`, `find_best_fuzzy_match`, `plan_merge` (pure, no DB/LLM), `extract_entities` (LLM call + parse, never raises), `apply_extraction` (DB glue: load candidates, call `plan_merge`, persist), `delete_orphaned_entities`.
- `services/llm_jobs.py`: `VALID_KINDS` / `IO_KINDS` (provider-API-bound, same pool as `tagging`) / `AUTO_RETRY_KINDS` gain `"entities"`; `enqueue_auto_entities` (mirrors `enqueue_auto_tagging`'s shape — confirmed by reading it directly, it uses `format_provider`/`format_model` from `user_settings`, same values this new helper should read); a new dispatch branch in `run_llm_job` mirroring `tagging`'s exact shape (progress_total=1, `db.refresh(job)` + cancellation check before any write, then the merge+persist step, then `_finish`).
- `app.py`: import wiring for the new models/helper; the auto-enqueue call site immediately after the existing `enqueue_auto_tagging(...)` call; the new verification-only GET route; an orphan-cleanup call added to `delete_transcript` (capture this transcript's mentioned entity ids before delete, cascade removes the mentions, then delete any of those ids left with zero mentions).
- `services/queue.py`: the matching auto-enqueue call site in `_finalize_if_done`, immediately after its existing `enqueue_auto_tagging(...)` call. **This is the one place this plan calls out explicitly as a Complement Rule risk**: `enqueue_auto_tagging` already has two call sites that the codebase's own comments say must stay in lockstep (one is the direct-upload finalize path, the other is the chunked-upload finalize path) — adding `entities` at only one of them would silently break auto-extraction for exactly one upload path (chunked) while looking complete from the other. Both sites must be touched in the same change.
- `services/settings.py`: `DEFAULT_SETTINGS` gains `entity_dedup_threshold` (default `0.82`).

## Data model / schema changes

```python
class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("user_id", "type", "normalized_name", name="uq_entity_user_type_normalized_name"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String(16), nullable=False)  # person | project | decision | action_item
    name = Column(Text, nullable=False)
    normalized_name = Column(Text, nullable=False)
    voice_profile_id = Column(Integer, ForeignKey("voice_profiles.id"), nullable=True)  # person-only
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    # Deliberately no `mentions` relationship here — see EntityMention note below.


class EntityMention(Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (Index("ix_entity_mentions_transcript_id", "transcript_id"),)
    entity_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), primary_key=True)
    mention_count = Column(Integer, default=1)  # occurrences within this one transcript
    context = Column(Text, default="")  # short LLM-proposed quote, length-clamped
    created_at = Column(DateTime, default=utcnow_naive)
```

Notes worth preserving from the design review:

- `name`/`normalized_name` are `Text`, not a length-capped `String`. `person`/`project` names are short, but `decision`/`action_item` identity is a full sentence deduped by exact match — a hard character cap would let two distinct decisions that happen to share their first N characters silently collapse into one row, exactly the information loss this design otherwise avoids by *not* fuzzy-matching that type. Any length clamping belongs in `services/entities.py`'s normalization step (different limits per type), not the column definition.
- No `Entity.mention_count` column. A cross-transcript total is derivable on demand (`COUNT(*) FROM entity_mentions WHERE entity_id = ...`) and its only proposed use (ranking/decay) belongs to a later part. Don't add the denormalized counter before something reads it — nothing does yet, and a counter nothing maintains-by-invariant is how "mention_count drifted from reality" bugs get born.
- Only **one** relationship in the whole model touches `EntityMention` with `cascade="all, delete-orphan"` — `Transcript.entity_mentions`. SQLAlchemy's orphan rule is "de-associate from *any* delete-orphan parent," so declaring a second one (e.g. `Entity.mentions`) would give `EntityMention` two independent triggers for deletion and produce cascade behavior neither side individually implies. `apply_extraction`/`delete_orphaned_entities` query `EntityMention` directly by filter instead of navigating a relationship.
- `apply_extraction` must call `db.flush()` after writing new `EntityMention` rows and before running any `COUNT`-based check (e.g. "does this entity still have mentions") — pending inserts aren't visible to a query until flushed, so an un-flushed count under-reports.
- Both new tables are additive — `Base.metadata.create_all(engine)` (already called unconditionally in `init_db()`) creates them on both fresh and pre-existing databases with no `ensure_columns`/migration call needed.

## Research notes / decisions made during design review

- **Single Entity table vs. four separate tables** — resolved in favor of one table with a `type` discriminator (see Proposed approach §1). Confirmed by direct decision, not left as an open question.
- **Orphan entities: auto-delete vs. keep-and-flag** — resolved in favor of auto-delete (see Proposed approach §5). Confirmed by direct decision.
- **Fuzzy library: stdlib `difflib` vs. adding `rapidfuzz`** — resolved in favor of stdlib, no new dependency (see Proposed approach §4).
- **Where the fuzzy threshold lives** — a `services/settings.py` `DEFAULT_SETTINGS` entry (`entity_dedup_threshold`), reusing the existing settings PATCH endpoint rather than adding a new one.
- **Serializer scope, cut during review**: an earlier draft of this plan added an `entities_job` field to `_serialize_transcript`/`_dictation_job_fields` "for observability." Cut: the existing `/api/jobs` Queue-screen endpoint already lists any `LlmJob` kind — including a brand-new one — with zero extra code (list/cancel/rerun/dismiss all work automatically), so that observability is already free. Adding a field to the transcript-detail payload is a public-contract change with no payoff in a slice that has no UI to consume it, and it would touch `tests/test_serialize_transcript_contract.py`/`test_bootstrap.py`-adjacent tests for no reason tied to part 1's actual goal. Deferred to part 2, alongside the `rack.js` wiring it actually needs.
- **Known pre-existing bug, flagged not fixed**: `rack.js`'s `_jobFingerprint` function was never updated to include `t.tagging_job` even though `llmJobActive(t.tagging_job)` *is* checked in the poll-continuation guard a few lines later — net effect, if tagging is the only active job on an open transcript detail page, the poll loop correctly keeps polling but never detects the change, so the UI doesn't repaint until something else changes. Out of scope for this backend-only slice (there's no `rack.js` change here at all), but whoever wires `entities_job` into the detail page in part 2 should add it to `_jobFingerprint` correctly — don't repeat the `tagging_job` gap.
- **Something to watch, not a blocker**: this adds a third always-on IO-pool LLM job per completed transcript (alongside auto-tagging and, when relevant, auto-summarization), against the existing `IO_KINDS` concurrency cap of 2, shortly after the Costs dashboard and Queue rate-limit budget gauge shipped (#210). Faithful to the issue's own wording ("after a transcript finishes... run a background LLM job"), so kept as designed — but worth a conscious look at per-transcript LLM spend once this is live, not a decision to revisit before shipping.

## Open questions

- The person-reconciliation gap noted in Proposed approach §4 (a `VoiceProfile` enrolled after a Person `Entity` already exists doesn't retroactively link) — is a one-time backfill pass worth adding later, or is this squarely a part-5 curation-pass concern? Leaning toward the latter; flag if it becomes a real annoyance.
- `enqueue_auto_entities` gates out `transcript.kind == "voice_note"` (single-speaker structured capture doesn't fit "people/projects/decisions" extraction the same way meeting transcripts do). Worth confirming this exclusion is right rather than assumed — voice notes could plausibly still mention a project or an action item worth extracting, just not other people.

## Rough phasing / checklist

**Schema**
- [ ] `database/__init__.py`: `Entity`, `EntityMention` models; `Transcript.entity_mentions` relationship; `__all__`; `Index` import

**Merge core (pure, testable without DB/LLM)**
- [ ] `services/entities.py`: `normalize_name`, `fuzzy_ratio`, `find_best_fuzzy_match`, `plan_merge`
- [ ] Unit tests: determinism under input reordering, short-name guard, fuzzy-vs-exact policy split between `person`/`project` and `decision`/`action_item`, intra-batch threading (two mentions of the same person in one transcript collapse to one create + one merge), voice-roster reconciliation and convergence on repeat mentions

**LLM call + job wiring**
- [ ] `services/entities.py`: `extract_entities` (prompt, parse, never raises), `apply_extraction`, `delete_orphaned_entities`
- [ ] `services/llm_jobs.py`: `VALID_KINDS`/`IO_KINDS`/`AUTO_RETRY_KINDS`; `enqueue_auto_entities`; `run_llm_job` dispatch branch
- [ ] `app.py` + `services/queue.py`: both auto-enqueue call sites (Complement Rule — see Code touchpoints)
- [ ] `services/settings.py`: `entity_dedup_threshold` default

**Verification surface**
- [ ] `app.py`: `GET /api/transcripts/{id}/entities`; orphan cleanup wired into `delete_transcript`

**Tests**
- [ ] `tests/test_entities.py`: kind-partition tests, `enqueue_auto_entities` kind-gating and keyless-skip, worker-dispatch tests with a stubbed LLM response (multi-type extraction, voice-roster reconciliation, cross-transcript merge onto the same entity, rerun replaces mentions and deletes now-orphaned entities but not ones mentioned elsewhere, cancel-during-LLM-call skips the write, cross-user isolation), the new GET route's shape and 404 behavior

## Testing considerations

- The merge core (`plan_merge` and its helpers) should be tested with zero DB/LLM involvement — a suite of synthetic name-variant fixtures proves the "LLM never decides merges" property directly, independent of any provider's actual behavior.
- Worker-dispatch tests need a stubbed LLM response, same pattern as `tests/test_tagging.py`'s `httpx.AsyncClient` patching.
- No `rack.js` change in this slice, so no e2e/UI regression risk from this plan; the existing `test_meeting_and_dictation_have_same_job_field_names`-style serializer contract tests are untouched by design (see the serializer-scope-cut decision above).
- Run scope for this slice: `pytest tests/test_entities.py tests/test_llm_jobs.py -v` is sufficient; no browser e2e pass needed given there's no UI surface.
