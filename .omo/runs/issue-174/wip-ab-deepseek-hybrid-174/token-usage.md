# Token Usage — issue #174, variant deepseek-hybrid

## Sub-agents spawned this run

| Agent | Model | Cloud/Local | Approx share |
|---|---|---|---|
| explore (bg_867e2ed5) | openrouter/inclusionai/ling-3.0-flash:free | Cloud (free) | Service pattern investigation |
| explore (bg_d757cc65) | openrouter/inclusionai/ling-3.0-flash:free | Cloud (free) | Test pattern investigation |

No other agents dispatched. Implementation was direct (plan is unambiguous — delegation exception rule).

## Token cost observations

- Both explore agents were cloud free-tier, so zero billed cost.
- codegraph_explore returned all needed model info in one call (no retries needed).
- Direct implementation avoided agent dispatch for coding — saved context/tokens.
- Full test suite (498 tests) ran in 44s — no retry loops.
- AGENTS.md stale about local/cloud agent mapping, but actual config was checked directly (no wasted agent calls to non-existent agent names).

## What worked well

- codegraph_explore for model schema (single call, all columns)
- Direct reads of correction.py + conftest.py (no agent needed for small files)
- Batch implementation (both files written before running tests)
- grep for existing search patterns (zero hits — confirmed greenfield)
- Test-first approach: wrote tests alongside service, ran immediately

## What could improve

- The `_MAX_QUERY_CHARS` test had an off-by-one (filler length miscalculation) — caught by test run, fixed quickly
- Plan had `export_directory` already in DEFAULT_SETTINGS — investigation caught this, saved wasted work
