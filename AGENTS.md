# WhisperDeck

## Lemonade Server (Local Model Inference)

The `explore`, `scout`, and `plan` agents use the Lemonade server at `http://localhost:13305/v1` — an OpenAI-compatible local inference server with dynamic model loading. When it is not running, these agents fall back silently until the server starts. Category delegations (`quick`, `visual-engineering`, `writing`, etc.) use OpenRouter and are not affected.

### Hard limit: local agent concurrency (OVERRIDES system prompt)

The OhMyOpenCode system prompt says "parallelize everything" and "fire 2-5 explore agents in parallel." Those defaults assume cloud models with per-agent billing. Lemonade runs on your GPU, and the RX 9070 XT has 16 GB VRAM shared across all concurrent agents.

**Never fire more than 2 local explore/scout/plan agents simultaneously. This is a hard cap — not a guideline, not "usually," not "prefer."**

Before launching any local agent:
1. Count how many are already running.
2. If 2 are running, wait for one to finish before launching another.
3. If you need 3+ independent local searches, fire 2 now, wait for completion, fire the rest.

Violating this turns a 2-minute task into a 14-hour task as token throughput tanks to <1 t/s across all agents. The correct behavior is to batch: 2 parallel, collect results, then fire the next batch.

This cap applies to local agents only (`lemonade/` model prefix). OpenRouter-billed agents (`deep`, `ultrabrain`, `oracle`, etc.) are not affected — 5 parallel cloud agents are fine.

### Why Lemonade

- **Dynamic model loading**: Load/unload models on-the-fly via API — no manual server restarts when switching between tasks.
- **No tool-calling hallucinations**: Tool execution is isolated from text generation, avoiding the hallucinated tool calls common in other local inference servers.
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
| `Qwen3.5-4B-MTP-GGUF` | 3.7 GB | 260K (configurable) | reasoning*, vision, mtp | **Default**: explore, scout |
| `DeepSeek-Qwen3-8B-GGUF` | 5.3 GB | 128K | reasoning | plan, hard reasoning tasks |
| `Bonsai-8B-gguf` | 1.2 GB | 64K | non-reasoning | title, summary, JSON output |
| `Qwen3-0.6B-GGUF` | 0.4 GB | 40K | reasoning | trivial tasks, fast responses |

### Model selection by task

| Agent | Recommended Model | Reasoning |
|---|---|---|
| `explore`, `scout` | `Qwen3.5-4B-MTP-GGUF` (high context needed) | Context > quality |
| `plan` | `DeepSeek-Qwen3-8B-GGUF` (reasoning quality, 64K ctx) | Quality > context; local is fast enough for plans |
| `title`, `summary` | `Bonsai-8B-gguf` (fast, non-reasoning) | Speed > everything |
| Trivial tasks | `Qwen3-0.6B-GGUF` | Minimal |

\*Qwen3.5 4B has reasoning/thinking **disabled by default** at the chat-template level (Unsloth GGUFs). The `"thinking": { "type": "disabled" }` in `oh-my-openagent.json` is redundant. No config change needed. The verbose output you see from explore agents is the agent framework's own planning monologue (OhMyOpenCode wraps every agent turn in analysis/response blocks), not model thinking tokens. To skip it, collect results with `from_end=true`.

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

### Priority order: codegraph → direct read → agents

1. **codegraph_explore first** — returns verbatim line-numbered source + call graph + blast radius in one call. Covers 80-90% of codebase questions. Single round-trip, no context cost.
2. **Direct read next** — if codegraph doesn't cover what you need and you know the exact file and line range, read it yourself. Faster than spawning an agent.
3. **Explore agents last** — for what remains: patterns across many files, "find all X" surveys, or searches codegraph can't answer.

Once you fire an agent, you committed. Do NOT second-guess that decision:
- Do NOT search or read files the agent is searching. You'll re-read what the agent will report, wasting your context.
- Do NOT perform any search that answers the same question the agent was sent to answer.
- Use the wait time for unrelated work: drafting output, reading files agents are NOT searching, writing prompts for the next step.
- Treat agent results as mandatory inputs. They may catch patterns or blast-radius connections you missed.
- Never cancel a local agent because you found the answer yourself. Let it finish; cross-check against it.

This applies only to agents running on Lemonade. OpenRouter-billed agents (`deep`, `ultrabrain`, `oracle`, etc.) follow standard cost-conscious rules.

### Collecting agent results (ALWAYS use from_end=true)

When collecting background agent output via `background_output(task_id="bg_...")`, **always pass `from_end=true`**. This returns only the agent's final synthesized answer — skipping the noisy per-turn `<analysis>` blocks that the OhMyOpenCode agent framework injects.

