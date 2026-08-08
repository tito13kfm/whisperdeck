# Token Usage: Issue #169

## Worst Token Usage

1. **Explore agents (Phase 1)**: 2 local Lemonade agents, ~4 minutes each
   - Model: `lemonade/Qwen3.5-4B-MTP-GGUF` (local)
   - Cost: Low (local inference, no API cost)
   - Issue: Agents read many files, context filled quickly
   - Fix: Scope agents more narrowly (one question per agent, name specific files)

2. **Implementation agents (Phase 2)**: 3 parallel agents
   - Backend service: `deep` category → `opencode-go/deepseek-v4-pro` (cloud)
   - Backend API: `deep` category → `opencode-go/deepseek-v4-pro` (cloud)
   - Frontend UI: `visual-engineering` category → `opencode-go/mimo-v2.5` (cloud)
   - Cost: High (cloud inference, ~3 agents × 4-6 minutes each)
   - Issue: Frontend agent's edits didn't persist, had to re-implement manually
   - Fix: Verify agent edits with `git diff` immediately after completion

3. **Orchestrator re-implementation**: Manual frontend edits after agent failure
   - Model: `opencode-go/qwen3.7-plus` (this session's model)
   - Cost: Medium (redundant work due to agent failure)
   - Fix: Better agent prompt or use `quick` category for simple UI changes

## What Would Cut Token Usage

1. **Scope explore agents more narrowly**: Instead of "find all dictation handling", use "read app.py lines 938-950 and report diarize logic for dictation"
2. **Verify agent edits immediately**: Don't wait for all agents to finish; check each agent's work as it completes
3. **Use cheaper categories for simple UI**: Frontend changes were mostly mechanical (add page, update array, add function); `quick` category might suffice
4. **Batch related changes**: Instead of 3 separate agents (service, API, frontend), use 2 agents (backend = service + API, frontend = UI) to reduce coordination overhead

## Agent Dispatch Summary

| Phase | Agent | Category | Model | Cloud/Local | Duration | Notes |
|-------|-------|----------|-------|-------------|----------|-------|
| 1 | explore (dictation) | explore | lemonade/Qwen3.5-4B-MTP-GGUF | Local | 4m 45s | Good results |
| 1 | explore (UI) | explore | lemonade/Qwen3.5-4B-MTP-GGUF | Local | 4m 11s | Good results |
| 2 | Backend service | deep | opencode-go/deepseek-v4-pro | Cloud | 4m 02s | Good results |
| 2 | Backend API | deep | opencode-go/deepseek-v4-pro | Cloud | 4m 25s | Good results |
| 2 | Frontend UI | visual-engineering | opencode-go/mimo-v2.5 | Cloud | 5m 55s | Edits didn't persist |
| 2 | Orchestrator (manual) | (this session) | opencode-go/qwen3.7-plus | Cloud | ~3m | Re-implemented frontend |

## Cost Estimate

- Local agents: ~0 (local inference on RX 9070 XT)
- Cloud agents: ~3 agents × 5 min × (token rate) = unknown exact cost, check OpenCode usage panel
- Manual re-implementation: ~3 min of this session's model

**Note**: Exact token counts and costs not available from within this workflow. Check OpenCode's usage panel for actual spend.
