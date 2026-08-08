# Issue #132 MCP-Verify Report

## Playwright MCP tool calls (in order, with raw response sizes)

### Phase 1: Bug reproduction (before fix)

| # | Tool | Target | Response chars |
|---|------|--------|---------------|
| 1 | `browser_navigate` | `http://localhost:8971` | ~380 |
| 2 | `browser_snapshot` | Login page | ~2,100 |
| 3 | `browser_click` | "No account? Register" | ~380 |
| 4 | `browser_snapshot` | Registration form | ~1,400 |
| 5 | `browser_fill_form` | testmcp132 / TestPass132! | ~410 |
| 6 | `browser_click` | "Register" | ~380 |
| 7 | `browser_snapshot` | Monitor (logged in) | ~6,800 |
| 8 | `browser_click` | "Service panel" | ~380 |
| 9 | `browser_snapshot` | Service panel (all jacks) | ~14,200 |
| 10 | `browser_click` | Groq "Fetch models" (empty) | ~390 |
| 11 | `browser_network_requests` | Filter groq/models | ~300 |
| 12 | `browser_network_request` | #27 groq/models body | ~1,200 |

**Bug confirmed**: `GET /api/providers/groq/models => [200] OK` fired with empty key field.

### Phase 2: Fix implementation

| # | Tool | Target | Response chars |
|---|------|--------|---------------|
| 13 | `grep` | jack-act / wireSettings in rack.js | ~200 |
| 14 | `read` | rack.js lines 4887-4934 | ~1,500 |
| 15 | `read` | JACK_DEFS lines 4678-4705 | ~1,200 |
| 16 | `edit` | Add guard in rack.js | ~200 |

### Phase 3: Re-verification (after fix)

| # | Tool | Target | Response chars |
|---|------|--------|---------------|
| 17 | `browser_navigate` | Reload app | ~380 |
| 18 | `browser_click` | Service panel | ~380 |
| 19 | `browser_snapshot` | Service panel | ~14,200 |
| 20 | `browser_click` | Groq "Fetch models" (empty) | ~390 |
| 21 | `browser_network_requests` | All requests | ~500 |
| 22 | `browser_find` | "Enter a key first" — not found | ~80 |
| 23 | `browser_console_messages` | Check errors | ~300 |
| 24 | `browser_navigate` | /static/rack.js (source check) | ~380 |
| 25 | `browser_find` | "Enter a key first" — FOUND | ~80 |
| 26 | `browser_evaluate` | toast test — "toast not defined" | ~120 |

**Discovery**: Page loads `rack.min.js`, NOT `rack.js`. My edit was only in `rack.js`. The fix was not in effect. Toast was mangled by terser.

| # | Tool | Target | Response chars |
|---|------|--------|---------------|
| 27 | `bash` (terser) | Minify rack.js → rack.min.js | ~50 |
| 28 | `grep` | Guard string in rack.min.js | ~80 |
| 29 | `browser_navigate` | Reload | ~380 |
| 30 | `browser_click` | Service panel | ~380 |
| 31 | `browser_click` | Groq "Fetch models" (empty) | ~390 |
| 32 | `browser_network_requests` | Filtered — groq/models #26 still there | ~300 |

**Still broken**: Browser served cached old `rack.min.js` from service worker.

### Phase 4: Cache-bypass verification

| # | Tool | Target | Response chars |
|---|------|--------|---------------|
| 33 | `browser_evaluate` | Unregister SW, clear caches | ~150 |
| 34 | `browser_navigate` | Reload | ~380 |
| 35 | `browser_run_code_unsafe` | Click Service panel | ~120 |
| 36 | `browser_run_code_unsafe` | Click Groq + wait 500ms + check toast | ~150 |

**Result**: `{ "toasts": ["Groq: 4 models available"] }` — still old behavior. HTTP cache from route handler not bypassing properly.

| # | Tool | Target | Response chars |
|---|------|--------|---------------|
| 37 | `browser_run_code_unsafe` | Set context route to strip cache headers + reload | ~250 |
| 38 | `browser_run_code_unsafe` | Click Service panel | ~120 |
| 39 | `browser_run_code_unsafe` | Click Groq + wait + check toast | ~150 |
| 40 | `browser_network_requests` | groq/models — #24 + #28 both present | ~300 |

**Both toast AND API call**: Old cached handler AND new handler both attached.

