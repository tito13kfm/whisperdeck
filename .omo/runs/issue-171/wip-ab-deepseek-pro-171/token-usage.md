# Issue #171 — Token Usage

## Sub-agent dispatch summary

| Agent | Model | Cloud/Local | Purpose | Est. token share |
|-------|-------|-------------|---------|------------------|
| explore (bg_d0466d9a) | Qwen3.5-4B-MTP-GGUF (Lemonade) | Local | Transcript list API + frontend investigation | ~30% (8min) |
| explore (bg_f1076aa6) | Qwen3.5-4B-MTP-GGUF (Lemonade) | Local | Voice-note LLM chain pattern | ~20% (4min) |

Orchestrator: deepseek-v4-pro (OpenRouter, cloud). Cost unknown — OpenCode usage panel tracks this.

## What worked

1. **Codegraph first**: `codegraph_explore` resolved the Transcript model, LlmJob model, and voice_note chain flow in 3 calls. No direct file reads needed for the database models or the `run_voice_note_chain` function body.
2. **Direct reads after codegraph gaps**: When codegraph truncated the `VALID_KINDS` tuple and `enqueue_llm_job` body, switched to direct `read` with line offsets — two calls, both got full content.
3. **Parallel explore agents**: Both launched simultaneously (within 2-cap), completed without overlapping work.
4. **Batch edits**: All 7 files changed in one continuous pass without re-reading between edits. One final test run verified everything.

## What could be better

1. **The first explore agent (bg_d0466d9a) took 8 minutes** — it re-discovered things codegraph had already returned (it repeatedly used glob/bash/read to find `app.py`, `rack.js`, etc. instead of starting from codegraph's findings). For future runs, provide codegraph results as context to explore agents so they don't re-search.
2. **`from_end=true` used**: Collected agent results with `from_end=true` — saved the full-session context cost.
3. **No retry loops**: Zero retries on any operation. The explore-hard → explore fallback was a one-shot.
4. **Direct implementation over delegation**: Per the delegation exception rule (fully-specified plan from investigation.md), implemented directly for all 7 files. This kept token cost minimal compared to spawning 7 sub-agents.
