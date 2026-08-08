# Token Usage — Issue #172 / variant minimax-m3

This run was executed **entirely in the orchestrator's own context** — no
explore/librarian/deep/ultrabrain/oracle agents were dispatched. All file
reads, edits, and test runs happened directly. This is the cheapest possible
shape of an /issueAB run, and worth recording as a baseline.

## What I dispatched

- **Zero explore agents.** Investigation was all direct Read/grep on the
  5 files in scope.
- **Zero librarian agents.** No external docs needed.
- **Zero deep/ultrabrain agents.** No multi-step research problem needed.
- **Zero oracle consults.** Phase 1.5 (completion-race check) is N/A for
  this feature — no LlmJob state machine.
- **Zero background tasks.** All work was synchronous.

The orchestrator model (MiniMax M3) consumed the largest share of the
budget — reads of 5 files, 9 atomic edits, 1 test run, 1 sanity check,
plus the 4 report files at the end. The user reviewing this against the
usage panel should see **no spend outside the orchestrator's own context**.

## Where token usage was highest

1. **`app.py` reads.** The route file is 2648 lines; I read 3 sections
   (bootstrap, format_transcript, around line 2014) totaling ~80 lines.
   One read could have been avoided — I re-read `app.py:594-664` after
   already seeing the section number earlier. Could have used the existing
   mental model.
2. **`static/rack.js` reads.** 4750-line frontend file; I read ~50 lines
   across 2 sections (exportToolbarHtml, detailBodyClick, settings page).
   Each grep+read cycle was necessary because line numbers in the plan
   had drifted.
3. **`tests/test_reformatting.py` read.** 321 lines, single read for the
   imports + first test helper. Necessary.

## Re-reads I could have avoided

- `app.py:1983-2015` — I read it once, then read it again to confirm the
  end of `format_transcript`. Could have read a larger range upfront.
- The 4 e2e test files (one quick `ls` + `pytest tests/e2e`) — I just ran
  them; the cost was 1 minute of wall time, not tokens.

## What worked well

- **No agent dispatch.** The plan was detailed enough that direct file
  reads + edits covered everything. The orchestrator stayed in its own
  context; no cloud agent spend, no local agent spend.
- **Batched edits.** For Task 3 (export endpoint), I read the file, edited
  3 places (import, bootstrap, route insertion), and committed once. Did
  not re-verify after every edit.
- **Single test run.** Ran the full suite once, fixed the 1 failure, ran
  the new tests once more, ran the full suite once more. Three test runs
  total for the whole change.
- **`codegraph_explore` not used.** Could have used it for "what calls
  `exportToolbarHtml`" (sibling sweep #1), but a single grep on
  `exportToolbarHtml` in the worktree was faster.

## Sub-session list (mandatory disclosure)

| Sub-session | Model | Cloud or local | Approx cost share |
|---|---|---|---|
| (none) | — | — | 0% |

All 100% of the spend was in the orchestrator's own context (MiniMax M3
per the variant label). The user should see this run's cost in the panel
as a single continuous session, not as multiple model invocations.

## For the next run

If I run another /issueAB on a feature where the plan is this detailed:

- Skip Phase 1 agent dispatch entirely; the orchestrator can read 5 files
  in a few hundred tokens, way cheaper than dispatching an explore agent
  that would re-read the same files into its own context.
- The 1+ failing test in any new test class is a routine occurrence. The
  fix-and-re-run loop costs ~30 seconds and 1 test run; not worth
  delegating to a `deep` agent.
- Sibling-sweep grep is faster than sibling-sweep agent — the answer is
  "where is X called" which is a 1-grep question, not a research question.
- The 4 e2e test files are 100 lines of code total. If a future feature
  needs e2e coverage, extending them is cheap; if it doesn't, don't
  force it.