| # | Tool | Target | Response chars |
|---|------|--------|---------------|
| 41 | `browser_tabs` | New tab → http://localhost:8971 | ~380 |
| 42 | `browser_run_code_unsafe` | Clear storage, cookies, cache, reload | ~300 |
| 43 | `browser_snapshot` | Login page (session cleared) | ~2,100 |
| 44 | `browser_fill_form` | testmcp132 / TestPass132! | ~410 |
| 45 | `browser_click` | "Power on" (login) | ~380 |
| 46 | `browser_run_code_unsafe` | Click Service panel + wait | ~120 |
| 47 | `browser_run_code_unsafe` | Performance API: count groq/models before/after click + check toast | ~220 |

**FINAL RESULT**: `{ "beforeCount": 0, "afterCount": 0, "newRequestsFired": false, "toasts": ["Enter a key first"] }`

| # | Tool | Target | Response chars |
|---|------|--------|---------------|
| 48 | `browser_run_code_unsafe` | HuggingFace "Verify" (empty, kind='hf') — no settings API call, toast shown | ~200 |
| 49 | `browser_run_code_unsafe` | Groq with dummy key — "Groq: 4 models available" (valid keys work) | ~180 |

## Did the live-verify sequence work?

**Yes, but with significant friction from browser caching.**

The mandatory sequence (reproduce → fix → re-verify) was completed. The bug was confirmed live via network monitoring before the fix. After the fix, both empty-field blocking and valid-key passthrough were verified.

The main friction was the 12-turn detour caused by:
1. Editing `rack.js` instead of `rack.min.js` (the file actually loaded by the page)
2. Service worker caching the old `rack.min.js` across reloads
3. HTTP cache returning stale responses even after cache-bypass route configuration

These are not Playwright MCP tool failures per se — they are browser caching behaviors that any live-verification tool would encounter. The tools themselves (click, snapshot, network monitoring, evaluate) all worked reliably.

## Actual diff/fix

**File**: `static/rack.js` (source) and `static/rack.min.js` (minified, regenerated via terser)

**Change**: Added guard in the credential jack action button click handler:

```js
// Before (line 4889):
$('jack-act-' + j.id).addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
  const val = $('jack-input-' + j.id).value.trim();
  try {
    if (j.kind === 'hf') {

// After:
$('jack-act-' + j.id).addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
  const val = $('jack-input-' + j.id).value.trim();
  if (!val) { toast('Enter a key first', 'info'); return; }
  try {
    if (j.kind === 'hf') {
```

**Coverage**: All `JACK_DEFS` kinds — `key` (Groq/OpenAI/Replicate/OpenRouter "Fetch models"), `url` (Local transcription "Test"), `url-save` (Local LLM "Save"), `hf` (HuggingFace "Verify").

**Minification**: `npx terser static/rack.js -o static/rack.min.js --compress --mangle`

## Friction and surprises

1. **rack.js vs rack.min.js**: The app loads the minified file. Edited the source file first, wasted a round before realizing the minified file wasn't updated. terser was available (`npx terser` at v5.49.0) and the remininification was straightforward.

2. **Service worker caching**: The app registers a service worker (`sw.js`) that precaches `rack.min.js`. This made browser cache invalidation much harder than a simple hard-reload. Had to unregister the SW, clear all caches, clear cookies, and use a new tab context to finally get the updated file.

3. **Terser function name mangling**: `toast` gets renamed (e.g. to `f`), which made direct `window.toast()` calls from `evaluate` fail. The guard logic works within the file because all references are consistently mangled. The `playwright_browser_find` tool searching for "Enter a key first" text in the accessibility snapshot also failed while the page had cached old JS.

4. **Network request tracking ambiguity**: The `browser_network_requests` tool shows cumulative requests, making it hard to distinguish page-load requests from click-triggered requests. Used `performance.getEntriesByType('resource')` as a more precise alternative — checking before/after counts eliminates ambiguity.

5. **withBusy spinner**: The `{ spinner: true }` option in the handler wraps the async callback via `withBusy()`. The function signature `const val = ...` + `if (!val) { toast(...); return; }` still works because `return` exits early before the `try` block. The already-redundant `if (val)` checks inside the `hf` and `key` branches are harmless (always true now) and were left in place.

6. **Total Playwright MCP calls**: 49 browser tool calls plus 7 non-browser tool calls (grep/read/edit/bash) to complete the fix-and-verify cycle. The cache-related detour accounts for roughly 25 of the 49 browser calls.
