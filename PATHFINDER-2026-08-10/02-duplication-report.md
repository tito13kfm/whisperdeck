# Duplication Report — WhisperDeck

Synthesized from two Phase 2 passes (within-feature, cross-feature), each independently reading source beyond the flowchart summaries. Ordered by severity/leverage, not by feature.

---

## 1. Two independent job-queue implementations, diverged concurrency-safety discipline (PRIMARY FINDING)

**Concern**: `services/queue.py` (TranscriptionJob, chunked-transcription queue) and `services/llm_jobs.py` (LlmJob, llm-job-queue) each run a worker loop with a "resurrect failed jobs to pending" sweep. `llm_jobs.py` does it atomically; `queue.py` does not.

**Locations**:
- `services/queue.py:812-816` — SELECT loads jobs with `status IN ("pending","failed")`.
- `services/queue.py:818-822` — plain read-then-write: `job.status = "pending"; job.error = None`, no WHERE-clause guard on current status, single `db.commit()` for the whole batch at `queue.py:832`.
- `services/queue.py:304-322` (`retry_failed_chunks`) — a second, independent writer targeting the identical `status=="failed"` filter, invoked from an HTTP route in its own DB session.
- `services/llm_jobs.py:1036` — `_transition(db, job.id, "pending", expect=("failed",), error=None)`, compiling to `UPDATE llm_jobs SET status='pending' WHERE id=? AND status IN ('failed')` (`_transition` at `services/llm_jobs.py:350-380`, `.filter().update(synchronize_session=False)`).
- No ORM-level optimistic locking (`version_id_col`) on either model (`database/__init__.py:85-101` TranscriptionJob, `104-123` LlmJob) — `queue.py`'s safety is only whatever discipline the code itself imposes, and here it imposes none.

**Why they diverged**: `_transition`'s docstring (`llm_jobs.py:358-364`) states PR #389 fixed two independent read-then-write races found across two review rounds (a terminal-state claim racing a cancel) by collapsing every LlmJob status write through this one CAS primitive. That fix was applied only to `llm_jobs.py` — the sibling `queue.py` still writes `job.status` directly wherever it needs to.

**Reachability / failure mode** (verified, not assumed): `cancel_transcript_jobs` only touches `pending` jobs, so the *exact* collision PR #389 fixed (cancel racing a terminal claim) has no direct analog on the same row here. The real analog: `retry_failed_chunks` (manual retry route) and the tick's resurrection sweep both filter on `status=="failed"` and can run concurrently from different sessions. SQLite's `busy_timeout=5000` (`database/__init__.py:661-672`) means a commit can queue up to 5 seconds — comparable to `queue_worker_loop`'s own 5s tick interval. During that stall, a job loaded as "failed" by one writer could be progressed all the way to completed/re-failed by the other before the first writer's delayed, unconditional `UPDATE ... SET status='pending'` finally lands — silently reverting a job that has since finished, back to pending for re-dispatch. `llm_jobs.py`'s CAS form would simply no-op (0 rows matched) in the same scenario.

