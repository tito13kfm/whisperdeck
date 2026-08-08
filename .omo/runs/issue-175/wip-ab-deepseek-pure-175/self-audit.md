# Self-audit: Issue #175 — wip/ab-deepseek-pure-175

## Investigation.md promises

[x] Schema migration: LlmJob.transcript_id nullable=True — `database/__init__.py:100`, migration function `ensure_nullable_llm_job_transcript_id` at line 320, wired in `init_db` at line 455
[x] enqueue_llm_job accepts transcript_id=None — `services/llm_jobs.py:94`, type changed to `int | None`
[x] get_active_job + latest_job accept int | None — both signatures updated at lines 73 and 85
[x] run_llm_job transcript fetch guard for NULL transcript_id — lines 284-290: skip fetch when transcript_id is None
[x] "assistant" in VALID_KINDS — line 23
[x] "assistant" in AUTO_RETRY_KINDS — line 35
[x] "assistant" in IO_KINDS — line 42
[x] run_assistant_job() function — lines 580-630 in services/llm_jobs.py
[x] Dispatch wired in run_llm_job() — lines 565-567
[x] interpret_request() — `services/assistant.py:58`, LLM → JSON plan with validation
[x] execute_plan() — `services/assistant.py:91`, search/summarize/save_markdown steps with validation
[x] Filename sanitization — `_sanitize_filename()` at `services/assistant.py:42`, strips path separators, limits 128 chars
[x] Path traversal guard — `_resolve_export_path()` at `services/assistant.py:52`, validates resolved path stays in export dir
[x] Plan validation (supported actions) — `interpret_request()` line 85, checks all actions against _SUPPORTED_ACTIONS
[x] Plan validation (dependency order) — `interpret_request()` line 89-92, search must precede summarize/save
[x] Unit tests for interpreter — `tests/test_assistant.py:TestInterpretRequest` (7 tests)
[x] Unit tests for executor — `tests/test_assistant.py:TestExecutePlan` (9 tests)
[x] Filename sanitization tests — `tests/test_assistant.py:TestSanitizeFilename` (7 tests)
[x] Path traversal test — `tests/test_assistant.py:TestExecutePlan::test_path_traversal_rejected`
[x] Full integration test (plan executes through all steps) — `tests/test_assistant.py:TestExecutePlan::test_full_plan_executes`

## Issue acceptance criteria (from issue body)

[x] Schema migration: transcript_id nullable — `database/__init__.py:100`, + migration function
[x] interpret_request() — `services/assistant.py:58`
[x] execute_plan() — `services/assistant.py:91`
[x] Wire run_assistant_job() into run_llm_job() dispatch — `services/llm_jobs.py:565`
[x] Unit tests: interpreter + executor, mocked LLM — `tests/test_assistant.py`, 24 tests

## Test suite verification

Sister test run (main worktree's venv): `522 passed, 5 deselected` — no regressions.
New `test_assistant.py`: 24 passed.
Full suite includes all existing tests for search, llm_jobs, correction, etc.

## Known gaps

[ ] No POST /api/assistant endpoint — that's Task 7 (sub-issue 3), not in scope for issue #175
[ ] No GET /api/assistant/result/{job_id} endpoint — Task 8 (sub-issue 3)
[ ] No frontend UI (Assistant page) — Tasks 12-15 (sub-issue 5)
[ ] No export_directory UI field — Task 11 (sub-issue 4, though export_directory already exists in DEFAULT_SETTINGS)
[ ] No live-server integration test — the assistant tests mock all external calls; this is correct for unit tests, live integration is sub-issue 3 (Task 9)
