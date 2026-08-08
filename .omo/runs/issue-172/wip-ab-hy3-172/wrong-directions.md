# Wrong directions — issue #172 (variant hy3)

## 1. AGENTS.md agent-cap table is stale/wrong vs the live config (verified 2026-07-27)
I read `~/.config/opencode/oh-my-openagent.json` directly. Findings contradict
AGENTS.md:

- AGENTS.md says `explore` / `explore-hard` are LOCAL Lemonade models
  (Qwen3.5-4B / DeepSeek-Qwen3-8B) subject to the 2-agent VRAM cap. Actual
  config: both map to `openrouter/inclusionai/ling-3.0-flash:free` (OpenRouter
  cloud). So the 2-cap does NOT apply to explore/explore-hard — they are cloud,
  not local. (For this run I used `codegraph_explore`, a free code-intel MCP,
  not these agents, so it did not matter — but the doc is wrong.)
- AGENTS.md "second known doc error" claims `atlas`, `quick`, `writing`,
  `unspecified-low` are ALL local Lemonade. Actual config: only `atlas`
  (lemonade/Qwen3.5-4B) and `writing` (lemonade/Bonsai-8B) are local;
  `quick` (openrouter/ling-3.0-flash:free) and `unspecified-low`
  (openrouter/ling-3.0-flash:free) are cloud. So that doc-error claim is itself
  partially wrong for `quick` and `unspecified-low`.
- AGENTS.md first doc error (no `scout` / `plan` keys; only `explore` /
  `explore-hard`) is CORRECT — config confirms only explore/explore-hard exist.

Recommendation: update AGENTS.md's agent-cap section to the live config. The
2-cap should be described as applying to `atlas` + `sisyphus-junior` + `writing`
(the actual lemonade/* models), not explore/explore-hard.

## 2. Plan line numbers were stale (expected, no functional impact)
`.omo/plans/markdown-export.md` cited app.py:1941, rack.js:3167/2584/4388,
reformatting.py:112, settings.py:31. Current code: format route at app.py:1983,
exportToolbarHtml at rack.js:3176, detailBodyClick at rack.js:2591,
loadSettingsPage at rack.js:4404, classify_intent ends at reformatting.py:112
(unchanged). All structures matched; only offsets shifted. Confirms the "don't
trust issue line numbers" rule — I verified every reference against current
source before editing.

## 3. Comment/docstring hook fired on my docstrings
The repo's comment/docstring hook flagged my `export_markdown` route docstring
and one inline comment. I kept the route docstring (matches the existing
per-route docstring convention in app.py — public API documentation) and
removed one unnecessary inline comment plus one test-helper docstring per the
hook's guidance. No behavioral change.
