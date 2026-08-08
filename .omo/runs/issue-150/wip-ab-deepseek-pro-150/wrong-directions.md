# Wrong Directions — Issue #150 run (wip/ab-deepseek-pro-150)

## explore-hard agent doesn't resolve despite being in config

**When**: Phase 1, attempting to delegate to `explore-hard`
**What happened**: `task(subagent_type="explore-hard", ...)` returned "Unknown agent: explore-hard". The agent IS defined in `~/.config/opencode/oh-my-openagent.json` (line 23-27) with model `lemonade/DeepSeek-Qwen3-8B-GGUF`.
**Workaround**: Fell back to `explore` agent.
**Recommended fix**: Check if `explore-hard` is excluded from the available agents list at the tool level. The task tool's list shows `explore` but not `explore-hard`. May need to add `explore-hard` to the agent registry separately from the model config.

## AGENTS.md line ~127 doc error: atlas/quick/writing/unspecified-low listed as cloud

**When**: Before Phase 1, verifying local-vs-cloud for concurrency cap
**What I found**: Live config (`~/.config/opencode/oh-my-openagent.json`) shows all four are LOCAL (`lemonade/` prefix), not cloud as AGENTS.md line ~127 describes. No disagreement this time since AGENTS.md already acknowledges this — the live config confirms the AGENTS.md note is correct.

## Grep tool can't find files in git worktrees on Windows

**When**: Phase 1, searching for function names in the worktree's `static/rack.js`
**What happened**: `grep(pattern="scheduleDetailPoll", path="C:\Claude\whisperdesk-wip-ab-deepseek-pro-150")` returned no matches, even though the function exists. Same tool against the main repo path worked fine. Bash `grep` also worked against both paths.
**Workaround**: Used agents to explore the main repo copy, then read files directly from the main repo.
**Recommended fix**: The grep tool may not resolve Windows git worktree symlinks. Document this as a known quirk alongside the glob-dot-directory quirk.

## Issue body's suggested code had wrong element reference

**When**: Phase 1, comparing issue's Option A snippet against actual code
**What I found**: The snippet references `$('detail-status')` which doesn't exist in the codebase. The actual status UI consists of: action bar button disabled states (3 buttons), content body re-rendering via `renderDetailBody()`, and a status badge in the metadata grid. The snippet's approach (targeted update) is correct, but the specific DOM elements it references are wrong.
**Impact**: None — I wrote the correct implementation based on actual code analysis.
