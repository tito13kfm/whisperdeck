# Wrong Directions for Issue #146

## 1. `explore-hard` agent does not exist

**When:** Phase 1 investigation start
**What happened:** Command instructions reference `explore-hard` as a distinct agent for reasoning-heavy tasks. Attempted to invoke it, got "Unknown agent: explore-hard".
**Reality:** Only `explore` exists as a local agent key. The config at `~/.config/opencode/oh-my-openagent.json` (or project override) is the source of truth for agent names.
**Fix:** Use `explore` for all local investigation tasks. Update command instructions to remove `explore-hard` references or map them to `explore`.
