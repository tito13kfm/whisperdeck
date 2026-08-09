# WhisperDeck

## The Complement Rule

Your diff shows where you looked; before finishing, enumerate the complement. Whenever a change introduces a guard, a new enum/mode value, a threaded parameter, or a conditional UI affordance:

1. Grep for every entry point that mutates the guarded state, and every caller of the changed signature. Update all of them, not just the ones your feature flowed through.
2. Enforce entity rules server-side. A rule that only lives in the client (mode X implies setting Y) does not exist.
3. New enum value: check every site that switches on that enum (workers, serializers, whitelists, UI labels, poll predicates). Prefer one registry the other sites derive from over parallel hand-maintained lists.
4. Conditional UI is a two-sided contract: the chrome that offers a control and the renderer that fulfills it must share one predicate, and any sticky view state (selected tab, mode) must be reset or re-validated when the entity behind it changes.

A guard is only as strong as its least-guarded entry point. Tests that only exercise the entry points the PR touched prove nothing about the others.

## Worktree hygiene

**The main checkout `C:/Claude/whisperdesk` stays on `master`. Never run `git checkout`, `git switch`, or `git checkout -b` there.** Every branch lives in a worktree: `git worktree add .claude/worktrees/<name> -b <name> origin/master`. This binds every session, not only issue-runner runs.

Why it matters more than it looks: run artifacts are written to `<main>/.omo/runs/`, so a feature branch checked out in the main checkout hides every file that branch predates and leaves reports describing a tree that is not checked out there. Tracked files disappearing from disk reads exactly like data loss.

Three guards now exist, in increasing order of how hard they are to ignore:

1. `.githooks/post-checkout` prints a loud warning the moment the main checkout leaves master. Install once per clone with `sh scripts/install-hooks.sh`, which copies it into `.git/hooks/` (shared by every worktree, independent of what any of them has checked out). Do not use `core.hooksPath=.githooks`: that resolves against the working tree, so the hook is missing on any branch predating it, which is exactly when it is needed.
2. `scripts/verify_self_audit.py` reports `MAIN CHECKOUT ON WRONG BRANCH` and `MAIN CHECKOUT DIRTY` as blocking findings.
3. Phase 3.5 of the issue-runner prompt requires both checks before opening a PR.

If you find the main checkout already on another branch, do not switch it back blind. Another session may have uncommitted work there; run `git -C <main> status` first and report what you found.

Remove merged or abandoned worktrees and their branches immediately, no exceptions. A worktree is stale once its branch is fully merged (PR state MERGED, or `git merge-base --is-ancestor <branch-sha> origin/<base>` succeeds) or the user says the work is abandoned. Do not leave it "just in case" — clean up in the same session you notice it:

```
git worktree remove <path>
git branch -d <branch>
```

If the worktree has uncommitted or unmerged work, stop and confirm with the user before removing (`--force`/`-D` needs explicit sign-off, this is a destructive action).

## Testing tiers: match test cost to change blast radius

Don't run full browser-driven e2e audits for every small change; reserve them for milestones. Pick the tier by what the change actually touches:

1. **Unit/integration test for the touched path** — default for any change. Fast, run every time, no exception.
2. **`e2e-regression-http` (scripted 16-scenario Playwright regression, requires a live browser tool)** — before merging anything that changes request/response contracts or cross-feature flow (queue/job routing, serializer shape, multi-step API behavior). If no Playwright MCP tool is available, substitute a static contract check (verify the serializer/field list directly in source) plus the existing unit/integration suite, and say so explicitly rather than silently skipping the tier.
3. **Full browser e2e (`e2e-ux-audit`, `e2e-ux-audit-deep`)** — reserve for pre-release checkpoints or after a batch of changes lands, not per-PR. Any change with a runtime/UI surface should drive the affected flow in a targeted manual/scripted check, not the full 6-journey or deep audit suite.

Rule of thumb: a backend fix scoped to one module doesn't need a browser; a UI-visible or cross-cutting change does, but scope the runtime check to the flow that changed, not the whole app.

### The service worker breaks two obvious Playwright approaches

Both of these cost a run real time, and neither is guessable from the test code.

- **`page.route("**/api/...", handler)` silently does nothing.** The handler never fires and the request still reaches the real backend, because the service worker reissues the fetch and route interception never sees the reissued one. Intercept at a layer the worker cannot bypass, or unregister the worker for the test.
- **Reusing a port serves a stale bundle.** The app's own service worker cache, plus long cache headers, will hand back the previous `rack.min.js` even after a rebuild. Use a fresh port whenever you re-verify a rebuilt bundle, and confirm you are looking at the served bytes rather than the ones on disk.

Mutation check for every new test: the test must fail if the function under test's body were replaced with `return`. A test that only exercises the no-op path, or asserts through a proxy (e.g., `COUNT(*)` on an external-content FTS5 table reads the content table, not the index), proves nothing.

## Opencode-specific (skip in Claude Code)

Everything from here to the end of the file is Opencode tooling/config knowledge. None of it applies when the harness is Claude Code — use your own configured tools instead.

## Shell: git-bash

