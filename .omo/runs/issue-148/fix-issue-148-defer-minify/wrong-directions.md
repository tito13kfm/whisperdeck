# Wrong directions: Issue #148

## Issue body staleness

- **Line counts stale**: Issue claims rack.js 4378 lines, rack.css 741 lines. Current: 5076 and 765.
- **Line numbers stale**: Issue says `index.html:138` for script tag. Current: line 143.
- **Size stale**: Issue says ~130KB for rack.js. Current: ~264KB (unminified).

Recommendation: issue bodies should either avoid quoting specific line counts/numbers (use function/symbol names instead) or include a "last verified" date.

## "esbuild is a single binary with no Node.js dependency required"

The issue claims esbuild needs no Node.js. True of the standalone Go binary, but the npm package approach (which the issue itself recommends step 1: "Create package.json with esbuild as a dev dependency") requires Node.js. The system had Node.js v24.15.0, so this wasn't a blocker, but a system without Node.js would need the standalone binary download approach instead.

Recommendation: note the Node.js requirement explicitly when recommending the npm approach; mention the standalone binary as an alternative for systems without Node.

## CSS defer not applicable

The investigation considered adding optimization to the CSS link tag as well. CSS doesn't have a `defer` attribute; `media="print"` onload-swap is the CSS equivalent. Not in scope for this issue.

## Service worker file size

The `rack.min.js` is 177KB — still substantial for a service worker precache. Future work: consider code splitting to reduce the initial precache payload. Out of scope for this issue.
