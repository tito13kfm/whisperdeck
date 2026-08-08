# PR Audit: #255 fix: _jobFingerprint never includes tagging_job, so tagging progress doesn't repaint   (reviewer: z-ai/glm-5.2, independent third family)

VERDICT: BLOCK

### Blocking            (empty = none)
- tests/e2e/test_detail_poll_tagging_fingerprint.py: the PR's own e2e test fails at runtime. Failure scenario: run `pytest tests/e2e/test_detail_poll_tagging_fingerprint.py -m e2e` against the checked-out PR ref -> `playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 5000ms exceeded. waiting for locator("#job-tagging") to be visible` (test fails in ~8s; the sibling mirror test test_detail_poll_partial_update.py passes in the same env, so the e2e infra is functional). Root cause: the test waits for `#job-tagging`, but no render path in static/rack.js ever creates a `#job-tagging` container. `renderDetailBody()` (rack.js:4649) transcript branch renders vm/nudge/tagRow/exportToolbar/segmentsHtml; `correctedHtml` (4276) renders `#job-correction`; `summaryHtml` (4462) renders `#job-summary`; `formatHtml` (4432) renders `#job-format-*`; none render `#job-tagging`. The only `#job-tagging` reference is the `runningContainers` patch loop at rack.js:4035, whose body (`if (el) el.innerHTML = ...`, rack.js:4040) is a no-op when the element does not exist. The mirror test it copied clicks the `corrected` tab (line 123) where `#job-correction` renders; this test never switches tabs and targets a widget that has no render path. Fix: the test does not match issue #246's acceptance criterion, which asks for a test "asserting the fingerprint string changes when only tagging_job.progress.done changes." Rewrite the test to assert `_jobFingerprint` directly: expose `_jobFingerprint` on window (add it to the Object.assign list at rack.js:5870), then via `page.evaluate` construct two payloads differing only in `tagging_job.progress.done` and assert `_jobFingerprint(a) !== _jobFingerprint(b)` (and assert equality when only an already-tracked field is unchanged, to anchor the test). This is faithful to the issue scope, non-vacuous (old code without `f(t.tagging_job)` yields equal fingerprints -> assertion fails), and does not depend on the separately-missing render path. Regression test: `assert _jobFingerprint({tagging_job:{status:'running',progress:{done:1,total:4}}, ...all others null}) !== _jobFingerprint({tagging_job:{status:'running',progress:{done:2,total:4}}, ...all others null})`.

### Should fix          (empty = none)
- (none)

### Nits                (empty = none)
- The fingerprint code fix at static/rack.js:3531 is correct and the sibling sweep holds: scheduleDetailPoll guard (rack.js:3541) and jobActiveSnapshot (rack.js:3995) already include tagging_job, so polling continues and the active/inactive boundary crossing still triggers a full renderDetailBody() via the `crossed` check. The one-line addition matches every other job field. The Oracle note "voice_note_job has same gap" is slightly misleading: voice_note_job is not in the poll guard/snapshot/fingerprint at all (it uses a different mechanism), so it is not the same gap.
- Out-of-scope observation (do not fix in this PR): the detail page never renders a tagging progress widget, so even with the fingerprint fix, mid-run tagging progress changes (e.g. 1/4 -> 2/4 while still running) still produce no visible repaint on the detail page, because `updateDetailJobStatus`'s patch loop has no `#job-tagging` target. Only the running->completed transition repaints (via jobActiveSnapshot `crossed`). The issue scoped itself to the fingerprint only, so this is a separate gap to file independently, not a defect of this PR's code change.

### Honesty check
- self-audit.md [x] lines verified: 6/7 (the code-fix, sibling-sweep, and artifact-existence claims check out). False [x] found: line 17 ("A detail-poll test asserting the fingerprint string changes when only tagging_job.progress.done changes -- delivered in test_detail_poll_tagging_fingerprint.py") is false: the test fails at runtime, so it does not verify the acceptance criterion. The self-audit never ran the test (note 3 records only a `node -c` syntax check of rack.js, not a pytest run), so "delivered" overstates a test that does not pass.
- Vacuous / loosened tests: none (the test is not vacuous; it hard-fails, which is the opposite problem).
- Undisclosed scope (diff vs claims): the test overreaches beyond issue #246's acceptance criterion (a fingerprint-change assertion) into a full widget-repaint e2e that depends on a tagging running-widget render that does not exist in the codebase. The PR body and self-audit do not disclose that the test depends on `#job-tagging` rendering, which it does not.

