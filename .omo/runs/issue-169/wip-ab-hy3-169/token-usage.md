# token-usage.md — issue 169 (variant hy3)

Where token spend was heaviest and what to cut next time. Sub-session/agent
disclosure below (required for cross-check against the OpenCode usage panel).

## Agents / sub-sessions spawned this run
- `explore` (LOCAL Lemonade Qwen3.5-4B-MTP-GGUF) x2 in parallel — Phase 1
  kind-branch audit + detail-tab mapping. Local, within 2-cap. ~19 min combined.
- `deep` category (Sisyphus-Junior wrapper -> opencode-go/deepseek-v4-pro, CLOUD)
  — Phase 2 backend: VoiceNote model, services/voice_notes.py, llm_jobs
  integration, completion trigger, app.py routes + serializer, tests.
- `visual-engineering` category (Sisyphus-Junior wrapper -> opencode-go/mimo-v2.5,
  CLOUD) — Phase 2 frontend: index.html nav + page, rack.js Voice Notes board,
  transcribe-page kind option, detail Note tab.

Model disclosure: both cloud agents bill to OpenCode's usage panel under the
`deepseek-v4-pro` (backend) and `mimo-v2.5` (frontend) models respectively.
They are NOT the invoking `hy3` model. The human should cross-check the panel
for these two runs' spend.

No sub-session token counts are visible to this file.

## Worst spend
1. `codegraph_explore` repeated calls on the same large functions
   (`run_llm_job`, `summarize`) hit budget truncation and returned partial
   source, forcing re-queries. Fix: one targeted call per symbol; switch to
   direct `Read` after 2 truncations.
2. Broad `grep` over the whole repo (include `*.py`) timed out at 60s
   scanning `.venv`. Fix: grep a specific directory or single file; glob
   does not support brace expansion (`{a,b}`), use separate calls.
3. codegraph on `index.html` pulled in the README (large) as a "project
   README" side-effect. Noted; prefer targeted reads for static files.

## Applied-this-run efficiencies
- Did the static/source-level contract reasoning via codegraph + direct
  reads before any implementation agent dispatch.
- Used `from_end=true` when collecting agent output (will apply on receipt).
