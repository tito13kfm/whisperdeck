# Wrong Directions — Issue #146 (deepseek-pro)

## AGENTS.md line 127: local/cloud agent labeling stale

AGENTS.md claims `atlas`, `quick`, `writing`, `unspecified-low` are "OpenRouter-only" (cloud).  
**Reality** (from live `~/.config/opencode/oh-my-openagent.json`): All four are mapped to `lemonade/` models — they ARE local and share the 2-agent VRAM cap.

Recommended fix: Replace the static list in AGENTS.md with a pointer to the live config, or remove the list entirely since config is the single source of truth.

---

## Issue #146 body — service worker scope bug

**Issue text** says: create `static/sw.js` and register as `navigator.serviceWorker.register('/static/sw.js')`.

**Problem**: Service worker scope is determined by the script's URL path. A worker at `/static/sw.js` has scope `/static/` — it can only intercept requests under `/static/*`. It cannot intercept `GET /` (root, the SPA shell), `GET /api/*` (API calls), or any other path. The `fetch` handler in the proposed `sw.js` checks for `/api/` paths and `/` root, but neither would ever reach it.

**What the fix actually does**: Serve `sw.js` from `/sw.js` (root scope) via an explicit `@app.get("/sw.js")` route in `app.py`, and register `navigator.serviceWorker.register('/sw.js')`. This gives the SW scope `/` (entire origin), making the fetch handler actually see `/api/*` and `/` requests.

**Recommended fix for the issue body**: Update the registration snippet to use `/sw.js` and add a note that the file must be served from the root path, not from `/static/`.

---

## Codegraph unavailable in worktree

`codegraph_explore` failed on the worktree with "not indexed" — wasted one tool call. The main workspace has a `.codegraph/` index but worktrees created via `git worktree add` are separate directories and don't share the index.

Recommended fix: The issue-runner prompt could note that worktrees won't have codegraph indexes, so skip the codegraph call entirely in Phase 1.
