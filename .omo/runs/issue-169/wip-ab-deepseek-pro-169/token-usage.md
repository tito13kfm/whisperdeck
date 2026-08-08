# token-usage.md — issue #169, deepseek-pro variant

## Agent dispatches (all models from live config at runtime)

| Phase | Agent/Category | Model | Local/Cloud | Purpose | Est. tokens |
|---|---|---|---|---|---|
| Phase 1 | explore (bg_f1f57a32) | `lemonade/Qwen3.5-4B-MTP-GGUF` | Local | LlmJob infrastructure investigation | ~15K |
| Phase 1 | explore (bg_d4916329) | `lemonade/Qwen3.5-4B-MTP-GGUF` | Local | Frontend UI + dictation investigation | ~13K |
| Phase 2 | deep (bg_297c226e) | `opencode-go/deepseek-v4-pro` | Cloud | Backend implementation (6 Python files) | ~25K |
| Phase 2 | deep (bg_af03b725) | `opencode-go/deepseek-v4-pro` | Cloud | Frontend implementation (3 JS/CSS/HTML files) | ~35K |

## Orchestrator model

- **Sisyphus (this session)**: `opencode-go/deepseek-v4-pro` (cloud, OpenRouter)
- Reasoning effort: default

## Token waste observations

1. **Backend agent wrote to wrong repo** — the `deep` agent for backend changes wrote to the main repo (C:/Claude/whisperdesk, on master) instead of the worktree (C:/Claude/whisperdesk-ab-deepseek-pro-169). Required manual patch-and-reapply (~5 min orchestrator time). Root cause: agent prompt didn't explicitly specify the worktree path, and the agent's cwd was the main repo. Fix: always include the worktree path explicitly in the prompt's [CONTEXT] block.

2. **Frontend agent did thorough complement sweep** — spent tokens re-verifying every kind/mode check, which was correct but redundant with the investigation.md. For future runs, the prompt could say "trust investigation.md kind checks, don't re-enumerate" to save tokens.

3. **`explore-hard` unavailable** — had to use `explore` (4B model) for reasoning-heavy investigation instead of `explore-hard` (8B model). The 4B model's results were adequate but the 8B would have been better for the LlmJob infrastructure analysis. This is a config/framework issue, not a prompt issue.

4. **codegraph_explore used twice** — first call was broad (47 symbols, truncated), second was targeted (66 symbols across 4 files). Two calls was appropriate; the truncation on the first call didn't lose critical data since the agents independently read the files.

## What worked well

- Parallel Phase 2 agents (backend + frontend dispatched simultaneously) — no dependencies between them since the API contract was fully specified in the prompts
- investigation.md as a comprehensive spec — both agents could read it and implement without follow-up questions
- `from_end=true` on all background_output calls — clean results without framework noise

## Recommendations for next run

1. Always include worktree path in agent prompts for Phase 2
2. If `explore-hard` is still unavailable, consider using `deep` for Phase 1 instead of `explore` when reasoning-heavy investigation is needed
3. investigation.md format worked well — continue writing it before delegating implementation
