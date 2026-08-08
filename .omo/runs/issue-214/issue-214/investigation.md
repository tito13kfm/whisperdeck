# Investigation — Issue #214

**Title**: esbuild bundle hides window.navigate/S/etc — breaks Playwright tooling

**Worktree**: `C:/Claude/whisperdesk-issue-214` (branch `issue-214`, base `origin/master` at `1314625`)
**Main checkout**: `C:/Claude/whisperdesk` (`master`)

## Root cause confirmed

`static/index.html` line 143 serves `<script defer src="/static/rack.min.js">`. `package.json` builds it via:

```
esbuild static/rack.js --bundle --minify --outfile=static/rack.min.js
```

No `--format` flag, no `--global-name`. `esbuild --bundle` wraps the entire file's top-level scope, so plain top-level `function navigate(...)` (line 427), `const S = {...}` (line 6), etc. are no longer `window` properties. `static/rack.js` itself is a plain non-module classic script (`'use strict'`) that DOES expose them as globals — the break is in the bundled output.

## Real code vs issue's claims

The issue's root-cause analysis is correct. Line numbers it references are accurate as of `1314625`. The suggested fix (Option 1: explicit `Object.assign(window, {...})` at the bottom of `rack.js`) is the right approach.

## Call sites / entry points in scope (Playwright/e2e tooling)

All files that use `page.evaluate()` to call `rack.js` internals:

| File | Symbols used |
|------|-------------|
| `scripts/capture_screenshots.py` | `navigate`, `curProv`, `S`, `syncTranscribe`, `renderDetail` |
| `tests/e2e/test_logout_polling_cleanup.py` | `navigate`, `logout` |
| `tests/e2e/test_detail_rapid_clicks.py` | `navigate`, `window.api`, `window.__origApi` (reads `window.api`) |
| `tests/e2e/test_detail_poll_partial_update.py` | `navigate` |

## Full sibling sweep

Checked all Python files under `tests/` and `scripts/` for `page.evaluate`, `pageEvaluate`, `evaluate(`, `navigate(`, `syncTranscribe`, `renderDetail`, `curProv`, `window.S` — found no additional call sites beyond the 4 files above.

## Sibling sweep for rack.js top-level declarations

All top-level `function`/`const`/`let`/`var` declarations in `static/rack.js` were enumerated (168 matches). Cross-referenced against the 4 tooling files above. The only declarations tooling actually depends on: `navigate` (line 427), `S` (line 6), `syncTranscribe` (line 1803), `renderDetail` (line 3828), `curProv` (line 1497), `logout` (line 1095), `api` (line 226).

## Issue's suggested approach — assessment

**Accurate**. The proposed `Object.assign(window, { navigate, S, syncTranscribe, renderDetail, curProv, /* ... */ })` at the bottom of `rack.js` is correct. The issue's list needs two additions the issue itself didn't enumerate: `logout` and `api`, both used by existing e2e tests.

The `scripts/capture_screenshots.py` screenshot regen that surfaced this bug further confirms the fix: it calls `navigate('transcribe')` on line 299, which times out with `ReferenceError: navigate is not defined` against the real `rack.min.js`.

## Implementation plan

1. Add an explicit `window` export block at the end of `static/rack.js`, before `</script>` in concept — as the last statement in the file:
   ```javascript
   if (typeof window !== 'undefined') {
     Object.assign(window, { navigate, S, syncTranscribe, renderDetail, curProv, logout, api });
   }
   ```
2. Add a regression test (browser-based) that loads the real served page and asserts `typeof window.navigate === 'function'` (see acceptance criteria).
3. Run existing e2e test suite to confirm no regressions.
