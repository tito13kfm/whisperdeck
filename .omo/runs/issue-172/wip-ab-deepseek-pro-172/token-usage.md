# token-usage.md — Issue #172

## Subagent summary

| Phase | Agent | Category | Model (from metadata) | Cloud/Local | Approx share |
|-------|-------|----------|----------------------|-------------|--------------|
| 1 | N/A | N/A | N/A | N/A | Direct investigation (codegraph + reads) |
| 2 | ses_059c59a81ffe | quick | inclusionai/ling-3.0-flash:free | Cloud (OpenRouter free) | Task 1 (~1 edit) |
| 2 | ses_059c57c24ffe | quick | inclusionai/ling-3.0-flash:free | Cloud (OpenRouter free) | Task 2 (~1 edit) |
| 2 | ses_059c3cc0dffe | deep | opencode-go/deepseek-v4-pro | Cloud (OpenRouter) | Task 4 (settings page) |
| 2 | ses_059c3a37effe | deep | opencode-go/deepseek-v4-pro | Cloud (OpenRouter) | Tasks 5+6 (button + wiring) |
| 3 | N/A | N/A | N/A | N/A | Direct implementation (Task 3 app.py, tests) |

## Cost drivers

1. **Direct implementation of Task 3 and tests** saved agent context costs — the plan had exact code snippets, so the orchestrator implemented these directly instead of delegating (per the Delegation Exception rule).
2. **Two deep agents** for frontend work — each used deepseek-v4-pro which is the orchestrator's own model. Combined cost ~moderate.
3. **Two quick agents** for simple backend edits — used the free tier `ling-3.0-flash`, effectively zero cost.
4. **No retries** — all agent tasks succeeded on first attempt.
5. **No local (Lemonade) agents used** — zero VRAM impact.

## Token-saving measures applied

1. **codegraph_explore** used for investigation (settings.py + reformatting.py) — single calls covered multiple symbols.
2. **from_end=true** not needed — no background tasks were used.
3. **Batched edits** — app.py had 4 edits made in one pass without re-reading between each.
4. **Tests written directly** — avoided deep agent context cost for mechanical test transcription.

## Notes for next run

- The "quick" category routed to ling-3.0-flash:free (cloud, free tier), not local Lemonade as AGENTS.md line ~127 suggests. The actual config determines routing, not the doc.
- Direct implementation of well-specified code (per Delegation Exception rule) was effective here — the plan had complete, verbatim code snippets for Tasks 3 and tests.
- The `app_module.get_db()` pattern in the initial test was wrong (creates new session, not test session). Fixed by using `db_session` fixture. Worth checking if any other A/B variant tests make the same mistake.
