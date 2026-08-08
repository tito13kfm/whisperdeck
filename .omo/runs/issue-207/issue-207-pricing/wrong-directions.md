# Wrong directions — issue #207 run

## Config issue: opencode-go weekly usage exhausted
The `deep` and `ultrabrain` categories in global config map to `opencode-go/minimax-m3`,
which hit its weekly usage cap during Phase 2. Overrode in project config
(`.opencode/oh-my-openagent.jsonc`) to `meta/muse-spark-1.1` (same model that backs
`oracle`, already configured and working). The first implementation attempt
(bg_b8aff62c) was cancelled; re-dispatched as bg_ce2d9f55 with the override.

Recommendation: add an OpenRouter-based fallback for `deep`/`ultrabrain` in the
global config so this doesn't block future runs. `openrouter/deepseek/deepseek-v4-flash`
or `openrouter/deepseek/deepseek-v4-pro` would work and are already in OpenRouter
rotation.

## No AGENTS.md vs live config discrepancies
- `explore` = cloud (openrouter/inclusionai/ling-3.0-flash:free) — matches AGENTS.md
- `oracle` = cloud (meta/muse-spark-1.1) — matches AGENTS.md
- `sisyphus-junior` = local (lemonade/Qwen3.5-4B-MTP-GGUF) — matches AGENTS.md
