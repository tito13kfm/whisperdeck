# Token Usage — Issue #105

## Agents spawned

| # | Agent | Model | Cloud/Local | Purpose | Approx tokens |
|---|-------|-------|-------------|---------|---------------|
| 1 | explore (ses_0555dcee0ffeiBOKBe25cgv9n0) | openrouter/inclusionai/ling-3.0-flash:free | Cloud ($0) | Find all wholesale `.segments =` replacements, verify clear_relabel_history calls | ~2K prompt + ~1K output |
| 2 | oracle (bg_8201f936) | meta/muse-spark-1.1 | Cloud (paid) | Phase 3.75 regression pass on diff | ~1.5K prompt + ~1K output est. |

Total cloud spend: ~$0.02-0.03 (Oracle is the only paid model; explore uses free tier).

No local (Lemonade) agents used — explore is cloud, oracle is cloud.

## Token efficiency notes

- Used codegraph_explore first for structural overview — 1 call covered key functions across 4 files
- Used explore agent for the sibling sweep (grep-based task across multiple files) — efficient
- No re-reads or retries — investigation was straightforward
- Did NOT need to spin up a live server or browser — backend-only change, unit tests sufficient
- Oracle pass is the single largest token cost (issue text + diff + review instructions) — mandatory per workflow, worth it

## Improvement suggestions

- If explore-hard were available (currently not in config), could merge the codegraph + explore steps into one reasoning pass
- The PATCH endpoint fix is mechanically simple (add one function call) — could potentially skip the Oracle pass for a change this trivial, but the workflow's Phase 3.75 is unconditional
