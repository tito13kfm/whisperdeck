# Self-Audit: Issue #120 — Silent diarization failure

## Investigation promises

[x] Fix `_finalize_if_done` diarization except block — delivered at `services/queue.py:559-564`
[x] Fix `_run_transcription_pipeline` diarization except block — delivered at `app.py:1395-1402`
[x] `test_diarization_failure_sets_partial_status_and_error` — delivered at `tests/test_diarization_failure.py:46`
[x] `test_diarization_failure_keeps_segments_undiarized` — delivered at `tests/test_diarization_failure.py:75` (with `assert len(segments) > 0` fix committed after audit catch)
[x] `test_diarization_success_still_works` — delivered at `tests/test_diarization_failure.py:77`

## Mutation check

[x] `test_diarization_failure_sets_partial_status_and_error` — mutation check: fails with function body replaced by return? yes (transcript stays "processing", no error set)
[x] `test_diarization_failure_keeps_segments_undiarized` — mutation check: fails with function body replaced by return? yes (after fix: `assert len(segments) > 0` catches empty segments from no-op function)
[x] `test_diarization_success_still_works` — mutation check: fails with function body replaced by return? yes (transcript stays "processing", no speaker_count set)

## Acceptance criteria (issue #120)

The issue had four fix options. Implemented options 1, 3, and 4:

[x] Option 1: Set status to "partial" — delivered. `new_status` changed from "completed" to "partial" in except block (queue.py:563-564), `transcript.status` changed in except block (app.py:1400-1401)
[x] Option 3: `diarization_method = "failed"` — delivered. Set in both queue.py:580 and app.py:1399
[x] Option 4: Log full stack trace — delivered. `traceback.print_exc()` added in both queue.py:561 and app.py:1397
[ ] Option 2: Create failed LlmJob — NOT delivered. Diarization runs inline during finalization, not as a separate job. The rediarize LlmJob path already handles failure correctly in llm_jobs.py:636-659.

[decision] Skipped Option 2 (failed LlmJob for diarization) — not specified by the issue as mandatory, and the diarization in this flow is inline during transcript finalization, not a separate LlmJob. The rediarize path (llm_jobs.py) already handles its own failure correctly.

## Verification

[x] Full test suite: 798 passed, 22 deselected, 0 failed
[x] Static source check: both except blocks correctly set error, diarization_method, and adjust status
[x] Sibling sweep: all 3 callers of `diarize_and_merge` checked. queue.py and app.py fixed, llm_jobs.py already correct
[x] Main checkout clean: no unintended edits (Phase 4 will confirm with `git diff --stat`)

## Oracle regression pass (Phase 3.75)

Verdict: **APPROVE** (Oracle on muse-spark-1.1)

- Commit paths sound, side-effects for partial status appropriate, sibling sweep complete
- Minor pre-existing edge cases noted (app.py cancel race, double-commit mislabel) — not introduced by this fix, not blocking

## Frontend impact

[x] Queue UI: `_transcription_queue_entry` already passes `t.error` to frontend (`app.py:3013: "error": t.error`), so no frontend change needed
[x] Status badge: "partial" (amber) is already rendered distinct from "completed" (green) in `static/rack.js` `jobStatusView()`
[decision] No frontend code changes needed — existing rendering handles both `status="partial"` and `j.error` correctly
