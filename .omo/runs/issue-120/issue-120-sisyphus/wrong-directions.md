# Wrong Directions: Issue #120

## verify_self_audit.py build failures

`scripts/verify_self_audit.py` reported 2 blocking build failures: `esbuild` not found for `build:js` and `build:css`. This is expected in a fresh worktree (no `node_modules`). The main checkout has esbuild but the verifier auto-detects the worktree as the repo root. Pre-existing condition, not introduced by this change. No source file touched by this issue is part of any esbuild bundle (changes are in `services/queue.py`, `app.py`, `tests/test_diarization_failure.py` — all Python), so a stale bundle is not a concern here.

## All other items resolved cleanly

No discrepancies found between:
- The issue text and current code
- The workflow prompt and actual execution
- AGENTS.md model assignments and live config

The `explore` agent model is `openrouter/deepseek/deepseek-v4-flash` (cloud), matching the live config.