**Verdict**: legitimate, not a mischaracterization. Narrower than "any cancel clobbers any resurrection" — requires a concurrent writer plus lock delay — but genuinely reachable under real contention (this app already has a documented SQLite-lock-contention history, see issue #391/#66 referenced in code comments). **This is the highest-value unification target in the codebase.**

---

## 2. The "auto-enqueue after finalize" mirror pair has already drifted (STRONGEST STRUCTURAL FINDING, corroborated independently by both passes)

**Concern**: Two call sites contain the same ~9-line sequence (auto_correct check → `enqueue_auto_correction` else `enqueue_pipeline_classify` → `enqueue_auto_classify` → voice_note/voice_dump conditionals → unconditional `enqueue_auto_tagging`), each carrying a comment pointing at the other, and each has already diverged in a way the comments don't capture.

**Locations**:
- `app.py:1450-1470` (inline transcription-pipeline finalize) — comment at 1468-1469: "keep this site in lockstep with services/queue.py:_finalize_if_done."
- `services/queue.py:678-709` (chunked-queue finalize) — mirror comment at 706-707.

**Divergence found**: `app.py:1450-1451` accepts a caller-supplied `auto_correct` override with a `None`-fallback to settings; `queue.py:690` reads straight from `user_settings`, no override parameter. `queue.py:678` additionally gates the whole block on `should_fire_side_effects` (issue #328's "don't re-enqueue on byte-identical merged text" guard); the inline path has no equivalent gate.

**Verdict**: worth consolidating into one shared `enqueue_post_transcription_jobs(db, transcript, user_settings, auto_correct_override=None)`, called from both sites with an explicit parameter for the one axis that legitimately differs. Both call sites have identical objects in scope; no transaction conflict expected (flag for a quick check against `enqueue_llm_job`'s transaction assumptions before implementing).

---

## 3. Diarization write-back boilerplate — small, real duplication wrapped in legitimate specialization

**Locations** (all call `diarize_and_merge` then unpack a 3-tuple and write `segments`/`speaker_count`/`diarization_method` + commit):
- `app.py:1420-1440` (inline transcription-pipeline)
- `services/queue.py:607-660` (chunked-queue finalize)
- `services/llm_jobs.py:743-768` (rediarize job branch)

**Why they diverged (verified as load-bearing, not incidental)**: `queue.py:607-660` explicitly rolls back before the diarization await and re-fetches via `expire_all()` afterward, because that coroutine shares one DB session across concurrently-processed transcripts in a single tick — required so a concurrent `/cancel` is actually observed. `llm_jobs.py:743-768` uses its own per-job session, checks `job.status=="cancelled"` via `db.refresh(job)` post-await, and calls `clear_relabel_history` first (rediarize-specific: wholesale re-segmentation invalidates index-based relabels). `app.py:1420-1440` has no cancel-check — single synchronous request, no concurrent actor to race.

**Verdict**: the 5-6 line "unpack and write" tail is a minor real duplication (small shared-helper candidate); the surrounding cancel/rollback logic is legitimate specialization driven by three different concurrency substrates and should NOT be unified. The 4th superficial "call site" (`POST /api/diarize`, app.py:2579-2616) is a different code path entirely (no merge, no transcript write) — not part of this duplication.

---

## 4. classification-tagging / llm-job-queue enqueue-helper family — repeated boilerplate

**Concern**: The six `enqueue_auto_*` functions (`services/llm_jobs.py:180-313`) each repeat: read settings keys → `resolve_provider_key` → keyless-provider guard → `enqueue_llm_job`. ~10 lines x 6, varying only in settings-key names, kind string, and message.

**Verdict**: worth a shared `_enqueue_auto(db, transcript, user_settings, kind, provider_key_setting, model_key_setting, label)`. Secondary/lower-priority: the outer commit/try/`_finish` shell in `run_llm_job` repeats loosely across ~6 job-kind branches, but bodies diverge enough that this is a smaller win than the enqueue-family finding.

---

## 5. Provider-key / provider-config resolution — mostly centralized, with one real drift risk

**Checked broadly**: every `app.py` LLM-route call site (8 locations: 1519, 2641, 2686, 2773, 3018, 3046, 3257, 3394) correctly calls the shared `resolve_provider_key` (`services/settings.py:90`). `decrypt_api_key` (`services/security.py:106`) has exactly one caller in the whole repo (`services/settings.py:84`). This lead is otherwise clean.

**One real exception found**: `_run_transcription_pipeline` (`app.py:1262-1275`) — the single function that kicks off every transcription (single, retranscribe, bulk) — **bypasses `resolve_provider_key` and re-implements the same `ProviderConfig` lookup + decrypt inline**, using a module-level `SESSION_SECRET` global instead of `resolve_provider_key`'s own secret read. Currently resolves to the same value, but is a real drift risk sitting on the most-executed code path in the app. Worth replacing with a direct call to `resolve_provider_key`.

**Secondary findings**:
- Raw `ProviderConfig` lookup query repeated verbatim at ~7 sites (`app.py:581,988,1014,1033,1070,1263,3284`) — worth a `_get_provider_config(db, user_id, name)` helper.
- The "resolve key + guard + raise 400" block is duplicated at 4 route sites across reformatting-export-assistant and voice-notes-dump (`app.py:2685-2688, 3017-3020, 3045-3048, 3393-3396`) and **the error message has already drifted** (one omits the "add one in the service panel" hint) — worth `require_provider_key(db, user_id, provider)` in `services/settings.py`.

---

## 6. Batch/queue status aggregation — cross-feature, confirmed dead-vs-live split (not a merge candidate)

**Locations**:
- Backend: `app.py:1695-1767` (GET /api/batches, GET /api/batches/{id}) — SQL `GROUP BY batch_id` over `Transcript.status`.
- Frontend: `static/batch_aggregate.js:8-31` (`computeBatchAggregate`) — pure JS reducer over a job list from GET /api/jobs, using a DIFFERENT status vocabulary (adds running/queued/waiting on top of completed/failed/partial/pending/cancelled).

**Confirmed**: grep of all `static/*.js` for `api/batches` finds only `rack.js:3578` calling the cancel endpoint — nothing fetches plain GET /api/batches; those two backend routes are exercised only by `tests/test_batch_api.py`. The UI's actual batch-progress bar comes entirely from the frontend JS aggregate over `/api/jobs`.

**Verdict**: this is cross-feature (backend batch-bulk-files vs. frontend web-ui-signal-rack), not an active bug (only one path is live), and the two implementations don't even share a status vocabulary — reconciling them would be wasted effort. **Correct action is deletion of the dead backend routes, not unification.**

---

## 7. Smaller within-feature findings (lower priority, listed for completeness)

- **correction-hotwords**: `app.py:1515-1528` (upload-time context_doc, swallows exceptions) vs `app.py:3254-3273` (POST /context, raises 502) — identical resolve-key → guard → `extract_hotwords_from_doc` sequence, diverging only in failure handling. Candidate: shared `_resolve_and_extract_hotwords(...)` returning `(terms, error)`.
- **chunked-queue**: (a) `compute_audio_seconds_used` (queue.py:55-78) and `_oldest_contributing_timestamp` (105-127) build an identical two-part query, differing only in the extracted aggregate — candidate `_contributing_rows(...)`. (b) `retry_failed_chunks` (304-322) and `resume_cancelled_chunks` (356-386) are near-identical bulk-reset routines — candidate `_reset_jobs(db, transcript_id, from_status, to_status, reset_attempts)`.
- **auth-accounts**: (a) `hash_reset_token`/`hash_device_token` (auth.py:19-23, 48-52) are byte-identical SHA-256 wrappers, trivial merge. (b) Rate-limit-check boilerplate repeated across 5 routes — candidate `_enforce_rate_limit(request, route, max_requests, window_seconds)`. (c) `admin_promote`/`admin_demote` (app.py:872-897) near-identical ~9-line bodies differing in one boolean and one error string — candidate shared `_set_admin(...)`.
- **transcription-pipeline (backends/)**: (a) `check_health()` byte-identical between groq.py and openai.py, structurally identical (minor variation) in replicate.py/openrouter.py/assemblyai.py — candidate `BaseProvider._probe(...)` template method. (b) The multipart `transcribe()` body against the OpenAI-compatible `/audio/transcriptions` endpoint is duplicated across groq.py, openai.py, openrouter.py, local.py — genuinely the same wire protocol — candidate shared `_transcribe_openai_compatible(...)` helper. replicate.py/assemblyai.py correctly excluded (different wire protocols).
- **batch-bulk-files**: `bulk_transcribe` (app.py:1546) validates `kind` and `provider` twice each (global vs. per-file override, 1581-1605) with identical logic — candidate small `_validate_kind`/`_validate_provider` helpers.
- **web-ui-signal-rack**: the 9-field LLM-job-slot name list is hand-typed three times (`_jobFingerprint` rack.js:3784-3789, `scheduleDetailPoll`'s gate 3796-3799, `jobActiveSnapshot` 4245-4256; a comment at 4240-4241 already acknowledges this). Candidate: one shared `DETAIL_JOB_SLOTS` array driving all three — real risk since new job kinds have been added over time and could miss one list.

---

## 8. Explicitly checked and ruled out (not worth consolidating)

- **LLM-call wrapper** (`services/llm_client.py:chat_completion`): zero retry/backoff logic itself. Every caller (correction.py, reformatting.py, voice_notes.py, transcription.py, assistant.py, classification.py, tagging.py) either calls it directly or through a one-hop per-feature wrapper supplying only feature-specific defaults. Divergent error handling (tagging never raises, classification deliberately re-raises to feed the retry sweep, correction/reformatting fail the job) is intentional policy per feature, not accidental duplication.
- **Migration/backfill patterns**: the "capture a was_absent flag before ensure_columns, then one-time bulk UPDATE" idiom repeats 3x but entirely within `database/__init__.py` itself (storage-db-layer's own file) — an appropriately centralized intra-file idiom, not cross-feature duplication.
- **voice-id-match**: PR #311 per-segment-similarity-discarded behavior confirmed true but by design (see voice-id-match.md), not a bug; single computation path, nothing to consolidate.
- **run-history-versions**: no duplicated chain-walking/diffing logic; each relabel function has exactly one write path.
- **search.py**: `search_transcripts` vs `search_transcripts_snippets` already share real primitives.
- **voice-notes-dump**: classify/structure/segment functions already funnel through a shared `_generate` helper; remaining parse/fallback logic diverges enough not to unify further.
- **reformatting.py**: `format_as_*` functions share only a 2-line tail; prompt text dominates each body.
- **assistant.py vs export_markdown filename sanitization**: deliberately different security postures (LLM-controlled input vs. server-known title), both independently verified sound — not a duplication to merge.
- **storage-db-layer**: no per-model serialization boilerplate in database/__init__.py (serialization lives in app.py, out of scope); `ensure_columns` is already the single shared migration helper.

## Confidence and gaps

High confidence overall — nearly every finding is backed by a direct source read (not flowchart summaries alone), and the top two findings (job-queue race divergence, enqueue-mirror drift) are corroborated independently by both Phase 2 passes plus the code's own comments admitting the coupling. Gaps: rack.js's ~13 page-render functions were spot-checked, not exhaustively diffed beyond the job-poll cluster; `backends/__init__.py`'s `get_provider` factory not read in full; whether extracting the enqueue-family helpers would hit hidden transaction-boundary assumptions inside `enqueue_llm_job` was flagged but not verified — check immediately before implementing any of section 2 or 4.
