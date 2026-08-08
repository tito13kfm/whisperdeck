# Investigation — Issue #146: Add service worker for static asset caching

## Target
Issue #146 (standalone, not tracking)

## Current State

**Static file serving:**
- `app.py:2377` — FastAPI `StaticFiles` mount at `/static`
- `app.py:192-203` — Middleware adds `Cache-Control: public, max-age=3600` for `/static/*`
- `app.py:2354-2364` — Root `/` serves `static/index.html` with dynamic meta tag replacement, `Cache-Control: no-cache`

**Files in static/:**
- `index.html` (140 lines) — SPA shell, loads rack.js at line 138
- `rack.js` (4,403 lines) — main JS entry point, vanilla JS SPA
- `rack.css` — styling

**No existing service worker** — confirmed via grep for `serviceWorker`, `sw.js`, `navigator.serviceWorker`.

## Issue's Suggested Fix — Assessment

The issue proposes:
```js
const STATIC_ASSETS = ['/static/rack.js', '/static/rack.css', '/'];
```

**What it gets right:**
- Cache-first for static assets is correct
- Network-first for `/api/` is correct
- Versioned cache name (`whisperdeck-static-v1`) enables cache invalidation on deploy
- `self.skipWaiting()` for immediate activation

**What it's missing:**
1. No `activate` event handler to clean up old caches when version changes (stale caches accumulate)
2. No error handling for registration in rack.js
3. `/` serves index.html dynamically (meta tag replacement) — caching it is fine but worth noting

**What it gets wrong:**
- Nothing structurally wrong. The snippet is minimal but functional.

## Call Sites / Entry Points in Scope

1. **`static/rack.js:4403`** — end of `DOMContentLoaded` handler, where service worker registration should go
2. **`static/sw.js`** — new file to create (the service worker itself)
3. **`app.py:192-203`** — existing cache middleware (no changes needed, SW takes precedence)

## Complement Rule Check

The service worker is a new feature, not a modification to existing code. Only two touch points:
1. Create `static/sw.js`
2. Add registration to `static/rack.js`

No other callers/entry points need updating.

## Plan

1. Create `static/sw.js` with:
   - `STATIC_ASSETS` list
   - `install` handler (pre-cache assets)
   - `fetch` handler (cache-first for static, network-first for API)
   - `activate` handler (clean old caches)

2. Add registration to end of `rack.js` (inside `DOMContentLoaded`)

3. Verify with static source-level check (no live server needed for this feature)
