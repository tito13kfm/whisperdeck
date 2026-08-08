# Issue #146 — Service Worker for Static Asset Caching

## Phase 0 resolution

Issue #146 is a **standalone** issue, not a tracking issue. No further
hierarchy to resolve. Target = #146 directly.

## Phase 1 — current code state

### Static assets

`static/` contains exactly three files (verified via `ls` and `find`):
- `index.html` (8.4 KB)
- `rack.css` (25 KB)
- `rack.js` (225 KB)

No other static files exist. No fonts are served locally — index.html
loads Google Fonts over HTTPS (lines 7-9 of index.html), so they are out
of scope for cache-first.

### How /static is served

- `app.py:2377` — `app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")`. This is the only place static files are wired.
- `app.py:192-203` — middleware `static_cache_headers` adds `Cache-Control: public, max-age=3600` for `/static/*` and `Cache-Control: no-cache` for `GET /`. This was issue #140, already merged.

So the browser HTTP cache (3600s) and `index.html` revalidation already
work. Issue #146's role is to add a second layer that (a) survives a
cold-start repeat visit, (b) provides an offline shell, and (c) does not
fight the existing 3600s `Cache-Control` (which the browser is already
honoring — but only within the lifetime, and only same-origin, etc.).

### /favicon.ico

`app.py:568-571` — `GET /favicon.ico` returns 204 No Content. The SW
fetch handler must not cache this (and must not crash on a non-2xx
response either).

### JS call sites in rack.js (no SW exists today)

Greps for `serviceWorker|cache|Cache` in `static/rack.js` only match
application-level caches (`bankListCache`, `bootData`, `csrfToken`),
**not** the Service Worker / Cache API. There is no existing registration
to extend or replace.

### API call shape

`static/rack.js:222-254` — every API call goes through `api(path, opts)`
which calls `fetch(path, { credentials: 'same-origin', ...opts, headers })`.
Mutations add `X-CSRF-Token`. The SW must preserve `credentials: 'same-origin'`
on every fetch it relays (passing the original `e.request` through is the
simplest way — do **not** construct a fresh `Request` and drop the cookies).

### Static-file mount path vs. SW scope (the issue's suggested fix has a bug)

The issue's proposed registration:

```js
navigator.serviceWorker.register('/static/sw.js');
```

…gives the SW the default scope of its parent directory: `/static/`.
A SW with scope `/static/` **cannot intercept** `GET /` or `GET /api/*`
requests, which is the whole point of the fix. The browser silently
ignores out-of-scope `respondWith()` calls — the SW would install, take
over `/static/*` only, and the user's "first visit is offline-ready"
claim would not hold for navigation or API calls.

The two ways to fix this:
- (A) Serve the SW at root (`/sw.js`) with no `scope` option, OR with
  `{ scope: '/' }`. The SW file still lives under `static/` for the
  release zip to pick it up, but app.py reads it from there and
  re-exposes it at `/sw.js`. **This is what we are doing.**
- (B) Serve it at `/static/sw.js` and add a `Service-Worker-Allowed: /`
  response header at that path. Works, but keeps the script at a non-root
  path which is harder to reason about and requires extra middleware
  tweaking.

(A) is cleaner. The `Service-Worker-Allowed: /` header is still set on
the response (the spec requires it for any non-default scope, and we
use the default scope `'/'` from `/sw.js`, so the header is technically
optional — but a defensive `Service-Worker-Allowed: /` makes the
intent explicit and survives any future scope change).

### Cache name versioning

Issue acceptance criterion: "Cache invalidated on deploy (versioned
cache name)". The cache name embeds a version string (`whisperdesk-static-v1`)
that gets bumped on each change to `static/sw.js`. Old caches are
deleted in the `activate` handler. This is the standard pattern; no
date-based busting is needed because the SW is itself served with
`Cache-Control: no-cache`, so the browser will detect a new SW within
one page load after deploy.

### What the issue's own snippet gets wrong (vs. our plan)

| Issue's snippet | Our plan | Why |
|---|---|---|
| `register('/static/sw.js')` | `register('/sw.js', { scope: '/' })` | Scope must reach `/` and `/api/*`, not just `/static/*` |
| `STATIC_ASSETS = ['/static/rack.js', '/static/rack.css', '/']` precached | Same list, plus the SW itself is implicitly cached by the install handler's `addAll` | Precache must include `/` so the offline shell is the last-shown page, not blank |
| No comment about credentials | All relayed fetches use the original `e.request` (which carries cookies for same-origin) | Required for session-cookie auth and CSRF |
| No `activate` cleanup | `activate` deletes any cache whose name does not match the current `STATIC_CACHE_NAME` | Otherwise the `whisperdesk-static-v1` cache accumulates forever across version bumps |
| No `favicon.ico` exclusion | `respondWith` only fires for `GET` requests; the existing `app.py` `GET /favicon.ico` returns 204 which is a no-op for the Cache API | The SW just passes through and doesn't store a 204 |

## Complement Rule scope (call sites to touch)

A service worker is a single new file plus three integration points. The
fix touches:

1. `static/sw.js` (new) — the worker itself.
2. `app.py` — one new `@app.get("/sw.js")` route to serve the worker at
   root, plus one extra branch in `static_cache_headers` middleware to
   keep `/sw.js` from being long-cached (so deploys propagate to clients).
3. `static/rack.js` — one new top-level block before any UI code to
   register the worker.
4. `tests/test_service_worker.py` (new) — mirror the pattern of
   `tests/test_static_cache.py`.

There are no other entry points. The fix does not touch CSRF, sessions,
or any other middleware.

## Acceptance criteria (mapped to code)

| Criterion | Where it lives |
|---|---|
| Service worker registered on first visit | `rack.js` registration block at top of file |
| Static assets served from cache on subsequent visits | `sw.js` `fetch` handler — cache-first for non-API |
| API calls still go to network | `sw.js` `fetch` handler — network-first for `/api/*` |
| Cache invalidated on deploy (versioned cache name) | `STATIC_CACHE_NAME` constant in `sw.js`; `activate` handler deletes old caches |
| No interference with existing functionality | All tests in `tests/` still pass; existing `/static/*` and `/` behavior unchanged |

## Out of scope

- Precache of Google Fonts. They're cross-origin, the SW would need a
  CORS-friendly strategy (or `no-cors` + opaque responses which lose the
  status), and they're already CDN-cached.
- Background sync or push notifications. Not in the issue.
- A manifest.json / PWA install flow. Not in the issue.
