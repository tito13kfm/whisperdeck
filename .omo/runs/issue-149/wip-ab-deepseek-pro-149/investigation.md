# Issue #149 - Investigation: Self-host Google Fonts

## Current State

### index.html (lines 7-9)
Three external references to Google Fonts CDN:
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Barlow:wght@400;500;600&family=IBM+Plex+Mono:wght@500;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
```

### rack.css (lines 57-60)
Four CSS custom properties reference the font families:
```css
--f-cond: 'Barlow Condensed', sans-serif;
--f-body: 'Barlow', sans-serif;
--f-mono: 'IBM Plex Mono', monospace;
--f-tube: 'Share Tech Mono', monospace;
```

### Fonts Required (8 woff2 files, latin subset)

| Family | Weight | File |
|--------|--------|------|
| Barlow | 400 | barlow-v13-latin-400.woff2 |
| Barlow | 500 | barlow-v13-latin-500.woff2 |
| Barlow | 600 | barlow-v13-latin-600.woff2 |
| Barlow Condensed | 600 | barlow-condensed-v13-latin-600.woff2 |
| Barlow Condensed | 700 | barlow-condensed-v13-latin-700.woff2 |
| IBM Plex Mono | 500 | ibm-plex-mono-v20-latin-500.woff2 |
| IBM Plex Mono | 700 | ibm-plex-mono-v20-latin-700.woff2 |
| Share Tech Mono | 400 | share-tech-mono-v16-latin-400.woff2 |

## Issue's Suggested Fix - Assessment

The issue correctly identifies:
- Remove the 3 `<link>` tags from index.html
- Add `@font-face` declarations with `font-display: swap`
- Place woff2 files in `static/fonts/`

What the issue's snippet misses:
- The example only shows one `@font-face` block. All 8 font/weight combinations need declarations.
- The latin-ext subset is not strictly necessary for WhisperDeck (English-only UI), but could be added for broader character coverage if desired.

## Scope

### Files to modify:
1. `static/index.html` - Remove lines 7-9 (Google Fonts links)
2. `static/rack.css` - Add `@font-face` declarations before the `:root` block

### Files to create:
3. `static/fonts/` - 8 woff2 font files

### No call sites to enumerate
The CSS custom properties (`--f-cond`, `--f-body`, `--f-mono`, `--f-tube`) are used across the entire CSS and JS codebase for font-family settings. Since we're only changing how the font files are served (same font-family names, same weights), no usage sites need updating.

## Download URLs (from Google Fonts CSS API, latin subset)

1. https://fonts.gstatic.com/s/barlow/v13/7cHpv4kjgoGqM7E_DMs5.woff2
2. https://fonts.gstatic.com/s/barlow/v13/7cHqv4kjgoGqM7E3_-gs51os.woff2
3. https://fonts.gstatic.com/s/barlow/v13/7cHqv4kjgoGqM7E30-8s51os.woff2
4. https://fonts.gstatic.com/s/barlowcondensed/v13/HTxwL3I-JCGChYJ8VI-L6OO_au7B4873z3bWuQ.woff2
5. https://fonts.gstatic.com/s/barlowcondensed/v13/HTxwL3I-JCGChYJ8VI-L6OO_au7B46r2z3bWuQ.woff2
6. https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgg.woff2
7. https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3pQPwlBFgg.woff2
8. https://fonts.gstatic.com/s/sharetechmono/v16/J7aHnp1uDWRBEqV98dVQztYldFcLowEF.woff2
