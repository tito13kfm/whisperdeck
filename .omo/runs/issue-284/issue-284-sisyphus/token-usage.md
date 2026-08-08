# token-usage.md -- issue #284

| Agent | Model | Cloud/Local | Phase |
|-------|-------|-------------|-------|
| explore (bg_b33fddd6) | openrouter/nvidia/nemotron-3-super-120b-a12b:free | Cloud | Phase 1: services codebase survey (task-abandoned) |
| Sisyphus-Junior (deep) | openrouter/deepseek/deepseek-v4-pro | Cloud | Phase 2: implementation (voice_notes.py + llm_jobs.py) |
| Sisyphus-Junior (deep) | openrouter/deepseek/deepseek-v4-pro | Cloud | Phase 3: test writing (14 tests) |

Note: Two deep agent calls (implementation + tests), one aborted explore agent. All cloud, no local (Lemonade) agents used. No cost estimate available for OpenRouter deepseek-v4-pro or nemotron free tier.
