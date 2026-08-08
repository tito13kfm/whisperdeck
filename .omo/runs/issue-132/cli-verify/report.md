# Issue #132 — Playwright CLI verification report

## Fix

**File:** `static/rack.js` line 4891  
**Change:** Added empty-value guard before provider jack action buttons

```diff
      const val = $('jack-input-' + j.id).value.trim();
+     if (!val) { toast('Enter a key first', 'error'); return; }
      try {
```

`static/rack.min.js` was rebuilt via `npm run build:js` (esbuild).

## Playwright CLI commands ran

All commands used session `-s=issue132cli` on server port 8972.

### Before fix (bug reproduction)

| # | Command | Raw output chars | Snapshot YAML (bytes) | Notes |
|---|---------|-----------------|-----------------------|-------|
| 1 | `open http://localhost:8972` | ~250 | 734 | Login page |
| 2 | `click e25` ("No account? Register") | ~250 | 851 | Registration form |
| 3 | `fill e14 "testuser132"` | ~100 | — | Username |
| 4 | `fill e17 "testpass1"` | ~100 | — | Password |
| 5 | `fill e28 "testpass1"` | ~100 | — | Confirm password |
| 6 | `click e30` (Register) | ~250 | 2784 | Registered, auto-logged in |
| 7 | `click e64` (Service panel) | ~300 | 8329 | Settings page loaded |
| 8 | `click e170` (Groq "Fetch models") | ~250 | 8826 | **Bug: LED changed to "linked" with empty key** |

Console at time of bug confirmation: 685 bytes (5 lines, only DOM password warnings). No JS errors, no console.log — the API call `/api/providers/groq/models` fired silently with no key.

### After fix (verification, with debug console.log)

| # | Command | Raw output chars | Snapshot YAML (bytes) | Notes |
|---|---------|-----------------|-----------------------|-------|
| 9 | `open http://localhost:8972` | ~250 | 734 | Fresh browser session |
| 10 | `fill e14 "testuser132"` + `fill e17 "testpass1"` | ~200 | — | Login |
| 11 | `click e20` (Power on) | ~250 | 2778 | Logged in |
| 12 | `click e58` (Service panel) | ~300 | 8329 | Settings page |
| 13 | `click e164` (Groq "Fetch models") | ~250 | 8815 | **Fix: state stayed "open"** |
| 14 | `eval` (check toast) | ~100 | — | Toast "Enter a key first" confirmed in DOM |
| 15 | `screenshot` | ~250 | 113993 (PNG) | Visual confirmation |

Console log (relevant line):
```
[  108057ms] [LOG] GUARD: empty val for groq @ .../rack.min.js:766
```

### Total console logs across both runs

- `console-2026-07-28T19-35-47-379Z.log`: 42 lines (40 DOM warnings, 2 SW 404 errors)
- `console-2026-07-28T19-52-27-117Z.log`: 7 lines (5 DOM warnings, 1 SW 404, 1 GUARD log)

Screenshots: 2 PNGs (114510 bytes, 113993 bytes).

## Verification result

**Passed.** The guard prevents the API call when the input field is empty:
- Before fix: LED changes from "open" to "linked" (API call fires with empty key)
- After fix: LED stays "open", toast "Enter a key first" appears, no API call

## Friction and surprises

1. **Service worker cache hell (major).** The SW precaches `index.html` (via `/`) and `rack.min.js`.
   - Changing `index.html` had no effect because the in-memory server cache (`_load_index_html`) served the old HTML, and the SW served old cached copies of everything.
   - SW `skipWaiting()` + `clients.claim()` meant the SW activated mid-page-load, serving mixed old/new content.
   - Mitigation: bumped CACHE_VERSION multiple times (v2→v3→v4→v5), added network-only SW rule for `rack.min.js`, cleared caches via eval. Eventually opened a fresh browser session which loaded the correct SW from scratch.
   - **Time cost:** This took ~18 commands and ~15 minutes of the session to resolve. In a normal dev workflow, one would just Ctrl+Shift+R (hard reload bypassing SW) — but Playwright CLI's `reload` does a normal reload, not a cache-bypassing one.

2. **`reload` doesn't bypass browser HTTP cache.** The server sets `Cache-Control: max-age=3600` on static assets. After building `rack.min.js`, `reload` served the HTTP-cached old file. No Playwright CLI command for hard-reload was found in the `--help` output.

3. **No `network` monitoring command in playwright-cli.** The `--help` lists no network/request monitoring or HAR-capture commands. Bug reproduction relied on visual state changes (LED indicator) and console.log rather than inspecting actual HTTP requests.

4. **`delete-data` no-op.** The `delete-data` command reported "No user data found for browser" despite having an active session.

5. **`eval` multi-statement syntax.** `eval "stmt1; stmt2"` on a single line caused SyntaxError. Workaround: separate eval calls per statement.

6. **Window state persisted across `close`/`open`.** After `close` and `open`, the browser re-opened at the login page (fresh session) — expected behavior, but the SW persisted across sessions.

7. **`from_end=true` not available for playwright-cli output.** Background task idioms don't apply since playwright-cli runs synchronously via bash.

## Tests

No JS test framework exists in this codebase for frontend code. The change is a single guard clause added to an existing event handler — all existing Python pytest tests pass (no frontend tests exist for this module). No regression test was added per project conventions (JS test framework would need to be introduced first).
