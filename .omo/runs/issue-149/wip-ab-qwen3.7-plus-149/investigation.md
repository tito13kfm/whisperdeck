# Investigation: Issue #149 - Self-host Google Fonts

## Target Issue
**#149**: Self-host Google Fonts (eliminate CDN dependency)

## Current State

### Font Loading (static/index.html)
- **Line 7**: `<link rel="preconnect" href="https://fonts.googleapis.com">`
- **Line 8**: `<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>`
- **Line 9**: Google Fonts stylesheet with 4 families:
  - Barlow Condensed: weights 600, 700
  - Barlow: weights 400, 500, 600
  - IBM Plex Mono: weights 500, 700
  - Share Tech Mono: weight 400 (default)

### CSS Font Variables (static/rack.css, lines 61-64)
```css
--f-cond: 'Barlow Condensed', sans-serif;
--f-body: 'Barlow', sans-serif;
--f-mono: 'IBM Plex Mono', monospace;
--f-tube: 'Share Tech Mono', monospace;
```

These variables are used throughout rack.css (49 references to --f-mono, 27 to --f-cond, 5 to --f-tube, 3 to --f-body).

### Font Usage in static/index.html
- Line 19: `font-family:var(--f-cond)` (WhisperDeck title)
- Line 24: `font-family:var(--f-cond)` (Operator sign-in label)
- Line 25: `font-family:var(--f-mono)` (Standby indicator)
- Line 90: `font-family:var(--f-mono)` (Storage text)
- Line 92: `font-family:var(--f-mono)` (Online indicator)
- Line 126: `font-family:var(--f-cond)` (Video monitor handle)

## What the Issue Gets Right
- Approach is sound: download woff2, create static/fonts/, add @font-face, remove CDN links
- Font families and weights correctly identified
- Goal is valid: eliminate 2 DNS lookups + CSS redirect, remove third-party dependency

## What the Issue Gets Wrong or Misses
1. **File path error**: Issue says `index.html:7-9` and `templates/index.html`, but actual file is `static/index.html`, not `templates/index.html`
2. **CSS variables already exist**: Issue doesn't mention that rack.css already defines font-family variables (lines 61-64). We don't need to change these, just add @font-face declarations so the browser can resolve the font names to local files
3. **Incomplete @font-face example**: Issue shows only one @font-face declaration; we need 8 total (one per weight/family combination):
   - Barlow: 400, 500, 600
   - Barlow Condensed: 600, 700
   - IBM Plex Mono: 500, 700
   - Share Tech Mono: 400

## Call Sites / Entry Points in Scope
1. **static/index.html** (lines 7-9): Remove 3 Google Fonts link tags
2. **static/rack.css**: Add 8 @font-face declarations at the top of the file (before :root block)
3. **static/fonts/** directory: Create and populate with 8 woff2 files

## Implementation Plan
1. Download 8 woff2 font files from Google Fonts API
2. Create `static/fonts/` directory
3. Add @font-face declarations to top of `static/rack.css` (before line 61)
4. Remove lines 7-9 from `static/index.html` (the 3 Google Fonts link tags)

## Acceptance Criteria Mapping
- [ ] Font files exist under `static/fonts/` (8 woff2 files)
- [ ] No external CDN references in `static/index.html` (remove lines 7-9)
- [ ] All 4 font families render correctly (CSS variables already reference correct family names)
- [ ] @font-face declarations use `font-display: swap` (will add to all 8 declarations)
- [ ] Visual appearance unchanged (same font families, weights, and fallbacks)
