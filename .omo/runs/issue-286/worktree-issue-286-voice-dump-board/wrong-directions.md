# Wrong directions — issue #286

Discrepancies found between what an instruction (issue text, runner prompt, AGENTS.md, a skill) said and what was actually true when executed. Written as they happen, not backfilled.

## 1. Tracking issue #261 checklist was stale on entry

`/issue-claude 261` was invoked against tracking issue #261. Its checklist showed:

```
- [x] #283 Schema + kind plumbing
- [x] #284 Backend LLM chain
- [ ] #285 API endpoints + serialization
- [ ] #286 Frontend: kind picker + board section
- [ ] #287 Frontend: Dump Review tab + inline edit
```

#285 is in fact **CLOSED**, with two merged PRs (#291, #293). The checkbox was never ticked. Phase 0's rule (check `gh issue view` state + merged PRs per item, do not trust the checkbox) is what caught it; had the runner trusted the rendered checklist it would have re-implemented already-merged work.

Recommended fix: none to the runner prompt (its Phase 0 already handles this correctly). Someone should tick #285's box on #261. Noted here so the discrepancy is on record.

## 2. `EnterWorktree` produced a worktree one commit behind `origin/master`

The runner prompt says to call `EnterWorktree` with no `path` argument because "you want a new worktree, fresh off `origin/master`". The worktree it created was at `290e5f7`, while `origin/master` was at `5207255` — and `5207255` was the entire #285 API work (`GET /api/voice-dump-items`, `_serialize_voice_dump_item`, the rerun/save-draft/finalize routes) that #286 depends on. Implementing the frontend against that worktree would have wired the board to a route that 404s.

Cause: the local `master` ref was stale, and the base ref resolves against what the repo already has rather than fetching first. Phase 1's investigator caught it (it checked `git merge-base --is-ancestor` rather than trusting the issue's "already merged" claim). Fixed with `git fetch origin && git rebase origin/master` before any edit.

Recommended fix to `.claude/issue-runner-prompt.md`: in the Setup section, add an explicit `git fetch origin` + verify `git merge-base --is-ancestor origin/master HEAD` step immediately after `EnterWorktree`, before Phase 1 dispatches. A dependency-chain issue like this one (sub-issue N depends on sub-issue N-1 that merged hours ago) hits this every time.

## 3. Phase 1's investigator reported "no `tests/e2e` directory exists in the repo at all"

That was wrong. `tests/e2e/` exists and holds a real committed Playwright harness: `conftest.py` (session-scoped `live_server` running real uvicorn on a free port, per-test headless Chromium `browser`/`page` fixtures), plus 8 e2e tests including `test_bundle_globals.py`, `test_costs_ui_e2e.py`, and `test_browser_smoke.py`. They are marked `pytest.mark.e2e` and deselected from the default run, which is why a plain `pytest -q` reports them as "8 deselected" rather than listing them — most likely how the agent concluded they were absent. `INSTALL.md:114` documents the harness explicitly.

Consequence had it gone unchallenged: the report's recommendation was "no committed frontend test is expected/blocked by precedent", which would have shipped a UI feature with no browser-level coverage in a repo that has the harness for exactly that. Caught by reading `INSTALL.md` while looking up the app's run command.

Recommended fix: none to the prompt itself; the existing instruction to verify the investigator's claims rather than implement them verbatim is what caught this. Worth noting as a concrete instance for future runs: a `-m` marker deselection reads as "these tests do not exist" if you only look at the default run's summary line.

## 4. `package.json`'s `build:js` script does not match how the committed bundle was actually built

`build:js` is `esbuild static/rack.js --bundle --minify --outfile=static/rack.min.js` — no `--sourcemap`. But the committed `static/rack.min.js` ends with a `//# sourceMappingURL=rack.min.js.map` comment and `static/rack.min.js.map` is committed alongside it (both last updated together in `f28e254`). So whoever builds this bundle passes `--sourcemap` by hand and the declared script has drifted.

This matters for `scripts/verify_self_audit.py`, whose BUILD check rebuilds each esbuild-declared script into a temp file and byte-diffs it against the committed artifact. Because the declared command omits `--sourcemap`, its rebuild can never byte-match a committed artifact that has the comment — the check reports `rack.min.js` stale on every run, regardless of the change under review. Confirmed empirically: an unmodified rebuild of the current source is byte-identical to the committed bundle after stripping the trailing `sourceMappingURL` line (216001 bytes both sides).

