# Token usage / delegation log — issue #287

Run branch: `worktree-issue-287-dump-review-tab`
Orchestrator: **Opus 5 (1M context)**, inline — Phase 0, Phase 2 (all implementation), the unit tests + mutation check, the bundle build, Phase 3.5, Phase 4, Phase 5.

Every delegated call below was a fresh `Agent()` (never `subagent_type: "fork"`), so each started with zero context of the orchestrator's conversation and received a self-contained prompt.

| # | Phase | Model | `subagent_type` | Purpose | Cost |
|---|---|---|---|---|---|
| 1 | Phase 1 — investigate | **Sonnet** | `Explore` (read-only) | Map the real backend contract, the actual draft-item field names, every per-kind detail-tab decision point in `static/rack.js`, and run the sibling sweep. Wrote `investigation.md` (276 lines). | 164,131 subagent tokens, 65 tool uses, ~17 min |
| 2 | Phase 1.5 — completion-race check | **Fable** | `general-purpose` | The one designated use of the Fable budget: check the job/state-completion path for the recurring "guard checks `cancelled` but not `completed`" bug class, in both its frontend (poll/render chain) and backend (`run_llm_job` voice_dump branch, `save-draft`, `finalize`) forms. | 58,088 subagent tokens, 9 tool uses, ~3.5 min |
| 3 | Phase 3 — test | **Sonnet** | `general-purpose` (needs `Bash`/`Edit`) | Static source-level check, full Python + JS suites, new Playwright e2e regression test for the Dump Review tab, red-green proof, acceptance-criteria walk. | see run log |

No Haiku call was made: Phase 2 had no bounded purely-mechanical multi-file sub-edit to delegate (every edit was a judgment call in a single file), so per the workflow it stayed inline.

## What each delegation actually changed about the outcome

- **Call 1 (Sonnet)** overturned the issue's central premise. The issue specifies rendering from `t.voice_dump_job.result_json.items`; the investigation proved `serialize_llm_job` emits no `result_json`, so the whole tab was redirected to `GET /runs/voice_dump`. Implementing the issue verbatim would have shipped a permanently empty tab.
- **Call 2 (Fable)** found a **fourth** sibling list the Phase 1 sweep missed (`updateDetailJobStatus`'s `runningContainers`, which would have frozen the in-flight progress line), established that a *cancelled* job can carry a committed items payload (so the draft gate had to key on `status === 'completed'`, not on items presence), and confirmed the finalize-then-rerun duplicate-row hazard — which is why the Rerun button is offered only from dead-end states.
- **Call 3 (Sonnet)** ran the suites and authored the permanent browser-level regression test.
