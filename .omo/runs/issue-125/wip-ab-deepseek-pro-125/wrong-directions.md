# Wrong Directions — Issue #125 (deepseek-pro)

## 1. `explore-hard` subagent type unresolved

**Discrepancy**: Dispatched `task(subagent_type="explore-hard", ...)` and got "Unknown agent: explore-hard". The live config (`oh-my-openagent.json`) defines `explore-hard` at lines 23-27 with model `lemonade/DeepSeek-Qwen3-8B-GGUF`. The agent framework didn't recognize it.

**Impact**: Falls back to plain `explore` (Qwen3.5-4B) for the sibling sweep. The task was "reasoning-heavy" (enumerating all call sites) so it would have benefited from the 8B model, but the 4B model still produced a thorough answer.

**Recommendation**: The agent resolver might need a separate `explore-hard` entry in some internal registry beyond the JSON config. Check if the `subagent_type` parameter uses a different resolution path than the `agent` config block.

## 2. AGENTS.md local/cloud labeling still disagrees with live config

**Lines**: AGENTS.md ~127 lists `atlas`, `quick`, `writing`, `unspecified-low` as OpenRouter-only (not subject to local cap). Live config maps all four to `lemonade/` models.

**Confirmed again**: This was noted in prior runs and is still present. Not a one-off — the doc is genuinely wrong and hasn't been corrected.

**Recommendation**: Replace AGENTS.md's local/cloud table with a directive to always read the live config per-run, or add a script that validates AGENTS.md against the current config and prints discrepancies.

## 3. Codegraph unavailable in worktrees

**Discrepancy**: `codegraph_explore` with `projectPath` pointing at the worktree returned "no .codegraph/ directory found." Worktrees inherit the parent repo's `.git` but not `.codegraph/`.

**Impact**: Had to use grep + direct reads instead of codegraph. Two extra round-trips (grep for function locations, then read those files).

**Recommendation**: Either copy `.codegraph/` into worktrees at creation time, or teach the tool to walk up from the worktree to the main repo to find the index.

## 4. Cross-worktree venv for test runs

**Discrepancy**: Worktree at `whisperdesk-ab-deepseek-pro-125` had no `.venv`. Had to reference main repo's `.venv` with `../whisperdesk/.venv/Scripts/python.exe`.

**Impact**: One failed command ("No such file or directory"), instantly corrected. Trivial.

**Recommendation**: Add `.venv` to worktree creation script or auto-detect and fall back to main repo venv.

## 5. (Not a doc error) Monkeypatch target mismatch in regression test

**Issue**: First attempt monkeypatched `services.auth.create_user` but the route's `from services.auth import create_user` binds the name at app module load time. Needed to patch `app.create_user` instead.

**Impact**: One test failure → corrected in one round. Standard Python import binding behavior, not a doc issue.
