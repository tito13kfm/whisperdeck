# wrong-directions.md — issue #169, deepseek-pro variant

## `explore-hard` agent rejected by task tool despite config definition

**When**: Phase 1, first agent dispatch.

**What happened**: The issue-runner prompt instructs using `explore-hard` for reasoning-heavy investigation. The global config (`~/.config/opencode/oh-my-openagent.json`) defines `explore-hard` at line 23 with model `lemonade/DeepSeek-Qwen3-8B-GGUF`. But `task(subagent_type="explore-hard", ...)` returns "Unknown agent: 'explore-hard'". The project config (`.opencode/oh-my-openagent.jsonc`) has no agent-level override for it.

**Why it matters**: Forces all Phase 1 investigation onto the lighter `explore` agent (Qwen3.5-4B), which may miss reasoning-heavy findings the 8B model would catch.

**Recommendation**: Either fix the framework to register `explore-hard` from config, or update the issue-runner prompt to say "try `explore-hard` first, fall back to `explore` if it fails". Since this is a known doc error already flagged in the prompt itself (AGENTS.md named `scout` and `plan` which also don't exist), this may be a recurring config-sync issue.

## AGENTS.md agent list vs config — `scout` and `plan`

**When**: Noted during prompt review.

**Finding**: AGENTS.md's model table (line ~127) references `scout` and `plan` as distinct agents. The current config defines neither. This was already documented in the issue-runner prompt as a known error. No impact on this run since we didn't try to use them.

**Recommendation**: Remove `scout` and `plan` from AGENTS.md or add them to the config if they're meant to exist.

## Backend agent wrote to main repo instead of worktree

**When**: Phase 2, backend implementation.

**What happened**: The `deep` category agent dispatched for backend changes wrote to the main repo checkout (C:/Claude/whisperdesk, on `master`) instead of the worktree (C:/Claude/whisperdesk-ab-deepseek-pro-169, on `wip/ab-deepseek-pro-169`). The frontend agent correctly wrote to the worktree. Required manual `git diff > patch`, `git checkout --` (revert on master), `git apply` (apply on worktree) to fix.

**Root cause**: The agent prompt's [CONTEXT] block mentioned the worktree path but the agent's working directory resolved to the main repo. The file paths in the prompt (e.g. `services/reformatting.py`) are relative, and the agent defaulted to its cwd.

**Recommendation**: In Phase 2 prompts, include the absolute worktree path explicitly as a working directory instruction: "All file edits must be made in C:/Claude/whisperdesk-ab-deepseek-pro-169. Run `pwd` to confirm you're in the right directory before editing."

## Format endpoint guard (app.py:1932) correctly rejects voice_note without changes

**When**: Investigation + implementation verification.

**Finding**: The investigation initially listed `app.py:1932` as needing a change, but on closer inspection the existing `if t.kind != "dictation"` guard already correctly rejects voice_note transcripts from manual format actions. No change needed. This is correct behavior: voice notes use automatic structuring, not manual reformat buttons.
