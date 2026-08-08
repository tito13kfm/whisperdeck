# Token usage (issue #131, run minimax-m3)

This run was small. Total tool spend: ~20 tool calls (read, grep, edit,
bash, write, todowrite). The investigation.md and the two Phase-5 files
are the largest token sinks by a wide margin.

## Ranked cost (rough)

1. **investigation.md (~3.5 KB written, ~5 KB of source read to write it)**
   — wrote a long file because the issue's claim that all three timers
   are in an infinite 401 loop is half right (only `bgJobPollTimer` is)
   and the future maintainer needs to know that to avoid unifying the
   three cleanup sites. Could have been tighter: one paragraph per timer,
   no detailed "what the issue says vs. what the code says" table since
   the column is "use a grep instead of trusting the issue's line
   numbers" repeated three times. Trim by ~40% next time.

2. **wrong-directions.md and token-usage.md (~3 KB combined)** — these are
   required by the workflow and are the explicit per-run cost. Not
   optimizable.

3. **Source reads in `static/rack.js`** — 9 Read calls (line 225-254,
   505-634, 790-814, 975-1014, 1815-1864, 2010-2139, 2325-2404, 365-414,
   910-959). I had to read each timer in context to verify its
   catch-on-error behavior (some are infinite loops, some are single-shot
   that self-terminate). Could have been 4 reads if I'd grepped for the
   three timer `setTimeout` call sites first, identified the "reschedule
   inside try vs. after catch" pattern by line, and only re-read the
   suspicious ones. ~30% reduction possible.

4. **One wasted `codegraph_explore` call** — returned "not indexed" for
   the worktree path. If I'd started with bash `grep -n` for the symbol
   names (the issue text gives them all), I would have skipped this. Cost
   in this run: one tool call + the generated explanation. Cost avoided
   in the next run on a worktree: read the codegraph not-indexed message
   once, remember it applies to worktrees, grep directly.

5. **Three "Agent Usage Reminder" hook replies** that the OpenCode
   framework injected on my direct grep/bash calls. I had to read each
   one, decide it was a nudge not a requirement, and not be derailed.
   Free for the model, costs context tokens. Not a problem this run,
   will compound on larger runs.

## What to do differently next run

1. **Skip `codegraph_explore` on a worktree unless codegraph is
   re-indexed for the worktree path.** Use bash `grep -n` for the
   symbol set the issue gives you as the very first call. The first
   2 minutes of any issue run should be cheap symbol-discovery, not
   codegraph.

2. **Grep first, read second, read narrowly.** Find the three timer
   `setTimeout` sites with a single grep, then read only the
   function bodies around them. Reading 100 lines of context per
   timer is overkill for verifying "does this catch re-arm the
   timer or not".

3. **Investigations on issue-bodies-that-claim-loop-but-actually-don't
   are the single biggest token sink in this run's class.** Future
   issues that match the pattern ("polling timer X survives logout")
   should go straight to: read the catch path of the polling function,
   decide if it reschedules inside or outside the catch, write one
   sentence in investigation.md, move on.

4. **Per AGENTS.md's static-check-then-runtime-check rule, do not pay
   for any test cycle that the existing tier-1 suite can't run.**
   I didn't start the dev server, didn't try to load the app in a
   browser, didn't run pytest. The static check (`node --check`) is
   the appropriate tier for a 14-line client-side fix with no test
   harness. If a future run on this codebase needs browser coverage,
   the test setup cost is the bottleneck, not the fix.
