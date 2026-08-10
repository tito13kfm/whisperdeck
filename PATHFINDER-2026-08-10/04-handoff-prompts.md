# Handoff Prompts — WhisperDeck Unification

Copy-paste each block into `/make-plan`. Ordered by priority (A is the safety fix, do first if only doing one).

---

## Handoff 1: Unify job-status transitions (A)

```
Extract the atomic compare-and-set job-status-transition primitive from services/llm_jobs.py
into a new shared module services/job_transitions.py, and port services/queue.py's unsafe
plain read-then-write status changes onto it.

Context: services/llm_jobs.py:350-380 (_transition) does
  UPDATE llm_jobs SET status=?, **fields WHERE id=? AND status IN (expect)
via SQLAlchemy .filter().update(synchronize_session=False). This was added in PR #389 to fix
two independent read-then-write races (a terminal-state claim racing a concurrent cancel).
services/queue.py never received the same fix: its resurrection sweep at queue.py:818-822 does
  job.status = "pending"; job.error = None
with no WHERE-clause guard on current status, and retry_failed_chunks (queue.py:304-322) does
the same pattern from a separate HTTP-triggered session. Under SQLite's busy_timeout=5000
(database/__init__.py:661-672), a delayed commit from one writer can silently revert a job that
has since completed back to "pending" — the lost-update PR #389 already fixed for the sibling
LlmJob queue.

Exact call sites to rewrite:
- services/queue.py:818-822 (queue_worker_tick resurrection sweep)
- services/queue.py:304-322 (retry_failed_chunks)
- services/queue.py:325-353 (cancel_transcript_jobs)
- services/queue.py:356-386 (resume_cancelled_chunks)
- services/llm_jobs.py:350-380 (_transition) — becomes a thin wrapper around, or is replaced
  entirely by, the new shared services/job_transitions.py:transition(db, model_cls, job_id,
  new_status, expect=..., **fields)

Relevant flowchart: PATHFINDER-2026-08-10/01-flowcharts/chunked-queue.md (state machine section)
and PATHFINDER-2026-08-10/01-flowcharts/llm-job-queue.md (state machine section, PR #389 note).

Anti-pattern guards:
- Do not merge the two queue engines (TranscriptionJob vs LlmJob) into one polymorphic job
  runner — they have genuinely different dispatch shapes and concurrency pools. Only the status
  UPDATE statement is unified.
- Do not add a feature flag or keep the old plain-write path "for compatibility" — replace it
  outright, this is a correctness fix.
- Verify TranscriptionJob has no ORM-level version_id_col before assuming the CAS filter alone
  is sufficient (checked already: it does not, confirmed database/__init__.py:85-101).
```

---

## Handoff 2: Unify the post-transcription enqueue path

```
Collapse the two mirrored "enqueue correction/classify/voice-note/voice-dump/tagging jobs after
a transcription finishes" call sites into one shared function, and collapse the six near-identical
enqueue_auto_* helper bodies in services/llm_jobs.py into one parameterized helper.

Context: app.py:1450-1470 (inline transcription-pipeline finalize) and services/queue.py:678-709
(chunked-queue finalize) contain the same ~9-line sequence and each carry a comment telling the
reader to keep the other one in sync ("keep this site in lockstep..."). They have already
diverged: app.py accepts a caller-supplied auto_correct override with None-fallback to settings;
services/queue.py reads straight from settings with no override, and additionally gates the whole
block on should_fire_side_effects (issue #328's guard against re-enqueuing on byte-identical
merged text) which app.py's inline path has no equivalent of.

Separately, services/llm_jobs.py:180-313 defines enqueue_auto_correction, enqueue_auto_classify,
enqueue_auto_voice_note, enqueue_auto_voice_dump, and enqueue_auto_tagging, each repeating:
read a settings key -> resolve_provider_key -> keyless-provider guard -> enqueue_llm_job.

Exact call sites to rewrite:
- New function services/llm_jobs.py:enqueue_post_transcription_jobs(db, transcript,
  user_settings, *, auto_correct_override=None) replacing the bodies at app.py:1450-1470 and
  services/queue.py:678-709. The should_fire_side_effects gate stays at the services/queue.py
  call site (it's specific to the chunked-finalize context), not inside the new function.
- New private helper services/llm_jobs.py:_enqueue_auto(db, transcript, user_settings, *, kind,
  provider_setting, model_setting) backing the five existing public enqueue_auto_* functions,
  which keep their names and per-kind gating logic (e.g. voice_note/voice_dump's effective_kind
  check) but delegate their shared body to _enqueue_auto.

Relevant flowchart: PATHFINDER-2026-08-10/01-flowcharts/classification-tagging.md and
PATHFINDER-2026-08-10/01-flowcharts/llm-job-queue.md.

Anti-pattern guards:
- Keep the five enqueue_auto_* functions individually named and callable — do not replace them
  with a single generic dispatch-by-string-kind call at every call site; existing callers outside
  this refactor (app.py, services/queue.py, services/llm_jobs.py's classification branch) must
  keep working unchanged.
- No feature flag for the merged enqueue_post_transcription_jobs — both call sites switch over
  outright.
- Before implementing, verify enqueue_llm_job's transaction-boundary assumptions aren't violated
  by calling it from the new shared function in either caller's session context (flagged as an
  unverified gap in 02-duplication-report.md).
```

