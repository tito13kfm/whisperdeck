# wrong-directions.md — issue #112

Written as each discrepancy was hit, not backfilled.

## 1. The issue's line numbers are stale

Issue #112 says `services/llm_jobs.py:368-373`. The `has_enrolled_voice` check is at **697-704**; line 368 is in the `correction` branch. **Recommended fix:** none needed for the codebase, but this is the fourth issue in this tracker whose line numbers had drifted. The workflow's "don't trust the issue's own snippet" rule earned its place again.

## 2. The issue's proposed fix, implemented literally, would have caused a regression

Issue text: "Filter by embedding_model in the early-exit check". Implemented as `.filter(VoiceProfile.embedding_model == voice_id_service.backend_name)` that would have introduced **two** false-negative classes, both worse than the wasted CPU being fixed:

- Legacy rows with `embedding_model IS NULL` are matched by `identify()` on purpose (`services/voice_id.py:235` short-circuits on falsy), so filtering on equality would hard-fail jobs for users who enrolled before the column existed.
- `_extract_embedding` (`services/voice_id.py:299-312`) silently degrades to MFCC when the primary backend is installed but throws at runtime, so `probe_model` is not always `backend_name`. A single-value filter would reject profiles enrolled during such a fallback.

**Recommended fix (applied):** the pre-flight predicate is a deliberate permissive superset via `compatible_embedding_models()`. Treat the issue's snippet as a hypothesis, not a spec.

## 3. Pre-existing vacuous test found and fixed: `test_identify_still_matches_legacy_profile_with_no_recorded_model`

`tests/test_voice_id.py:434` claimed to prove that a NULL `embedding_model` still matches. It did not. `VoiceProfile.embedding_model` carries a column default (`database/__init__.py:250`, `default="speechbrain/spkrec-ecapa-voxceleb"`), so passing `embedding_model=None` to the constructor stores the default, not NULL. The test's probe model was that same default string, so it passed via the equality branch at `voice_id.py:235` and **would still have passed with the NULL-wildcard check deleted**.

**Fix applied:** force the real NULL with a post-insert `UPDATE`, assert `profile.embedding_model is None`, and probe with a *different* model id so the only path to a match is the NULL wildcard. Fixed here rather than deferred because this change's correctness depends on that branch being real: it is the unit-level proof behind the pre-flight guard's NULL clause.

Same trap hit my own new test first (it failed with `assert 'failed' == 'completed'` for the wrong reason), which is how the pre-existing case was found.

## 4. Complement-rule miss on my part, caught only by the full suite

I grepped `embedding_model="test"` early and saw the hit at `tests/test_relabel_undo.py:164`, then scoped the fix to `tests/test_voice_match_job.py` alone and did not carry the second file into the task list. The full-suite run failed `test_relabel_undo.py::test_voice_match_records_relabel_history`. Fixed.

**Recommended workflow fix:** when a grep for a literal returns hits in more than one test file, every hit belongs in the task list at grep time. Reading the grep output is not the same as scoping it. This is exactly what the full-suite-before-checking-boxes rule is there to catch, and it caught it.

## 5. Phase 1.5 (Fable) found a REAL pre-existing defect that is out of scope

The Fable completion-race check reported, correctly, that the `voice_match` branch has **no cancellation check anywhere**: `services/llm_jobs.py:744-750` commits `record_relabel(...)` and overwrites `transcript.segments` unconditionally, and only then calls `_finish(db, job, "completed", error)`, which downgrades to `"cancelled"` if a cancel raced. Sibling branches do guard this (`voice_dump` at 630-632, `tagging` at 531-533, `voice_note` at 568-570, the last with the explicit comment "the transcript itself is unchanged"). Failure scenario: a user cancels a long voice_match run; every remaining segment still processes, the relabel history entry and the segment overwrite still land, and the Queue shows the job as cancelled while the transcript's speaker labels were in fact all rewritten.

It also flagged two SUSPICIOUS-NEEDS-HUMAN items: `classify_pipeline`'s pre-`_finish` enqueues (`llm_jobs.py:486-516`) and `_finish`'s lack of a "don't downgrade a terminal completed" guard against the outer handler at `757-761`.

