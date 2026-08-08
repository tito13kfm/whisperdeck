# Issue #171 — Wrong Directions

## AGENTS.md: explore-hard agent doesn't exist

AGENTS.md describes `explore-hard` as an agent backed by `DeepSeek-Qwen3-8B-GGUF`. The current `~/.config/opencode/oh-my-openagent.json` has no `explore-hard` key in its `agents` block — only `explore` is defined.

**Impact**: Attempted to invoke `task(subagent_type="explore-hard", ...)` — got "Unknown agent" error. Had to fall back to `explore`.

**Fix**: Either add `explore-hard` to the config, or remove it from AGENTS.md's agent table. This is the same class of error as the already-documented `scout`/`plan` issue. Consolidate: AGENTS.md should enumerate only agents that actually exist in the config.

## AGENTS.md: AGENTS.md line 127 local/cloud labeling

Already documented in the issue-runner prompt. Not re-verified fresh in this run since the task used `explore` (known local) and didn't need to query this. But this is still stale per the last verification.

## Issue #171 body: no explicit acceptance criteria checklist

The issue body describes the goal, why, and scope but has no `Definition of Done` or `Requirements` section. This is fine for an open-ended feature but means there's nothing to checkmark against. Added implicit criteria in self-audit.md.
