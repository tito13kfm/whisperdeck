# WhisperDeck

This project uses Serena, an MCP server providing symbol-aware code retrieval, editing, and refactoring tools.
Serena runs in `--context=ide` mode, which excludes its file-reading and shell tools (Opencode provides those) but keeps all symbolic and editing tools.

## Serena Usage (Opencode-only — Claude Code: skip this section, use your own configured tools)

- **Prefer Serena over built-in tools** for: finding symbol definitions, symbol overview/file outline, finding references to a symbol, renaming symbols, replacing symbol bodies, inserting before/after symbols, safe deletes, `replace_content` (regex or literal file edits), and `replace_in_files` (bulk edits across files).
- **Search order:** `find_symbol` first, then `search_for_pattern`, then built-in `grep` as last resort.
- **File reading:** use built-in `read` tools (more efficient than Serena's `read_file`, which is excluded in IDE mode).
- **Parallel calls:** batch independent Serena operations in a single turn to minimize round-trips.
- If Serena tools are not visible, run `serena start-mcp-server --context=ide --project=WhisperDeck` manually or use the `/mcp` command.

## Advisor Escalation (Opencode-only — Claude Code: skip, @advisor-pro/@advisor-qwen are not reachable here, use your own advisor tool instead)

This project is configured with advisor subagents for plan validation and unstucking. The primary model runs DeepSeek V4 Flash (ClinePass).

### Available advisors
| Agent | Model | Use when |
|---|---|---|
| `@advisor-pro` | DeepSeek V4 Pro (OpenRouter) | Default first escalation - plan validation, architecture review, stuck after 2+ failures |
| `@advisor-qwen` | Qwen Plus (OpenRouter) | Second opinion, different architectural perspective |

### Escalation rules
- **Hard cap: 2 advisor invocations per session.** Track how many advisors have been called. After 2, work with the best answer available. This cap spans providers; switching models does not reset the count.
- **First call**: `@advisor-pro` (shared DeepSeek ecosystem, faster context transfer).
- **Second call** (only if still unresolved): `@advisor-qwen` for a different architectural perspective.
- **When to call**: before implementing a non-trivial plan, when stuck after 2+ failed approaches (two distinct attempted fixes, not two tweaks to the same approach), when choosing between two materially different architectures, or after a significant change for correctness review.
- **Do not call** for trivial changes, mechanical edits, or questions already resolved.
- Advisors are read-only (cannot edit files or run commands).

### ClinePass limit fallback
When ClinePass hits usage limits, switch the primary model via `/models` to `openrouter/deepseek/deepseek-v4-flash` (or `openrouter/deepseek/deepseek-chat`). Advisors already run on OpenRouter and don't need changes.

## Worktree hygiene

Remove merged or abandoned worktrees and their branches immediately, no exceptions. A worktree is stale once its branch is fully merged (PR state MERGED, or `git merge-base --is-ancestor <branch-sha> origin/<base>` succeeds) or the user says the work is abandoned. Do not leave it "just in case" — clean up in the same session you notice it:

```
git worktree remove <path>
git branch -d <branch>
```

If the worktree has uncommitted or unmerged work, stop and confirm with the user before removing (`--force`/`-D` needs explicit sign-off, this is a destructive action).

## Testing tiers: match test cost to change blast radius

Don't run full browser-driven e2e audits for every small change; reserve them for milestones. Pick the tier by what the change actually touches:

1. **Unit/integration test for the touched path** — default for any change. Fast, run every time, no exception.
2. **`e2e-regression-http` (scripted HTTP, no browser)** — before merging anything that changes request/response contracts or cross-feature flow (queue/job routing, serializer shape, multi-step API behavior).
3. **Full browser e2e (`e2e-ux-audit`, `e2e-ux-audit-deep`)** — reserve for pre-release checkpoints or after a batch of changes lands, not per-PR. Also required for any change with a runtime/UI surface per the Testing section below, but "drive the affected flow" there means a targeted manual/scripted check of that flow, not the full 6-journey or deep audit suite.

Rule of thumb: a backend fix scoped to one module doesn't need a browser; a UI-visible or cross-cutting change does, but scope the runtime check to the flow that changed, not the whole app.

## The Complement Rule

Your diff shows where you looked; before finishing, enumerate the complement. Whenever a change introduces a guard, a new enum/mode value, a threaded parameter, or a conditional UI affordance:

1. Grep for every entry point that mutates the guarded state, and every caller of the changed signature. Update all of them, not just the ones your feature flowed through.
2. Enforce entity rules server-side. A rule that only lives in the client (mode X implies setting Y) does not exist.
3. New enum value: check every site that switches on that enum (workers, serializers, whitelists, UI labels, poll predicates). Prefer one registry the other sites derive from over parallel hand-maintained lists.
4. Conditional UI is a two-sided contract: the chrome that offers a control and the renderer that fulfills it must share one predicate, and any sticky view state (selected tab, mode) must be reset or re-validated when the entity behind it changes.

A guard is only as strong as its least-guarded entry point. Tests that only exercise the entry points the PR touched prove nothing about the others.
