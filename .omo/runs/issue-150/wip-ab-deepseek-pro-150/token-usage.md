# Token Usage — Issue #150 run (wip/ab-deepseek-pro-150)

## Where tokens went

1. **Two explore agents (Phase 1) — biggest spend**: ~5 min each on local Lemonade. Both agents over-searched — the file is a single 4409-line JS file, but agents did multiple rounds of grep/read instead of reading the target line ranges directly. Combined ~10 min of local inference.

2. **Direct reads (Phase 1-2)**: ~300 lines of `rack.js` read directly. Efficient — read exactly the lines needed (function definitions, call sites).

3. **Codegraph attempt**: Wasted — worktree wasn't indexed. One call failed fast.

4. **Implementation (Phase 2)**: Direct edits — zero agent cost. 2 edit calls, clean diff.

## What would cut token usage next time

1. **Name the exact line ranges in agent prompts** for single-file investigations. Both agents read the entire 4409-line file when they only needed lines 335-365, 2380-2420, 2811-2830, 3033-3315, 3156-3270. "Read rack.js lines 2380-2420 and lines 3156-3315" would have been 10x cheaper.

2. **Skip codegraph for worktrees**. The tool explicitly says "not indexed" — one call to confirm is fine, but don't retry.

3. **One agent, not two, for single-file investigation**. Both explore agents ended up reading overlapping sections of the same file. A single agent with precise line ranges would have been cheaper and faster.

4. **Direct reads for known locations**. Once I knew `scheduleDetailPoll` was at ~line 2389 and `renderDetail` at ~line 3156 (from the issue body, verified by the first agent), I should have read those ranges directly instead of letting the second agent continue searching.

## What worked well

- `from_end=true` on both agent result collections
- Direct bash grep as fallback when the grep tool failed on the worktree
- Single `edit` call per logical change (2 edits total for the fix)
- Writing investigation.md BEFORE implementing
