# Token Usage Report — Issue #125 (deepseek-pro)

**Orchestrator model**: opencode-go/deepseek-v4-pro (cloud)

## Agent dispatch log

| Agent | Model | Cloud/Local | Duration | Purpose |
|-------|-------|-------------|----------|---------|
| explore (bg_61403b99) | lemonade/Qwen3.5-4B-MTP-GGUF | Local | ~5m | Read User model + create_user function |
| explore (bg_2c3e7022) | lemonade/Qwen3.5-4B-MTP-GGUF | Local | ~5m | Sibling race pattern sweep |

No cloud agents (deep, ultrabrain, oracle, etc.) were dispatched. All investigation and implementation was done by the orchestrator directly or through the 2 local agents above.

**Total cloud cost**: Only the orchestrator's own turns (OpenRouter-billed). No additional cloud agent spend.

## Token hotspots

1. **app.py direct read (offsets 330-379, then 395-454)**: The first offset returned serialization helpers, not the register route. Required a second read at the correct offset. Could have been one query with codegraph.

2. **User model + create_user agent (bg_61403b99)**: 5 minutes for what was essentially reading two files (~250 lines total). The agent did its own exploration (globbing, grepping) to locate the files even though the orchestrator already knew they were at `services/auth.py` and `database/__init__.py`. Providing file paths in the prompt would have cut this to ~1 minute.

3. **Full test suite runs (3x)**: Cost of verifying all 388 tests — necessary for confidence but expensive in wall time (~34s each). The subset runs (auth only, single test) were much faster.

## Efficiency gains applied

- `from_end=true` on both background_output calls
- No throwaway server-start/auth/upload test cycle — static source-level check was sufficient
- No codegraph retries (one attempt, fell back to direct reads immediately)
- Direct file reads at known offsets rather than full-file reads
- Fix was trivial (3 lines of app.py) — implemented directly rather than dispatching a cloud agent for a mechanical change

## What could cut cost next time

1. **Give agents exact file paths**: The explore agents spent time finding what the orchestrator already knew. `TASK: Read services/auth.py lines 44-56 — report create_user.` would save 3-4 minutes.
2. **Combine the two agent tasks**: Both local agents were reading from the same 2-3 files. A single agent could have done both tasks (but would have been slower — 10m vs 2x5m).
3. **Pre-warm the Lemonade server**: Both agents had ~5m durations. The Qwen3.5-4B model likely needed to be loaded from disk on first call.
