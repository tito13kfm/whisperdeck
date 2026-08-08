# Self-audit: Issue #267

Not an OMO issue-runner session (Claude Code, no `investigation.md` precursor) — the sections below map to #267's own acceptance criteria and plan items instead.

## Issue acceptance criteria

- [x] Classification is persisted and observable through the existing contract — delivered, confirmed at app.py:319 (`classification_status`/`classification_confidence`/`classification_provenance`) and app.py:351 (`classify_pipeline_job`)
- [x] No duplicate or skipped routing jobs — delivered, confirmed at services/llm_jobs.py:359 (single trigger call site for both inline and chunked completion), test_run_llm_job_correction_completion_triggers_pipeline_classify_when_pending (tests/test_llm_jobs.py:493)
- [x] Failure leaves a safe, retryable state and does not destroy the transcript — delivered, confirmed at services/llm_jobs.py:432 (`classification_status` set to `"failed"`, distinct from ambiguous `"pending"`), test_run_llm_job_classify_pipeline_fails_retryably_on_malformed_response (tests/test_llm_jobs.py:585)
- [x] Existing explicit kind behavior remains unchanged until the follow-up routing issue lands — delivered, confirmed by zero changes to any of the guard call sites listed in decision 11's table (app.py:1050, 1269-1277, 2352, 2394-2397, 2511-2514, 2544-2547, 2696) — those are #268's scope

## Plan items (issue #267 body)

- [x] Persisted classification/override representation with migration and safe defaults for existing transcripts — delivered, confirmed at database/__init__.py:43-45 (columns), database/__init__.py:433 (one-time legacy backfill)
- [x] Column-absence flag captured before `ensure_columns` runs, so no startup ORM query crashes against a pre-#267 database (fixed after an advisor-caught bug in the first draft — see "Review history" below) — delivered, confirmed at database/__init__.py:418
- [x] Narrow classifier service with a versioned JSON schema, provider/model settings, parse validation, fallback, timeout, retry — delivered, confirmed at services/classification.py:34 (`classify_pipeline_kind()`), services/settings.py:29-33 (`classification_provider`/`classification_model`/`classification_confidence_threshold`); timeout/retry reuse the existing shared httpx timeout and `AUTO_RETRY_KINDS` job-level retry rather than bespoke per-call logic — no separate retry loop needed inside the classifier itself
- [x] Classifier raises on malformed/empty response, invalid `kind`, or invalid `confidence` (no silent fallback, unlike `classify_intent()`) — delivered, confirmed at services/classification.py:69, test_classify_pipeline_kind_raises_on_malformed_json (tests/test_classification.py:72)
- [x] New LlmJob kind and worker dispatch, covering inline and chunked finalization without duplicate enqueue — delivered, confirmed at services/llm_jobs.py:23 (kind added to `VALID_KINDS`/`IO_KINDS`/`AUTO_RETRY_KINDS`/`_SERIALIZED_JOB_KINDS` together), services/llm_jobs.py:235 (`enqueue_pipeline_classify()` no-ops unless `classification_status == "pending"`), test_enqueue_pipeline_classify_noops_when_not_pending (tests/test_llm_jobs.py:469)
- [x] Store provenance and result state so the UI/API can distinguish pending, successful, failed, and manually overridden classification — delivered, confirmed at services/llm_jobs.py:432-439 (provenance dict + status), test_run_llm_job_classify_pipeline_accepts_above_threshold (tests/test_llm_jobs.py:533) and test_run_llm_job_classify_pipeline_stays_uncertain_below_threshold (tests/test_llm_jobs.py:559)
- [x] Keep `classify_intent()` behavior unchanged and preserve #253 boundaries — delivered, confirmed by zero changes to services/reformatting.py and full suite pass
- [x] Update the post-pipeline router only after the classification result is available, with a deterministic fallback — delivered, confirmed at services/llm_jobs.py:350 (`job.status == "completed"` gate skips the classify-enqueue when a concurrent cancel wins over a correction "ok" result), test_run_llm_job_correction_cancel_race_skips_pipeline_classify (tests/test_llm_jobs.py:642)

## Tests (issue #267 body)

- [x] Classifier schema, malformed/empty responses, provider errors, retry and fallback — delivered, confirmed at tests/test_classification.py (10 tests: valid response, corrected-text preference, full_text fallback, no-text, malformed JSON, empty response, invalid kind, invalid confidence x5 parametrized, provider HTTP error)
- [x] One job per transcript across inline/chunked completion and retry — delivered, confirmed at test_run_llm_job_correction_completion_skips_pipeline_classify_when_override (tests/test_llm_jobs.py:515), test_run_llm_job_correction_cancel_race_skips_pipeline_classify (tests/test_llm_jobs.py:642)
- [x] Serialization contract for all existing kinds plus auto/pending/failed states — delivered, confirmed at tests/test_serialize_transcript_contract.py (`test_classification_state_defaults_to_override`, updated `EXPECTED_KEYS`)
- [x] Post-restart worker behavior and existing voice-note/dictation/meeting regressions — delivered, confirmed at test_backfill_legacy_classification_marks_preexisting_rows_as_override (tests/test_classification_migration.py:12) and full suite (650 passed, 8 deselected)

## Not delivered (deferred to later issues in the chain, not silently dropped)

- [ ] UI-reachable path for this feature — NOT delivered: out of scope for #267; nothing sets `classification_status="pending"` in production yet, that's #268's "auto" kind sentinel
- [ ] Trigger for the no-correction case (auto_correct off / correction failure / voice_note kind) — NOT delivered: filed as a follow-up comment on issue #268, since it only matters once #268 introduces transcripts that can reach `pending`
- [ ] Reclassification of a previously-`failed` transcript on retranscribe — NOT delivered: that's #271's scope (design decision 9); filed a comment on #271 noting `enqueue_pipeline_classify`'s gate needs an explicit reset from `failed`, not just carrying `success`/`override` forward

## Review history

Second-opinion review was `advisor()` (Opus, full transcript context), called before committing to the implementation approach (trigger placement, column naming, threshold gating) and again before opening the PR (full diff, no findings at that point). A third-party `/audit-pr` pass (GPT-5.6 Luna) then ran independently and returned **BLOCK** with two real findings:

1. Classifier failure left `classification_status` at `"pending"` instead of a distinct `"failed"` — fixed at services/llm_jobs.py:432, with a cancel-guard so a concurrent cancel isn't overwritten.
2. Classification could enqueue after a correction job's `"ok"` result raced against a concurrent cancel — fixed at services/llm_jobs.py:350.

Both fixes re-verified against the code (not just re-running tests) via a follow-up `advisor()` call, with two new regression tests (test_run_llm_job_classify_pipeline_failure_respects_concurrent_cancel, test_run_llm_job_correction_cancel_race_skips_pipeline_classify). Also caught a migration-ordering bug pre-audit that neither unit tests nor the full suite exercised (nothing ran `init_db` against a pre-migration DB with a completed job present) — fixed at database/__init__.py:418, with a discriminating regression test at tests/test_classification_migration.py:12.

**As of this writing, PR #273 has not been re-audited after the fixes** — the BLOCK verdict should be treated as open until a second `/audit-pr` pass clears it.
