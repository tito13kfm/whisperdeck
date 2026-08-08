# Wrong Directions — issue #174, variant deepseek-hybrid

## AGENTS.md agent-cap table is stale (global config drift)
- AGENTS.md says `explore` and `explore-hard` are local Lemonade agents subject to the 2-cap.
- Current global config (`~/.config/opencode/oh-my-openagent.json`) maps both to `openrouter/inclusionai/ling-3.0-flash:free` — cloud, not local, no cap applies.
- AGENTS.md says `atlas`, `quick`, `writing`, `unspecified-low` are cloud. Actual config:
  - `atlas`: LOCAL (lemonade/Qwen3.5-4B) — IS subject to cap
  - `quick`: CLOUD (openrouter/free)
  - `writing`: LOCAL (lemonade/Bonsai-8B) — IS subject to cap
  - `unspecified-low`: CLOUD (openrouter/free)
- Only `sisyphus-junior`, `atlas`, and `writing` are local/capped. Everything else is cloud.
- Fix: Remove the table from AGENTS.md entirely and replace with "check the current config."

## Plan `.omo/plans/llm-assistant.md` Task 10 (export_directory) already done
- The plan says to add `"export_directory": ""` to `DEFAULT_SETTINGS` in services/settings.py.
- It already exists at line 31: `"export_directory": ""`.
- The generic validation `patch = {key: value for key, value in updates.items() if key in DEFAULT_SETTINGS}` already handles it.
- No work needed for Task 10.

## Issue #174 body is in scope with the plan's Task 1+2
- The issue asks for `search_transcripts(db, user_id, query)` matching the plan's Task 1 spec.
- No discrepancies found between issue description and plan — both describe the same function.
