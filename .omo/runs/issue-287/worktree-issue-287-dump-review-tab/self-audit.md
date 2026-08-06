# Self-audit — issue #287 "Voice dump: Dump Review tab + inline edit UI"

Branch: `worktree-issue-287-dump-review-tab`. Target resolved from tracking issue #261 (see `investigation.md`).

**This workflow runs no independent-model audit pass.** Claude Code's `/issue-claude` has no equivalent of opencode's `/issue` Phase 3.75 Oracle review, so everything below is self-review only. Independent review happens separately via opencode's `/audit-pr` after this PR is opened. Self-audit-only must not be mistaken for a full review. (One partial exception: the Phase 1.5 completion-race check ran on **Fable**, a genuinely different model, and its findings are recorded in `wrong-directions.md` items 4 and reflected in the design; but it reviewed one specific bug class, not the change as a whole.)

## Files changed

| File | Status |
|---|---|
| `static/dump_review.js` | new — pure draft helpers |
| `tests_js/dump_review.test.js` | new — 17 unit tests |
| `tests/e2e/test_voice_dump_review_tab_e2e.py` | new — 7 Playwright tests |
| `static/rack.js` | modified — the tab, the registry, the poller wiring, the actions |
| `static/rack.min.js`, `static/rack.min.js.map` | rebuilt |
| `package.json` | modified — `build:js` now declares `--sourcemap` (see `[decision]` below) |

## Promises from `investigation.md` — the "call sites / entry points in scope" list

Every line below was re-opened and confirmed at the cited location; none are marked from memory.

```
[x] `KIND_TABS` sticky-tab reset covers the review tab — delivered, confirmed at static/rack.js:3755 (`const kindTab = KIND_TABS[detailData.kind] || null;`, replacing the two hardcoded `format`/`notes` lines that had no `review` equivalent)
[x] `detailTabsHtml` pushes the `KIND_TABS` tab for voice_dump — delivered, confirmed at static/rack.js:3792-3793 (`const kindTab = detailData ? KIND_TABS[detailData.kind] : null; if (kindTab) tabs.push(kindTab);`)
[x] `renderDetailBody` gains the `dumpReviewHtml` review branch — delivered, confirmed at static/rack.js:5106 (`S.detailTab === 'review' && t.kind === 'voice_dump'`) plus the mismatched-kind fallback at static/rack.js:5115 (`Not available for non-voice-dump transcripts`)
[x] `detailAction` gains the save-draft and finalize handlers — delivered, confirmed at static/rack.js:5193 (`act === 'dump-save-draft'`) and static/rack.js:5211 (`act === 'dump-finalize'`)
[x] `dumpReview` module-level draft state, independent of `detailData` — delivered, confirmed at static/rack.js:4666 (`let dumpReview = null;`) with its cache key at static/rack.js:4668 (`function dumpReviewKey`)
[x] `_jobFingerprint` learns `voice_dump_job` — delivered, confirmed at static/rack.js:3765 (`f(t.voice_dump_job)`)
[x] `scheduleDetailPoll` guard learns `voice_dump_job` — delivered, confirmed at static/rack.js:3776 (`llmJobActive(t.voice_dump_job))) return;`)
[x] `jobActiveSnapshot` learns `voice_dump_job` — delivered, confirmed at static/rack.js:4231 (`voice_dump: llmJobActive(t.voice_dump_job),`)
[x] `navigate('dumpnotes')` after finalize — delivered, confirmed at static/rack.js:5225 inside the `dump-finalize` handler
[x] `loadDumpReview` sources items via the runs route, not the serializer — delivered, confirmed at static/rack.js:4686 (`const raw = (run && run.result && run.result.items) || [];`)
```

**Citation correction:** an earlier draft of this file cited `static/rack.js:3757-3759` for the `detailTabsHtml` change. That was wrong — those lines are the tail of `loadTranscriptDetail`. The real location is `static/rack.js:3792-3793`, corrected above. Caught by `scripts/verify_self_audit.py`, which is exactly what that check exists for.

### The fourth sibling list — found by the Phase 1.5 Fable check, NOT by `investigation.md`

