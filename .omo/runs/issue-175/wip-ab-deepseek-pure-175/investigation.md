# Issue #175 Investigation — Assistant: Intent interpreter + action executor

## Target
Issue #175, standalone. Sub-issue 2 of the LLM Assistant plan. Tasks 3-6.

## Status of dependencies
- Task 1 (`services/search.py` — `search_transcripts()`): **ALREADY EXISTS** at `services/search.py:32`. Fully implemented with term-splitting, LIKE escaping, per-segment matching, user isolation. No changes needed.
- Task 2 (`tests/test_search.py`): **ALREADY EXISTS** at `tests/test_search.py`. Covers the search function. No changes needed.
- Task 10 (`export_directory` in DEFAULT_SETTINGS): **ALREADY EXISTS** at `services/settings.py:31` as `"export_directory": ""`. Settings validation already auto-whitelists any key in `DEFAULT_SETTINGS` (line 113: `if key in DEFAULT_SETTINGS`). No additional settings code needed.

## Real file/function inventory

### Task 3: `interpret_request()` — does NOT exist
File to create: `services/assistant.py`
- Function signature: `async def interpret_request(user_request: str, api_key: str, provider_name: str, model: str, provider_config: dict | None = None) -> dict`
- Pattern to follow: `services/reformatting.py` uses `llm_client.chat_completion()` with `json_mode=True` and a system prompt describing output format
- The plan specifies `transcript_context: str` as a first param, but based on the full plan's `execute_plan()` calling structure, the interpreter doesn't actually get transcript context — it just translates the user's natural language request into a JSON action plan. The transcript text is fed into the `summarize` step later.
- System prompt must describe available actions: `search`, `summarize`, `save_markdown` with their params
- Returns: parsed JSON dict `{steps: [{action, params}]}` or `{error: "..."}` on parse failure
- Validates: all actions in supported set, each step has required params

### Task 4: `execute_plan()` — does NOT exist
File to create: same `services/assistant.py`
- Function signature: `async def execute_plan(db, user_id: int, plan: dict, api_key: str, provider_name: str, model: str, provider_config: dict | None = None, job: LlmJob | None = None) -> dict`
- Depends on: `search_transcripts()` from `services/search.py` (exists)
- Plan validation: supported actions (search, summarize, save_markdown), required params, search before summarize dependency order
- Step dispatching:
  - `search` → `search_transcripts(db, user_id, query)` → collect matching segments
  - `summarize` → LLM call via `chat_completion()` with summarization prompt
  - `save_markdown` → write text to file with path sanitization
- Filename sanitization: strip path separators, limit 128 chars, replace non-`[-_.a-zA-Z0-9]` with `-`
- Path traversal guard: resolved path must stay within export directory
- Progress tracking on job: `progress_done/total` updates per step

### Task 5: Schema + llm_jobs.py changes

#### (a) `database/__init__.py` line 100: `LlmJob.transcript_id`
Currently: `Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)`
Needs: `nullable=True` in the Column definition PLUS a migration for existing databases

Migration approach: The codebase uses `ensure_columns()` for additive changes and the rename-recreate pattern for constraint changes. Since making a column nullable is a constraint change (relaxation), we need the recreate approach. I'll add a new migration function `ensure_nullable_llm_job_transcript_id()` following the existing patterns.

For fresh databases, `Base.metadata.create_all()` will create the column as nullable automatically.

#### (b) `services/llm_jobs.py` line 94: `enqueue_llm_job()`
Currently: `def enqueue_llm_job(db, user_id: int, transcript_id: int, kind: str, ...)`
Needs: `transcript_id: int | None = None` (accepts None for assistant jobs)

#### (c) NULL-safe paths audit
- `get_active_job(db, transcript_id, kind)` at line 73: Uses `LlmJob.transcript_id == transcript_id`. For NULL, SQL becomes `WHERE transcript_id IS NULL`. Correct dedup behavior for assistant jobs — prevents duplicate active assistant jobs. ✅ No changes needed.
- `latest_job(db, transcript_id, kind)` at line 85: Same pattern. Will query `WHERE transcript_id IS NULL` for assistant. ✅ Safe.
- `rerun_llm_job()` at line 248: Passes `job.transcript_id` through to `enqueue_llm_job`. With NULL transcript_id, creates a new NULL-transcript_id job. ✅ Safe after (b) change.
- `cancel_llm_job()`: Takes `job_id`, not `transcript_id`. ✅ No change needed.
- `serialize_llm_job()` at line 48: Generic field extraction. ✅ No change needed.

#### (d) `run_llm_job()` transcript fetch guard
Lines 284-287 fetch transcript unconditionally: `transcript = db.query(Transcript).filter(Transcript.id == job.transcript_id).first()`. With NULL transcript_id, `Transcript.id == None` returns nothing, and the job fails with "Transcript no longer exists". Need guard for assistant jobs.

