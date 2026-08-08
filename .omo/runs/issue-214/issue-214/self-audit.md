# Self-audit — Issue #214

## Promises from investigation.md

[x] Add window export block to `static/rack.js` — delivered, confirmed at `static/rack.js:5167-5169`
[x] Rebuild `rack.min.js` — delivered, `Object.assign` present in minified output
[x] Export symbols: navigate, S, syncTranscribe, renderDetail, curProv, logout, api — confirmed all 7 in export block at `static/rack.js:5168`

## Acceptance criteria from issue #214

[x] `scripts/capture_screenshots.py` runs to completion against real `rack.min.js` — verified by regression test: `window.navigate`, `window.S`, `window.syncTranscribe`, `window.renderDetail`, `window.curProv` all present and callable
[x] Audit other e2e tests — 4 files found, all use exported symbols. `test_detail_rapid_clicks.py` has pre-existing failure (monkey-patch of `window.api` doesn't intercept esbuild-scoped `api` calls — predates this fix, broken since #186)
[x] Regression test exists, fails on master — confirmed RED (`window.navigate is undefined` without export block), confirmed GREEN with fix. Test: `tests/e2e/test_bundle_globals.py`
[x] No loss of minification — only 7 symbols exported, bundling/minification intact, `rack.min.js` 175.9KB

## Mutation check for new test

[x] `test_bundled_rack_exposes_tooling_globals` — mutation check: fails with export block removed? YES (confirmed during red-green: `window.navigate is undefined`)

## Full test suite

[x] Unit/API: 590 passed, 0 failed
[x] E2e: 5/6 passed, 1 pre-existing failure (test_detail_rapid_clicks — unrelated to this fix)

## Main checkout check

[x] `git -C C:/Claude/whisperdesk diff --stat` — clean (no unexpected edits)
