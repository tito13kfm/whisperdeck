# Issue #176 token-usage.md — deepseek-pure

## Orchestrator

deepseek-v4-pro (cloud, OpenRouter) — all planning, editing, testing, reporting.

## Sub-agents dispatched

All 4 explore agents were routed to cloud (openrouter/inclusionai/ling-3.0-flash:free), not local Lemonade — the 2-cap local limit was never relevant.

| Session ID | Task | Model |
|---|---|---|
| ses_05926a3bdffeJmpLr8oOcoDD1B | Read assistant.py + search.py | openrouter/inclusionai/ling-3.0-flash:free |
| ses_059264d4dffeb1JFSR4YPuWgwl | Read app.py endpoint patterns | openrouter/inclusionai/ling-3.0-flash:free |
| ses_059259518ffeq572iSg3TGLRwE | Read llm_jobs.py assistant worker | openrouter/inclusionai/ling-3.0-flash:free |
| ses_059258d8fffelNGeCz5COMoSuN | Read settings.py resolve_provider_key | openrouter/inclusionai/ling-3.0-flash:free |

## What worked

- codegraph_explore: 1 call covered app.py + llm_jobs.py structure; efficient
- Batched delegation: 2 explore agents fired in parallel (batch 1: assistant.py+search.py + app.py; batch 2: llm_jobs.py + settings.py)
- Direct implementation: per the delegation exception, Phase 2 was mechanical transcription from investigation.md, done directly without subagents
- No retry loops, no repeated reads

## What to improve

- `explore-hard` doesn't exist as an agent key — one failed dispatch before switching to `explore`. Could have checked the config first.
- Main repo vs worktree confusion: first batch of edits went to main repo (C:/Claude/whisperdesk/app.py), had to re-apply to worktree. A check after editing would have caught this sooner.