Fix: Skip transcript fetch for jobs where `transcript_id` is None (currently only assistant). The `transcript` variable won't be used in the assistant handler — the executor accesses search/db directly.

Actually, looking more carefully: ALL existing job kinds require a valid transcript. Only assistant jobs (with `transcript_id=None`) need the skip. The cleanest approach: check `if job.transcript_id is None` before the transcript fetch, set `transcript = None`, and let the assistant handler work without it.

#### (e) Add "assistant" to kind registries
- `VALID_KINDS` (line 20): Add `"assistant"`
- `AUTO_RETRY_KINDS` (line 35): Add `"assistant"` (LLM calls can fail transiently)
- `IO_KINDS` (line 42): Add `"assistant"` (network-dependent, not CPU-bound)
- `_MAX_CONCURRENT_IO_JOBS` = 2 (line 44): No change — assistant shares I/O pool

#### (f) `run_assistant_job()` function
New function in `services/llm_jobs.py` following the `voice_note` pattern:
```python
async def run_assistant_job(SessionLocal, job_id: int, transcription_service, diarization_service=None):
```
Creates its own session, resolves provider key, calls `interpret_request()` then `execute_plan()`, stores result in `job.result_json`, handles errors.

#### (g) Wire into `run_llm_job()` dispatch
Add `elif job.kind == "assistant":` branch that calls `run_assistant_job()`. Place after the assistant-specific transcript guard logic.

### Task 6: `tests/test_assistant.py`
File to create. Tests for `interpret_request()` and `execute_plan()` using mocked `chat_completion`.

## Sibling sweep

### 1. All `transcript_id` params in llm_jobs.py signatures
- `get_active_job(db, transcript_id: int, kind)` line 73 — needs `int | None` for NULL support. Currently `int`. Change to `int | None`.
- `latest_job(db, transcript_id: int, kind)` line 85 — same. Change to `int | None`.
- `enqueue_llm_job(db, user_id, transcript_id: int, kind, ...)` line 94 — same. Change to `int | None = None`.

### 2. All callers of `enqueue_llm_job` in `app.py`
Grep shows only existing callers pass valid transcript IDs. No signature-breaking changes. ✅

### 3. All `transcript_id` uses in `run_llm_job()`
- Line 284: transcript fetch — guard needed for assistant (NULL check)
- Line 323: `job.transcript_id` passed to `transcription_service.summarize()` — only called for `summary` kind (which always has a valid transcript) ✅
- Other kind handlers: all use `transcript` variable for existing kinds. With NULL guard, assistant handler won't access it.

### 4. Queue worker `llm_worker_tick()`
Line 592: `get_active_job(db, job.transcript_id, job.kind)` — for NULL `transcript_id`, this queries `WHERE transcript_id IS NULL AND kind = ...`. Doesn't accidentally dedup across different assistants. ✅

### 5. No other pollers/timers found with the same shape
Sweep of `services/llm_jobs.py` for timer-like patterns (`await asyncio.sleep`, `interval_seconds`): only `llm_worker_loop` at line 630. No other timers need the same guard. ✅

## Phase 1.5: Completion-race check
The assistant job flow is:
1. `interpret_request()` → LLM call, no side effects
2. `execute_plan()` → search (DB read), summarize (LLM call), save_markdown (file write)
3. `_finish(db, job, "completed")` → sets status, commits

No side effects after "completed" status. No other jobs enqueued. No dependent records written that could race. The completion-race pattern (mark completed then trigger further side effect in same try block) is NOT present here. Phase 1.5 oracle consult is **NOT triggered**.

## What the plan gets right/wrong

### Correct
- Schema migration approach is appropriate
- Adding to VALID_KINDS/AUTO_RETRY_KINDS/IO_KINDS is correct
- Search service already exists as dependency ✅
- `export_directory` already in DEFAULT_SETTINGS ✅

### Missing/incorrect in plan
- Plan text says "run_llm_job() skips transcript fetch for voice_note" — this is NOT true in current code. All kinds fetch transcript unconditionally at line 284. The fix is to add a NULL guard for assistant specifically.
- Plan's `interpret_request()` signature lists `transcript_context: str` as first param, but this doesn't match `execute_plan()`'s actual flow where the transcript context comes from the search step, not from the interpreter. I'll drop `transcript_context` — interpreter doesn't need it.
- Plan doesn't mention that `get_active_job` and `latest_job` signatures also need the `int | None` type change for correctness, not just `enqueue_llm_job`.

## Scope decisions
- `export_directory` already exists in DEFAULT_SETTINGS (line 31). No new settings code needed. Frontend/UI work for the export directory input is Task 11 (separate sub-issue), not in scope for this issue.
- `services/search.py` already complete. No changes needed.
- Tests will use mocked `chat_completion` — no real LLM calls in CI.
