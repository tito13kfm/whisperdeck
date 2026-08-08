# Token Usage — Issue #192

| Component | Agent/Category | Model | Cloud/Local | Notes |
|---|---|---|---|---|
| Orchestrator | — | openrouter/deepseek/deepseek-v4-pro | Cloud | ~15 turns for investigation, coordination, test writing, verification |
| Phase 1 investigation | — | — | — | Performed by orchestrator (codegraph_explore + direct reads), not delegated |
| Phase 2 fix | deep (Sisyphus-Junior) | opencode-go/qwen3.7-plus | Cloud | ses_026659333ffe4DGnQC3ldNBk3F — single file edit |
| Phase 3 testing | — | — | — | Performed by orchestrator |
| Phase 3.75 Oracle | oracle | (below) | Cloud | See self-audit.md for verdict |

| Phase 3.75 Oracle | deep (Sisyphus-Junior) | opencode-go/qwen3.7-plus | Cloud | ses_02658f251ffeTX8U06uAWra3fv — APPROVE |

Independent review: Oracle (Phase 3.75) - APPROVE, shared-prefix heuristic solves the stemmer mismatch; false positives are minor quality trade-offs, not correctness regressions.
