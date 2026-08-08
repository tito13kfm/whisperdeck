# Token usage: Issue #234

## Orchestrator

- Sisyphus (this session): `openrouter/deepseek/deepseek-v4-pro` (cloud)

## Sub-agents

- Oracle (bg_1f567131): `meta/muse-spark-1.1` (cloud) — Phase 3.75 regression pass, 2m41s

No other sub-agents dispatched. Direct implementation under delegation exception (complete investigation plan).

## Tool calls

- `codegraph_codegraph_explore` x3: free (local index)
- `bash` (git fetch, worktree, gh issue views, pytest, esbuild): free
- `read` x10+: free
- `edit` x9: free
- `grep` x4: free
- `write` x4: free

## Total

No sub-agent token spend. Only orchestrator + codegraph + local tool calls.