```
[x] `updateDetailJobStatus`'s `runningContainers` learns `voice_dump_job` — delivered, confirmed at static/rack.js:4272 (`{ id: 'job-voice-dump', job: t.voice_dump_job, label: 'Voice dump' }`), and the container id it patches matches the id emitted by the in-flight branch of `dumpReviewHtml` (static/rack.js:4720, `<div id="job-voice-dump">`). Without this the in-flight progress line would have frozen at whatever it said when the tab was opened, since progress ticks do not set `crossed` and therefore never trigger a full re-render.
```

`investigation.md` enumerated three poller sites and missed this one. Recording that plainly: the sibling sweep was incomplete, and a different model caught it.

## New functions — each has its own test

```
[x] `normalizeDumpItems` (static/dump_review.js:22) — tested by 6 cases in tests_js/dump_review.test.js
[x] `materializeDumpItems` (static/dump_review.js:53) — tested by 11 cases in tests_js/dump_review.test.js
[x] `DUMP_NOTE_TYPES` (static/dump_review.js:15) — pinned against the backend vocabulary by `DUMP_NOTE_TYPES matches the backend NOTE_TYPES vocabulary exactly`
[ ] `dumpReviewKey` (static/rack.js:4668), `loadDumpReview` (:4676), `dumpDeadEndUnit` (:4706), `dumpReviewHtml` (:4714), `bindDumpReviewFields` (:4798) — NOT unit-tested in isolation: they are browser-only (DOM, `api()`, `$`) and `rack.js` is a bundled non-module script with no DOM unit-test harness in this repo. Covered at the browser layer instead by tests/e2e/test_voice_dump_review_tab_e2e.py (7 tests). The pure logic worth isolating was deliberately extracted into dump_review.js precisely so it could be unit-tested; see the `[decision]` on the extra file below.
```

## Mutation checks — every new or changed test

```
[x] tests_js/dump_review.test.js (all 17 cases) — mutation check: fails with `normalizeDumpItems` body replaced by each of `return;` / `return [];` / `return null;` / `return false;`? yes — 6 failures each
[x] tests_js/dump_review.test.js (all 17 cases) — mutation check: fails with `materializeDumpItems` body replaced by each of `return;` / `return [];` / `return null;` / `return false;`? yes — 11 failures each
```
All 8 mutants killed. Run mechanically by snapshotting `static/dump_review.js` to the scratchpad, rewriting one function body per run, and restoring from the snapshot afterwards (never `git checkout`/`git stash`). Restore verified by the suite returning to 17 pass / 0 fail.

```
[x] tests/e2e/test_voice_dump_review_tab_e2e.py (7 tests) — mutation check equivalent for a browser test is the red run against the pre-feature bundle: 6 of 7 FAIL on the original HEAD bundle, 7 of 7 pass on the current bundle. The 1 test that passes on both is `test_voice_note_detail_tab_unaffected_by_kind_tabs_refactor`, which is supposed to be unaffected — that is the intended result, not a vacuous test (it asserts an exact string, and would fail if the KIND_TABS refactor had broken the Notes tab into the kind-mismatch fallback).
```

## Red-green

```
[x] Red-green performed at the browser layer, where the symptom lives. Red: built the pre-change bundle from `git show HEAD:static/rack.js` + `git show HEAD:static/batch_aggregate.js` with the exact build command into a temp dir, copied it over `static/rack.min.js`/`.map`, ran the new e2e file → `6 failed, 1 passed`. Representative red failure: `playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 5000ms exceeded ... waiting for locator("[data-tab='review']") to be visible`, and `assert 0 == 1` on the tab-button count. Green: restored the bundle from a byte-verified snapshot → `7 passed`.
[x] Restore was verified byte-exact (`cmp` against the snapshot for both rack.min.js and rack.min.js.map) and the work confirmed still present (`grep -c "dump-save-draft" static/rack.min.js` → 2) before trusting green.
```

## Full suite runs (before checking any test box)

```
[x] `pytest tests -q` (full suite, e2e deselected by the repo's own marker config) — 794 passed, 1 skipped, 22 deselected
[x] `node --test "tests_js/**/*.test.js"` (full JS suite, not just the new file) — 25 passed, 0 failed
[x] `pytest tests/e2e/test_voice_dump_review_tab_e2e.py -m e2e -q` — 7 passed
[ ] `pytest tests/e2e -m e2e -q` (whole e2e directory in ONE process) — NOT clean: 6 passed, 15 errors, every error an `HTTPError 429` from `/api/register`. This is the repo's own shared rate-limit bucket (5 requests / 300s per client IP, shared across the whole `live_server` pytest session), not a regression: each e2e module registers its own user, and this branch adds a 9th registering module. Existing e2e files were therefore run one process per file; all pass except a pre-existing failure in `test_detail_rapid_clicks.py`, which was confirmed to fail identically against the pre-change bundle and is unrelated to this change.
```