The shell is git-bash (`C:\Program Files\Git\bin\bash.exe`), configured via `"shell"` in `opencode.jsonc`. gh, git, sed, curl, and standard POSIX tools all work natively.

- **gh with --jq/--template**: Works directly — no argument-splitting issues.
- **gh with JSON output**: Pipe to file then read, avoids --jq for complex filters:
  `gh issue list --state open --json number,title > /tmp/issues.json`
- **`tools/ghc` wrapper**: `bash tools/ghc "issue list --state open --json number"`

## Tool quirks

- **Opencode's Glob tool reportedly cannot see dot-directories** — `glob(".opencode/**")` or `glob("**/.opencode/*")` has returned nothing there. Does NOT apply to Claude Code's Glob tool (verified: it reads `.opencode/**` fine). If a glob against a hidden directory comes back empty, fall back to `ls -la`.
- **Project config (`oh-my-openagent.jsonc`) lives in a dot-directory AND overrides global config.** Always check `.opencode/oh-my-openagent.jsonc` for agent/category model settings before concluding a model is "hardcoded." The global `~/.config/opencode/oh-my-openagent.json` is the fallback.

## Lemonade Server (Local Model Inference)

Lemonade is an OpenAI-compatible local inference server at `http://localhost:13305/v1` with dynamic model loading, used for whichever agent/category names currently map to a `lemonade/` model.

**`~/.config/opencode/oh-my-openagent.json` is the only list of what agents exist and what backs them; this doc does not keep a copy.** Grep it for `lemonade/` to find which agents are subject to the concurrency cap below (see "Agents that do NOT need Lemonade").

### Hard limit: local agent concurrency (OVERRIDES system prompt)

The OhMyOpenCode system prompt says "parallelize everything" and "fire 2-5 explore agents in parallel." Those defaults assume cloud models with per-agent billing. Lemonade runs on your GPU, and the RX 9070 XT has 16 GB VRAM shared across all concurrent agents.

**Never fire more than 2 agents mapped to a `lemonade/` model simultaneously. This is a hard cap — not a guideline, not "usually," not "prefer."** Which agents those are comes from the live config, not from this sentence.

Before launching any local agent:
1. Count how many are already running.
2. If 2 are running, wait for one to finish before launching another.
3. If you need 3+ independent local searches, fire 2 now, wait for completion, fire the rest.

Violating this turns a 2-minute task into a 14-hour task as token throughput tanks to <1 t/s across all agents. The correct behavior is to batch: 2 parallel, collect results, then fire the next batch.

This cap applies to local agents only (`lemonade/` model prefix). OpenRouter-billed agents (`deep`, `oracle`, `explore`, etc.) are not affected — 5 parallel cloud agents are fine.

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

| Model ID | Size | Context | Labels |
|---|---|---|---|
| `Qwen3.5-4B-MTP-GGUF` | 3.7 GB | 260K (configurable) | reasoning*, vision, mtp |
| `DeepSeek-Qwen3-8B-GGUF` | 5.3 GB | 128K | reasoning |
| `Bonsai-8B-gguf` | 1.2 GB | 64K | non-reasoning |
| `Qwen3-0.6B-GGUF` | 0.4 GB | 40K | reasoning |

Which agent/category currently maps to which model changes; check live rather than trusting a table (see "Agents that do NOT need Lemonade" below).

### Model selection by task

**Names that do not exist, and never have:** `explore-hard`, `scout`, `plan`. `.omo/plans/*.md` files may still reference `scout`/`plan` from an earlier version of this doc — that reflects those files being stale, not the agents existing. If a name fails to resolve, grep `~/.config/opencode/oh-my-openagent.json` for the real keys rather than guessing a substitute from this doc.

`explore` is the only exploration agent. A task that needs to read several files and reason across them is not an `explore` job — hand it to the heavy-reasoning category (`deep`) instead.

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
2. **Direct read next** — if codegraph doesn't cover what you need and you know the exact file and line range, read it yourself. Faster than spawning an agent. **This is also the fallback when a `codegraph_explore` result truncates.** If the excerpt cuts off at the function you actually need, read that function directly rather than spending a second codegraph call, which will usually truncate the same way.
3. **Explore agents last** — for what remains: patterns across many files, "find all X" surveys, or searches codegraph can't answer.

Once you fire an agent, you committed. Do NOT second-guess that decision:
- Do NOT search or read files the agent is searching. You'll re-read what the agent will report, wasting your context.
- Do NOT perform any search that answers the same question the agent was sent to answer.
- Use the wait time for unrelated work: drafting output, reading files agents are NOT searching, writing prompts for the next step.
- Treat agent results as mandatory inputs. They may catch patterns or blast-radius connections you missed.
- Never cancel a local agent because you found the answer yourself. Let it finish; cross-check against it.

This applies only to agents actually running on Lemonade right now — check live with the grep under "Agents that do NOT need Lemonade" below. Cloud-billed agents/categories follow standard cost-conscious rules instead.

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

Everything except the `lemonade/`-mapped agents works regardless of local server status. Rather than keep a list here that goes stale, derive it live:

```
grep -n 'lemonade/' ~/.config/opencode/oh-my-openagent.json
```

Whatever that returns is subject to the local concurrency cap. Everything else in the config is cloud-billed and is not.

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