---

## Handoff 3: Centralize provider-key/config resolution

```
Fix the one real drift risk in provider-key resolution and deduplicate two smaller repeated
query/error patterns.

Context: services/settings.py:resolve_provider_key (line 90) is already the correct single
source of truth for API-key resolution and is called from 8 sites in app.py. The one exception:
_run_transcription_pipeline (app.py:1262-1275) — the single most-executed function in the app,
backing every transcribe/retranscribe/bulk-transcribe call — bypasses it and re-implements the
same ProviderConfig lookup + decrypt inline against a module-level SESSION_SECRET global. It
currently resolves to the same value as resolve_provider_key would, but is a live drift risk on
the app's hottest path.

Exact call sites to rewrite:
- app.py:1262-1275: delete the inline ProviderConfig lookup + decrypt, replace with
  api_key, provider_config = resolve_provider_key(db, user.id, provider_name). Pure
  behavior-preserving substitution.
- New helper services/settings.py:get_provider_config(db, user_id, name) -> ProviderConfig | None,
  replacing the verbatim raw ProviderConfig query repeated at app.py:581, 988, 1014, 1033, 1070,
  1263, 3284.
- New helper services/settings.py:require_provider_key(db, user_id, provider) -> str (resolves
  the key, raises HTTPException(400, ...) with one canonical message), replacing the 4
  resolve+guard+raise-400 blocks at app.py:2685-2688, 3017-3020, 3045-3048, 3393-3396 — note
  their error text has already drifted (one omits the "add one in the service panel" hint); pick
  one canonical message when merging, flag the choice to the user rather than guessing which is
  correct.

Relevant flowchart: PATHFINDER-2026-08-10/01-flowcharts/providers-settings-cost.md.

Anti-pattern guards:
- This is pure deduplication of read-only lookups and error-message text — do not introduce any
  new caching, config layer, or behavior branch while touching these call sites.
- Confirm each of the 7 raw-query call sites actually wants "config or None" semantics (matching
  get_provider_config's signature) before blindly substituting — a couple may currently 404 on
  missing config inline and would need the None-check kept at the call site.
```

---

## Handoff 4: Delete dead batch-aggregation backend routes

```
Delete (or explicitly repurpose) the two backend batch-status aggregation routes that have no
frontend caller, rather than trying to unify them with the frontend's own aggregation.

Context: GET /api/batches and GET /api/batches/{id} (app.py:1695-1770) run a SQL GROUP BY
aggregate over Transcript.status. Grep across all of static/*.js confirms zero callers of either
route from the frontend — the only caller found anywhere is tests/test_batch_api.py. The actual
batch-progress UI users see is driven entirely by computeBatchAggregate (static/batch_aggregate.js)
reducing the job list from GET /api/jobs, which uses a DIFFERENT status vocabulary (adds
running/queued/waiting on top of completed/failed/partial/pending/cancelled) than the backend's
transcript-status-based aggregate. These are not a clean mirror pair — do not try to reconcile
their vocabularies into one shared implementation.

Exact action:
- Remove app.py:1695-1770 (list_batches, get_batch) and their SQL aggregate query, OR confirm
  with the user first whether these are a deliberate external/API-consumer-facing surface worth
  keeping despite having no internal caller — ASK, don't assume, since only the user knows if
  external tooling depends on this API.
- Update or remove tests/test_batch_api.py accordingly.
- Leave POST /api/batches/{batch_id}/cancel untouched — it IS called from the frontend
  (rack.js:3578) and is unrelated to the aggregation duplication.

Relevant flowchart: PATHFINDER-2026-08-10/01-flowcharts/batch-bulk-files.md.

Anti-pattern guards:
- Do not build a shared aggregation layer that both the SQL route and the JS reducer call into —
  the two consumers need different vocabularies for different reasons (transcript-level status
  for a hypothetical external API vs. job-level status for the live progress UI); forcing one
  implementation would either break the vocabulary the UI needs or add a translation layer nobody
  asked for. Deletion is simpler than unification here.
```
