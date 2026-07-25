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

## Lemonade Server (Local Model Inference)

The `explore`, `scout`, and `plan` agents use the Lemonade server at `http://localhost:13305/v1` — an OpenAI-compatible local inference server with dynamic model loading. When it is not running, these agents fall back silently until the server starts. Category delegations (`quick`, `visual-engineering`, `writing`, etc.) use OpenRouter and are not affected.

### Why Lemonade over Unsloth Studio

- **Dynamic model loading**: Load/unload models on-the-fly via API — no manual server restarts when switching between tasks.
- **No tool-calling hallucinations**: Lemonade isolates tool execution from text generation. Unsloth required `--disable-tools` to prevent hallucinated tool calls; Lemonade handles this natively.
- **Persistent recipe options**: `ctx_size` and backend saved to `recipe_options.json`, survives restarts.
- **OpenAI-compatible API**: Standard `/v1/chat/completions`, `/v1/models`, etc.

### Checking if it is running

```powershell
Invoke-RestMethod -Uri "http://localhost:13305/v1/models" -TimeoutSec 2
```

A `data` array in the response means the server is alive. Check `/api/v1/health` for per-model load state.

### Starting the server

Lemonade starts with Windows and runs in the system tray. If it is not running, launch it from the Start Menu or run `lemonade` from a terminal. No CLI flags needed — all model configuration happens via the API or config files.

### Available models

| Model ID | Size | Context | Labels | Use |
|---|---|---|---|---|
| `Qwen3.5-4B-MTP-GGUF` | 3.7 GB | 260K (configurable) | reasoning, vision, mtp | **Default**: explore, scout |
| `DeepSeek-Qwen3-8B-GGUF` | 5.3 GB | 128K | reasoning | plan, hard reasoning tasks |
| `Bonsai-8B-gguf` | 1.2 GB | 64K | non-reasoning | title, summary, JSON output |
| `Qwen3-0.6B-GGUF` | 0.4 GB | 40K | reasoning | trivial tasks, fast responses |
| `DeepSeek-R1-Distill-Qwen-1.5B-GGUF-Q4_K_M` | 1.0 GB | 128K | reasoning | lightweight reasoning fallback |
| `gpt-oss-20b-mxfp4-GGUF` | 12.1 GB | 128K | reasoning, ROCm | quality over speed (VRAM-heavy) |
| `Qwen3-Coder-30B-A3B-Instruct-GGUF` | 19 GB | — | coding specialist | not currently used (too large) |

### Model selection by task

| Agent | Recommended Model | Reasoning |
|---|---|---|
| `explore`, `scout` | `Qwen3.5-4B-MTP-GGUF` (high context needed) | Context > quality |
| `plan` | `DeepSeek-Qwen3-8B-GGUF` (reasoning quality, 64K ctx) | Quality > context; local is fast enough for plans |
| `title`, `summary` | `Bonsai-8B-gguf` (fast, non-reasoning) | Speed > everything |
| Trivial tasks | `Qwen3-0.6B-GGUF` | Minimal |

**Context vs. quality tradeoff**: The RX 9070 XT has 16 GB VRAM. Larger context = more KV cache = less room for model weights. The 4B model at 260K leaves ~3 GB headroom. The 8B model at 128K uses ~11 GB (tight). Choose based on task needs.

### Loading models with custom context

```powershell
# Load with specific context size (persists via save_options):
$body = '{"model_name":"Qwen3.5-4B-MTP-GGUF","ctx_size":260000,"save_options":true}'
Invoke-RestMethod -Uri "http://localhost:13305/v1/load" -Method POST -Body $body -ContentType "application/json"

# Unload to free VRAM:
Invoke-RestMethod -Uri "http://localhost:13305/v1/unload" -Method POST -Body '{"model_name":"Qwen3.5-4B-MTP-GGUF"}' -ContentType "application/json"

# Check what's loaded:
Invoke-RestMethod -Uri "http://localhost:13305/api/v1/health" | Select-Object -ExpandProperty all_models_loaded | Where-Object loaded | Format-Table model_name, @{N='ctx';E={$_.recipe_options.ctx_size}}
```

