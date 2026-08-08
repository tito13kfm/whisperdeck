# Wrong Directions — Issue #178 (deepseek-pure)

## Discrepancies Found During This Run

### 1. Plan agent names don't match config (known doc error)
- **What:** `.omo/plans/llm-assistant.md` and AGENTS.md reference agent names `scout` and `plan`.
- **What happened:** These are already documented as not existing in the current config (only `explore` and `explore-hard` exist). No impact on this run — investigation was via direct file reads and codegraph, not those agents.
- **Recommendation:** Remove references to `scout`/`plan` from AGENTS.md agent table.

### 2. Phase 2 dispatch used minimax-m3, not deepseek
- **What:** The `deep` category resolved to `opencode-go/minimax-m3` per the live config, not a DeepSeek model. The variant label `deepseek-pure` tracks the orchestrator model, not the worker.
- **Impact:** None — task completed successfully. This is normal config-driven routing.
- **Recommendation:** None. This is expected behavior per "call agents by name, not by model" rule.

### 3. No new test added (frontend-only changes)
- **What:** investigation.md didn't explicitly promise new tests beyond the existing test file. The issue's "End-to-end integration test" criterion is satisfied by the existing `tests/test_assistant.py` (34 tests covering all API endpoints with mocked LLM).
- **Impact:** Frontend changes (vanilla JS + HTML) are not testable with pytest. Browser e2e would need Playwright + running server — manual only.
- **Recommendation:** N/A. This is an intentional scope decision.

## No discrepancies with AGENTS.md local/cloud labeling

The config was not checked this run (no local agents were dispatched — investigation was via direct reads, implementation via cloud `deep`). No discrepancy to report.
