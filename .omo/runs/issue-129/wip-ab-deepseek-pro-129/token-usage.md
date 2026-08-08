# Token Usage Report — Issue #129 (deepseek-pro variant)

## Agent Dispatch Summary

| Agent | Model | Cloud/Local | Purpose | Duration |
|-------|-------|-------------|---------|----------|
| (orchestrator) | deepseek-v4-pro | Cloud (OpenRouter) | Phase 0-5 orchestration | Entire session |
| `explore` (bg_c0b01666) | Qwen3.5-4B-MTP-GGUF | Local (Lemonade) | Enumerate loadTranscriptDetail call sites | 2m 6s |
| `explore` (bg_c71494ef) | Qwen3.5-4B-MTP-GGUF | Local (Lemonade) | Sibling race-condition sweep | 4m 7s |

**Total cloud cost**: Orchestrator only (deepseek-v4-pro). No cloud agents dispatched.
**Total local cost**: 2 explore agents, ~6 minutes combined Lemonade GPU time.
**No delegation to a different cloud model occurred** — all agent work was on local Lemonade.

## Efficiency Notes

### What worked well
1. **codegraph_explore saved context**: One call returned verbatim source of `loadTranscriptDetail`, `renderDetail`, `detailAction` plus blast radius (69 symbols). Avoided multiple file reads.
2. **Parallel explore agents**: Both launched simultaneously (2/2 local cap), waiting overlapped.
3. **from_end=true**: Used on both background_output calls, avoided noisy framework monologue.
4. **No retries**: No codegraph budget-truncation issues, no agent failures.

### What was wasteful
1. **Sibling-sweep agent over-scoped**: The prompt asked for all `= await api(` patterns plus polling patterns plus abort-controller search. The agent read large chunks of rack.js (4482 lines) multiple times. A more targeted prompt naming specific functions would have been cheaper. This is the 4m 7s agent.
2. **Issue's own fix was correct**: The investigation confirmed what the issue already suggested. A simpler agent prompt ("read lines 2372-2387 and confirm the race condition") would have been sufficient for Phase 1. The sibling sweep was valuable for due diligence but found nothing actionable beyond what the issue described.

### What to improve next time
1. **Narrower agent prompts for known bugs**: When the issue body is accurate and the bug is in one function, don't ask the agent to sweep the entire file. Ask "confirm this function has the bug described" + "check if N other functions have the same pattern" with N known upfront.
2. **Pre-fetch line numbers**: The issue said ~2338, actual was 2372. Asking the agent to find the function by name first added a round-trip. Name-based search was fine here, but for numbered references, pre-read the function with codegraph and give the agent exact line numbers.
3. **Fix was one line**: The entire investigation-to-fix ratio was high (6 agent-minutes for a 3-line diff). For a simple guard-addition bugfix, the investigation could have been a single codegraph call + a single targeted explore agent.
