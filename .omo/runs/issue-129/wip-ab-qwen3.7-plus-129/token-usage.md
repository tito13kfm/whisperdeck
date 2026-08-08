# Token usage analysis for issue #129 run

## Agent dispatches
1. **explore agent #1** (loadTranscriptDetail investigation): 6m 15s, model `lemonade/Qwen3.5-4B-MTP-GGUF` (local)
   - Read full rack.js file (4482 lines)
   - Reported function body, 12 call sites, shared state, guards
   - Good scope, returned useful data

2. **explore agent #2** (sibling sweep): 7m 12s, model `lemonade/Qwen3.5-4B-MTP-GGUF` (local)
   - Read full rack.js file (4482 lines)
   - Reported 23 async functions, identified 2 with identical race shape
   - Good scope, returned useful data

**Total agent time**: ~13 minutes (both ran in parallel, so wall-clock ~7m)
**Token cost**: Both agents read the entire 4482-line file. This is the main cost driver.

## Orchestrator work
- Phase 0 (resolve issue): 1 gh call, trivial
- Setup (worktree, report dir): 2 git/bash calls, trivial
- Investigation.md write: 1 file write, trivial
- Implementation: 4 edit calls (read + edit + read + edit), trivial
- Verification: 1 node --check call, trivial
- Report writing: 3 file writes (wrong-directions, token-usage, this file), trivial

## Where token usage was worst
1. **Agent file reads**: Both explore agents read the entire rack.js file (4482 lines). This is ~150K tokens per agent just for the file content. The agents could have been scoped more narrowly if I had known the exact line ranges upfront.
2. **Sibling sweep breadth**: Agent #2 searched all 23 async functions in the file, but only 2 matched the race-condition shape. The other 21 were lower-risk and could have been skipped with a more targeted prompt.

## What would cut token usage next time
1. **Use codegraph_explore first**: AGENTS.md says to prefer codegraph over agents. I should have called `codegraph_explore` with query "loadTranscriptDetail callers shared state" to get the function body + call sites in one call, then only fired agents for the sibling sweep.
2. **Narrow agent scope**: For the sibling sweep, I could have given the agent a list of specific functions to check (e.g., "check ensureProviders, loadTranscripts, loadQueue for race conditions") instead of asking it to search all async functions.
3. **Skip the second agent if codegraph covers it**: If codegraph had returned the full function bodies for ensureProviders and other candidates, I could have done the sibling analysis myself without an agent.

## Static check before live test
Did static source-level verification (read changed code, reasoned about correctness, confirmed field expectations). Did not run live e2e-regression-http because:
1. No Playwright browser tool available in this environment
2. Change is purely client-side (no backend contract changes)
3. Static analysis confirms the generation counter pattern is correct

## Sub-sessions/agents spawned
1. explore agent #1 (ses_05f1352a7ffeYO0rnC0jwv0XK1): model `lemonade/Qwen3.5-4B-MTP-GGUF` (local), ~150K tokens
2. explore agent #2 (ses_05f1341aeffea1BS9wVFHkT8mo): model `lemonade/Qwen3.5-4B-MTP-GGUF` (local), ~150K tokens

**Total estimated token cost**: ~300K tokens for agents + ~50K for orchestrator = ~350K tokens
