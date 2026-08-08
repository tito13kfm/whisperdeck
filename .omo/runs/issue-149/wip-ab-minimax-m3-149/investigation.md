# Issue 149 — Self-host Google Fonts

## Target

`#149` (standalone). Title: "Self-host Google Fonts (eliminate CDN dependency)".

## What the issue claims vs. reality

| Claim | Actual |
|-------|--------|
| `index.html:7-9` | File is at `static/index.html` (path line refs in issue body are wrong; content is correct). |
| "No `font-display: swap` (see #140)" | The Google Fonts URL already has `display=swap` query param, so text does render immediately. But there are zero `@font-face` declarations in source. `#140` is "Add Cache-Control headers for static assets" (closed, merged) — not actually about font-display. The issue's referenced issue numbers are stale/incorrect. |
| "Combined with #138 (cache headers)" | `#138` is "docs: add exploratory planning doc for mobile capture and intent routing" (merged) — unrelated to cache headers. The actual cache-headers issue is `#140`. |
| 4 font families, 8 weights total | Correct. Barlow 400/500/600, Barlow Condensed 600/700, IBM Plex Mono 500/700, Share Tech Mono 400. |

## All Google Fonts references in the repo

```
static/index.html:7  <link rel="preconnect" href="https://fonts.googleapis.com">
static/index.html:8  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
static/index.html:9  <link href="https://fonts.googleapis.com/css2?family=...&display=swap" rel="stylesheet">
static/rack.css:61   --f-cond: 'Barlow Condensed', sans-serif;
static/rack.css:62   --f-body: 'Barlow', sans-serif;
static/rack.css:63   --f-mono: 'IBM Plex Mono', monospace;
static/rack.css:64   --f-tube: 'Share Tech Mono', monospace;
static/rack.js:1055  ctx.font = 'bold ...px Barlow, sans-serif';
static/rack.js:1073  ctx.font = 'bold ...px Barlow, sans-serif';
```

No `@font-face` declarations anywhere. No other file references Google Fonts CDN.

## Sites in scope (Complement Rule)

| File | Change | Why |
|------|--------|-----|
| `static/index.html` | Delete lines 7-9 (the two preconnects + the stylesheet link) | Remove the only external CDN references in the rendered app. |
| `static/rack.css` | Add 8 `@font-face` blocks at top of file (above `:root`) pointing to `/static/fonts/*.woff2` with `font-display: swap` | The `font-family` custom props already reference the four families; without `@font-face`, the browser falls back to `sans-serif` / `monospace`. The CSS variables are the consumer contract, this is the source. |
| `static/fonts/` (new dir) | Download 8 woff2 files (latin subset only) | Self-host the font binaries. |
| `static/sw.js` | Add 8 font URLs to `PRECACHE` array | The service worker uses cache-first; without precaching fonts, the offline shell works on second load (via runtime cache) but first-load offline breaks for any page using fonts. The existing PRECACHE explicitly lists `/`, `/static/rack.js`, `/static/rack.css` — same pattern for the font files. |

`static/rack.js` already uses `'Barlow, sans-serif'` with a generic fallback, so it does not need a change.

No tests reference fonts, no docs, no CI, no Python code outside of the static-file mount. No CSP header is currently set, so removing the Google hosts does not break a policy.

## Font files to download (latin subset only)

WhisperDeck UI is English-only (default transcription provider Moonshine is English-only per README; no i18n in the app). The Google Fonts CSS serves 2-3 unicode-range subsets per family/weight (vietnamese, latin-ext, latin) — only the `/* latin */` blocks are needed.

| Output filename | Source URL | Weight |
|-----------------|------------|--------|
| `barlow-400.woff2` | `https://fonts.gstatic.com/s/barlow/v13/7cHpv4kjgoGqM7E_DMs5.woff2` | 400 |
| `barlow-500.woff2` | `https://fonts.gstatic.com/s/barlow/v13/7cHqv4kjgoGqM7E3_-gs51os.woff2` | 500 |
| `barlow-600.woff2` | `https://fonts.gstatic.com/s/barlow/v13/7cHqv4kjgoGqM7E30-8s51os.woff2` | 600 |
| `barlow-condensed-600.woff2` | `https://fonts.gstatic.com/s/barlowcondensed/v13/HTxwL3I-JCGChYJ8VI-L6OO_au7B4873z3bWuQ.woff2` | 600 |
| `barlow-condensed-700.woff2` | `https://fonts.gstatic.com/s/barlowcondensed/v13/HTxwL3I-JCGChYJ8VI-L6OO_au7B46r2z3bWuQ.woff2` | 700 |
| `ibm-plex-mono-500.woff2` | `https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgg.woff2` | 500 |
| `ibm-plex-mono-700.woff2` | `https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3pQPwlBFgg.woff2` | 700 |
| `share-tech-mono-400.woff2` | `https://fonts.gstatic.com/s/sharetechmono/v16/J7aHnp1uDWRBEqV98dVQztYldFcLowEF.woff2` | 400 |

Naming: human-readable `<family>-<weight>.woff2` instead of Google's hash names — easier to audit, easier to bump by hand if needed.

## Out of scope (intentional)

- **Longer cache headers for fonts (`max-age=31536000`)**: the existing `app.py:192-201` middleware applies `max-age=3600` to all `/static/*` uniformly. Bumping that to a year just for `/static/fonts/*` is a separate concern. The issue says "combined with #138 (cache headers)" — but the cache-headers issue is `#140`, and that's already merged with a one-hour policy. Not changing it here; that's a follow-up.
- **CSP header**: no CSP exists. Removing Google hosts makes CSP easier to add later if desired, but adding one now is scope creep.
- **Italic / bold-italic variants**: not used by the UI (the mono body, the condensed title, and `Barlow` use the regular/italic axes the issue lists).
- **Adding `latin-ext` / `vietnamese` subsets**: not used. UI is English. Smaller file, faster first paint.