### Read scope
- Focused read on static/rack.js (_jobFingerprint 3527-3531, scheduleDetailPoll 3533-3557, llmJobActive/jobActiveSnapshot 3977-3998, runningContainers/updateDetailJobStatus 4028-4045, correctedHtml 4276, formatHtml 4432, summaryHtml 4462, renderDetail 4529-4625, renderDetailBody 4649-4710, window export 5870) and the full new test file (cost guard: rack.js is large; read only the relevant regions). Read self-audit.md, investigation.md, wrong-directions.md, token-usage.md from the main checkout run dir.

### Summary
The one-line fingerprint fix is correct and the sibling sweep is sound, but the PR ships an e2e test that fails at runtime because it waits for a `#job-tagging` widget that no render path in rack.js ever creates. The test overreaches beyond issue #246's actual acceptance criterion (a fingerprint-change assertion). BLOCK until the test is rewritten to assert `_jobFingerprint` directly, matching the issue scope.

---

# Re-audit after autofix (commit 7b28be6, pushed to issue-246-sisyphus)

VERDICT: APPROVE

The autofix (reviewer-applied, `--post`) made two changes:
1. static/rack.js: added `_jobFingerprint` to the `Object.assign(window, {...})` test-hook export at line 5870, so the test can call the real function via `page.evaluate`.
2. tests/e2e/test_detail_poll_tagging_fingerprint.py: rewrote the test to assert `_jobFingerprint` directly. It logs in (so rack.js loads in a real browser), then via `page.evaluate` constructs two payloads identical except `tagging_job.progress.done` (1 vs 2), and asserts `fa != fb` (regression) plus `fa == fa2` (determinism anchor). This matches issue #246's acceptance criterion exactly and does not depend on the separately-missing `#job-tagging` render path. The bundle (static/rack.min.js) was rebuilt via `npx esbuild` so the export is live.

Verification:
- `node -c static/rack.js` -> OK.
- `pytest tests/e2e/test_detail_poll_tagging_fingerprint.py -m e2e` -> 1 passed (~1.7s).
- Mutation check: temporarily removed `+ '|' + f(t.tagging_job)` from `_jobFingerprint`, rebuilt, re-ran -> test FAILED (non-vacuous). Restored the fix, rebuilt, re-ran -> passed. `git diff HEAD -- static/rack.js` confirmed only the export line changed in the source after restore.
- Sibling regression: `pytest tests/e2e/test_detail_poll_partial_update.py tests/e2e/test_detail_poll_tagging_fingerprint.py -m e2e` -> 2 passed (bundle rebuild did not break the mirror test).

Notes:
- The `rack.min.js` diff is large (esbuild reassigns short variable names on each build); it is bundle-rebuild noise, functionally identical to the 2-line source change (the committed fingerprint fix + the export addition). Reviewers should judge the source `static/rack.js` diff, not the minified churn.
- The out-of-scope observation still stands: the detail page has no `#job-tagging` running-widget render, so mid-run tagging progress still won't visibly repaint even with the fingerprint fix. That is a separate gap to file independently, not a defect of this PR, which is correctly scoped to the fingerprint per issue #246.

### Final verdict
APPROVE. The fingerprint fix is correct, the sibling sweep holds, and the test now faithfully and non-vacuously verifies issue #246's acceptance criterion and passes at runtime.

---

Audit rerun timestamp: 2026-07-31 02:34:46 UTC

## PR Audit: #255 fix: _jobFingerprint never includes tagging_job, so tagging progress doesn't repaint   (reviewer: GPT-5.6 Luna, independent third family)
PINNED COMMIT 9ee887b, not the PR's current tip, which is 7b28be6. The diff for this run was derived from `git diff master...9ee887b`, not the PR's live tip. `--post` was not supplied, so this audit was read-only.

