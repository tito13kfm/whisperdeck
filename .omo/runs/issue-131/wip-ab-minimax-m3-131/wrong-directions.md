# Wrong directions (issue #131, run minimax-m3)

## AGENTS.md / canonical-prompt friction points hit during this run

### 1. codegraph_explore returned "not indexed" for the worktree path

The worktree is a fresh `git worktree add` checkout at
`C:/Claude/whisperdesk-minimax-m3-131`, which has no `.codegraph/`
directory — the index lives at `C:/Claude/whisperdesk/.codegraph/` in the
main repo only. `codegraph_explore` walks up from the projectPath I pass
and bails when it doesn't find one.

Recommended fix in the canonical prompt: when launching a worktree, the
prompt should also tell the runner to either (a) symlink/copy the
`.codegraph/` dir from the main repo into the worktree before any
codegraph call, or (b) pass the main repo path as `projectPath` for
read-only queries and read the worktree files directly when it comes
time to edit. The latter is the cheaper option and matches what the
prompt's own codegraph-uses-direct-reads fallback already implies.

Cost: one wasted codegraph_explore round-trip (this run).

### 2. The grep tool's path parameter silently failed with a worktree path

`grep(pattern=..., path="C:\\Claude\\whisperdesk-minimax-m3-131\\static\\rack.js")`
returned "No matches found" even for symbols I knew were there. Bash
`grep -n -E ... C:/Claude/whisperdesk-minimax-m3-131/static/rack.js`
returned the matches immediately with the same query. Same tool, different
path-style inputs, different results. Looks like a path-normalization
quirk in the grep tool's path parameter on Windows (mixed forward/back
slashes, or escaped backslashes vs POSIX paths).

Workaround used: `bash` + `grep -n` for the multi-symbol search. Cost: one
extra tool call. Not worth a prompt change, just note it.

### 3. The system prompt's "MUST USE explore/librarian" hook kept firing on direct tool calls

The OpenCode framework injects an "Agent Usage Reminder" hook on every
grep/bash call suggesting I should have used a delegated agent. AGENTS.md
overrides this for the local-agent cap and the "Decompose and delegate"
rule, and the canonical prompt's "Delegation exception" clause says
mechanical transcription of a complete plan is the right case for direct
implementation. So I deliberately kept the work in main context for this
14-line fix and did not apologize for skipping delegation. Worth noting
that the hook will keep firing on similar small changes; future A/B
runners on similarly scoped issues should not let it sway them into
spinning up an agent for what is genuinely a one-file, three-site fix.

### 4. AGENTS.md test-tier guidance on this change

The change is purely client-side (no API surface change, no serializer
shape change, no cross-feature flow). Per AGENTS.md's tier-1 rule, the
default for any change is a unit/integration test for the touched path.
None exists in the JS layer (the tests/ tree is Python pytest +
Playwright). The Python suite has no JS coverage, so the static check
(`node --check`) is the only tier-1 evidence available without writing
a new test. Tier 2 (e2e-regression-http) needs a live browser tool,
which is not confirmed available in this session; logged here as
"tier skipped, not silently". Tier 3 (full e2e audit) is overkill for
a 14-line frontend fix. No action needed for the run, just flagging
that the JS layer has no test harness at all and the next person who
wants to add a logout-flow regression test will need to set up a JS
test runner from scratch.

## Doc bugs in the issue body that the run had to work around

| Issue claim | Actual | What to do |
|---|---|---|
| `resetDeckState` at line 511 | line 512 | Re-grep from a known symbol, not the issue's line |
| `bgJobPollTimer` at line 571 | line 573 | Same |
| `bgJobPollTimer` 8s | 8s (8000 ms) — correct, luck of the draw | n/a |
| `bankPollTimer` at line 2104 | line 2018 (decl) / 2131 (set) | Same |
| `bankPollTimer` 5s | 4s (4000 ms) | Use the actual interval, not the issue's number, when reasoning about request volume |
| `detailPollTimer` at line 2365 | line 2332 (decl) / 2396 (set) | Same |
| `detailPollTimer` 2.5s | 2.5s (2500 ms) — correct | n/a |
| bankPollTimer/detailPollTimer "fire → 401 → catch → reschedule itself" (infinite loop) | They die on the first 401 because the reschedule is only in the success path, not the catch | The fix is still right (clear stale handles, don't leave them as footguns), but the bug-as-described is overstating. Worth knowing for any future related issue. |

## No-collision note

`wip-ab-minimax-m3-131` is the report-dir suffix and is a different path
from `wip-ab-deepseek-pro-131` (sibling run, different variant label).
A/B runners that pick variant labels that aren't disambiguated by label
in the dir name will collide; this run followed the canonical prompt's
"both tokens in the branch name" rule and the two runs coexist
peacefully. Logging here so a future reviewer doesn't think one of them
is stale.
