# Issue #176 investigation — API endpoint + output handler

## Target
Standalone issue #176: "Assistant: API endpoint + output handler" (sub-issue 3 of LLM Assistant plan).

## Pre-existing code (tasks 1-6 already merged)

All dependencies already on master (PR #182):
- `services/search.py` — `search_transcripts()` (100 lines)
- `services/assistant.py` — `interpret_request()`, `execute_plan()` (179 lines)
- `services/llm_jobs.py` — `run_assistant_job()` at L580-633, wired into `run_llm_job()` dispatch at L564-565
- `"assistant"` in `VALID_KINDS` (L23), `AUTO_RETRY_KINDS` (L35), `IO_KINDS` (L42)
- `DEFAULT_SETTINGS` has `correction_provider`/`correction_model` (L25-26), `export_directory` (L31)
- `enqueue_llm_job()` already accepts `transcript_id: int | None` (L94)

## What needs to be added (tasks 7-9)

### Task 7: POST /api/assistant
- Route: `@app.post("/api/assistant")`
- Input: `request: str = Form(...)` (non-empty, max 2000 chars)
- Provider resolution: reads `correction_provider`/`correction_model` from user settings (NOT form params — unlike summarize/correct which accept provider/model as form defaults, this endpoint reads saved settings since the user is typing a natural language request, not picking a provider per-request)
- Validate API key exists (with KEYLESS_PROVIDERS exception)
- Enqueue: `enqueue_llm_job(db, user_id, None, "assistant", provider, model)` with `result_json = {"user_request": request}`
- Return: `{"job": serialize_llm_job(job)}`
- Auth + CSRF required (via `Depends(get_current_user)` + middleware)

### Task 8: GET /api/assistant/result/{job_id}
- Route: `@app.get("/api/assistant/result/{job_id}")`
- Query `LlmJob` by id, scoped to `current_user.id`
- Return 404 if not found
- Response shape: `{status, progress, result_json, error, created_at}` for completed/failed, progress-only for running/pending
- Auth required (no CSRF for GET)

### Task 9: Integration tests
- `tests/test_assistant.py` — endpoint tests (success, empty request → 400, oversize → 400, unauth → 401, CSRF missing → 403, result endpoint → 404 for non-existent, 403 for other user's job)

## Sibling sweep

### `_SERIALIZED_JOB_KINDS` in app.py (L268-272)
Currently: `("correction", "summary", "voice_match", "format_markdown", "format_email", "format_coding_prompt", "classify_intent", "voice_note", "tagging")`
Missing: `"assistant"`
Impact: `_serialize_transcript()` uses this to batch-fetch LLM jobs for the transcript detail view. Without "assistant" in this tuple, assistant jobs won't appear in the transcript detail runs/versions views. **Must add "assistant"**.

### `get_active_job()` dedup
Already NULL-safe per plan: "Dedup guard get_active_job already handles NULL by returning no match." No change needed.

### Other endpoint patterns that might need "assistant" awareness
- `GET /api/transcripts/{id}/runs/{kind}` — validates kind against a fixed tuple at L2170. No change needed (assistant jobs aren't transcript-scoped).
- `scheduleDetailPoll()` in rack.js — polls for active LLM jobs on the detail page. Assistant jobs aren't transcript-scoped, so no new LLM job kind needs to be added to the poll predicate.

## What the issue's suggested approach gets wrong

Nothing significant. The plan is thorough and the dependencies are already in place.

## Completion-race check (Phase 1.5)

`run_assistant_job()` calls `_finish()` as its last action — no further side effects after completion. No completion-race bug here. Oracle consult not needed.

## Implementation plan summary

1. Add `"assistant"` to `_SERIALIZED_JOB_KINDS` in app.py (L268-272)
2. Add `POST /api/assistant` endpoint (new route, ~40 lines)
3. Add `GET /api/assistant/result/{job_id}` endpoint (new route, ~30 lines)
4. Add integration tests in `tests/test_assistant.py` (~80 lines)
5. Run existing test suite + new tests