Models auto-load on first request and auto-unload when idle. The `pinned` option (not default) keeps a model loaded.

### Agents that do NOT need Lemonade

These use OpenRouter and work regardless of local server status: `deep`, `ultrabrain`, `oracle`, `librarian`, `unspecified-high`, `artistry`, `quick`, `visual-engineering`, `writing`, `unspecified-low`, `metis`, `momus`, `atlas`.

### OpenCode configuration

The `lemonade` provider and agent model assignments live in `~/.config/opencode/opencode.json`:

```json
"provider": {
  "lemonade": {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Lemonade (local)",
    "options": {
      "baseURL": "http://localhost:13305/v1",
      "apiKey": "not-needed"
    }
  }
}
```

API key can be any non-empty string — Lemonade ignores it.

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

## CLI tool invocation: use bash, never PowerShell for gh/git

PowerShell 5.1 splits arguments at `|` when passing to external commands (10-year-old bug, PS #1995). OpenCode's `bash` tool goes through PS 5.1 by default and mangles `gh --jq`/`gh --template` expressions containing `|`, double quotes, or parentheses.

**Fix applied**: `"shell": "pwsh"` in `~/.config/opencode/opencode.json` switches to PS 7.x, which fixes argument-passing. Requires session restart to take effect. If PS 7 isn't working, fall back to the temp-script pattern below.

**Fallback pattern** (when `shell: pwsh` doesn't work or session not restarted):
```powershell
# Write command to temp .sh, execute via git-bash:
$script = 'C:\Users\tito1\AppData\Local\Temp\opencode\gh_cmd.sh'
Set-Content -Path $script -Value '#!/usr/bin/env bash
gh issue list --state open --json number,title --jq '"'"'.[] | "#\(.number) \(.title)"'"'"'
& "C:\Program Files\Git\bin\bash.exe" $script
```

Also available: `tools/ghc` wrapper — `& "C:\Program Files\Git\bin\bash.exe" tools/ghc "issue list --state open --json number --jq '.[].number'"`

**For gh with JSON + PowerShell processing** (avoids --jq entirely):
```powershell
gh issue list --state open --json number,title | Out-File -Encoding utf8 $env:TEMP\issues.json
# Then Read the file
```

## Tool quirks

- **Glob tool cannot see dot-directories.** `glob(".opencode/**")` or `glob("**/.opencode/*")` returns nothing — the tool silently skips paths starting with `.`. Use `Get-ChildItem -Force` (PowerShell) as a fallback when searching inside `.opencode/`, `.claude/`, `.omo/`, or any hidden directory.
- **Project config (`oh-my-openagent.jsonc`) lives in a dot-directory AND overrides global config.** Always check `.opencode/oh-my-openagent.jsonc` for agent/category model settings before concluding a model is "hardcoded." The global `~/.config/opencode/oh-my-openagent.json` is the fallback.


## File editing on Windows: use bash + sed, NEVER PowerShell

PowerShell text manipulation silently corrupts files in this project:
- `$()` interpolation eats JavaScript jQuery calls (`$(''rail-operator'')`)
- `@""@` here-strings interpolate variables; `@''''@` here-strings use CRLF against LF files
- `Set-Content` / `Out-File` convert line endings
- `gh pr create --body` argument splitting with special characters
- Every workaround introduces a new edge case

### File content replacements — use git-bash sed

```bash
# Single-line edit (line 228, replace content):
sed -i "228s/OLD_TEXT/NEW_TEXT/" static/rack.js

# Multi-line block replacement via temp file:
cat > /tmp/replacement.js << 'HEREDOC'
async function checkAuth() {
  ...
}
HEREDOC
# ... sed to insert at specific line range
```

### Why this works
- `sed -i` preserves line endings exactly (no LF↔CRLF conversion)
- Single-quoted heredocs (`<< 'EOF'`) zero interpolation
- git-bash's sed is real GNU sed — no PowerShell argument-passing bugs
- Works for Python, JavaScript, any text file with any special characters

### PowerShell is fine for: git commands, running scripts, process management, file system ops (Test-Path, Get-ChildItem), API calls (Invoke-RestMethod)
### PowerShell is NEVER for: editing file contents, text replacements, writing heredocs into files
