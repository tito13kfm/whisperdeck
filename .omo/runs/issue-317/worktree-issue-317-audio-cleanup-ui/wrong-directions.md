# wrong-directions.md — issue #317, branch `worktree-issue-317-audio-cleanup-ui`

Written as each discrepancy was hit, not backfilled.

## 1. The issue's central factual claim is wrong

Issue #317 says the cleanup settings keys "can only be changed by editing the
database." Not true. `PUT /api/settings` (`app.py:900-902`) takes a raw
`dict = Body(...)` with no schema, and the only gate is
`services/settings.py:143`: `patch = {k: v for k, v in updates.items() if k in
DEFAULT_SETTINGS}`. All twelve `cleanup_*` keys were added to
`DEFAULT_SETTINGS` by #270, so the route has accepted them the whole time. Any
authenticated HTTP client could set them.

That changes the shape of the fix: it is frontend-only for these keys, with no
backend registration step. Recommended fix to the issue text: say "no UI sends
them, so they are only reachable by hand-writing an HTTP request", which is
both accurate and still makes the case for building the panel.

## 2. The issue's line numbers for `static/rack.js` are stale

Issue cites `static/rack.js:5772-5773` (controls) and `:5978-5979` (save
handler). Current code: the render is at `:6026-6028` and the save handler at
`:6230-6242`, with `loadSettingsPage()` starting at `:5978`. The `app.py` and
`services/queue.py` citations were accurate. Expected per the runner prompt's
own warning, noted for completeness.

## 3. The issue omits the committed-bundle hazard entirely

`static/index.html:155` loads `/static/rack.min.js`, never `rack.js`. A
source-only edit to `rack.js` is invisible in the running app. The issue says
nothing about this, and it is the single highest-risk gotcha for the task: the
repo already carries two tests (`tests/test_static_nav_wiring.py`,
`tests/e2e/test_bundle_globals.py`) that exist because prior issues (#214,
#286) were bitten by exactly this.

Confirmed empirically rather than assumed: the committed `HEAD` bundle contains
zero occurrences of any `cleanup_*` key, so the new coverage test genuinely
fails on a stale bundle.

Recommended fix: the issue template for any `static/rack.js` change should
carry a "rebuild and commit `rack.min.js`" line.

## 4. The issue omits a real backend inconsistency in its own scope

`services/queue.py` called `filter_hallucinations` with `rep_window=3`,
`logprob_cutoff=-2.0`, `no_speech_cutoff=0.6` hardcoded, while
`app.py:1375-1377` read the same three values from user settings. The moment a
UI exposes those dials, tuning them applies to files small enough for the
inline path and is silently ignored for any file large enough to be chunked.
Fixed in this PR (user-approved scope decision), with a red-green regression
test.

## 5. `scripts/verify_self_audit.py` build check has two real defects

**Filed as issue #329.** Line numbers there were re-verified against `master`
before filing.

Worth stating first what is NOT wrong: the script correctly auto-detects the
worktree root (added by #292) and targets this branch's files, not the main
checkout's. I initially assumed otherwise and was wrong; corrected here rather
than left standing.

### 5a. The rebuild cannot run in a fresh worktree at all

`check_build_freshness` puts `<repo_root>/node_modules/.bin` on PATH
(`scripts/verify_self_audit.py:112-113`), but `repo_root` is the auto-detected
worktree, and a fresh worktree has no `node_modules` (gitignored). First run:

```
BUILD [build:js]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
BUILD [build:css]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
```

Two blocking findings, neither about the code. Worked around by exporting the
main checkout's `node_modules/.bin` onto PATH before invoking the script.

Recommended fix: fall back to the main checkout's `node_modules/.bin` when the
worktree has none. The script already knows both paths, since it derived the
worktree root from the main checkout.

### 5b. The byte-diff reports a false STALE BUILD for any `--sourcemap` bundle

With PATH fixed, the script reports:

```
STALE BUILD [build:js]: static/rack.min.js does not match a fresh build of
static/rack.js (sizes: committed=228805b, fresh=228808b).
```

The bundle was NOT stale. `check_build_freshness` rebuilds into
`tempfile.NamedTemporaryFile(suffix=".js")`
(`scripts/verify_self_audit.py:106-110`), and `build:js` passes `--sourcemap`,
so esbuild appends the temp file's own name to the output:

```
//# sourceMappingURL=tmpab3d9x2z.js.map     <- fresh build, into the temp path
//# sourceMappingURL=rack.min.js.map        <- committed
```

18 characters against 15, which is the entire 3-byte delta. Proven by
reproducing the exact temp filename and comparing: byte-identical once the
`sourceMappingURL` comment is stripped, and `npm run build:js` over the
committed bundle reproduces it at exactly 228805 bytes.

This fires on every run against any `--sourcemap` build, whether or not
anything is stale, so the check currently cannot distinguish a real stale
bundle from a clean one for `rack.min.js`. Not introduced by this task.

Recommended fix: build into a temp *directory* using the real output filename,
so `sourceMappingURL` matches, rather than into a temp file with a generated
name. Stripping the comment before comparing also works but is the weaker fix,
since it stops checking that line.

Bundle freshness for this branch was therefore verified directly instead: a
no-op rebuild before any source edit produced a byte-identical file (proving the
main checkout's esbuild version matches the one that built the committed
bundle), `npm run build:js` after the change reproduces the committed bytes
exactly, and `tests/test_settings_ui_coverage.py:72` asserts every exposed key
is present in the committed bundle.

## 6. Nothing was skipped for lack of a testable path

No backfill/migration/repair function was involved, no broken state was
impossible to construct, and every new function got a test. Recorded here
because the runner prompt asks for a reason when any of those are skipped.
