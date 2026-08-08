# token-usage.md — issue 169 (variant hy3-r2)

## Delegation ledger (every sub-session/agent this run spawned)

All dispatch is by the orchestrator (model: `opencode-go/deepseek-v4-pro`, cloud —
the `sisyphus` agent in `~/.config/opencode/oh-my-openagent.json`).

| # | Phase | Agent / mechanism | subagent_type or category | Model (tier) | Cloud or Local | Purpose |
|---|-------|-------------------|--------------------------|--------------|----------------|---------|
| 1 | Phase 1 | explore (investigation agent A) | `explore` | `lemonade/Qwen3.5-4B-MTP-GGUF` | **Local** (Lemonade, counts toward 2-cap) | Transcript model + `kind` enum + dictation diarization-off + all kind-switch sites |
| 2 | Phase 1 | explore (investigation agent B) | `explore` | `lemonade/Qwen3.5-4B-MTP-GGUF` | **Local** (within 2-cap) | LlmJob dispatch registry + VALID_KINDS + auto-enqueue path + enqueue-complement sweep |
| 3 | Phase 2 | backend implementer | `deep` (category) | `opencode-go/deepseek-v4-pro` | **Cloud** (OpenRouter) | model columns + migration, voice_note_chain kind, services/voice_notes.py chain, queue.py wiring, app.py routes/validation/serialization, tests |
| 4 | Phase 2 | frontend implementer | `deep` (category) | `opencode-go/deepseek-v4-pro` | **Cloud** (OpenRouter) | Voice Notes board page, third capture mode, detail Note tab (static/rack.js + static/index.html only) |

Notes on the local-cap correction: AGENTS.md line 127 claims `atlas`, `quick`,
`writing`, `unspecified-low` are OpenRouter-only. Re-checked against the live config:
all four are `lemonade/*` models, i.e. LOCAL. The two Phase 1 `explore` agents are the
only local dispatch this run and stayed within the 2-concurrent cap (fired together,
batched). `explore-hard` is NOT a resolvable `subagent_type` (config-only model alias),
so the reasoning-heavy investigation used `explore` with concrete file:line prompts.

No cloud sub-agent was dispatched that differs from the orchestrator's own model
(deepseek-v4-pro) other than tier parity, so there is no separate cost mismatch to flag.

## Where token usage was worst (filled after agents return)
- TBD: explore agents each ran ~4-7 min on local 4B; reasonable. Will note if a retry
  or re-read spiked usage.
- TBD: deep agents (Phase 2) are the dominant cost — two parallel deepseek-v4-pro runs.
  Acceptable because the feature is large and the cap does not apply to cloud agents.

## What would cut it next time
1. (Carried from the prompt's standing guidance) Do the static/source-level contract
   check before any throwaway server-start/auth/upload test cycle. Not yet exercised
   this run since no live browser was available.
2. If codegraph_explore truncates on the same function twice, switch to a direct Read
   (two attempts max). Not hit this run.
3. Use `from_end=true` when collecting background agent output (done for Phase 1).
4. The two Phase 1 `explore` calls could have been one if the prompts were merged, but
   the 2-cap forced batching anyway; no waste.

## Post-run (actuals)

- Phase 1: 2 local `explore` agents (Qwen3.5-4B), ran ~4 min and ~7 min. No retries.
- Phase 2: 2 cloud `deep` (deepseek-v4-pro) agents in parallel — backend ran ~10 min,
  frontend ~5 min. This is the dominant token cost of the run (two parallel heavy
  reasoning agents), but acceptable since the cap does not apply to cloud agents and
  the feature is large. No wasted re-reads; the agents were given concrete file:line
  specs so they did not thrash.
- No live browser / Playwright tool was available, so the e2e-regression-http tier was
  SKIPPED (documented as an open item in self-audit.md). The static source-level check
  was done instead (full pytest 411 passed + `node --check` on rack.js + manual reads).
  This is the one place runtime behavior is unverified; a human in-browser spot-check is
  recommended before merge.
