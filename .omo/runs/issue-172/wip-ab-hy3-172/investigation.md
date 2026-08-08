# Investigation — Issue #172: Markdown export to filesystem

**Phase 0 decision:** Issue #172 is a *standalone feature issue* (not a tracking
issue pointing at other issues). Its body is a master checklist of 9 atomic
tasks, and it points to a complete work plan at `.omo/plans/markdown-export.md`.
That plan already contains exact files, line numbers, code snippets, acceptance
criteria, and commit messages. So #172 is the real target directly. Per the
orchestrator delegation exception, the plan is a fully-specified mechanical
transcription, so implementation is done directly (no `deep`/`ultrabrain`
delegation), with a focused Phase 1 to verify the plan's file:line references
against *current* code (they were written against an earlier revision).

## Plan references verified against current code

| Plan said | Current reality | Verdict |
|---|---|---|
| `DEFAULT_SETTINGS` at `services/settings.py:13-31` | Confirmed `services/settings.py:13-31`; last key `format_model` at line 30, closing `}` at 31 | Correct |
| `build_export_markdown` after `classify_intent` at `reformatting.py:112` | `classify_intent` ends at line 112; file is 112 lines (function is last) | Correct; append at EOF |
| bootstrap route at `app.py:594-625` | bootstrap route is `app.py:633-664` (line shift +8) | Shifted, structure identical |
| format route at `app.py:1941` | format route is `app.py:1983` (line shift +42) | Shifted, structure identical |
| `exportToolbarHtml` at `rack.js:3167-3171` | `exportToolbarHtml` is `rack.js:3176-3180` (shifted +9) | Shifted, structure identical |
| `detailBodyClick` around `rack.js:2584` | `detailBodyClick` is `rack.js:2591`; delegates export to `handleExportClick` at 3194 | Shifted; delegation pattern confirmed |
| `loadSettingsPage` `rack.js:4388-4637` | Confirmed `rack.js:4404-4647`; Maintenance card at 4506-4513 | Correct |
| `DEFAULT_SETTINGS` auto-whitelists keys in `update_user_settings` at line 112 | `update_user_settings` filters `if key in DEFAULT_SETTINGS` (line 112) | Correct |
| `get_user_settings` merges defaults | `services/settings.py:86-92` | Correct |

**Model facts (from `database/__init__.py`):**
- `Transcript` (line 31): `title` (str), `status` (str: pending/processing/completed/failed/partial), `full_text` (Text), `segments` (JSON `[{start,end,speaker,text}]`), `created_at` (DateTime), `summary` = relationship to `Summary` (uselist=False → one or None).
- `Summary` (line 139): `short_summary` (Text), `key_points` (JSON list), `action_items` (JSON list), `decisions` (JSON list).
- Summary serialization shape (lines 347-353): `{short_summary, key_points, action_items, decisions}`.

**Other confirmed facts:**
- `app.py` imports `os` (line 7) but **not** `re` → must add `import re`.
- `get_user_settings` already imported at `app.py:36`.
- `escapeHtml`, `withBusy`, `api`, `toast` all exist in `rack.js`.
- `S` global literal is `rack.js:6-50`; no `exportDir` key yet → add `exportDir: ''`.
- `loadDashboard` consumes `bootData` at `rack.js:855-857` (set `S.exportDir` here from `boot.settings`).
- `checkAuth` caches bootstrap as `bootData` at `rack.js:645`.

## Call sites / entry points in scope (Complement Rule)

1. **Settings field** — `DEFAULT_SETTINGS` (`services/settings.py:13`). New key `export_directory` auto-whitelisted by `update_user_settings` (line 112) and auto-merged by `get_user_settings` (line 92). No other site touches the settings dict literally; GET/PUT routes consume it generically.
2. **Builder** — new `build_export_markdown(transcript, summary=None)` in `services/reformatting.py`. Synchronous, no LLM.
3. **API route** — new `POST /api/transcripts/{id}/export-markdown` in `app.py`, plus adding `settings` to the `/api/bootstrap` payload (Task 3 step 0).
4. **Settings UI** — new export-dir input + save handler in `loadSettingsPage` (`rack.js:4506` Maintenance card, handler near 4644).
5. **Toolbar UI** — `exportToolbarHtml` (`rack.js:3176`) gains the "Save as .md" button, gated on `S.exportDir`.
6. **Wire-up** — `detailBodyClick` (`rack.js:2591`) gains `[data-export-save]` branch; `S.exportDir` set from bootstrap in `loadDashboard` (855) and from the settings save handler.

## Sibling sweep (did the issue miss anything?)

This is a feature add, not a timer/poller bug, but I still checked for parallel
surfaces that should honor `export_directory`:
- **Other export surfaces:** `handleExportClick` (rack.js:3194) does Copy/Download (clipboard/txt) — those are *download*, not filesystem export, and correctly stay independent. No change needed.
- **Other settings UI cards:** only the Maintenance card hosts directory-style config; the audio/LLM/hotword cards are unrelated scalar fields. No sibling missed.
- **Other bootstrap consumers:** only `loadDashboard` reads `bootData` (line 855). `checkAuth` writes it. Setting `S.exportDir` in `loadDashboard` covers every page that later renders the toolbar.
- **Serializer:** `export_directory` is a settings blob field, not a transcript field, so no serializer change needed.
- **Conclusion:** no missed siblings. The plan's scope is complete.

## Where the issue/plan snippet differs from current code

Only line numbers shifted (table above). The snippets themselves are accurate.
One precision note: the plan's Task 3 prose says directory-inaccessible →
"Export directory is not accessible" (500), but the **error table** (which the
plan marks as exact user-facing text) says distinct messages:
- not exists → 500 `"Export directory does not exist: {path}"`
- not writable → 500 `"Export directory is not writable: {path}"`
- OSError on write → 500 `"Failed to write file: {error_message}"`

I follow the **error table** (more specific, and the plan explicitly says those
are exact). The plan's bootstrap "step 0" and the S.exportDir wiring from
Task 5 are both required and are included.

## Acceptance criteria (from plan, will walk in Phase 3)

- T1: `export_directory` in DEFAULT_SETTINGS = `""`; GET/PUT round-trips.
- T2: `build_export_markdown` handles full/summary/None/empty-segments/full_text fallback/title sanitization/omits empty sections; synchronous.
- T3: route returns ok+path on success; 400 (not configured / not completed); 404 (not found); 500 (dir missing / not writable / OSError); auth (401) + CSRF (403) via middleware.
- T4: settings input visible, prefilled, save persists, empty clears.
- T5: Save button present iff `S.exportDir` set; tooltip shows path.
- T6: button wired to endpoint with toast feedback.
- T7-9: unit tests for builder (all paths), integration test for route, settings round-trip.
