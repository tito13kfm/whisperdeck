# self-audit.md — Issue #172

## Issue acceptance criteria (9 tasks)

[x] Task 1: Add `export_directory` to DEFAULT_SETTINGS — delivered, confirmed at `services/settings.py:31`
[x] Task 2: Add `build_export_markdown()` helper — delivered, confirmed at `services/reformatting.py:115-175`
[x] Task 3: Add export endpoint + bootstrap settings — delivered, confirmed at `app.py:2025-2085` (endpoint), `app.py:656` (bootstrap settings), imports at lines 12, 37
[x] Task 4: Add export directory input to settings page — delivered, confirmed at `static/rack.js:4524-4535` (input row + save handler)
[x] Task 5: Add "Save as Markdown" button to export toolbar — delivered, confirmed at `static/rack.js:3192` (conditional button in exportToolbarHtml)
[x] Task 6: Wire export button to API endpoint — delivered, confirmed at `static/rack.js:2597-2606` (detailBodyClick handler)
[x] Task 7: Unit tests for build_export_markdown() — delivered, 6 tests in `TestBuildExportMarkdown` class, all passing
[x] Task 8: Integration tests for export route — delivered, 6 tests in `TestExportMarkdownRoute` class, all passing
[x] Task 9: Settings round-trip tests — delivered, 2 tests in `TestExportDirectorySettings` class, all passing

## Scope fidelity (from plan)

[x] Only writes files to user-configured directory — no MCP, no Obsidian features, no batch export, no auto-export
[x] Composes from existing data (transcript + summary) — no LLM call
[x] File naming: {sanitized_title}-{YYYY-MM-DD}.md
[x] Empty export_directory disables feature (button hidden)
[x] Export overwrites existing files (idempotent)

## Plan promises verified

[x] S.exportDir populated from bootstrap in checkAuth — confirmed at `static/rack.js:647`
[x] S.exportDir added to state object — confirmed at `static/rack.js:50`
[x] S.exportDir updated on settings save — confirmed in settings save handler at `static/rack.js:4598`
[x] `import re` added to app.py — confirmed at `app.py:12`
[x] `from services.reformatting import build_export_markdown` added — confirmed at `app.py:37`
[x] Full test suite passes — 478 passed, 0 failed (`pytest tests/ -x --ignore=tests/e2e`)

## Discipline checks

[x] New function (build_export_markdown) has tests — 6 unit tests in TestBuildExportMarkdown
[x] Sibling sweep completed — no sibling code found (see investigation.md)
[x] No completion-race pattern — not applicable (no job/state completion path)
[x] No AI-authorship trailers in git
