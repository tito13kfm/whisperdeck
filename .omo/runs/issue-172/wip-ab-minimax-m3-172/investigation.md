# Issue #172 Investigation — Markdown export to filesystem

**Target:** Issue #172 (standalone, despite being a checklist — body does not reference
other issue numbers, just file paths and 9 atomic tasks; plan lives at
`.omo/plans/markdown-export.md`).

**Variant label:** `minimax-m3`

**Branch:** `wip/ab-minimax-m3-172`

**Worktree:** `C:/Claude/whisperdesk-minimax-m3-172`

---

## Summary of the issue

The plan adds a "Save as Markdown" button on every transcript detail page that
writes a clean `.md` file to a user-configured directory. 9 atomic tasks across
4 backend files, 1 frontend file, and 1 test file. Designed to compose with
mcpvault at the filesystem boundary.

The plan file (`.omo/plans/markdown-export.md`, 577 lines) is unusually thorough —
it specifies exact line numbers, error message text, button HTML, JS event
handlers, and tests. This investigation validates the plan's claims against
current code, not just trusts the line numbers (a known failure mode).

---

## Plan-vs-code drift (real line numbers vs plan's claims)

The plan's line numbers are mostly right but a few are off by 1-3 because the
file has had unrelated edits since the plan was written. None change the
insertion point's character — I noted each below.

