# wrong-directions.md — issue 169 (variant hy3)

Discrepancies found vs the docs/instructions while executing the run. Each
entry has a recommended fix. Re-checked against the live config/current files
before logging.

## 1. AGENTS.md line 127 local/cloud labeling is wrong (CONFIRMED, second known error)
AGENTS.md claims `atlas`, `quick`, `writing`, and `unspecified-low` are
OpenRouter-only and "not subject to the local cap." The actual
`~/.config/opencode/oh-my-openagent.json` maps ALL FOUR to local Lemonade
models (`lemonade/Qwen3.5-4B-MTP-GGUF`, `lemonade/Qwen3-0.6B-GGUF`,
`lemonade/Bonsai-8B-gguf`). They ARE local and DO share the VRAM-bound
2-agent concurrency limit. Treat `atlas/quick/writing/unspecified-low` as
local-cap-counted.

Recommended fix: update AGENTS.md line 127 table to mark those four as local
Lemonade (`lemonade/...`), or delete the "OpenRouter-only" claim.

## 2. `explore-hard` agent does not exist in this runtime's registry (NEW)
AGENTS.md says the current config "only defines `explore` and
`explore-hard`". The `task()` tool's available subagent_types here are:
`explore, scout, plan, Metis, Momus, librarian, oracle, general, advisor,
architect, Sisyphus-Junior, code-simplifier, multimodal-looker, caveman:*`.
There is NO `explore-hard`. `scout` and `plan` DO exist (contrary to
AGENTS.md's other claim). So AGENTS.md is wrong in BOTH directions about
this pair.

Impact: Phase 1 "explore-hard" reasoning work was run via `explore`
(local Lemonade) instead. `explore` is local, so the 2-cap still applies.
Two `explore` agents were run in parallel (within cap).

Recommended fix: fix AGENTS.md — `explore-hard` is not a valid
subagent_type; use `explore` (local) or a cloud reasoning category
(`deep`/`ultrabrain`/`unspecified-high`) for heavier reasoning.

## 3. `codegraph_explore` budget truncation
When probing a function body twice with budget truncation, stop and use a
direct `Read`. Observed once on `run_llm_job` (large). Mitigated by reading
the truncated sections via targeted second calls.
