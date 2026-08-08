# Token Usage — Issue #104

## Agent/model breakdown

| Agent | Model | Cloud/Local | Calls | Purpose |
|-------|-------|------------|-------|---------|
| Sisyphus (orchestrator) | opencode-go/deepseek-v4-pro | cloud (OpenCode) | N/A | Orchestration, direct reads, edits, gh, git |
| oracle | meta/muse-spark-1.1 | cloud (OpenRouter) | 1 | Phase 3.75 regression pass |

## What went well

1. **Zero explore agents used.** Phase 1 investigation was entirely direct reads (codegraph_explore + targeted file reads). The fix scope was small (2 functions, 1 file), so agents would have been overhead. This saved significant tokens.

2. **Single oracle call.** The Phase 3.75 regression pass cost one subagent dispatch. Oracle returned a thorough analysis that flagged valid edge cases but confirmed no merge blockers.

3. **Batch edits.** Both changes (cancel_llm_job + _finish) were applied in one round of edits, with verification after.

4. **Test ran clean first time.** 532 passed, no retries needed.

## What could be better next time

1. **codegraph_explore truncation.** The initial codegraph call truncated before showing _finish. A direct read filled the gap. Budget: make at most 1 call per AGENTS.md — second codegraph call would have likely truncated too, so direct read was the right choice.

2. **Oracle cost.** One oracle dispatch (~$0.02) is well within acceptable range for the pre-PR regression pass.

## Local agent cap compliance
0 local agents used this run. No cap concerns.
