# Investigation — Issue #177: Assistant: Export path setting

## Target
Issue #177, standalone. Not a tracking issue (body has self-contained task list, no cross-referenced issue table).

## Findings

### Task 10: `export_directory` in DEFAULT_SETTINGS — ALREADY DONE

`services/settings.py:31` (worktree, commit 6057b36):
```python
"export_directory": "",  # empty = feature disabled (Save as .md button hidden in detail toolbar)
```

Added in commit `709359f` (`feat(settings): add export_directory to DEFAULT_SETTINGS`), merged into master before the current HEAD.

The `update_user_settings` function at line 96 already filters unknown keys via `DEFAULT_SETTINGS` membership at line 113:
```python
patch = {key: value for key, value in updates.items() if key in DEFAULT_SETTINGS}
```
So `export_directory` passes through the validation gate — no additional whitelist entry needed.

### Task 11: Export directory input in settings UI — ALREADY DONE

`static/rack.js:4529`:
```html
<input id="export-dir-input" type="text" value="${escapeHtml(settings.export_directory || '')}" placeholder="e.g. C:\\Users\\you\\Documents\\Vault" ...>
```

Wiring confirmed at lines:
- **42**: `exportDir: ''` — S state initialization
- **650**: `S.exportDir = (body.settings && body.settings.export_directory) || '';` — populated from `/api/bootstrap`
- **3190-3191**: `exportToolbarHtml()` uses `S.exportDir` to conditionally show "Save as .md" button
- **4679-4680**: Settings save handler sends `PUT /api/settings` with `export_directory` and updates `S.exportDir`

Added in commit `7767782` (`feat(ui): add export directory input to settings page`).

### Sibling sweep

Checked for:
- Other places `export_directory` is consumed: `app.py:2044` (export-markdown endpoint), `services/assistant.py:46` (`_resolve_export_path` in the assistant executor), `tests/test_assistant.py` (uses `export_directory` in test fixtures). All already wired.
- Other DEFAULT_SETTINGS keys without UI inputs: all existing keys appear to have frontend inputs. No gaps found.
- Other settings page inputs not wired to DEFAULT_SETTINGS: all inputs map to existing keys.

### Tests

Existing coverage for export_directory:
- `tests/test_reformatting.py:448-544` — multiple tests: export-markdown endpoint (success, not configured, does-not-exist, filename conflict, not-completed transcript) plus settings round-trip (lines 519-544)
- `tests/test_assistant.py:204,240,279` — `export_directory` used in assistant test fixtures via `_resolve_export_path`

### Issue acceptance criteria

The issue body lists two tasks, both checked `[ ]` (unchecked) but both are implemented:
- `[x]` Add `"export_directory": ""` to `DEFAULT_SETTINGS` — confirmed at `services/settings.py:31`
- `[x]` Add text input to settings UI (Service panel → Maintenance card) — confirmed at `static/rack.js:4529`

No additional acceptance criteria listed beyond the checklist.

## Conclusion

Issue #177 is resolved. Both deliverable items exist in master and have test coverage. No code changes needed.
