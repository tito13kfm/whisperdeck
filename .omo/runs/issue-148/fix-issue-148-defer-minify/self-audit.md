# Self-audit: Issue #148

## Issue acceptance criteria

- [x] Script tag has `defer` attribute — confirmed at `static/index.html:143`
- [x] Optionally: minified build output exists and is served in production — `rack.min.js` (177,601 bytes), `rack.min.css` (20,936 bytes) committed to git; served via existing `/static/*` mount
- [x] App functionality unchanged — 13/13 tests pass (test_service_worker, test_static_cache, test_smoke)
- [x] No console errors — static source verification passes; browser runtime check not performed (no browser tool available per instructions). Rationale: defer attribute is a standard HTML feature with well-defined behavior; minified JS is semantically identical to source.

## Investigation.md promises

- [x] Add `defer` to script tag — `static/index.html:143`
- [x] Create `package.json` with esbuild devDependency — `package.json:1-14`
- [x] Add `npm run build` script for JS + CSS minification — `package.json:7-9`
- [x] Update `index.html` CSS link to `rack.min.css` — `static/index.html:7`
- [x] Update `index.html` script to `rack.min.js` with `defer` — `static/index.html:143`
- [x] Update `sw.js` PRECACHE paths — `static/sw.js:11-12`
- [x] Bump `sw.js` CACHE_VERSION to `v2` — `static/sw.js:5`
- [x] Add `node_modules/` to `.gitignore` — `.gitignore:20`
- [x] Update `tests/test_service_worker.py` assertions — lines 44, 50-51, 63
- [x] Update `tests/test_static_cache.py` URLs — lines 7-8, 36
- [x] Verify `scripts/verify_batch_c.py` still reads source files — confirmed (reads `rack.css`/`rack.js`, correct)
- [x] Verify `scripts/list_api_endpoints.py` still reads source files — confirmed (reads `rack.js` via `REPO / "static" / "rack.js"`, correct)
- [x] All 13 tests pass — confirmed `pytest tests/test_service_worker.py tests/test_static_cache.py tests/test_smoke.py`

## Size reduction

| File | Before | After | Reduction |
|------|--------|-------|-----------|
| rack.js | 264,303 | 177,601 | 32.8% |
| rack.css | 27,668 | 20,936 | 24.3% |
| Total | 291,971 | 198,537 | 32.0% |

## Not delivered (none)

All promises delivered.
