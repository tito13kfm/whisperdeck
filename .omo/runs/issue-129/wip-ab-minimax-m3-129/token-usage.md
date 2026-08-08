# Token Usage — Issue #129 (variant minimax-m3)

## Sub-sessions / agents dispatched

| Dispatch | Model | Cloud/Local | Purpose | Token share (est.) |
|----------|-------|-------------|---------|-------------------|
| `task(subagent_type="explore", ...)` sibling sweep | `lemonade/Qwen3.5-4B-MTP-GGUF` | local | Sibling pattern search in `static/rack.js` | small (~1-2k output) |
| `codegraph_explore` (direct tool, not an agent dispatch) | indexed at main-repo path | n/a | Source + call graph for `loadTranscriptDetail`, `renderDetail`, `detailAction` | n/a (cached index) |
| Direct Read/Grep/Bash | n/a | n/a | Verification reads, file:line lookups | small |
| Orchestrator (this session) | `minimax-m3` (the variant label) | cloud (per system prompt) | Planning, fix application, test authoring, commit, push | majority of session spend |

Note: the orchestrator model label is `minimax-m3` (the variant label passed to `/issueAB`); the underlying model ID and exact cost live in OpenCode's own usage panel, not in this file. The dispatched `explore` agent used the project's default local model (`Qwen3.5-4B-MTP-GGUF`) per `~/.config/opencode/oh-my-openagent.json` — local, no cloud spend.

## Where token usage was worst

1. **Sibling-sweep agent had to fight its own tooling.** Dispatched it on a fresh worktree, which doesn't have codegraph indexed. The agent tried `grep` / `glob` repeatedly with Windows path quirks before settling on reading the file directly. Burned ~30s of latency, no real cost. The right move would have been to **point the agent at the indexed main-repo path** for its searches (same as I did with codegraph_explore) — the worktree's checkout is byte-identical to master's, so there's no staleness risk. Noted for next run.

2. **Initial dispatch of `explore-hard` failed.** Cost: one round-trip + one error message. The agent name doesn't exist in the live config; AGENTS.md's table still lists it. Re-dispatched as `explore` and got the answer. Burned maybe 5s of latency, no significant tokens. Fix: read the live config before dispatching, don't trust the doc.

3. **Direct re-read of `loadTranscriptDetail` was redundant.** Codegraph already returned the verbatim source. I then `read` the same lines (offset 2380, limit 50) to double-check. Both calls were effectively the same content. The follow-up was useful only for confirming `scheduleDetailPoll` lines 2398-2415 — that one wasn't returned by codegraph in the first call and the Read was necessary. Note for next run: codegraph returns "key symbols" grouped by file; if a sibling in the same file is needed, ask codegraph explicitly for it in the same query rather than burning a second `Read`.

4. **The race-simulation Node harness** (`race-sim.js` in the opencode temp dir) was a 90-line extra step. Worth it for the source-level confidence ("pre-fix: BUG present, post-fix: FIX works" in one terminal output) but it does add tokens. The static reasoning in `investigation.md` already covered the same ground. Could have skipped it if the static check were the only tier.

## What would cut token use next time

1. **Pass the indexed main-repo path to codegraph** when working in a worktree. One-line fix in the prompt template.
2. **Don't dispatch `explore-hard` if it's not in the live config.** Read the config first; it changes often.
3. **Ask codegraph for the sibling symbols in the same query.** `loadTranscriptDetail scheduleDetailPoll loadTape` in one call returns all three with their call paths; I only asked for one and then read the file for the other two.
4. **Skip the Node race-simulation harness** when the static reasoning + a written e2e test gives the same confidence. It's only worth it when the static check is ambiguous.

## Tiers run vs. skipped

- **Static source-level check:** run. Both by reading the patched function and by the Node race-simulation harness.
- **Existing pytest unit/integration suite:** NOT run. No `.venv` in the worktree; system Python lacks `librosa` (conftest fails fast on import). My change is in `static/rack.js` (browser-side JS), so the Python unit suite doesn't exercise it directly anyway.
- **`e2e-regression-http` / live browser e2e:** NOT run. No Playwright/Chromium installed in the worktree's environment. The new `tests/e2e/test_detail_rapid_clicks.py` is written and committed; it'll run on CI.

## Spend transparency

The cloud spend for this run is the `minimax-m3` orchestrator session itself, which is whatever OpenCode's usage panel reports. The one dispatched sub-agent (`explore`) ran on the local Lemonade Qwen3.5-4B-MTP-GGUF model and contributes zero cloud cost — only local VRAM time. No other cloud agents were dispatched.
