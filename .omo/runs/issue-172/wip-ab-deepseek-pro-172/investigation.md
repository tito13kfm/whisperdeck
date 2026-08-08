# investigation.md — Issue #172: Markdown export to filesystem

## Phase 0: Target resolution

Issue #172 is a **standalone tracking issue** — 9-task internal checklist with no cross-references to other issue numbers. All 9 tasks are the target directly.

## Phase 1: Plan verification against live code

### Task 1: `export_directory` in DEFAULT_SETTINGS

- **File:** `services/settings.py`
- **DEFAULT_SETTINGS** at lines 13-31. Current last entry is `"format_model": "gpt-oss-20b-mxfp4-GGUF",` at line 30, closing `}` at 31. Plan is correct about location.
- **`update_user_settings()`** at line 95 already filters against `DEFAULT_SETTINGS` keys (line 112: `if key in DEFAULT_SETTINGS`). Adding to DEFAULT_SETTINGS auto-whitelists it.
- **`get_user_settings()`** at line 86 merges stored over defaults. New key auto-appears in GET responses.
- **No code changes needed beyond adding one line to the dict.**

### Task 2: `build_export_markdown()` helper

- **File:** `services/reformatting.py`
- **`classify_intent()`** ends at line 112. Plan says to add after this — correct.
- **Calld sites verified:** `Transcript.title` (String(255)), `Transcript.full_text` (Text), `Transcript.segments` (JSON, list of {start, end, speaker, text}), `Transcript.kind` (String(16)), `Transcript.created_at` (DateTime).
- **Summary model** at database/__init__.py:139-150: `short_summary` (Text), `key_points` (JSON), `action_items` (JSON), `decisions` (JSON).
- **The plan's signature**: `build_export_markdown(transcript, summary: dict | None = None) -> str` — correct.
- **No sibling code with the same shape exists** — `format_as_markdown()` at line 39 is LLM-backed and async, different purpose. The new function is synchronous, no-LLM. Sweep confirms no other "build_composed_markdown" or similar exists.

### Task 3: Export endpoint + bootstrap settings

- **File:** `app.py`
- **Bootstrap route** at lines 633-664 returns `{csrf_token, user, status, recent_transcripts, jobs}` — NO `settings` field. Plan step 0 is correct: needs to add it.
- **`get_user_settings`** already imported and used at app.py line 2532 (voice clip route) and in settings routes (lines 775, 780).
- **`re` is NOT imported** in app.py. Plan says it "may need to be added" — confirmed, must add.
- **`os` IS imported** at line 7.
- **No existing import from `services/reformatting`** in app.py. Must add `from services.reformatting import build_export_markdown`.
- **Transcript ownership pattern** (line 1296-1301): query by id + user_id, raise 404 if not found. Plan matches.
- **Status check pattern** (line 1963): `if t.status != "completed"`. Plan matches.
- **Placement:** Format route ends at line 2015, correct route starts at 2018. Insert new route at line 2016 (after format route blank line, before correct route).

### Task 4: Export directory input on settings page

- **File:** `static/rack.js`
- **`loadSettingsPage()`** at line 4404.
- **Maintenance card** at lines 4506-4512 is a grid cell with flex layout containing Admin reset code button + Log out button. Plan's reference to "grid cell at line 4490-4497" is stale (~15 line drift). Actual is 4506-4512.
- **Plan's HTML snippet needs integration** — the maintenance card is `display:flex;align-items:center;justify-content:flex-end;gap:8px`. Export directory input must be added BEFORE the buttons. Structural change: wrap with a containing div or change the flex direction.
- **Settings save event handlers** around lines 4616-4628 (audio + LLM saves). Plan's insertion point for export dir save handler is correct.

### Task 5: "Save as Markdown" button in export toolbar

- **File:** `static/rack.js`
- **`exportToolbarHtml()`** at lines 3176-3180. Two buttons (Copy, Download .txt). Plan says add third.
- **`S.exportDir`** does NOT exist in the S state object (lines 6-50). Must be added.
- **Plan's condition** `S.exportDir ? ... : ''` is correct for conditional rendering.

### Task 5 prerequisite: populate `S.exportDir` from bootstrap

- **`checkAuth()`** at lines 640-654: boots from `/api/bootstrap`, caches `bootData = body`. Currently extracts `csrfToken`, `S.user`, `S.isAdmin`. Does NOT extract settings.
- **`loadDashboard()`** at lines 855-857: consumes `bootData`, nulls it.
- **Must add:** After `bootData = body;` in checkAuth: `S.exportDir = (body.settings && body.settings.export_directory) || '';`
- **Also needed in loadSettingsPage:** After PUT save succeeds (Task 4), update `S.exportDir` so the detail page picks it up on next render.

