# Wrong Directions - Issue #125 (qwen3.7-plus variant)

## Agent name: explore-hard

**What happened:** Command instructions said to use `explore-hard` for reasoning-heavy investigation. Agent invocation failed: "Unknown agent: explore-hard".

**Re-check:** Ran `task(subagent_type="explore-hard")` and got error listing available agents. `explore-hard` is not in the list.

**Available agents:** Metis - Plan Consultant, Momus - Plan Critic, Sisyphus-Junior, advisor, architect, caveman:cavecrew-builder, caveman:cavecrew-investigator, caveman:cavecrew-reviewer, code-simplifier:code-simplifier, explore, general, librarian, multimodal-looker, oracle, plan, scout

**Fix:** Use `explore` instead of `explore-hard`. AGENTS.md's agent table is stale.
