# Wrong directions: Issue #175 — wip/ab-deepseek-pure-175

## Plan inaccuracies

1. **Plan says "run_llm_job() skips transcript fetch for voice_note"** (plan.md line 138)
   - Reality: `run_llm_job()` at line 284 fetches transcript unconditionally for ALL job kinds including voice_note. There is no existing skip. The plan describes desired behavior, not current state.
   - Fix: Added NULL guard for assistant specifically (`if job.transcript_id is not None`). This is fine since only assistant jobs have NULL transcript_id.

2. **Plan's `interpret_request()` signature includes `transcript_context: str`** (plan.md line 126)
   - Reality: The interpreter translates natural language → JSON plan. It doesn't need transcript context — the context comes from the search step at execution time.
   - Fix: Dropped `transcript_context` parameter. Interpreter only takes `user_request`, API credentials, and config.

## Prompt instruction accuracy

- No AGENTS.md discrepancies found in this run. The lemonade local/cloud cap warning is pre-existing and already documented.
- The issue-runner-prompt.md instructions for A/B test mode (no PR, push as `wip/ab-<variant>-<N>`) were followed correctly.
- The report-file scoping (main repo path vs worktree path) was handled correctly: all `.omo/runs/issue-175/wip-ab-deepseek-pure-175/` files written to `C:/Claude/whisperdesk/`, not the worktree.

## Edit tool hazard

The `edit` tool's `else:` replacement matched the wrong occurrence twice:
- First: replaced the inner `else:` of the voice_note handler (if existing/else db.add(VoiceNote))
- Second: same problem on the attempted fix
- Root cause: multiple `else:` lines exist in `run_llm_job()`, the tool matched the first one
- Fix: used more surrounding context (3 lines before + 2 after) to uniquely identify the target
- Recommendation: use at least 5 lines of context when editing within large if/elif/else chains

## Scope creep avoided

The issue asked for tasks 3-6 only (interpreter + executor + schema + job wiring + tests). Did not attempt:
- API endpoints (tasks 7-9, sub-issue 3)
- Settings UI (task 11, sub-issue 4)
- Assistant UI page (tasks 12-15, sub-issue 5)