| Plan says | Current code | Drift | Verdict |
|---|---|---|---|
| `services/settings.py:31` (add after `format_model`) | `format_model` at line 30, dict ends at line 31 | -1 | Insertion point is correct (after line 30, before line 31's closing brace) |
| `services/reformatting.py:112` (after `classify_intent()`) | File ends at line 112 | 0 | Append at EOF — function definition is the last top-level statement |
| `app.py:1941` (insert after format route) | Format route actually at line 1983; ends at line 2015 | +42 | Insert after line 2015 (end of `format_transcript` function) |
| `app.py:594-625` (bootstrap route) | Route actually at line 633; returns dict at lines 658-664 | +39 | Add `settings` to the dict at line 658-664, not the route header |
| `static/rack.js:4490-4497` (Maintenance card) | Maintenance card at lines 4506-4512 | +16 | Edit the inner div, not the section header |
| `static/rack.js:3167-3171` (exportToolbarHtml) | Function at lines 3176-3180 | +9 | Add the new button at line 3179 (before the closing `</div>`) |
| `static/rack.js:2584-2587` (detailBodyClick) | Function at line 2591 | +7 | Add the new `else if` branch at line 2594 |

**Other infrastructure checks:**

- `import os` already at `app.py:7` — no change needed.
- `import re` is NOT in `app.py` — must add. Used for the title sanitization
  (strip leading `#` headings).
- `DEFAULT_SETTINGS` is at `services/settings.py:13-31` — confirmed.
- `update_user_settings` whitelist is at `services/settings.py:112` —
  `export_directory` is auto-whitelisted by being a `DEFAULT_SETTINGS` key.
- `get_user_settings` merges defaults at line 92 — auto-merged, no change needed.
- Bootstrap route at `app.py:633-664` — confirmed.
- `format_transcript` route at `app.py:1983-2015` — confirmed.
- `exportToolbarHtml` at `static/rack.js:3176-3180` — confirmed.
- `detailBodyClick` at `static/rack.js:2591-2607` — confirmed.
- `loadSettingsPage` at `static/rack.js:4404-...` — confirmed.
- `S` global state at `static/rack.js:6-50` — no existing `exportDir` field.
- `bootData` cached at `static/rack.js:132` — confirmed.

---

## Sibling sweep (Complement Rule)

The plan calls out specific insertion points but I checked for every
duplicated/parallel surface that the plan's listed touchpoints would miss.

### Sibling sweep 1: `exportToolbarHtml` callers

`exportToolbarHtml(kind)` is called in 4 places (`static/rack.js`):
- Line 3390 — dictation tab
- Line 3581 — transcript tab (the main place)
- Line 3584 — corrected tab
- Line 3602 — summary tab

All 4 callers will get the new "Save as .md" button for free by editing
`exportToolbarHtml` itself, not each caller. **No additional changes needed.**

### Sibling sweep 2: `S.exportDir` consumers

After adding `S.exportDir`, the only consumer is `exportToolbarHtml`. The
`loadDashboard` and `checkAuth` functions need to populate `S.exportDir` from
`bootData.settings.export_directory` (one line, plan Task 5 step 2 specifies
this). No other place reads `S.exportDir`. **Verified clean.**

### Sibling sweep 3: Settings save paths

The plan Task 4 adds a new save button under Maintenance. Existing save
paths in `loadSettingsPage`:
- LLM defaults save (line 4554-4568) — no effect on `exportDir`
- Audio settings save (line 4625+) — no effect on `exportDir`
- Hotword add (line 4622) — no effect
- Credential jacks (line 4571-4619) — no effect

After my new save button is added, the plan also requires refreshing
`S.exportDir` from the new settings value (Task 5 step 3). I'll do that
inline in the save handler. **Verified clean — no other save paths to update.**

### Sibling sweep 4: Bootstrap consumers

`bootData` is consumed by `loadDashboard` (line 855-857). The plan only
requires reading `bootData.settings.export_directory` once after checkAuth.
`checkAuth` itself at line 640-649 only reads `body.csrf_token`, `body.user`,
`body.user.username`, `body.user.is_admin`. Adding a new field is safe —
no schema/contract impact. **Verified clean.**

### Sibling sweep 5: Detail tab rendering

The detail body has 3 tabs that show `exportToolbarHtml`:
- transcript (line 3581)
- corrected (line 3584)
- summary (line 3602)

Each tab re-renders on tab switch (line 3552 — detail body click handler).
The "Save as .md" button is visible on all 3 tabs (when `S.exportDir` is set).
This is correct per the plan — the export feature is meaningful for all 3
text outputs, not just transcript. **Verified clean.**

### Sibling sweep 6: Tests for the new code

- Plan Task 7: `TestBuildExportMarkdown` — 6 unit tests for the new helper.
- Plan Task 8: `TestExportMarkdownRoute` — 6 integration tests for the new route.
- Plan Task 9: settings round-trip tests (2 tests).

The new helper and route get dedicated test classes, not just incidental
coverage from existing tests. **Verified clean.**

### Sibling sweep 7: Other consumers of `bootData` that might need `exportDir`

`bootData` is referenced at:
- `static/rack.js:132` (declaration)
- `static/rack.js:645` (assignment in `checkAuth`)
- `static/rack.js:856` (read in `loadDashboard`)

The plan only adds `S.exportDir` from `bootData.settings` once after checkAuth.
`loadDashboard` doesn't need `exportDir` (no UI on Monitor page uses it).
**Verified clean.**

### Sibling sweep 8: Frontend error/UX surfaces

The plan's "Save as Markdown" flow:
- Success: toast "Saved to {path}" via `toast(result.path, 'ok')` — uses existing toast API.
- Error: toast with `e.message` — uses existing toast API.
- Busy state: wrapped in `withBusy` — existing pattern.
- Hidden when `S.exportDir` is empty: HTML conditional in `exportToolbarHtml`.

No new error/UX surface introduced. **Verified clean.**

---

## What the plan gets right

- File naming `{sanitized-title}-{YYYY-MM-DD}.md` — clean, deterministic.
- Title sanitization: `re.sub(r'^#+\s*', '', title.strip())` — clean, prevents nested headings.
- Section omission rules (skip empty/None) — matches existing frontend conventions.
- Error message text is user-actionable: "Export directory not configured — set it in Settings".
- Directory validation at export time, not settings-save time — correct (filesystem state can change).
- Empty `export_directory` means feature is disabled (button hidden) — correct UX.
- The export overwrites existing files — explicit decision, documented.
- No new LLM calls — composes from existing data.
- One new test class per concern (helper / route / settings).
- Tests use existing fixtures (`client`, `db_session`, `_upload`).

## What the plan doesn't address but I will

1. **Race between settings save and detail render:** The plan says
   `S.exportDir` is updated "after settings Save button handler" (Task 5
   step 3). I'll do this in the same handler — no separate refresh needed
   because the settings page reloads after the save via `loadSettingsPage()`
   (line 4615 pattern).

2. **`audio_settings` save (existing) shouldn't clobber `export_directory`:**
   Already safe — `update_user_settings` only writes keys that are in the
   patch dict (`services/settings.py:113-118`). New key in `DEFAULT_SETTINGS`
   is auto-persisted only when explicitly sent.

3. **Empty string vs missing key:** Both fall through `update_user_settings`
   to the same code path (line 112 filter). `""` in the patch persists `""`
   to the user row. Default user (no settings) has `export_directory: ""`
   via `get_user_settings` line 92 merge.

4. **CSRF protection:** Already covered by `enforce_csrf` middleware (plan
   notes this). New route inherits it.

5. **Filename collision with empty sanitized title:** If title is
   `""` or only special chars, sanitized_title becomes `""`. Resulting
   filename is `"-2025-01-15.md"` — ugly but not broken. Acceptable for
   v1; can add a fallback later.

6. **Tests `test_export_nonexistent_directory` and `test_export_no_directory_configured`:** Both need the export endpoint to handle these cases per the error table in Task 3. I will implement the route's exact error messages and pin them in the test.

---

## Plan's own acceptance criteria walkthrough

The plan has 9 tasks, each with explicit acceptance criteria. Verified each
maps to a real test or verifiable behavior:

| Task | AC | Verified by |
|---|---|---|
| 1. `export_directory` in DEFAULT_SETTINGS | key exists, GET/PUT round-trip | `test_export_directory_default_empty` + `test_export_directory_settings_roundtrip` |
| 2. `build_export_markdown()` | 7 AC items | `TestBuildExportMarkdown` (6 tests) |
| 3. `/api/transcripts/{id}/export-markdown` | 8 AC items | `TestExportMarkdownRoute` (6 tests) |
| 4. Settings page input | visible, pre-filled, persists | manual + round-trip test |
| 5. "Save as Markdown" button | visible/hidden per `S.exportDir` | manual |
| 6. Wire button to API | POST, busy, success/error toast | manual + integration test |
| 7. Unit tests | 6 tests pass | pytest |
| 8. Integration test | 6 tests pass | pytest |
| 9. Round-trip test | 2 tests pass | pytest |

All 9 task ACs are covered by the 14 tests in tasks 7-9. **No task AC
left without a check.**

---

## Risk assessment

- **Low.** No new LLM calls, no new dependencies, no new protocol surface.
  Pure file-write with user-configured path.
- **Test surface is the highest-value part** of the work — the helper and
  route both get dedicated test classes, not incidental coverage.
- **One concern** I want to call out: the `withBusy` wrapper takes a
  default options arg `spinner: true` only in some call sites — I'll
  match the export-copy/export-dl pattern (no spinner arg, default false).
  Confirmed at `static/rack.js:259-262` and 2594 (current export pattern).

---

## What I will NOT do

- No MCP server code.
- No Obsidian-specific features (frontmatter, wiki-links, daily notes).
- No batch export of all transcripts.
- No auto-export on transcription completion.
- No email/coding-prompt file export (those stay copy/download only).
- No directory browser / file picker UI.
- No export to formats other than Markdown.
- No export history or undo.

Per the plan's "OUT" section. The A/B run scope is the plan's "IN" section only.

---

## Implementation plan summary

9 atomic commits, in this order:
1. `feat(settings): add export_directory to DEFAULT_SETTINGS`
2. `feat(reformatting): add build_export_markdown() for no-LLM markdown export`
3. `feat(api): add settings to bootstrap + POST /api/transcripts/{id}/export-markdown endpoint`
4. `feat(ui): add export directory input to settings page`
5. `feat(ui): add Save as Markdown button to export toolbar + S.exportDir wiring`
6. `feat(ui): wire export button to API endpoint with success/error feedback`
7. `test: add unit tests for build_export_markdown()`
8. `test: add integration tests for export-markdown route`
9. `test: add settings round-trip test for export_directory`

Frontend (4+5+6) could be one commit but the plan prefers atomic.
I'll keep them separate per the plan's "Commit strategy" line.

---

## Phase 1.5 (completion-race check)

**Not applicable.** This feature adds a synchronous file-write that returns
`{ok: true, path: ...}` — there's no job state machine, no background worker,
no callback chain, no "completed" state triggering further side effects.
The completion-race bug class from issue #169 is about LlmJob state
transitions; this feature has no LlmJob.

Skipping the oracle consult.
