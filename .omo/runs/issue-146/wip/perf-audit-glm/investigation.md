# Phase 1 investigation — issue #146

## Issue (verbatim target)
#146 "Add service worker for static asset caching". Standalone, no tracking issue.

## Real code state (current, verified against worktree at origin/master tip)

### Server (FastAPI, not Flask — issue's doc note was wrong)
- `app.py:156-161` — `app = FastAPI(...)` construction. No `static_folder`/`static_url_path` params (FastAPI pattern is `app.mount`).
- `app.py:2377` — `app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")`. static files served at `/static/*`.
- `app.py:2354-2364` — `@app.get("/")` returns `HTMLResponse` from cached `_load_index_html()` (cache loaded once per process, ref test_static_cache.py:41).
- `app.py:192-203` — `static_cache_headers` middleware:
  - `/static/*` → `Cache-Control: public, max-age=3600`
  - `GET /` → `Cache-Control: no-cache`
  - middleware registered at `app.py:221` (BaseHTTPMiddleware)
- `app.py:20` — `FileResponse, HTMLResponse` already imported.

### Frontend assets (verified by direct grep)
- `static/index.html:9` — Google Fonts CSS (cross-origin `https://fonts.googleapis.com/...`)
- `static/index.html:10` — `<link rel="stylesheet" href="/static/rack.css">`
- `static/index.html:138` — `<script src="/static/rack.js"></script>` then `</body></html>`
- `static/rack.js` is 225818 bytes, `static/rack.css` is 25846 bytes. Total same-origin static ~250KB. **Issue body says ~155KB** — stale.
- No favicon, no apple-touch, no manifest, no other same-origin static assets referenced.
- No existing service worker / navigator.serviceWorker anywhere in index.html or rack.js.

## In-scope entry points (Complement Rule enumeration)

This change touches THREE entry points; missing any one is a regression:

1. **NEW file** `static/sw.js` — the service worker script. Must serve from a URL whose default scope covers `/` (otherwise the SW can only intercept `/static/*` and the HTML at `/` is never served from cache, defeating the entire feature). The issue's snippet registers `/static/sw.js` — a SW at that path defaults to scope `/static/`, NOT `/`. So either:
   - (a) Add a dedicated `/sw.js` route serving `static/sw.js` with `Service-Worker-Allowed: /` header so registration with `scope: '/'` succeeds, OR
   - (b) Register `/static/sw.js` with `scope: '/'` and set `Service-Worker-Allowed: /` on that response. StaticFiles doesn't allow per-file custom headers, so this requires the route anyway.
   - **Chosen: (a)** — add `@app.get("/sw.js")` route; place sw.js under `static/` so it's served through the existing `static_cache_headers` middleware-free path (the dedicated route skips the `/static/*` middleware branch). Register with `navigator.serviceWorker.register('/sw.js')`.
   
2. **NEW route** in `app.py` — `@app.get("/sw.js")` returning `FileResponse` for `static/sw.js` with `Content-Type: application/javascript` and `Service-Worker-Allowed: /` header. Must be added in the static-serving section (after `app.mount("/static", ...)` at line 2377). Without this, scope hoisting fails silently and the SW controls only `/static/*`.

3. **MODIFY** `static/index.html` — add inline registration script after the `<script src="/static/rack.js"></script>` tag at line 138 (before `</body>`). Inline because: registration has no business being part of the 225KB bundle; user's browser should register ASAP after document load without waiting for rack.js init.

## What the issue's own snippet gets wrong

1. **Scope problem (load-bearing)**: registers `/static/sw.js` which controls only `/static/*` by default. Cannot cache `/` (the HTML document the issue's plan explicitly caches). Must use `Service-Worker-Allowed: /` AND pass `scope: '/'` to register, OR serve from `/sw.js`. The issue's snippet omits both.

2. **Cache-first for `/` (HTML) without a deploy-time cache-bust**: the issue caches `/` cache-first. Once cached, the HTML never revalidates — the `Cache-Control: no-cache` header on `/` (set by `static_cache_headers` middleware) becomes irrelevant because the SW intercepts the request before the network. The only invalidation is bumping the SW cache name `vX` and shipping a new `sw.js`. The acceptance criteria mentions "versioned cache name" so this is the intended path — but only works if `sw.js` itself is updated on every deploy. Acceptable but worth calling out; clean up old caches in `activate` to avoid unbounded growth.

3. **No `clients.claim()` on activate**: `skipWaiting()` alone doesn't make the SW control the current page — `clients.claim()` is required for the first install to take over without a reload. Otherwise the SW only controls pages loaded AFTER it activates.

4. **No `activate` handler at all**: missing cleanup of old caches, preventing disk growth across deploys.

5. **`c.addAll(STATIC_ASSETS)` is atomic**: if any precache request fails (eg `static/rack.js` returns 404, or temporary network issue on LAN access), the entire install fails and the SW never activates. Either accept failure (silent degradation, browser retries next load) or use `Promise.allSettled` to install partially. Going with `addAll` per the issue for simplicity; the failure mode degrades gracefully because the fetch handler falls through to network on cache miss, and the next install attempt retries.

6. **Fonts not in the cache list AND can't easily be**: the issue body says "rack.js, rack.css, fonts" but the fonts are Google Fonts CSS, fetched cross-origin. `caches.match` for opaque cross-origin responses can serve them but `c.addAll` against `https://fonts.googleapis.com/...` would create cross-origin no-cors cache entries with limited revalidation. Skipping precache for fonts in v1 — the browser's HTTP cache handles those (issue #138 is about same-origin Cache-Control only). This is a deliberate scope cut, not a miss.

7. **No fallback when offline**: acceptance says "Offline: show cached shell, indicate no connection for API calls". The network-first `/api/*` falls back to `caches.match` (an API response cached from a prior network call) — but stale API data is arguably worse than nothing for transcription results. Going with the issue's network-first pattern but only returning cached API response on network failure, no offline-fallback HTML for non-API GET routes. The cache-first branch handles `/` and any same-origin asset so the shell works offline; cross-origin Google Fonts silently degrade to fallback serif when offline (acceptable — text remains readable).

## Static asset cache header interaction

The existing `static_cache_headers` middleware still runs on requests that BYPASS the SW (first navigation before SW installs, or when register scopes don't intercept, or with SW disabled). It also runs when the SW's fetch handler calls `fetch(e.request)` for cache misses — that fetch goes through the full middleware stack. Result: pre-cache responses contain `Cache-Control: public, max-age=3600` (good, will be cached by browser too as a backstop), and `/` HTML gets `no-cache` (good, ensures the initial document load always shows current HTML before SW takes over). No middleware changes needed.

## Tests touched (existing)
`tests/test_static_cache.py` — 5 tests asserting Cache-Control on `/static/*`, `/`, `/api/health`, and disk-read-once behavior. None of these touch `/sw.js` so they all stay green. My new route must NOT regress them (no ClassVar leak — `@app.get("/sw.js")` returns a FileResponse, will go through GZip middleware if >500 bytes — SW script is well under that).

## Tests added (Phase 3)
A new `tests/test_service_worker.py` covering:
- `GET /sw.js` returns 200, `Content-Type` includes `javascript`, `Service-Worker-Allowed: /` header present.
- `static/sw.js` file exists and is parseable JS (basic syntax sanity).

No browser/Playwright tier required for a static asset SW — Phase 3 will do the source-level static check (the new tests) plus the existing pytest suite.