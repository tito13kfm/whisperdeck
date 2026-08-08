# Token Usage — Issue #169 (minimax-m3-r2)

This run was implemented directly by the orchestrator (me, minimax-m3)
without delegating to subagents. Token usage breakdown:

## Agent dispatches

**Zero subagent dispatches.** The implementation was done by me,
sequentially, with all context already loaded. No `explore` /
`explore-hard` / `deep` / `ultrabrain` / `quick` / `visual-engineering`
subagents were invoked.

This was deliberate, not accidental:
- I had full context loaded (read 7+ files in Phase 1) before
  starting implementation.
- The work was mechanical transcription of a plan I had already
  written into `investigation.md`.
- The codegraph MCP wasn't available in this worktree
  (no `.codegraph/`), so the fall-back was direct Read/Grep.
- Lemonade's 2-agent cap wasn't relevant — even if I'd wanted to
  delegate to `explore-hard` for verification, the agent would have
  needed the same 7 files re-read into its context, doubling the
  total token cost.

## Token costs of doing it myself

| Phase | Files read | Approx. tokens |
|---|---|---|
| Phase 1: investigation | 7 files (services/llm_jobs.py 499 lines, app.py ~500 lines around kind, services/reformatting.py 112, services/transcription.py 271, database/__init__.py 374, static/rack.js 4500+ lines via targeted reads, static/index.html 137) | ~25K input |
| Phase 2a-i: implementation edits | each file read on edit (re-reads count) | ~40K total (writes are smaller) |
| Phase 3: tests written + run | test files referenced for shape | ~15K |
| Phase 5: self-audit | grep over edited files | ~5K |

**Approximate total: ~85K tokens for this run.**

A delegated approach with `deep` (heavy reasoning) would have been:
- 1 dispatch for "implement the investigation.md plan" with the plan
  in the prompt: ~15K input to the agent (the plan + brief context).
- Agent's own context-loading would re-read 7 files: ~25K input.
- Agent's output: ~40K.
- Plus my context: ~10K for review.
- Total: ~90K with cloud billing, AND I'd lose the token-by-token
  reasoning visibility I had doing it myself.

**Net: this was cheaper done directly. The investigation was 60% of
the total token cost, the implementation was 40%.**

## Where I was wasteful

- **Reading static/rack.js in two passes.** I read it once for the
  toggle/tabs handling, then again for the rail-button/loadVoiceNotes
  area. Could have grep'd the right line ranges from the first read
  and saved 2-3K tokens.

- **Initial test files used the wrong `client` fixture's auth
  context.** I created a separate User in test_voice_note_route.py
  before realizing the `client` fixture already authenticates as
  "testuser" — burned ~2K on the failing tests and the fix.

- **The comment-detection hook fired ~10 times during this run.**
  Each fire required a justification. The justifications are in the
  wrong-directions.md or in inline replies. They cost ~500 tokens per
  fire. Net: ~5K spent on compliance with the comment-discipline
  hook.

## Where the next run should cut

1. **Read static/rack.js once with a broader line window, not
   targeted reads.** Targeted reads felt surgical but cost more
   round-trips than a single 2000-line read.

2. **Read the conftest.py BEFORE writing any test that uses the
   `client` fixture.** I had to debug the 404s / 422s that came from
   creating a separate User instead of using testuser. One pre-read
   would have saved 2K.

3. **Skip the defensive branch in transcription_service.summarize.**
   Per wrong-directions.md #2: unreachable in practice, costs 30 lines
   + a test.

4. **Don't re-test pre-existing behavior in new test files.** I
   retested `test_io_cpu_pools_partition_valid_kinds` indirectly by
   asserting "voice_note in IO_KINDS" in the new file. The existing
   test still passes unchanged. The new assertion is redundant.

## Reminder for the human

This run's cost/token numbers live in OpenCode's own usage panel, not
in this file. The estimate above is based on input/output sizes
visible to me (file lengths × read count). The actual panel cost
includes any cloud-model usage I delegated to (which was zero in this
run).
