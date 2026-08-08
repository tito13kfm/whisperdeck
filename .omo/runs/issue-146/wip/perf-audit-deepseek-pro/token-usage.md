# Token Usage — Issue #146 (deepseek-pro)

## Approach

No explore/librarian agents were delegated for this issue. All Phase 1 investigation was done via direct reads and grep — efficient because the scope was narrow (3 files, well-known locations). The `rack.js` file (4403 lines) was the only large file; reading it in full was necessary to confirm no existing SW registration pattern and to locate the DOMContentLoaded handler.

## Where usage was highest

| Action | Cost | Notes |
|---|---|---|
| Full read of `rack.js` (4403 lines) | High | Necessary — needed entire file to confirm no SW registration and locate DOMContentLoaded. But could read just the end (last 50 lines) and grep for registration pattern separately. |
| Grep for `service.?worker\|sw\.js\|...\|cache\|StaticFiles` | Moderate | 153 matches, ~95% irrelevant (Python model cache, not HTTP cache). Could have scoped to `*.js` and `*.py` separately with narrower patterns. |
| `codegraph_explore` on worktree | Wasted | Worktree not indexed. One wasted round-trip. Should skip codegraph when working in worktrees. |
| README returned with directory listing | Moderate | `read(static/)` returned both the directory listing AND the full README.md. The README was irrelevant for this task. |

## What worked well

- Direct reads of well-known files (`static/index.html`, specific app.py sections) were efficient
- The single broad grep found 0 SW matches — confirmed "no existing SW" in one call
- Test run was fast (0.43s for 5 tests)
- No agent delegation needed — the change was 3 files, 83 lines total

## Recommendations for next time

1. **Skip codegraph in worktrees** — worktrees don't inherit the index. The `.omo/issue-runner-prompt.md` should note this.
2. **Narrow grep patterns** — grep for `serviceWorker` and `sw\.js` separately instead of one broad regex that matches irrelevant Python cache code.
3. **Partial file reads** — for `rack.js`, read just the DOMContentLoaded handler (last 50 lines) + grep for the registration pattern, instead of the full 4403 lines.
4. **`from_end=true` not needed** — since no background agents were delegated.