## Acceptance criteria from issue #287, walked one by one

```
[x] "Dump Review tab renders draft items from `voice_dump_job.result_json`" — MET IN SUBSTANCE, NOT LITERALLY. The tab renders the draft items, verified at the browser layer by `test_review_tab_renders_seeded_items`. But it does NOT read them from `voice_dump_job.result_json`, because that field does not exist: `serialize_llm_job` (services/llm_jobs.py:47-69) emits no `result_json` key, and `_dictation_job_fields` (app.py:412-464) sets `"voice_dump_job": serialize_llm_job(vd_job)`. Both read directly and confirmed. The items come from `GET /api/transcripts/{id}/runs/voice_dump` → `runs[...].result.items` instead (static/rack.js:4686), matching how `formatHtml()` already sources job results. Flagging explicitly because a reviewer checking this criterion against the diff will grep for `result_json` and not find it on the read path.
[x] `data-dfield="title"` / `data-dfield="body"` / `data-dfield="type"` inputs are editable per item — delivered, confirmed at static/rack.js:4779 (`data-dfield="title"`), :4780 (`data-dfield="body"` textarea), :4774 (`data-dfield="type"` select). Verified end-to-end by `test_edit_save_draft_reload_persists`, which asserts each edited value with `==` after a real page reload.
[x] `data-dfield="discarded"` checkbox flags an item for removal at finalize — delivered, confirmed at static/rack.js:4776. The wire key is `discarded` (boolean), matching what `finalize_voice_dump` filters on; verified by `test_finalize_lands_on_board_without_discarded_item`, which asserts the board list equals exactly `["Buy milk and eggs"]` (the discarded item absent).
[x] `materializeDumpItems` appends clarifying-question answers to `body` on the client — delivered, confirmed at static/dump_review.js:53, which folds each answered question into `body` and drops it from `clarifying_questions`. Verified by `test_clarifying_answer_folds_into_body_without_duplication`, asserting the exact body string `"Get milk from store\n\nWhich store?\nWhole Foods"` and that a SECOND save does not append it twice.
[x] "Save draft + page reload → edits persist" — delivered, verified by `test_edit_save_draft_reload_persists` with a real `page.reload()` between the save and the assertions.
[x] "Finalize → items appear in board section as separate notes" — delivered, verified by `test_finalize_lands_on_board_without_discarded_item` (lands on `#page-dumpnotes.active`, exact title list) and by `test_reopening_tab_after_finalize_shows_finalized_state`.
[x] "Existing voice_note Notes tab unaffected" — UNAFFECTED, but not "working". Verified by `test_voice_note_detail_tab_unaffected_by_kind_tabs_refactor`, which passes identically on the pre-change and post-change bundles, and the diff never touches `voiceNoteHtml`'s body. The Notes tab was ALREADY broken before this branch, in two independent ways, both confirmed live in a browser: (1) `voiceNoteHtml` gates on `t.voice_note_job.result_json`, which the serializer never emits, so a successfully completed chain renders "No voice-note result yet"; (2) the `notes` branch of `renderDetailBody` never binds `[data-dact]`, and the delegated `#detail-body` handler `detailBodyClick` dispatches only `[data-export-*]`/`[data-seg-*]`, so that tab's own "Rerun chain" and "Discard note" buttons are dead. Not fixed here — this criterion demands the tab be left alone. Written up in `wrong-directions.md` item 3 and filed as its own issue.
```

## Decisions the issue did not ask for

```
[decision] Data source changed from the issue's stated `t.voice_dump_job.result_json.items` to `GET /api/transcripts/{id}/runs/voice_dump` — not specified by the issue, because the stated field does not exist on the wire (serialize_llm_job, services/llm_jobs.py:47-69). Implementing the issue literally would have shipped a permanently empty tab.
[decision] Added a file outside the issue's stated `Files:` list (`static/dump_review.js`) — not specified by the issue, because `static/rack.js` is a bundled browser-only script with no DOM unit-test harness, so pure logic inlined there cannot get the per-function test the workflow requires. Follows the existing `static/batch_aggregate.js` precedent exactly (CommonJS `require`, inlined by esbuild, tested by `node --test`). The alternative was a `wrong-directions.md` skip of the new-function test requirement, which is a worse outcome.
[decision] Replaced two hand-written `if (kind === ...)` tab lines with a `KIND_TABS` registry (static/rack.js:3696) rather than adding a third — not specified by the issue, because AGENTS.md's Complement Rule 3 prefers one registry over parallel hand-maintained lists and Rule 4 requires the chrome offering a control and the renderer fulfilling it to share one predicate. Behaviour for `format`/`notes` is unchanged (verified equivalent line by line, and by the voice_note e2e test); the registry additionally supplies the sticky-tab reset case for `review`, which the two old lines had no equivalent of.
[decision] Modified `package.json`'s `build:js` to add `--sourcemap` — not specified by the issue, because the committed `static/rack.min.js` ends with `//# sourceMappingURL=rack.min.js.map` and `static/rack.min.js.map` is tracked, so the committed artifacts were not reproducible from the declared command. Confirmed the committed `.map` was CURRENT, not stale (its `sourcesContent` for `rack.js` byte-matched `git show HEAD:static/rack.js`), so building without the flag would have dropped a working sourcemap and orphaned a tracked file — a regression, not a cleanup. Detail in `wrong-directions.md` item 6.
[decision] The Rerun button is offered ONLY from dead-end states (failed at static/rack.js:4726, cancelled at :4730, zero-items at :4739) and never from a completed draft or an already-finalized dump — not specified by the issue, because the Phase 1.5 check confirmed that `finalize_voice_dump` sets no marker on the job and `rerun_voice_dump_chain` does not check for existing `VoiceDumpItem` rows, so complete → finalize → rerun → finalize inserts a duplicate batch whose `sequence_index` restarts at 0 and interleaves under `order_by(sequence_index)`. Offering rerun beside a finalized dump would have added a frontend path straight into that hazard. Filed as a backend issue.
[decision] The editable draft is gated on `job.status === 'completed'`, with a distinct `cancelled` state, rather than on whether items exist — not specified by the issue, because the Phase 1.5 check found the voice_dump job runner rechecks `cancelled` only at the TOP of each span iteration, so a cancel arriving during the final `_structure_from_text` await still commits `result_json`. A cancelled job can therefore carry a complete-looking but partial item list.
[decision] "Already finalized" is detected by cross-checking `GET /api/transcripts/{id}/voice-dump-items` for a row whose `source_job_id` equals this job's id (static/rack.js:4692) — not specified by the issue, which requires the tab render only "while ... draft items haven't been finalized" but names no mechanism. No such flag exists on the job, so the cross-check is the only available signal. Covered at runtime by `test_reopening_tab_after_finalize_shows_finalized_state`.
[decision] `rerun-voice-dump` and `open-dumpnotes` actions added (static/rack.js:5177, :5228) — the issue's Actions section names only save-draft and finalize. Rerun is the only recovery from a failed/cancelled chain (the backend route already existed, unwired), and `open-dumpnotes` is the finalized state's only affordance. Both are small and mirror the existing `rerun-voice-note` shape.
[decision] Did NOT add `voice_note_job` to the four poller lists alongside `voice_dump_job` — a deliberate narrowing. The same gap exists for voice_note, but polling it would re-render the identical "No voice-note result yet" string because of the pre-existing `result_json` bug above, so it would add a poll loop with no user-visible effect while touching a surface this issue requires be left unaffected. Filed as part of the voice_note issue instead of half-fixing it here.
```

## Coverage gaps — disclosed, not hidden

```
[ ] The following branches are verified by source reading only, never driven in a live browser: the in-flight `#job-voice-dump` progress branch (static/rack.js:4720), the `failed` dead-end (:4726), the `cancelled` dead-end (:4730), the zero-items dead-end (:4739), and the `rerun-voice-dump` handler (:5177). Each requires seeding a job in a non-completed status and, for rerun, a live worker; the finalized branch (:4744) and `open-dumpnotes` (:5228) ARE browser-covered by test 7. Flagging so this is not mistaken for full runtime coverage of every state.
[ ] Server-side enforcement of the type enum is still absent: `finalize_voice_dump` does `item.get("type", "general")` with no check against NOTE_TYPES. AGENTS.md Complement Rule 2 says a rule that lives only in the client does not exist. The dropdown is client-side only. Out of this issue's frontend-only file scope; included in the backend follow-up issue. Mitigated defensively on the client: an unknown stored type is offered as an extra `<option>` (static/rack.js:4768) so it round-trips instead of being silently rewritten to the first entry.
[ ] `toggle-kind`'s 3-state cycle (static/rack.js:5148) still cannot reach `voice_dump`; the transcribe-time mode picker is the only way to create one. Pre-existing, predates this issue, left alone. Noted in `investigation.md` §4 and filed as a follow-up.
```

## Environment changes made during verification (not repo changes)

```
[decision] Playwright and Chromium were installed into the shared `C:\Claude\WhisperDeck\.venv` (`pip install -r requirements-browser.txt`, `python -m playwright install chromium`) so the browser tier could actually run. `requirements.txt` is untouched and no repo file records this, so CI is unaffected. Machine state only.
```

## Pre-Phase-4 gates

```
[x] Bundle freshness proved: copied static/rack.js + batch_aggregate.js + dump_review.js into a temp DIRECTORY, ran the corrected `build:js` command with the real `--outfile=static/rack.min.js` basename, and byte-compared — both `static/rack.min.js` and `static/rack.min.js.map` came out IDENTICAL. Done this way because `scripts/verify_self_audit.py` rewrites `--outfile` to a temp filename (scripts/verify_self_audit.py:106), and with `--sourcemap` esbuild derives the `sourceMappingURL` comment from that name, so its byte-diff can never match any sourcemapped bundle. Reported with a recommended fix in `wrong-directions.md` item 7.
[x] Main repo checkout clean: `git -C C:\Claude\WhisperDeck diff --stat` shows only the three files that were already modified at session start (`.claude/commands/issue-claude.md`, `.claude/issue-runner-prompt.md`, `.gitignore`) — no code leaked out of the worktree despite Serena MCP being activated on the main checkout rather than the worktree (`wrong-directions.md` item 8). `.omo/` is gitignored, so the run artifacts do not dirty the tree.
[x] All four self-report files exist: investigation.md, self-audit.md, wrong-directions.md, token-usage.md.
```

## Post-PR review pass (advisor, after PR #298 was opened)

Two questions were raised against the shipped code. One was a real gap and is fixed in commit `eff60f2`.

```
[x] FIXED — `dumpReview` survived logout. `resetDeckState()` (static/rack.js:790-832, called by `showLogin()` which `logout()` calls) clears every other piece of per-account client state — `detailData`, `seedClips`, `bankListCache`, `expandedVoice`, `_jobCache` — for the reasons established by issue #54's state-leak fix. The new `dumpReview` global holds user-authored, not-yet-saved draft edits and was NOT in that list, so it survived a logout: after re-login the tab could present unsaved edits as if they were the stored draft. Fixed by adding `dumpReview = null;` beside `detailData = null;`, confirmed at static/rack.js:829.
    A cross-account read was not reachable in practice — `dumpReviewKey` includes the transcript and job ids, and `GET /api/transcripts/{id}` 404s for a transcript the caller does not own, so a second user could not populate a colliding key. Fixed anyway rather than relying on that, because the repo has prior art for exactly this leak class (#54) and the established pattern is that per-account client state is cleared in `resetDeckState`.
[x] CHECKED, no change needed — whether a persisted `discarded: true` on a draft item affects any other consumer. `save_voice_dump_draft` stores the flag verbatim into `result_json["items"]`, so it is now durable on the draft. Grepped `app.py` and `services/` for reads of `result_json["items"]` / `result_json.get("items")`: there are none. The only consumers of a voice_dump job's `result_json` are `save_voice_dump_draft` (writes it) and the kind-agnostic `GET /runs/{kind}` route (returns it wholesale to the Dump Review tab, which is what filters on `discarded`). `finalize_voice_dump` reads the request body, not the stored job. No board or backend path counts or displays unfiltered draft items, so the flag is inert outside the review tab.
[x] DOCUMENTED — the new e2e file's tests 2 through 5b compound state through a module-scoped fixture, so it requires in-order execution. Added an explicit "do NOT enable test shuffling (e.g. pytest-randomly) for it" line to the module docstring so the constraint is discoverable to whoever adds shuffling, rather than surfacing as a confusing failure in test 5b's `"1 note"` assertion.
```
