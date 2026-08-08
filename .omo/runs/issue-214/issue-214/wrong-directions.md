# Wrong Directions — Issue #214

## 1. test_detail_rapid_clicks.py — pre-existing failure

**File**: `tests/e2e/test_detail_rapid_clicks.py`

**Observation**: Test fails with `Cannot read properties of undefined (reading 'resolve')`. Root cause: the test monkey-patches `window.api` to intercept transcript fetches, but since `rack.min.js` (bundled by esbuild) resolves `api` by local scope, not `window.api`, the monkey-patch never intercepts internal calls. This test was written in #167 (before the `rack.min.js` bundling was introduced in #186/#148) and has been silently broken since.

**Recommendation**: Fix the test to use `page.route()` for network interception instead of monkey-patching `window.api`. Or rewrite to use `page.waitForFunction()` to detect state transitions.

**Status**: Not fixed in this PR (out of scope for #214).

## 2. worktree needs node_modules to rebuild rack.min.js

**Observation**: The worktree created by `git worktree add` doesn't have `node_modules/`. I used the main checkout's esbuild directly (`C:/Claude/whisperdesk/node_modules/.bin/esbuild`). Not a code issue, but an infra note for future /issue runs that touch `rack.min.js`.

**Recommendation**: Consider adding `npm install` to the /issue workflow setup step when the target touches `static/` or `package.json`. Or document the workaround in the runner prompt.

**Status**: Workaround used, no code change needed.

## 3. AGENTS.md agent config snapshot matches live config

**Observation**: Both AGENTS.md (dated 2026-07-28) and the live `~/.config/opencode/oh-my-openagent.json` agree: `explore`, `deep`, `ultrabrain`, `oracle` are all cloud. `atlas` and `writing` are the only local agents. No discrepancy to report.

## 4. test_detail_rapid_clicks — test_rapid_clicks missing from self-audit

This test is a pre-existing failure unrelated to #214's fix. The root cause is the monkey-patch approach (setting `window.api`) which worked when `rack.js` was served directly but broke when #186 introduced `rack.min.js` bundling. The esbuild scope wrapper hides `api` from `window`, and the monkey-patch on `window.api` cannot intercept internal `api()` calls that resolve through the bundled closure.

This is separately tracked — not caused by #214 and not fixed here.