**Not fixed here, deliberately.** Issue #112 is about a pre-flight guard; this change adds only early-exit paths *before* any work and introduces no post-completion side effect. Fixing write-after-cancel means adding cancellation checks inside the segment loop and before the dependent writes, which is a separate behavior change with its own tests. **Recommended: file it as its own issue** (suggested title: "voice_match commits relabel + segment overwrite after a cancel"), with the two SUSPICIOUS items as follow-ups.

## 6. `verify_self_audit.py` needs esbuild on PATH when run against a worktree

First run reported two blocking BUILD findings, both `'esbuild' is not recognized as an internal or external command`. Cause: the script auto-detects the worktree as its repo root (the behavior added in `290e5f7`), and a fresh worktree has no `node_modules`. The runner prompt's own infra note covers the general case ("use the main checkout's installed binaries") but the checker invokes `esbuild` by bare name itself, so the caller has to put it on PATH:

    cd C:\Claude\whisperdesk
    PATH="/c/Claude/whisperdesk/node_modules/.bin:$PATH" .venv/Scripts/python.exe scripts/verify_self_audit.py <audit path>

**Recommended fix:** add that PATH prefix to the Phase 3.5 instructions in `.claude/issue-runner-prompt.md`, or have `verify_self_audit.py` fall back to `<main checkout>/node_modules/.bin/esbuild` when the detected root has no `node_modules`. Otherwise every worktree run reports two false blocking findings.

## 7. CORRECTED: the "stale `rack.min.js`" finding was a checker bug, not a stale bundle

**What I first wrote here was wrong**, and I am leaving the correction visible rather than quietly rewriting it, because the wrong version is what the PR body said for a while.

First conclusion: with esbuild on PATH the checker reported `static/rack.min.js does not match a fresh build of static/rack.js (committed=225038b, fresh=225041b)`, and since `git diff --stat origin/master HEAD -- static/` was empty I filed it as a pre-existing stale bundle on `origin/master`, out of scope per the runner prompt.

The out-of-scope part was right. The diagnosis was not. **The bundle was never stale.** `verify_self_audit.py` rebuilds to a temp *filename*, and `build:js` passes `--sourcemap`, so esbuild emitted `//# sourceMappingURL=tmpXXXX.js.map` where the committed bundle has `rack.min.js.map`. That difference is the entire 3-byte delta. Building with the real basename gives a byte-identical result:

    80d44f8803511f743b07063848cfcc35 *static/rack.min.js       (committed)
    80d44f8803511f743b07063848cfcc35 *<tmpdir>/rack.min.js     (fresh)
    225038 both

**What I did wrong, process-wise:** a 3-byte delta on a file I had not touched should have been diagnosed before being labelled. I reached for "pre-existing, out of scope" (which was true and convenient) instead of asking why the number was 3. The runner prompt's "note it in wrong-directions.md rather than fixing it" instruction is about *scope*, and I let it stand in for *diagnosis*. A stale-build finding whose delta is a handful of bytes is far more likely to be a checker artifact than a real dead bundle, since a genuinely un-rebuilt bundle differs by whatever the source change was worth.

Both checker bugs (this one and the missing esbuild in item 6) are fixed in **PR #332**, with tests. After that fix, this branch's tree reports `OK: no stale builds, no suspect line citations.`

The `[x]` line in `self-audit.md` that cited the stale build has been corrected to match.

## 8. origin/master moved forward mid-run

`origin/master` gained `7fd6911` ("fix(diarization): surface non-fatal failure in UI as partial status (#325)") after this worktree was created, so the branch was one commit behind by the time the work was done. Rebased onto it before opening the PR and re-ran the full suite on the new base: **802 passed** (up from 799, the extra three are `#325`'s own new tests). Clean rebase, no conflicts.

Separately noticed: the **main checkout's local `master` was 4 commits behind `origin/master`** at the start of this run. Not something this task should change, but worth a `git -C C:\Claude\whisperdesk pull` outside it, since a stale local master is what made the bundle byte counts look inconsistent between the two checkouts while diagnosing item 7.

## 9. Nothing wrong with the runner prompt's infra notes

The worktree genuinely has no `.venv` and no `node_modules`; using `C:\Claude\whisperdesk\.venv\Scripts\python.exe` against worktree test paths worked as documented. No `esbuild` rebuild was needed because no `static/` file was touched, so `verify_self_audit.py`'s bundle byte-diff is a no-op for this change; any stale-bundle report from it is pre-existing (there was bundle work on Aug 2) and not introduced here.
