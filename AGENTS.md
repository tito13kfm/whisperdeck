# WhisperDeck

This project uses Serena, an MCP server providing symbol-aware code retrieval, editing, and refactoring tools.
Serena runs in `--context=ide` mode, which excludes its file-reading and shell tools (Opencode provides those) but keeps all symbolic and editing tools.

## Serena Usage

- **Prefer Serena over built-in tools** for: finding symbol definitions, symbol overview/file outline, finding references to a symbol, renaming symbols, replacing symbol bodies, inserting before/after symbols, safe deletes, `replace_content` (regex or literal file edits), and `replace_in_files` (bulk edits across files).
- **Search order:** `find_symbol` first, then `search_for_pattern`, then built-in `grep` as last resort.
- **File reading:** use built-in `read` tools (more efficient than Serena's `read_file`, which is excluded in IDE mode).
- **Parallel calls:** batch independent Serena operations in a single turn to minimize round-trips.
- If Serena tools are not visible, run `serena start-mcp-server --context=ide --project=WhisperDeck` manually or use the `/mcp` command.

## Advisor Escalation

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

## The Complement Rule

Your diff shows where you looked; before finishing, enumerate the complement. Whenever a change introduces a guard, a new enum/mode value, a threaded parameter, or a conditional UI affordance:

1. Grep for every entry point that mutates the guarded state, and every caller of the changed signature. Update all of them, not just the ones your feature flowed through.
2. Enforce entity rules server-side. A rule that only lives in the client (mode X implies setting Y) does not exist.
3. New enum value: check every site that switches on that enum (workers, serializers, whitelists, UI labels, poll predicates). Prefer one registry the other sites derive from over parallel hand-maintained lists.
4. Conditional UI is a two-sided contract: the chrome that offers a control and the renderer that fulfills it must share one predicate, and any sticky view state (selected tab, mode) must be reset or re-validated when the entity behind it changes.

A guard is only as strong as its least-guarded entry point. Tests that only exercise the entry points the PR touched prove nothing about the others.
