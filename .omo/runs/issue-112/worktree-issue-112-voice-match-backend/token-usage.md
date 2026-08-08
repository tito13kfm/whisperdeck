# token-usage.md — issue #112 run

Orchestrator: **Opus 5** (Phase 0, Phase 2 design + all source/test edits, both mutation checks, red-green runs, full-suite runs, Phase 3.5, Phase 4, Phase 5). One `advisor()` call before Phase 2, at the approach gate.

`Agent()` calls made this run, three total:

| # | Phase | Model | `subagent_type` | Purpose | Cost |
|---|---|---|---|---|---|
| 1 | Phase 1 (investigate) | **Sonnet** | `Explore` (read-only) | Locate the current `has_enrolled_voice` check, quote `identify()`'s model-mismatch skip, complement + sibling sweep, frontend consumers of a voice_match job result, existing test coverage | 75,762 subagent tokens, 33 tool uses, 303s |
| 2 | Phase 1.5 (completion-race check) | **Fable** | `general-purpose` | Single designated use: check whether a guard on a post-completion side effect checks only `"cancelled"` and not `"completed"` in `run_llm_job` | 49,180 subagent tokens, 3 tool uses, 118s |
| 3 | Phase 3 (verify) | **Sonnet** | `general-purpose` | Independent mirror check of the new guard against `identify()`, registry dedup grep, full-suite run, static UI contract check on `job.error` | 105,679 subagent tokens, 31 tool uses, 477s |

No Haiku call was made: Phase 2 needed no bounded mechanical multi-file sub-edit (five files, each edit a judgment call about a mirrored predicate). No Fable call was reused outside its one designated Phase 1.5 use.

Outcomes worth the cost:
- Call 1 surfaced that `job.error` renders on **completed** jobs too and that `result_json` is never read for `voice_match`, which settled the choice of channel for the new message.
- Call 2 found a real pre-existing defect (write-after-cancel at `services/llm_jobs.py:744-750`), correctly ruled out of this issue's scope. See `wrong-directions.md` item 5.
- Call 3 found the `""`-vs-`None` wildcard asymmetry in the new guard, which was then closed at `services/llm_jobs.py:716`.
