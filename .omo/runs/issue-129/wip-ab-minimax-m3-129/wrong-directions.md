# Wrong Directions / Discrepancies Log

Run timestamp: 2026-07-26

## `explore-hard` agent does not exist

The runner-prompt and AGENTS.md both reference `explore-hard` as a distinct agent name. The current `~/.config/opencode/oh-my-openagent.json` only defines `explore` (and the rest of the agent/category entries). The OpenCode subagent dispatcher returns `Unknown agent: "explore-hard". Available agents: ...` when invoked by that name.

- **Confirmed by:** initial Phase 1 dispatch in this run.
- **Impact:** any prompt that hard-codes `subagent_type="explore-hard"` will fail. The runner-prompt already anticipates this ("If invoking `scout` or `plan` by name fails to resolve, use `explore`/`explore-hard` instead") but the fallback chain is broken because `explore-hard` itself doesn't resolve.
- **Recommended fix to runner-prompt / AGENTS.md:** Either (a) re-add `explore-hard` to the live config (would map to `lemonade/DeepSeek-Qwen3-8B-GGUF` per the AGENTS.md model table), or (b) drop the reference to `explore-hard` and use `explore` only, with a note that reasoning-heavy tasks are still served by `explore` for code-context work and by `oracle`/`deep`/`ultrabrain` for hard cross-system reasoning. AGENTS.md's "model table" already says `explore-hard` should exist, so config is the source of truth and the agent was just dropped from `oh-my-openagent.json` without updating the doc.

## `quick` / `writing` / `unspecified-low` are local, not cloud

AGENTS.md's "Agents that do NOT need Lemonade" section lists these three plus a fourth as cloud-only. The current `oh-my-openagent.json` maps all four to `lemonade/Qwen3-0.6B-GGUF` (quick, unspecified-low) and `lemonade/Bonsai-8B-gguf` (writing), plus `atlas` to `lemonade/Qwen3.5-4B-MTP-GGUF`. All five count toward the 2-local-agent cap.

- **Impact:** silently violated the cap if anyone trusted AGENTS.md's list. Verified at the start of this run by reading the live config.
- **Recommended fix to AGENTS.md:** rewrite the section to read the live config, not assert a hard-coded list.

## Issue line-number drift

Issue body says "static/rack.js:2338 loadTranscriptDetail()". Current file has it at line 2372. The function body is the same; only the line number drifted, presumably from unrelated edits between issue filing and now. Worth noting because anyone blindly using the issue's line number for a tool call would hit the wrong line.

- **Recommended fix to issue template (if the repo owns one):** don't ask reporters for line numbers. Ask for function/symbol name and let the tool resolve.

## Codegraph not indexed in this worktree

`codegraph_explore` with `projectPath=C:\Claude\whisperdesk-ab-minimax-m3-129` returns "project isn't indexed" (no `.codegraph/`). The main repo checkout IS indexed — passing `projectPath=C:\Claude\whisperdesk` (the main repo) worked. Worktrees created via `git worktree add` apparently don't get the codegraph index by default.

- **Workaround used in this run:** passed the main repo's path to `codegraph_explore`. The result was still correct (line numbers and function bodies match the worktree's on-disk content because the worktree is a fresh checkout off the same `master`).
- **Recommended fix:** either re-point codegraph at the worktree path, or document the workaround. (This is a `codegraph init`-in-the-worktree issue, not a tool bug.)