VERDICT: BLOCK

### Blocking            (empty = none)
- tests/e2e/test_detail_poll_tagging_fingerprint.py:115 The PR's new e2e test fails at runtime against the checked-out pinned commit. Failure scenario: run `C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest -m e2e tests/e2e/test_detail_poll_tagging_fingerprint.py -q` -> `playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 5000ms exceeded; waiting for locator("#job-tagging") to be visible`; the test exits 1 after 6.70s. The test waits for a `#job-tagging` element, but `renderDetailBody()` only renders the running voice-match container in its transcript branch, while `updateDetailJobStatus()` merely patches `#job-tagging` if one already exists (`static/rack.js:4035-4040`). Thus a transcript with only a running tagging job never creates the element the test requires. Fix: rewrite the test to assert `_jobFingerprint` directly, or add the missing render path if that broader UI behavior is intended. Regression test: `page.evaluate("() => { const a = { tagging_job: { status: 'running', progress: { done: 1 } } }; const b = { tagging_job: { status: 'running', progress: { done: 2 } } }; return _jobFingerprint(a) !== _jobFingerprint(b); }")` should be true on the fixed source and false before the fix.

### Should fix          (empty = none)
- [feature] static/rack.min.js: the pinned bundle does not contain the source fix. Failure scenario: the browser loads `/static/rack.min.js` from `static/index.html:151`, and evaluating the minified bundle's fingerprint logic for two payloads differing only in `tagging_job.progress.done` produces the same fingerprint, so the live browser does not detect tagging-only changes even though `static/rack.js:3531` is fixed. Fix: rebuild and commit `static/rack.min.js` from the pinned source, then run the browser test against the bundle.

### Nits                (empty = none)
- The pinned source fix at static/rack.js:3531 is logically correct. `scheduleDetailPoll` includes `tagging_job` at static/rack.js:3541, and `jobActiveSnapshot` includes it at static/rack.js:3995. The existing runtime path still has no `#job-tagging` render target, so this source fix alone only causes `updateDetailJobStatus()` to run; it does not visibly patch an in-progress tagging widget.
- The static scan found `asyncio.run()` in services/cost.py:96 and many tests, but none is reachable from the changed JavaScript or the new synchronous Playwright test. Existing `except Exception:` hits were outside newly added code and were not changed by this pinned commit.

### Honesty check
- self-audit.md [x] lines verified: 15/17. False [x] found: line 17 claims an acceptance-criterion test was delivered, but the checked-in test fails at runtime; line 28 claims the main checkout is clean, but this audit found untracked `docs/research/`, `static/rack.js.backup`, `static/rack.js.bak`, and `worklist.md`. Line 21's N/A mutation claim is not the blocking issue, because the test fails before exercising the intended assertion. Line 32's Oracle APPROVE claim was not independently verifiable from an Oracle artifact in the run directory.
- Vacuous / loosened tests: none. The new test is non-vacuous in intent, but it is unreachable past its missing DOM fixture and therefore does not prove the fingerprint behavior.
- Undisclosed scope (diff vs claims): the test claims to verify a fingerprint change but instead requires an unrendered tagging widget and then tests widget repaint behavior. The pinned commit also includes a stale minified bundle that the served page loads instead of the changed source file.

### Read scope
- Focused read on static/rack.js relevant detail-poll, render, and export regions; full read of tests/e2e/test_detail_poll_tagging_fingerprint.py; app.py serialization path; e2e fixtures; self-audit.md, investigation.md, wrong-directions.md, and token-usage.md. The total pinned diff is 1455 lines because the PR branch contains unrelated prior changes, so the large static file and unrelated files were read selectively. Tests were run from the pinned fixture using the main checkout's venv.

### Summary
The one-line source change is correct, but this pinned commit does not ship a passing regression test and its served minified bundle still has the old fingerprint. The checked-out test fails before proving the acceptance criterion, and the browser loads `rack.min.js`, so the pinned artifact cannot be approved.
