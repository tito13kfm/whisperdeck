# Investigation — Issue #146: Service Worker for Static Asset Caching

## Files examined

| File | Lines | Purpose |
|---|---|---|
| `static/index.html` | 140 | SPA shell, loads rack.css + rack.js + Google Fonts |
| `static/rack.js` | 4403 | All frontend logic, DOMContentLoaded at L4370-4403 |
| `static/rack.css` | — | All styling |
| `app.py` L192-203 | — | `static_cache_headers` middleware: `/static/*` → max-age=3600, `/` → no-cache |
| `app.py` L2343-2364 | — | `_load_index_html()` + `GET /` route — serves index.html with dynamic `wd-password-min-length` meta |
| `app.py` L2375-2377 | — | `StaticFiles` mount: `/static` → `static/` dir |
| `tests/test_static_cache.py` | 48 | Existing cache header tests |

## Current state

- **No service worker registered** — confirmed by grep for `service.?worker`, `sw\.js`, `navigator\.serviceWorker` across entire repo (0 matches)
- **No PWA manifest.json** either
- **Static asset loading**: Single SPA with 3 static files + 2 Google Fonts CDN links
  - `rack.css` loaded in `<head>` (line 10)
  - `rack.js` loaded at end of `<body>` (line 138)
  - Google Fonts: `fonts.googleapis.com` (CSS) + `fonts.gstatic.com` (font files) in `<head>` (lines 7-9)
- **Server already has cache headers** (#140): `/static/*` gets `max-age=3600`, root `/` gets `no-cache`
- **Root route** `/` serves `index.html` with dynamic `wd-password-min-length` meta injection

## Complement Rule — call sites in scope

### Service worker registration (1 entry point)
- `rack.js` DOMContentLoaded handler (L4370-4403) — the single JS entry point. Registration goes here.

### Static asset consumers (all files the SW should cache)
- `GET /` → `index.html` (with dynamic meta, via `_load_index_html()` + replace)
- `GET /static/rack.css` → CSS
- `GET /static/rack.js` → JS
- Google Fonts: `fonts.googleapis.com/css2?family=...` (CSS) + `fonts.gstatic.com/s/...` (font files) — external CDN, optional to cache

### API endpoints (must remain network-first, NOT cached)
- All `/api/*` routes — the SW fetch handler must match network-first for these

### Non-static files that must NOT be cached
- `/api/transcripts/{id}/audio` — audio files (large)
- `/api/jobs` — live job status
- All other `/api/*` endpoints

## What the issue's proposed fix gets wrong

### 1. CRITICAL: Service worker scope bug (wrong location)
**Issue**: Serves `sw.js` from `/static/sw.js`, registered as `navigator.serviceWorker.register('/static/sw.js')`.

**Problem**: Service worker scope is determined by the script's URL path. A worker at `/static/sw.js` has scope `/static/` — it can ONLY intercept requests under `/static/*`. It **cannot** intercept `GET /` (root, the SPA shell), `GET /api/*` (API calls), or any other path outside `/static/`.

**Fix**: Serve `sw.js` from root path (`/sw.js`) so scope covers the entire origin. Requires an explicit route in `app.py`.

### 2. Missing `activate` event for cache cleanup
**Issue**: Only defines `install` and `fetch`. On deploy with a new cache version, old caches accumulate indefinitely.

**Fix**: Add `activate` event that deletes all caches not matching the current version. Also call `clients.claim()` so existing tabs pick up the new SW.

### 3. Hardcoded cache version with no bump mechanism
**Issue**: `'whisperdeck-static-v1'` — no way to invalidate on deploy without editing the file.

**Mitigation**: Use a `CACHE_VERSION` const at the top of `sw.js`. Developer bumps it manually on deploy. `activate` event cleans old versions automatically.

### 4. `cache.addAll` is all-or-nothing
**Issue**: If any of the 3 assets fails to fetch during install, the entire cache is empty and no assets are served offline.

**Fix**: Wrap in try/catch, or add assets individually. With only 3 assets the risk is low, but robust handling is cheap.

### 5. No `clients.claim()` in activate
**Issue**: `skipWaiting()` is called in install but `clients.claim()` is never called. Existing pages won't get the new service worker until a hard refresh.

**Fix**: Add `self.clients.claim()` in `activate`.

### 6. Root `/` cached at install time — may serve stale shell
**Issue**: The root route returns dynamic HTML (password min length injected server-side). If cached at install time and never updated, a deploy that changes `PASSWORD_MIN_LENGTH` won't be reflected until the SW updates (up to 24h).

**Mitigation**: This is inherent to cache-first for the shell. Acceptable tradeoff for offline support. The SW update mechanism (skipWaiting + claim) handles it within a page load after deploy, and users can hard-refresh.

## Implementation plan

1. **Create `static/sw.js`** with:
   - `CACHE_VERSION = 'v1'` const
   - `install`: precache `['/', '/static/rack.js', '/static/rack.css']` into versioned cache
   - `activate`: delete old caches, claim clients
   - `fetch`: cache-first for static, network-first for `/api/*`, network-only fallback for everything else

2. **Add route in `app.py`**: `GET /sw.js` → `FileResponse(static/sw.js)` with `application/javascript` content type

3. **Register in `rack.js`**: After `checkAuth()` at DOMContentLoaded, register `/sw.js`

4. **Test**: existing `test_static_cache.py` tests remain relevant. Static source check covers the SW logic.
