# Token Usage

## Where it was worst

1. **codegraph_explore calls**: 3 calls to get full picture of timers, logout flow, and stop functions. Each returned large source blocks. Could have been 2 if I'd queried for all timer names in one call from the start.

2. **grep tool failure**: Wasted a call on grep that returned nothing. Should have gone straight to `read` at codegraph-reported line numbers.

3. **Reading worktree file for edit verification**: Read the same area twice (once before edit, once after). The "after" read was unnecessary since the edit tool confirms success.

## What would cut it next time

1. Query codegraph once with ALL timer names + logout/showLogin/resetDeckState in a single call, instead of splitting into 3 queries.
2. Skip grep on Windows for JS files, go directly to `read` at known offsets.
3. Trust the edit tool's success confirmation, skip the verification read for trivial edits.
4. This was a simple 4-line addition to one function. Could have been done directly without delegation. The investigation was the real work; the fix was mechanical.
