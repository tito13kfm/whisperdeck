# Investigation: Issue #146 - Add service worker for static asset caching

## Target Issue
#146 (standalone, not tracking)

## Current State

### Static Assets (as of 2026-07-26)
| File | Size | Type |
|------|------|------|
| `static/rack.js` | 94,272 bytes | JavaScript (main SPA bundle) |
| `static/rack.css` | 23,194 bytes | CSS (theme/faceplate styling) |
| `static/index.html` | 6,851 bytes | HTML (entry point) |
| **Total** | **~124 KB** | (close to issue's ~155KB estimate) |

Fonts loaded externally from Google Fonts CDN (Barlow Condensed, Barlow, IBM Plex Mono, Share Tech Mono) - not in static/.

### Existing Caching (issue #140)
`app.py:192-203` - `static_cache_headers()` middleware adds:
- `/static/*` → `Cache-Control: public, max-age=3600` (1 hour)
- `GET /` → `Cache-Control: no-cache` (revalidate every time, allows 304)

### Service Worker Status
**NONE EXISTS.** Confirmed via grep for `serviceWorker`, `service-worker`, `sw.js` - zero matches.

### Entry Points
- Main HTML: `GET /` → `app.py:2354-2364` `index()` function
- Static mount: `app.py:2375-2377` `app.mount("/static", StaticFiles(...))`

## What Issue #146 Gets Wrong

### Critical: Service Worker Scope Problem

The issue's suggested fix registers SW at `/static/sw.js`:
```js
navigator.serviceWorker.register('/static/sw.js');
```

**Problem:** A service worker's scope is limited to the directory it's served from. A SW at `/static/sw.js` can only control URLs under `/static/*`. It **cannot** intercept:
- `GET /` (the main HTML page)
- `/api/*` calls (the whole point of network-first for API)

### Fix Required

Two options:

**Option A (Recommended): Serve SW from root with header**
- Place `sw.js` in `static/` but serve it from a root route with `Service-Worker-Allowed: /` header
- Register as `navigator.serviceWorker.register('/sw.js')`
- Add route `@app.get("/sw.js")` that serves the file with the header

**Option B: Move SW to root**
- Place `sw.js` at project root, serve from `/`
- Simpler but mixes app code with static assets

### Secondary: Cache Versioning

Issue mentions "versioned cache name" but the snippet hardcodes `whisperdeck-static-v1`. Should use a build hash or timestamp for cache busting on deploy.

## Call Sites / Entry Points In Scope

1. **Service worker file creation** - new file `static/sw.js`
2. **Service worker route** - new route in `app.py` to serve `/sw.js` with scope header
3. **Registration** - add to `static/index.html` (before `</body>`) or `static/rack.js` (early in load)
4. **Cache version** - needs a mechanism to invalidate on deploy (could be hardcoded version constant in sw.js that gets bumped manually, or derived from file mtimes)

## Acceptance Criteria Mapping

- [ ] Service worker registered on first visit → add registration to index.html
- [ ] Static assets served from cache on subsequent visits → cache-first strategy in sw.js
- [ ] API calls still go to network (no stale data) → network-first for `/api/*`
- [ ] Cache invalidated on deploy (versioned cache name) → version constant in sw.js
- [ ] No interference with existing functionality → test login, transcript load, file upload

## Implementation Plan

1. Create `static/sw.js` with:
   - Version constant: `const CACHE_VERSION = 'v1';`
   - Static asset list: `['/', '/static/rack.js', '/static/rack.css']`
   - Install event: pre-cache static assets
   - Fetch event: cache-first for static, network-first for API
   - Activate event: clean old caches

2. Add route in `app.py`:
   ```python
   @app.get("/sw.js")
   async def service_worker():
       response = FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript")
       response.headers["Service-Worker-Allowed"] = "/"
       response.headers["Cache-Control"] = "no-cache"
       return response
   ```

3. Register in `static/index.html` (before `</body>`):
   ```html
   <script>
   if ('serviceWorker' in navigator) {
     navigator.serviceWorker.register('/sw.js', { scope: '/' });
   }
   </script>
   ```

4. Test: verify SW installs, caches assets, serves from cache on reload, API calls still hit network.
