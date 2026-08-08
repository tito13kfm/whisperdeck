# wrong-directions.md — Issue #172

## Plan vs live code discrepancies

1. **Line number drift in rack.js:** Plan references `loadSettingsPage()` at lines 4388-4637, Maintenance card at 4490-4497. Actual: function starts at 4404, Maintenance at 4506-4512. ~15-line drift, non-blocking.

2. **`re` import not in app.py:** Plan says "may need to be added." Confirmed missing. Added at app.py:12.

3. **`services/reformatting` not imported in app.py:** Plan references `build_export_markdown` but app.py had no import from reformatting. Added at app.py:37.

4. **`S.exportDir` not in state object:** Plan references it in Task 5 but doesn't explicitly add it to S. Added at static/rack.js:50.

5. **Maintenance card structure:** Plan's HTML assumes buttons in a named div. Actual structure was two buttons directly in a flex container. Adapted by wrapping in container divs.

6. **`app_module.get_db()` pattern in test:** Initial test for "not completed" tried `import app as app_module; db = next(app_module.get_db())` which creates a new session instead of reusing the test db_session. Fixed to use `db_session` fixture directly.

## Agent model routing notes

Per this run's task metadata:
- Task 1 (quick): Sisyphus-Junior → openrouter/inclusionai/ling-3.0-flash:free (cloud)
- Task 2 (quick): Sisyphus-Junior → openrouter/inclusionai/ling-3.0-flash:free (cloud)
- Task 4 (deep): Sisyphus-Junior → opencode-go/deepseek-v4-pro (cloud)
- Task 5+6 (deep): Sisyphus-Junior → opencode-go/deepseek-v4-pro (cloud)

All subagents used OpenRouter-billed models. No local Lemonade models involved.
- Note: `quick` category was routed to `ling-3.0-flash:free` not a Lemonade model, contrary to AGENTS.md line ~127 which lists `quick` as local. The actual config apparently routes it to cloud.

## No other wrong directions found

AGENTS.md agent cap table error (line ~127 listing quick/writing/etc as cloud) was not encountered because quick agents were actually routed to cloud in this run.
