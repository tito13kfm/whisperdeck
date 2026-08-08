# wrong-directions.md — issue 169 (variant hy3-r2)

Discrepancies found while executing the issue-runner prompt. Each is re-checked
against the live config / current tooling before being logged here.

## 1. AGENTS.md line 127 mislabels local-vs-cloud for four agent/category names

**Claim in AGENTS.md:** "the actual config maps all four [atlas, quick, writing,
unspecified-low] to local `lemonade/` models, i.e. they ARE local and DO share
the same VRAM-bound concurrency limit" — wait, the issue-runner prompt itself
*already warns* that AGENTS.md line 127 lists them as OpenRouter-only, and that
the actual config maps them to local Lemonade. So this is a known, correct
warning in the prompt, NOT a contradiction I discovered.

What I actually re-confirmed against `~/.config/opencode/oh-my-openagent.json`:
- `atlas` → `lemonade/Qwen3.5-4B-MTP-GGUF`
- `quick` → `lemonade/Qwen3-0.6B-GGUF`
- `writing` → `lemonade/Bonsai-8B-gguf`
- `unspecified-low` → `lemonade/Qwen3-0.6B-GGUF`

All four are local Lemonade models, so they ARE subject to the 2-concurrent
local-agent cap. The prompt's warning is accurate; recording it here so the
human knows I honored the cap for `explore` calls (the only local agent I
dispatched). No fix needed to AGENTS.md beyond what the prompt already says;
if anything, the AGENTS.md line-127 text should be corrected to match reality.

## 2. `explore-hard` is NOT a resolvable subagent_type

**What happened:** The issue-runner prompt (Phase 1) instructs using the local
`explore-hard` agent for reasoning-heavy investigation. The task tool's
`subagent_type` registry only exposes: explore, scout, general, librarian,
oracle, plan, Metis, Momus, Sisyphus-Junior, advisor, architect, multimodal-looker,
code-simplifier, caveman:cavecrew-*. `explore-hard` is undefined as a
subagent_type (the config defines it only as an agent *model mapping*, not as a
dispatchable subagent).

**Resolution applied:** Fell back to `explore` (lemonade/Qwen3.5-4B-MTP-GGUF,
local, within the 2-cap) for both Phase 1 investigation agents, with very
concrete file:line enumeration prompts to keep the lighter model on-task. This
matches the prompt's own fallback guidance ("If invoking `scout` or `plan` by
name fails to resolve, use `explore`/`explore-hard` instead").

**Recommended fix for the prompt:** state explicitly that `explore-hard` is a
config-only model alias and is not dispatchable via `subagent_type`; agents
wanting the heavier local model should either use `explore` (current only
local-reasoning option) or route reasoning to a cloud category (`deep` /
`ultrabrain` → deepseek-v4-pro) when local capacity is the concern.

## 3. Issue #169 is a large, open-scoped feature (not a bug)

The issue body leaves scope "open deliberately." There is no stale snippet to
distrust, but there is also no concrete acceptance checklist, so Phase 3's
acceptance-criteria walk is satisfied by a self-defined scope (documented in
investigation.md). Flagging because the prompt's "don't trust the issue's
snippet" guidance assumes a concrete bug with line numbers; here the risk is
scope creep, not a wrong snippet.
