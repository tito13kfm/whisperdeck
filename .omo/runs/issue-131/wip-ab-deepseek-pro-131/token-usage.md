# Token Usage — issue #131, A/B run deepseek-pro

## What worked well (kept tokens low)

1. **Codegraph first**: 2 `codegraph_explore` calls gave me `resetDeckState()`, `stopBackgroundJobPoll()`, `showLogin()`, all timer declarations, and the call graph. This replaced what would have been 4-6 raw file reads of a 4500-line file. Token cost: ~3K tokens for both calls vs ~15K+ for reading multiple 200-line windows.

2. **Two narrow explore agents, parallel**: One agent for bank/detail poll timer lifecycle, one for showLogin call sites. Both returned in ~2-3 minutes. Using `from_end=true` on collection.

3. **Delegation exception**: The fix was a 5-line mechanical insertion with no ambiguity. Implementing directly (1 edit call) instead of dispatching a `deep`/`ultrabrain` agent saved ~5K+ tokens in subagent overhead.

4. **No throwaway server-start/auth/upload e2e cycle**: Recognized no Playwright MCP available; did static source-level check + existing pytest suite instead. 387 tests passed in 35s. No wasted live-server setup.

## What cost tokens unnecessarily

1. **`explore-hard` mis-fire**: Two attempts to spawn a non-existent agent before falling back to `explore`. Cost: 2 tool-call roundtrips + error messages. ~500 tokens wasted. Root cause: AGENTS.md mentions `explore-hard` but the live config doesn't define it.

2. **Worktree venv issue**: Tried to run pytest from worktree which has no `.venv`. Had to switch to main repo's Python. 1 wasted bash call (~200 tokens).

## To improve next time

- Check agent existence in the live config before first dispatch (the agent list returned by the system is authoritative)
- If `explore-hard` isn't in the config, use `explore` directly — skip the trial-and-error
