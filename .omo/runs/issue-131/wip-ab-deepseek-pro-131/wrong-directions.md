# Wrong Directions — issue #131, A/B run deepseek-pro

## `explore-hard` agent doesn't exist

**When**: Phase 1, immediately after codegraph call.
**What**: The issue-runner prompt says to use `explore-hard` for "anything requiring actual reasoning." The task tool returned "Unknown agent: explore-hard" for both attempts. The live config only defines `explore` and `scout` — no `explore-hard` key.
**Impact**: Fall back to `explore` for all Phase 1 work. `explore-hard` likely renamed/removed since issue-runner was written.
**Fix**: Replace every reference to `explore-hard` in the issue-runner prompt with `explore` (which runs Qwen3.5-4B, the heavier local model), or add `explore-hard` back to the config if the model still exists.
