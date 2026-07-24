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

## Unsloth Studio Server (Local Model Inference)

The `explore` agent depends on the Unsloth Studio server at `http://127.0.0.1:8888/v1`. When it is not running, explore agents hang silently with no timeout — the server must be started before using explore or scout subagents. Category delegations (`quick`, `visual-engineering`, `writing`, etc.) use OpenRouter and are not affected.

### Checking if it is running

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8888/v1/models" -Headers @{"Authorization"="Bearer sk-unsloth-9be9b00467f065d8521794b4889d1fe4"}
```

A `loaded: true` entry in the response means the model is ready.

### Starting the server

**Default (Qwen3.5-4B, 260K context):**

```powershell
Start-Process -FilePath "C:\Users\tito1\.unsloth\studio\bin\unsloth.exe" -ArgumentList "studio","run","--api-only","--disable-tools","--model","C:\Users\tito1\.unsloth\gguf\Qwen3.5-4B-UD-Q4_K_XL.gguf","--port","8888" -WindowStyle Minimized
```

**Fallback (Qwythos-9B, 100K context — for difficult reasoning tasks but limited file capacity):**

```powershell
Start-Process -FilePath "C:\Users\tito1\.unsloth\studio\bin\unsloth.exe" -ArgumentList "studio","run","--api-only","--disable-tools","--model","C:\Users\tito1\.unsloth\gguf\Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf","--port","8888" -WindowStyle Minimized
```

The model argument must be the absolute path to a `.gguf` file in `C:\Users\tito1\.unsloth\gguf\`. A HuggingFace repo ID (e.g. `unsloth/Qwen3.5-4B-MTP-GGUF`) does NOT work — the process stays at 5 MB and never loads the model. Loading takes 30-90 seconds.

**`--disable-tools` is critical.** Without it, Unsloth's server-side tools swallow OpenCode's tool calls (grep, read, etc.), causing agents to hallucinate answers instead of actually searching the codebase. All models appeared broken until this flag was discovered.

**`--max-seq-length` controls context window.** UnslothStudio passes this as `-c` to llama-server. The Qwen3.5-4B model defaults to ~260K at Q4; the Qwythos-9B supports up to 1M native context but the RX 9070 XT (16 GB) runs out of VRAM for KV cache above ~100K at Q8. Use `--max-seq-length <tokens>` to adjust. Larger context = more VRAM for KV cache. The model's `native_context_length` (from `GET /v1/models`) is the hard ceiling.

**`--parallel` sets concurrent decode slots.** Each slot gets `context / parallel` tokens of KV cache. Limited to 2 on the RX 9070 XT (VRAM constraint). Never fire more than 2 explore agents simultaneously — the third+ will block or timeout.

### Available local models

| GGUF file | Size | Used by |
|---|---|---|
| `Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf` | 9.1 GB | explore, scout, plan — fallback only; 100K context max on RX 9070 XT (VRAM) |
| `Qwen3.5-4B-UD-Q4_K_XL.gguf` | 2.8 GB | explore, scout, plan — default; 260K context, proven reliable with `--disable-tools` |
| `Qwen3.6-27B-UD-IQ3_XXS.gguf` | 11.4 GB | unused (categories switched to OpenRouter) |
| `DeepSeek-R1-0528-Qwen3-8B-Q4_1.gguf` | 4.9 GB | unused, available as fallback |
| `Qwen3-Coder-30B-A3B-Instruct-UD-Q3_K_XL.gguf` | 12.9 GB | unused, available as fallback |
| `Devstral-Small-2-24B-Instruct-2512-Q3_K_M.gguf` | 10.7 GB | unused, available as fallback |

### Agents that do NOT need Unsloth

These use OpenRouter and work regardless of local server status: `deep`, `ultrabrain`, `oracle`, `librarian`, `unspecified-high`, `artistry`, `quick`, `visual-engineering`, `writing`, `unspecified-low`, `metis`, `momus`, `atlas`.

### Single-model limitation

Only one GGUF model per Unsloth instance. Auto-switch is OFF by default — requests for an unloaded model silently get the loaded model instead. Do not split local agents across different models without running separate Unsloth instances on different ports.

### API key

`sk-unsloth-9be9b00467f065d8521794b4889d1fe4` (configured in `~/.config/opencode/opencode.json` under `provider.unsloth-studio.options.apiKey`).

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
