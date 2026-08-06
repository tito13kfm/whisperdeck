# Agent calls, issue #109

Orchestrator ran on Opus 5. Every delegated call was a fresh `Agent()` (never `fork`), so each
prompt was self-contained.

| # | Phase | Model | subagent_type | Purpose | Outcome |
|---|---|---|---|---|---|
| 1 | Phase 1, investigate | **Sonnet** | `Explore` (read-only) | Map `services/voice_id.py`, enumerate every caller of `_extract_embedding()` / `_mfcc_fallback()` / `identify()`, find the voice_match job's result shape and frontend consumer, sibling sweep, evaluate the issue's own proposed fixes | Wrote `investigation.md` (230 lines). Found the enroll/add_clip mirror-image bug the issue never mentions, and that `result_json` is not wired to the UI |
| 2 | Phase 1.5, completion-race check | **Fable** | `general-purpose` | Check whether a guard later in the job path tests only `"cancelled"` and not `"completed"`, letting a side effect fire after a successful finish | Found `_finish()` (`services/llm_jobs.py:322`) guards only `"cancelled"`; combined with the catch-all at 757-761 it flips completed to failed if anything raises after `_finish("completed")`. Directly changed the plan: `result_json` dropped, error assembly kept strictly before `_finish` |
| 3 | Phase 3, verification | **Sonnet** | `general-purpose` | Static contract check across all changed call sites, full pytest suite, `npm run test:js`, bundle freshness re-check, live check of the changed `/api/voices/*` response shapes | See `self-audit.md` for the result |

No Haiku call was made: there was no bounded, purely mechanical multi-file sub-edit in this change.
Every edit needed judgment about scope or wording, so all of Phase 2 ran inline on Opus.

The Fable call was used once, for its one designated purpose, and was worth it: it is the reason
this PR does not write `job.result_json` after `_finish()`.
