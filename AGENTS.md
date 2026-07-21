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
