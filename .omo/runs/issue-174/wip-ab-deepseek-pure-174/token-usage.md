# Token Usage — issue #174, wip/ab-deepseek-pure-174

## Agent/model breakdown

| Session | Agent | Model | Cloud/Local | Purpose | Est. tokens |
|---|---|---|---|---|---|
| Main orchestrator | sisyphus | opencode-go/deepseek-v4-pro | Cloud | Orchestration, direct implementation, report writing | ~25K |
| explore ses_05969e34dffe | explore | openrouter/inclusionai/ling-3.0-flash:free | Cloud (free) | Test fixture patterns | ~8K |
| explore ses_05969daf2ffe | explore | openrouter/inclusionai/ling-3.0-flash:free | Cloud (free) | Existing search code check | ~5K |
| explore ses_05969cf1bffe | explore | openrouter/inclusionai/ling-3.0-flash:free | Cloud (free) | Service conventions | ~8K |
| codegraph_explore | N/A (MCP) | N/A | Local | Transcript model + blast radius | ~5K |

**Total estimated:** ~51K tokens. All three explore agents were cloud/free tier. codegraph_explore is single-digit-millisecond index lookup. Main orchestrator is the primary cost driver (deepseek-v4-pro).

## No deep/ultrabrain agents used

This issue was a clean greenfield implementation with a fully-specified plan (task 1 of `.omo/plans/llm-assistant.md`). No ambiguity, no research needed. Direct implementation by orchestrator + explore lookups for codebase conventions was sufficient. Did not dispatch any `deep` or `ultrabrain` category tasks, saving significant cost.

## What worked well

1. **codegraph_explore first** — one call returned the full Transcript model source, fields, and blast radius (110 callers). No file reads needed for the data model.

2. **Parallel explore agents** — three independent lookups (test fixtures, existing search, service conventions) fired simultaneously. All returned within ~16s. Since all three were cloud/free (inclusionai/ling-3.0-flash), no local concurrency bottleneck.

3. **Direct implementation** — the plan spec was complete and unambiguous. No need to delegate to a `deep` agent for implementation. Wrote both files directly, saving at least one agent round-trip.

4. **from_end=true not needed** — explore agents were synchronous (no background_output calls), so no context was wasted on analysis blocks.

5. **Tests passed first try** — all 21 new tests + 500 existing tests passed on first run. No retry loops.

## What would cut tokens next time

1. **Skip the manual config check** — AGENTS.md's model table is confirmed stale. Next run can just note "explore = cloud per last check" in a single sentence instead of reading the config file fresh. But the workflow says to check every time, and that's correct for robustness.

2. **Fewer explore agents** — the "existing search code" check could have been done with a single grep. But the explore agent confirmed there's nothing across all services/*.py + app.py + tests/ in one call, which a grep would also find. ~5K tokens is cheap for the confidence.

3. **Batch implementation** — writing both files in one go (2 write tool calls) avoided incremental edit-then-reread cycles. No files were re-opened after writing.
