# Token usage — Issue #171 (variant hy3)

## Delegation actually performed this run
ZERO successful agent/sub-session dispatches. The two `task(subagent_type="explore-hard")`
calls in Phase 1 FAILED immediately with "Unknown agent" (the runtime only exposes
`explore`, not `explore-hard`/`scout`/`plan`), so no sub-session was created and nothing
billed. I fell back to:
- `codegraph_explore` (free MCP tool, reads verbatim source — not an agent, not billed as a
  sub-session) for all codebase reading.
- Direct `Read`/`grep`/`Edit`/test runs by the orchestrator (hy3, the invoking model).

So the only model that did work this run is **opencode-go/hy3** (cloud, this run's own
model). No delegation to a *different* cloud model occurred. The `deep`/`ultrabrain`
categories are also `opencode-go/hy3` per the live config, so even a Phase-2 delegation
would have stayed on-model. All cost for this run lives in OpenCode's own usage panel
under the hy3 row; this file cannot read it.

## Where tokens were spent (approx, by activity)
1. Investigation reads: ~6 `codegraph_explore` calls (LlmJob infra, enqueue helpers,
   app.py transcripts endpoint, rack.js list/voice-note UI, serializer, kind-switch sweep).
   Cheaper than agent dispatches, but each returns a large verbatim dump.
2. Reading `run_llm_job` body (Read, 320 lines) + queue.py finalize block + rack.js
   loadTranscripts/renderBankRows.
3. Implementation: ~12 Edit calls across 6 files + 1 new file (services/auto_tag.py) +
   1 new test file. Edits were batched per file, not re-verified after each single one.
4. Tests: full suite run TWICE (first 1 fail on a test bug I introduced and fixed;
   second full run 442 passed). The full-suite re-run is ~42s / one of the larger costs.

## What cut cost vs the known anti-patterns
- Batched edits per file (did not re-read after every edit). One re-read of run_llm_job
  was enough.
- Used `codegraph_explore` (verbatim source tool) instead of spawning weak local agents
  for reasoning — both faster and higher quality than the 4B `explore` model would have
  given for the dispatch logic.
- Reused the exact `test_voice_note_chain.py` / `_chat_response` stub pattern rather than
  inventing a stub, so the new tests passed first try (except one username-collision bug
  I introduced and fixed in <1 min).

## Known doc errors confirmed (logged in wrong-directions.md)
- `explore-hard` does not resolve as a `task()` agent in this runtime (only `explore`).
- AGENTS.md line ~127 local/cloud labeling is wrong: `atlas`/`quick`/`writing`/`unspecified-low`
  are ALL local Lemonade models (confirmed in live `~/.config/opencode/oh-my-openagent.json`),
  so they share the 2-agent VRAM cap. `deep`/`ultrabrain`/`oracle` are cloud (hy3 / qwen3.7-max).
