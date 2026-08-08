# Token usage (issue #261 investigation run)

This run did not spawn any sub-agents. Investigation was done directly via `codegraph_explore` calls and direct reads — no explore/deep/oracle agents dispatched.

**Direct tool calls used:**
- 4 `codegraph_explore` calls (free, local SQLite index)
- 8 `grep` calls (free)
- 4 `read` calls (free)
- 5 `gh issue create` + 1 `gh issue edit` (free)
- Several `bash` calls for `gh issue view`, `git fetch`, config reads
- Several `write` calls for investigation.md, issue body files

**Models consumed:** Only the orchestrator (DeepSeek V4 Pro via OpenRouter). No sub-agent models billed.
