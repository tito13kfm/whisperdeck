# Wrong Directions - Issue #169 (qwen3.7-plus variant)

## Agent availability mismatch

**Issue:** AGENTS.md references `explore-hard` as a distinct agent key for reasoning-heavy investigation. The actual runtime environment does not expose `explore-hard` as an available agent type. Available agents: `explore`, `general`, `oracle`, `librarian`, `architect`, `advisor`, `plan`, `scout`, `Metis - Plan Consultant`, `Momus - Plan Critic`, `Sisyphus-Junior`, `caveman:cavecrew-*`, `code-simplifier:code-simplifier`, `multimodal-looker`.

**Fix applied:** Fell back to `explore` for both Phase 1 investigation tasks. The `explore` agent is the lighter-weight of the two per AGENTS.md, but it's the closest available match for codebase exploration.

**Recommendation:** AGENTS.md should be updated to reflect the actual agent keys available in the runtime config, or the config should define `explore-hard` if it's meant to be a distinct agent.