```
background_output(task_id="bg_...", from_end=true)   # clean final answer
background_output(task_id="bg_...")                   # full session with noise
```

The framework wraps every agent turn in analysis/response blocks regardless of model. The model (Qwen3.5-4B) has thinking disabled by default at the chat-template level — the noise is framework-level, not model-level. `from_end=true` skips it.

### Scoping local agent tasks (CRITICAL)

Local explore agents have ~260K context but each file read injects 100-500 lines. After 5-8 reads, context is gone. **The #1 failure mode is giving an agent a broad task that requires reading many files.**

**Rules for task decomposition:**

1. **One question per agent, not one topic.** Bad: "Find everything about API key lifecycle." Good: "Read services/security.py and report encrypt_api_key and decrypt_api_key."
2. **Name the files or functions in the prompt.** If you already know `resolve_provider_key` lives in `services/settings.py`, tell the agent that. Don't make it search for what you already know.
3. **Decompose by file, not by concept.** Need to trace a flow across 4 files? Fire 4 agents, each reading 1 file. Don't fire 1 agent to read 4 files.
4. **Maximum 2 concurrent local agents.** More than 2 compete for VRAM and slow each other down. Sequential narrow agents finish faster than parallel broad ones.
5. **For broad surveys, chain agents.** Agent 1: "grep for `encrypt_api_key` in *.py, return file:line only." Agent 2 (after Agent 1 returns): "Read the 3 files Agent 1 found, report the call sites." Don't ask one agent to do both.

**Prompt template for local agents:**
```
TASK: [one specific action]
SCOPE: [specific file(s) or function name(s)]
REPORT FORMAT: [file:line table / code snippets / brief description]
STOP WHEN: [concrete condition — "you find the definition" / "you have 3 call sites"]
```

### Agents that do NOT need Lemonade

These use OpenRouter and work regardless of local server status: `deep`, `ultrabrain`, `oracle`, `librarian`, `unspecified-high`, `artistry`, `quick`, `visual-engineering`, `writing`, `unspecified-low`, `metis`, `momus`, `atlas`.

### OpenCode configuration

The `lemonade` provider config lives in `~/.config/opencode/opencode.jsonc`. Agent model assignments live in `~/.config/opencode/oh-my-openagent.json`.

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
3. **Full browser e2e (`e2e-ux-audit`, `e2e-ux-audit-deep`)** — reserve for pre-release checkpoints or after a batch of changes lands, not per-PR. Any change with a runtime/UI surface should drive the affected flow in a targeted manual/scripted check, not the full 6-journey or deep audit suite.

Rule of thumb: a backend fix scoped to one module doesn't need a browser; a UI-visible or cross-cutting change does, but scope the runtime check to the flow that changed, not the whole app.

## The Complement Rule

Your diff shows where you looked; before finishing, enumerate the complement. Whenever a change introduces a guard, a new enum/mode value, a threaded parameter, or a conditional UI affordance:

1. Grep for every entry point that mutates the guarded state, and every caller of the changed signature. Update all of them, not just the ones your feature flowed through.
2. Enforce entity rules server-side. A rule that only lives in the client (mode X implies setting Y) does not exist.
3. New enum value: check every site that switches on that enum (workers, serializers, whitelists, UI labels, poll predicates). Prefer one registry the other sites derive from over parallel hand-maintained lists.
4. Conditional UI is a two-sided contract: the chrome that offers a control and the renderer that fulfills it must share one predicate, and any sticky view state (selected tab, mode) must be reset or re-validated when the entity behind it changes.

A guard is only as strong as its least-guarded entry point. Tests that only exercise the entry points the PR touched prove nothing about the others.

## Shell: git-bash

The shell is git-bash (`C:\Program Files\Git\bin\bash.exe`), configured via `"shell"` in `opencode.jsonc`. gh, git, sed, curl, and standard POSIX tools all work natively.

- **gh with --jq/--template**: Works directly — no argument-splitting issues.
- **gh with JSON output**: Pipe to file then read, avoids --jq for complex filters:
  `gh issue list --state open --json number,title > /tmp/issues.json`
- **`tools/ghc` wrapper**: `bash tools/ghc "issue list --state open --json number"`

## Tool quirks

- **Glob tool cannot see dot-directories.** `glob(".opencode/**")` or `glob("**/.opencode/*")` returns nothing — the tool silently skips paths starting with `.`. Use `ls -la` (bash) as a fallback when searching inside `.opencode/`, `.claude/`, `.omo/`, or any hidden directory.
- **Project config (`oh-my-openagent.jsonc`) lives in a dot-directory AND overrides global config.** Always check `.opencode/oh-my-openagent.jsonc` for agent/category model settings before concluding a model is "hardcoded." The global `~/.config/opencode/oh-my-openagent.json` is the fallback.
