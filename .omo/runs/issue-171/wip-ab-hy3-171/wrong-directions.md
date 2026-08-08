# Wrong directions / discrepancies (issue #171, variant hy3)

## 1. `explore-hard` agent does not resolve in this runtime
The issue-runner prompt (Phase 1) names `explore-hard` as the reasoning agent.
But `task(subagent_type="explore-hard")` returns "Unknown agent". The available
agent names are: Metis, Momus, Sisyphus-Junior, advisor, architect,
caveman:cavecrew-*, code-simplifier, explore, general, librarian,
multimodal-looker, oracle, plan, scout.

The live `~/.config/opencode/oh-my-openagent.json` DOES define an `explore-hard`
key (lemonade/DeepSeek-Qwen3-8B-GGUF), but the agent dispatch registry only
exposes `explore` (and `scout`/`plan` are also absent). Recommended fix: the
runner prompt should say "use `explore` if `explore-hard` fails to resolve"
(which it does state as a fallback) — but also note that the agent *registry*
the `task()` tool sees is narrower than the model config. For reasoning-heavy
Phase 1 work here I fell back to `codegraph_explore` (verbatim source tool) +
orchestrator reasoning, which is higher quality than the 4B `explore` model for
this dispatch logic. Logged 2026-07-27.

## 2. AGENTS.md line 127 local/cloud labeling is wrong (confirmed against live config)
AGENTS.md says `atlas`, `quick`, `writing`, `unspecified-low` are OpenRouter-only
(not subject to the local 2-agent cap). The live config maps ALL FOUR to local
Lemonade models:
- atlas      -> lemonade/Qwen3.5-4B-MTP-GGUF   (LOCAL)
- quick      -> lemonade/Qwen3-0.6B-GGUF        (LOCAL)
- writing    -> lemonade/Bonsai-8B-gguf         (LOCAL)
- unspecified-low -> lemonade/Qwen3-0.6B-GGUF   (LOCAL)

So they ARE local and DO share the VRAM-bound 2-agent concurrency cap. The
runner prompt's "Known doc error, second one" already flags this; confirmed
firsthand. Also note: this run's own orchestrator model is `opencode-go/hy3`
(cloud), and the heavy-reasoning categories `deep`/`ultrabrain` are also
`opencode-go/hy3` (cloud, not capped). `explore`/`explore-hard` are the only
true local-cap agents.

## 3. (placeholder for execution-phase findings)