Not fixed here (out of #286's scope, and adding `--sourcemap` to the script would not fix the checker anyway: esbuild derives the `sourceMappingURL` filename from the `--outfile` name, which the checker rewrites to a temp path, so the comment would differ by filename instead of by presence). Built with `--sourcemap` to match the committed convention and keep the `.map` in sync.

Recommended fix, separate issue: either add `--sourcemap` to `build:js` and teach `verify_self_audit.py` to normalize the `sourceMappingURL` line before diffing, or drop the committed `.map` entirely.

Confirmed numerically by the checker run: `committed=216039b, fresh=216002b`, a 37-byte difference, exactly the length of `\n//# sourceMappingURL=rack.min.js.map`.

## 5. `verify_self_audit.py` cannot find `esbuild` when the run is in a worktree

`check_build_freshness()` prepends `<repo_root>/node_modules/.bin` to `PATH` before shelling out to the build command. Combined with the script's own `find_worktree_for_branch_dir()` auto-detection (added in #292), `repo_root` resolves to the *worktree*, which never has `node_modules` (gitignored, and `EnterWorktree` does not populate it). Result on first invocation:

```
- BUILD [build:js]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command,
- BUILD [build:css]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command,
```

Two blocking findings that say nothing about the change under review. Worked around by prepending the main checkout's `C:\Claude\whisperdesk\node_modules\.bin` to `PATH` in the invoking shell; the script appends its own entry after the inherited `PATH`, so the outer one still resolves.

Recommended fix to `scripts/verify_self_audit.py`: when `repo_root` is a worktree and `<repo_root>/node_modules/.bin` does not exist, fall back to the main checkout's `node_modules/.bin` (resolvable via `git rev-parse --git-common-dir`) rather than reporting a rebuild failure. This will hit every worktree-based run of `/issue-claude` on a change that touches a bundled asset.

## 6. Phase 1's investigation downgraded the service-worker cache bump to a nice-to-have. It was a blocking correctness bug.

`investigation.md` §4 item 8 concluded:

> Service worker / bundle manifest: `static/sw.js` precaches `/static/rack.min.js` and `/static/rack.min.css` as whole-file entries (`static/sw.js:11-12`) — it does not enumerate individual pages/routes, so no per-page SW registration is needed. It does need the build artifact regenerated (see Section 5) and ideally a `CACHE_VERSION` bump (`static/sw.js:5`), though that is a general deploy practice rather than something specific to this feature.

"Ideally", "a general deploy practice". Both wrong. I accepted that framing and shipped the PR without touching `sw.js`; the independent `/audit-pr` reviewer correctly blocked on it.

What the investigation missed by describing the precache list without reading the fetch handler:

- `sw.js`'s fetch handler is **cache-first** for everything outside `/api/` (`caches.match(e.request).then(cached => cached || fetch(...))`). No revalidation.
- `activate` deletes only caches whose name `!== CACHE_NAME`, and `CACHE_NAME` is `'whisperdeck-static-' + CACHE_VERSION`.
- `install` (which is what re-fetches the precache list) only runs when the browser sees a **changed worker script**.

So with `sw.js` unchanged: no new install, cache-first serves the old `rack.min.js`, and `activate`'s purge is a no-op because the cache name never changed. Every existing client keeps the previous bundle indefinitely. `skipWaiting()`/`clients.claim()` do not help, because the worker script is byte-identical and the browser never treats it as new. The feature would have been invisible to exactly the users who already had the app open.

And it is not a one-PR slip. **17 commits changed `static/rack.min.js` between the last `CACHE_VERSION` bump (`9d59417`, PR #186) and this one.** Every one of them shipped a bundle that installed clients could not see. A hand-maintained cache version that has been missed 17 consecutive times is not a process to document harder.

Fix applied here, beyond the reviewer's request: the reviewer asked for a `v2` → `v3` bump plus a regression test asserting `CACHE_VERSION != 'v2'`. A bump alone repeats the pattern, and their suggested assertion hardcodes the very literal it is checking, so it goes stale on the next bump and then passes vacuously forever. Instead the `/sw.js` route in `app.py` now appends a 12-hex content fingerprint of the precached first-party assets (`rack.min.js`, `rack.min.css`, `index.html`) to whatever literal is on disk, so the worker script, and therefore the cache name, changes whenever the bundle changes, with no human step. The on-disk literal was still bumped to `v3` and kept as the manual override for changes the asset bytes cannot capture (e.g. editing the caching strategy in `sw.js` itself).

Recommended fix to the runner prompt / `AGENTS.md`: add the service worker to the Complement Rule's list of sites a bundled-asset change must touch. The Complement Rule already covers "new enum value: check every site that switches on that enum"; the parallel case here is "changed a precached asset: check the cache-invalidation path". Phase 1 enumerated the precache list and stopped there without reading the fetch strategy, which is the difference between "this file mentions my asset" and "this file will serve my asset stale".

## 7. Playwright's `page.route()` cannot intercept `/api/*` in this app, because the service worker reissues the fetch

Found while writing the round-2 test that asserts `startJob()` posts `kind=voice_dump`. The obvious approach, `page.route("**/api/transcribe", handler)`, silently does nothing: the handler never fires, and the request still reaches the real backend (observed as a real failed transcription of the test's fake wav bytes, returning no `doneId`).

Cause: `static/sw.js`'s fetch handler does `e.respondWith(fetch(e.request).catch(...))` for `/api/*` paths. That inner `fetch` is issued from the **service worker's** scope, not the page's. `page.route()` only patches requests the page itself makes, so once the worker is active every `/api/*` call is invisible to it.

Fix used: give the test its own browser context with `service_workers="block"` rather than using the shared `page` fixture from `tests/e2e/conftest.py`.

This is not specific to #286. Any future e2e test in this repo that wants to intercept, stub, or assert on an `/api/*` request through Playwright routing will hit the same wall and, worse, will appear to pass while asserting nothing, since a route handler that never fires records no failure by itself. The test added here guards against that by asserting the recorded-intercepts list equals the expected value rather than merely checking no wrong value was seen.

Recommended fix: add a `page_no_sw` fixture (or a `service_workers="block"` variant of the existing `page`) to `tests/e2e/conftest.py`, with a comment pointing at this behavior, so the next person does not rediscover it. Worth a follow-up issue.

(Further entries appended as the run proceeds.)
