# Issue #178 Investigation Report

**Target:** #178 (standalone) — "Assistant: Assistant UI page"
**Worktree:** `C:/Claude/whisperdesk-deepseek-pure-178`
**Branch:** `wip/ab-deepseek-pure-178`
**Base:** `origin/master` at 6057b36

## Phase 0: Issue Resolution

Standalone issue. Target is #178 directly. Backend already merged (PR #183: `POST /api/assistant`, `GET /api/assistant/result`).

## What Already Exists

**Backend (complete):**
- `POST /api/assistant` — accepts `request` (form data, max 2000 chars), returns `{job: {...}}`
- `GET /api/assistant/result/{job_id}` — returns status/progress/result  
- `services/assistant.py` — `interpret_request()`, `execute_plan()`, `_sanitize_filename()`, `_resolve_export_path()`
- `services/llm_jobs.py` — `kind="assistant"`, `run_assistant_job()`
- `tests/test_assistant.py` — 422 lines, covers interpret, execute, endpoints

**Frontend (nothing yet):**
- No `assistant` entry in `PAGES`, `loaders`, or `S` state object in `static/rack.js`
- No `#page-assistant` div in `static/index.html`
- No nav entry for Assistant

## What Needs Building (Tasks 12-15)

### Task 12: Page Shell + Nav Entry (`static/index.html`)

**Nav entry:** Add a `<button class="rail-btn" data-nav="assistant">` between Files and Service panel in the rail nav (after line 82, before line 83).

**Page shell:** Add `<div class="page" id="page-assistant"></div>` alongside existing page divs (after line 112, before closing `</div>`).

### Task 13: Assistant Chat UI (`static/rack.js`)

**State:** Add `assistantHistory: []` to `S` object.

**PAGES array (line 405):** Add `'assistant'`.

**Loaders (line 435-445):** Add `assistant: loadAssistant`.

**`loadAssistant()` function:** Renders the assistant page UI.

**`renderAssistant()` function:** Full page rendering:
- Text input with placeholder, character counter (max 2000), Send button
- On submit: disable input, show spinner, call `POST /api/assistant`
- Job polling: every 1.5s via `GET /api/assistant/result/{job_id}`
- Progress bar with step descriptions
- Result display: summary text, "Copy path" button, "Download" button
- Error display with "Retry" button
- History: last 5 request/response pairs in scrollable list, stored in `sessionStorage`

**Existing patterns to follow:**
- `api()` function (line 223) for API calls
- `llmJobActive()` (line 2949) for job status checks
- `downloadTextFile()` (line 3177) for download
- `copyToClipboard()` (line 3168) for copy
- `scheduleDetailPoll()` pattern (line 2515) for polling
- `S.exportDir` pattern for checking export directory availability

### Task 14: "Open in Assistant" Bridge Button (`static/rack.js`)

Add a button to `exportToolbarHtml()` (line 3189) that navigates to Assistant page with pre-filled input. Use existing `data-nav` button pattern.

### Task 15: Integration Test

Write an E2E test in `tests/e2e/` or extend `tests/test_assistant.py` to cover the full flow with mocked LLM. Since frontend is vanilla JS without a test framework, a backend integration test that exercises the API endpoints and verifies response shape is the practical approach (existing `TestAssistantEndpoint` and `TestAssistantResult` classes already cover the API).

## Sibling Sweep

Checked all call sites for patterns touched by this change:

1. **PAGES array:** No sibling page registrations missed — all pages are in the array, adding one more follows the pattern.
2. **Nav entries:** All nav buttons follow same `.rail-btn` pattern. No duplicate navigation paths.
3. **Export toolbar:** `exportToolbarHtml()` is called from 3 places (transcript, corrected, summary tabs). Adding a 4th button to the toolbar HTML template affects all callers equally — intentional, should be available on all tabs.
4. **Poll pattern:** `scheduleDetailPoll` checks `llmJobActive` for each job type. Assistant poll is separate (not per-transcript), no overlap.
5. **Detail page side effects:** Adding assistant to `PAGES` doesn't break `navigate()` — it falls through to `loaders[page] || (() => {})` pattern.

No siblings found with the same shape that need updating.

## Issue's Own Acceptance Criteria

The issue body lists:
- [ ] Add page shell to `static/index.html` + nav entry
- [ ] Implement chat UI in `static/rack.js` — input, polling, result display, history (sessionStorage, last 5)
- [ ] Add "Open in Assistant" button from transcript detail export toolbar
- [ ] End-to-end integration test (mocked LLM)

## Plan Specification Deviations

The plan at `.omo/plans/llm-assistant.md` specifies:
- Export path UI field (Task 11) — already done (commit 6057b36 includes `export_directory` in settings, `S.exportDir` populated at bootstrap line 650)
- "Assistant export path" settings input — exists at line 4529-4680 in rack.js

## Non-Scope Items (Not in This Issue)

Per the plan scope section:
- NO voice input for assistant
- NO multi-turn conversation  
- NO semantic/embedding search
- NO new database tables
- NO streaming responses (poll-based only)
- NO new LLM provider config (reuses existing)
- NO batch operations on transcripts
