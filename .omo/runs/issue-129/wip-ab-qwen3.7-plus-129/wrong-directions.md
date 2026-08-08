# Wrong directions encountered during issue #129 run

## 1. `explore-hard` agent not available
**Instruction**: AGENTS.md says to use `explore-hard` for reasoning-heavy investigation.
**Reality**: The available agents list does not include `explore-hard`. Only `explore` is available.
**Fix**: Use `explore` for all investigation tasks. Update AGENTS.md to reflect that `explore-hard` is not currently defined in the agent config, or add it to the config.
**Logged**: 2026-07-26

## 2. LSP not available in worktree
**Instruction**: Run `lsp_diagnostics` on changed files for verification.
**Reality**: LSP tool rejects paths outside the main repo checkout. Worktree paths are not recognized.
**Fix**: Use `node --check` for syntax validation in worktrees, or run LSP against the main repo copy after copying changes back. For this run, used `node --check` which is sufficient for vanilla JS.
**Logged**: 2026-07-26
