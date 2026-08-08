# Wrong Directions — Issue #146

## AGENTS.md local/cloud labeling error (logged 2026-07-26)

AGENTS.md line 127 claims `atlas`, `quick`, `writing`, `unspecified-low` are OpenRouter-only (not subject to the local Lemonade 2-agent concurrency cap).

**Actual config** (`~/.config/opencode/oh-my-openagent.json`):
- `atlas` → `lemonade/Qwen3.5-4B-MTP-GGUF` (LOCAL)
- `quick` → `lemonade/Qwen3-0.6B-GGUF` (LOCAL)
- `writing` → `lemonade/Bonsai-8B-gguf` (LOCAL)
- `unspecified-low` → `lemonade/Qwen3-0.6B-GGUF` (LOCAL)

All four are local Lemonade models and DO share the VRAM-bound 2-agent concurrency cap. AGENTS.md is wrong on this point.

**Recommendation:** Update AGENTS.md's agent-cap table to reflect the current config, or remove the table entirely and point readers at the config file as the single source of truth.

## Issue snippet accuracy

The issue's proposed fix snippet was structurally correct but missing:
1. `activate` event handler for old cache cleanup
2. Error handling for service worker registration

Both were added in the implementation.