### Task 6: Wire export button to API

- **File:** `static/rack.js`
- **`detailBodyClick()`** at lines 2591-2609. Currently handles `[data-export-copy]`, `[data-export-dl]`, playback, seed toggle, segment selection, speaker rename.
- **Plan's handler pattern** matches existing delegation pattern. Correct placement: after line 2594 (the copy/dl handler), before the playback handler at line 2595.

### Tasks 7-9: Tests

- **File:** `tests/test_reformatting.py`
- **Existing test helpers:** `_make_user_and_dictation()` at lines 34-45. Plan's `_make_transcript()` helper follows same pattern.
- **`client` fixture** exists in conftest.py.
- **`_upload()` helper** at test_reformatting.py:170-182.
- **No existing tests for `TestBuildExportMarkdown`, `TestExportMarkdownRoute`, or `test_export_directory_settings`** — all new.
- **Summary is a plain dict** in the plan's test helpers, matching the function signature. Plan is correct.
- **Note:** Plan says `from database import Transcript` in test helper, but `database` is the module name — check if it's `from database import Transcript` or `from database import Base, Transcript`. The existing tests import it internally through conftest fixtures.

### Plan issues / discrepancies found

1. **Line drift (minor):** Plan's line numbers for rack.js are ~10-20 lines off from current code. Not blocking.
2. **`re` import missing:** Plan says "may need to be added" — confirmed, app.py doesn't import `re`.
3. **`services/reformatting` not imported in app.py:** New import needed.
4. **`S.exportDir` doesn't exist:** Plan references it in Task 5 but doesn't explicitly add it to the S object. Must be done.
5. **Maintenance card structure:** Plan's HTML snippet assumes a specific structure (buttons in a div). Actual structure is two buttons directly in the flex container. Need to adapt.
6. **Plan says "refers to `os` already present" and "re may need to be added" for app.py** — confirmed both.

### Sibling sweep

Per Phase 1 step 3: searched for other places that would need the same treatment:

- **Directory validation pattern:** No other code writes files to user-configured directories. The only file writes are to the data directory (uploads, voice clips) which are internal. No sibling to fix.
- **Export button pattern:** `exportToolbarHtml()` is the only function rendering export toolbar buttons. No other export toolbar exists on other pages.
- **Settings input pattern:** The Maintenance card is the only "maintenance" section. No sibling card that would also need an export directory input.
- **Bootstrap settings inclusion:** The bootstrap route is the only one-stop boot endpoint. `GET /api/settings` is a separate call. Adding settings to bootstrap is the correct path.
- **Result: no siblings found. Sweep is clean.**

### Phase 1.5: completion-race check

Not applicable. The export endpoint writes a file synchronously with no job/state machine side effects. No "mark completed then fire side effect" pattern.

### Acceptance criteria mapping

The issue's body lists explicit acceptance criteria per task (embedded in each task section of the plan). Will verify task-by-task in self-audit.md.

### Implementation plan summary

| Task | File | Change | Dependencies |
|------|------|--------|--------------|
| 1 | services/settings.py | Add `"export_directory": ""` to DEFAULT_SETTINGS | None |
| 2 | services/reformatting.py | Add `build_export_markdown()` function | None |
| 3 | app.py | Bootstrap: add settings field. New route: POST export-markdown. Add `import re`, import reformatting | 1, 2 |
| 4 | static/rack.js | Add export dir input to settings page + save handler | 1 |
| 5 | static/rack.js | Add `S.exportDir` + populate from bootstrap + conditional button in exportToolbarHtml | 3 |
| 6 | static/rack.js | Wire `[data-export-save]` in detailBodyClick | 3, 5 |
| 7 | tests/test_reformatting.py | TestBuildExportMarkdown class (6 tests) | 2 |
| 8 | tests/test_reformatting.py | TestExportMarkdownRoute class (6 tests) | 3 |
| 9 | tests/test_reformatting.py | Settings round-trip tests (2 tests) | 1, 3 |

**Parallel batches:**
- Batch A: Tasks 1 & 2 (parallel, independent files)
- Batch B: Task 3 (after 1+2)
- Batch C: Tasks 4, 5, 6 (frontend group, after 3 — can parallelize)
- Batch D: Tasks 7, 8, 9 (tests, after 2+3 — can parallelize)
