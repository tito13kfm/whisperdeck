# Investigation: Issue #148 — Add JS build step (defer + minify rack.js)

## Issue staleness

Issue body claims:
- `rack.js`: 4378 lines, ~130KB — **stale**: current is 5076 lines, ~177KB
- `rack.css`: 741 lines, ~25KB — **stale**: current is 765 lines, ~23KB
- `index.html:138`: script tag — **stale**: line 143 in current file

## Real current state

### `static/index.html` (145 lines)
- Line 7: `<link rel="stylesheet" href="/static/rack.css">` — render-blocking, no `media` attribute
- Line 143: `<script src="/static/rack.js"></script>` — render-blocking, no `defer`/`async`

### `static/sw.js` (74 lines)
- Line 5: `CACHE_VERSION = 'v1'`
- Line 11: `'/static/rack.js'` in PRECACHE array
- Line 12: `'/static/rack.css'` in PRECACHE array

### `static/rack.js` (5076 lines)
- Single vanilla JS file, no modules, no imports
- All page renderers (dashboard, transcribe, transcripts, queue, voices, files, settings, detail, assistant, voicenotes) in one file

### `static/rack.css` (765 lines)
- Single CSS file

### No build tooling
- No `package.json`
- No Vite/Webpack/esbuild config
- Node.js v24.15.0 available on system

## Files referencing rack.js/rack.css (full sweep)

### Files that need updating if output filename changes

| File | Line | What | Action needed |
|------|------|------|---------------|
| `static/index.html` | 143 | `<script src="/static/rack.js">` | Add `defer`; change `src` if renamed |
| `static/sw.js` | 11 | `'/static/rack.js'` | Update path if renamed |
| `static/sw.js` | 5 | `CACHE_VERSION = 'v1'` | Bump to invalidate old precache |
| `tests/test_service_worker.py` | 50-51 | Asserts `'/static/rack.js'` in sw.js body | Update assertion |
| `tests/test_static_cache.py` | 36 | `client.get("/static/rack.js")` | Update URL if renamed |
| `scripts/verify_batch_c.py` | 7 | Reads `rack.js` directly | Update path if renamed |
| `scripts/list_api_endpoints.py` | 124 | Reads `rack.js` directly | Update path if renamed |

### Files with comment-only references (no update needed)

| File | Lines | Type |
|------|-------|------|
| `app.py` | 181, 189 | Comments mentioning rack.js |
| `services/diarization.py` | 369 | Comment |
| Various `docs/` and `tests/` | many | Comments, test descriptions |
| `scripts/append_signoff.py` | 35-36, 84-85 | Report text referencing rack.js/rack.css |

### .gitignore
- No entry for build output currently

## Sibling sweep

Checked for other scripts/styles loaded the same way:
1. **`<link rel="stylesheet" href="/static/rack.css">`** (line 7) — also render-blocking. Not in scope for this issue (issue only mentions JS).
2. **`<script src="/static/rack.js">`** (line 143) — the only `<script>` tag in index.html. No other script tags.
3. **`sw.js`** is served via a dedicated route (`app.py:2724`), not a `<script>` tag — not render-blocking, not in scope.
4. **No other `<link>` elements** that load assets besides rack.css and the meta charset/viewport tags.
5. **No inline `<script>` blocks** in index.html — all JS is in rack.js.
6. **No `<img>` or `<video>` tags** with `src` in index.html that could block.

Result: no sibling scripts or stylesheets missed. Only one `<script>` tag, only one `<link>` tag.

## Scope decision

The issue has two tiers:
1. **Required**: Add `defer` to script tag
2. **Optional**: esbuild minification build step

Both are in scope for this fix. Approach:
- Add `package.json` with `esbuild` devDependency
- Add `npm run build` script: `esbuild static/rack.js --bundle --minify --outfile=static/rack.min.js`
- Also minify CSS: `esbuild static/rack.css --minify --outfile=static/rack.min.css`
- Update `index.html` to load `rack.min.js` with `defer` and `rack.min.css`
- Update `sw.js` PRECACHE and bump CACHE_VERSION to `v2`
- Add `.gitignore` entries for `rack.min.js` and `rack.min.css`
- Update tests referencing old paths
- Update scripts referencing old paths

### CSS minification decision
The issue only mentions `rack.js`, but if we're adding a build step, it's trivial to also minify CSS. The link tag doesn't need `defer` (CSS doesn't have that attribute). Decision: include CSS minification since the build step is already being added.

### Production vs development
The built outputs (`rack.min.js`, `rack.min.css`) will be committed to git (not gitignored). Rationale:
- The app has no "dev mode" server flag — it always serves from `static/`
- If minified files are gitignored, every fresh clone or CI run needs Node.js + npm install + build before the app works
- If minified files are committed, the app works immediately after clone with no build step
- Dev workflow: edit source files (`rack.js`, `rack.css`), run `npm run build` to regenerate minified versions, commit both

### File size estimates
- `rack.js`: 5076 lines / ~177KB → estimated 90-100KB minified (~45% reduction)
- `rack.css`: 765 lines / ~23KB → estimated 14-16KB minified (~35% reduction)
