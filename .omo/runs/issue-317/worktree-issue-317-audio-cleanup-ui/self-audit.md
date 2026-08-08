# self-audit.md — issue #317, branch `worktree-issue-317-audio-cleanup-ui`

Issue: "Audio cleanup stage has no UI: the entire #270 backend is unreachable
from the app". Every box below was re-confirmed by opening the file at the cited
line, not from memory of what was intended.

## Scope of independent review

**This workflow runs no independent-model audit pass.** Opencode's `/issue` has
one (Oracle, its Phase 3.75); `/issue-claude` does not. Everything below is
self-review by the same model that wrote the code, with one exception: the
Phase 1.5 completion-race question went to a Fable agent (see below).
Independent review happens separately, via opencode's `/audit-pr` after this PR
is open. Self-audit-only is not a substitute for that.

## Promises from investigation.md

### The panel itself

- [x] All eleven UI-exposed `cleanup_*` keys have a control, driven by one
  registry — `CLEANUP_FIELDS` at `static/rack.js:5995`, eleven entries.
- [x] Render reuses the existing `.tog` boolean pattern rather than inventing a
  control — `cleanupFieldRow` at `static/rack.js:6016` emits
  `.tog-plate`/`.tog-track`/`.tog-paddle`, and the numeric branch reuses the
  labeled-input shape from the audio-prep card.
- [x] Toggles rely on `.tog.on .tog-paddle { top: 1px }` at
  `static/rack.css:482` instead of the inline-`style` + JS idiom the audio-prep
  card uses — confirmed live: paddle computed `top` was `1px` on and `13px`
  off.
- [x] Card is rendered inside `loadSettingsPage()`, prefilled from
  `GET /api/settings` — `static/rack.js:6107` (caption) and `:6110` (the
  `CLEANUP_FIELDS.map` call), reading the same `settings` object the existing
  cards read.
- [x] Toggle wiring is one loop over the registry, not five hand-written
  handlers — `static/rack.js:6314`.
- [x] Save handler PUTs every registry key — `$('cleanup-save')` at
  `static/rack.js:6318`. Verified live: all eleven keys landed in
  `GET /api/settings` after one click.
- [x] No backend change was needed to accept the keys, as investigation.md
  predicted — `services/settings.py:143` already allowlists them via
  `DEFAULT_SETTINGS` membership. `services/settings.py` is unmodified in this
  branch.

### Numeric coercion

- [x] Save does NOT use the audio-prep card's `parseInt(v, 10) || default`
  shape, which maps a legitimate `0` to the default — `static/rack.js:6318`
  handler uses `Number.isFinite(n) ? Math.min(max, Math.max(min, n)) :
  settings[key]`. Verified live: `cleanup_vad_threshold` set to `0` persisted
  as `0`, not `0.5`.
- [x] Out-of-range input is clamped, not stored raw — verified live:
  `cleanup_hallu_rep_window` typed as `99` persisted as `20`.
- [x] Unparseable input leaves the setting at its CURRENT stored value rather
  than resetting it — verified live: `cleanup_hallu_no_speech_cutoff` typed as
  `abc` persisted as `0.6`.
  - Honest note on how this box got its `[x]`: the first implementation used
    the `settings` object fetched at page load as the fallback and never
    updated it, so a second save in the same page session would have reverted
    to the page-load value instead of the value the first save stored. The
    single live drive at the time saved once and never hit it. Caught at review
    and fixed with `Object.assign(settings, body)` after a successful PUT, then
    re-verified live with the two-save sequence specifically: page loaded with
    `cleanup_vad_threshold` 0.5, saved 0.3, then saved unparseable input, and
    the stored value stayed 0.3 rather than snapping back to 0.5.
- [x] Clamped values are written back into the inputs after a successful save,
  so the box never shows a value the server did not receive — verified live:
  the box read `20` after `99` was submitted.
- [x] `cleanup_hallu_rep_window` floor is `2`, not `0` — `static/rack.js:5995`
  registry, because `filter_hallucinations` returns segments untouched when
  `rep_window < 2` (`services/audio_cleanup.py:141`), so a lower value would
  silently disable a filter whose toggle still read as on.

### The mirror-path fix (`services/queue.py`)

- [x] `hallu_rep_window`, `hallu_logprob_cutoff`, `hallu_no_speech_cutoff` are
  read from user settings — `services/queue.py:439-441`, in the existing
  pre-`try` read-only settings block, so nothing new happens after the file's
  documented single await point.
- [x] Both branches initialize to the same defaults `app.py:1375-1377` uses, so
  the two paths read identically side by side — `services/queue.py:426-428`.
- [x] The call site passes them instead of the hardcoded `3` / `-2.0` / `0.6` —
  `services/queue.py:465-467`.
- [x] Verified the mirror pair does not diverge anywhere else in the chain
  before editing: `app.py` recomputes `transcript.full_text` after filtering,
  and the chunked path reaches the same result because
  `merge_chunk_results` rebuilds `full_text` from the merged segment list at
  `services/queue.py:240`, and nothing reads `result_json["full_text"]`
  (repo-wide grep: only `app.py:2087`, an unrelated manual-edit route). So the
  three thresholds were the whole divergence.

## Acceptance criteria walk

Issue #317 states no numbered acceptance criteria. Its concrete claims,
one by one:

- [x] "No UI shipped ... none of the cleanup steps are reachable from the app" —
  now reachable. Eleven controls, verified end to end in a real browser.
- [x] "building it once closes the UI half of all four at once" (#236, #237,
  #238, #239) — the UI half of #236 (loudnorm/highpass/denoise), #237 (VAD) and
  #238 (hallucination filter) is delivered. #239's is NOT, deliberately, see the
  decision line below.
- [x] "Design question to settle first: global settings panel, or per-job
  advanced section?" — settled as the global settings panel, by the user, before
  any code was written. Reasoning recorded in the decision lines below.
- [ ] The individual remaining work #317 lists per child issue (#236's chunk
  silence retuning, #238's silent-deletion behavior decision, #239's download
  consent) — NOT delivered, and out of scope by the issue's own text, which
  lists them as remaining after this lands. This PR closes #317 only.

## Decisions the issue did not ask for

- [decision] Placement is the global settings panel, not a per-job section —
  the user chose this when asked. The backing reason: all twelve keys are
  per-user globals in `User.settings`, read by `app.py` and
  `services/queue.py`. A per-job section would need new request fields on
  `POST /api/transcribe` plus merge-over-settings plumbing in both the inline
  and chunked paths, which is a much larger change than the issue scoped.
- [decision] `cleanup_demucs_enabled` is deliberately NOT exposed — eleven of
  the twelve keys get controls. `cleanup_demucs` (`services/audio_cleanup.py:193`)
  is written and unit-tested but called from no production code, so a toggle
  for it would persist a value that changes nothing. That is a worse defect
  than a missing control. #239 owns wiring it up plus the consent flow for its
  multi-GB model download. User approved this narrowing. The exclusion is
  enforced and documented in `tests/test_settings_ui_coverage.py:34`, so it
  cannot be forgotten.
- [decision] Numeric bounds (the min/max in each `CLEANUP_FIELDS` entry) are
  mine, not the issue's — it specifies none. They are client-side only. A raw
  `PUT /api/settings` can still store an out-of-range value, because
  `services/settings.py` does no validation for any key. I deliberately did NOT
  add a server-side bounds layer: it was not in the scope the user approved, it
  would route every existing settings test through new coercion code, and
  clamp-versus-reject-versus-store is a real decision rather than an
  implementation detail. The failure modes degrade safely today (a bad
  `rep_window` no-ops per `services/audio_cleanup.py:141`; a bad
  `loudnorm_target` makes ffmpeg fail into the documented original-audio
  fallback). Worth a follow-up issue, and stated plainly rather than left
  implied.
- [decision] Fixed `services/queue.py`'s hardcoded thresholds in this PR —
  user-approved. Without it the three new hallucination dials would apply to
  small files and be silently ignored for chunked ones.
- [decision] Did not refactor the existing `audio-autocorrect` toggle onto the
  new registry, despite the duplication — it is coupled downstream
  (`static/rack.js` save handler reads its class and sets `S.autoCorrect`), and
  rewiring it buys nothing for this issue while adding a diff a reviewer has to
  verify is behavior-preserving. Verified live that it still works unchanged.
- [decision] Substituted a one-off Playwright MCP drive for a new
  `tests/e2e/` settings test — disclosed, not silent. No e2e test currently
  touches the settings page, so a permanent one means building
  login-plus-navigate scaffolding from scratch. The high-risk failure (panel
  invisible at runtime because of a stale bundle) is covered permanently by
  `tests/test_settings_ui_coverage.py:72`. The MCP drive covered the rest.

## Tests

- [x] `test_chunk_job_passes_user_hallucination_thresholds` —
  `tests/test_queue_hallu_settings.py:71`. Asserts `rep_window == 7`,
  `logprob_cutoff == -4.5`, `no_speech_cutoff == 0.25` with `==`, not
  membership. Mutation check: fails with the three threshold reads replaced by
  the previous hardcoded constants? **yes, verified by actually reverting them
  and running the test** (see red-green below), and fails if
  `filter_hallucinations` were never called (asserts `call_count == 1`).
- [x] `test_filtered_segments_are_dropped_from_the_chunk_result` —
  `tests/test_queue_hallu_settings.py:122`. Drives the real filter, not a spy,
  and asserts the segment list in `job.result_json`. Mutation check: fails with
  the threshold reads reverted to constants? **yes, verified.** Fails if the
  filter body were `return segments` unchanged (asserts `segments == []` in the
  drop case).
- [x] `test_chunk_job_falls_back_to_defaults_when_user_left_dials_alone` —
  `tests/test_queue_hallu_settings.py:88`. Honest note: this one passes both
  before and after the fix, because the old hardcoded values happened to equal
  the defaults. It is not a red-green test; it guards the fallback branch
  against future drift. Mutation check: fails if the initializers at
  `services/queue.py:426-428` were removed or changed? yes.
- [x] `test_chunk_job_skips_filter_when_disabled` —
  `tests/test_queue_hallu_settings.py:106`. Also passes pre-fix, stated for
  honesty. Mutation check: fails if the `hallu_enabled` guard were dropped so
  the filter always ran? yes (`spy.assert_not_called()`).
- [x] `test_cleanup_key_has_a_settings_control` —
  `tests/test_settings_ui_coverage.py:62`, parametrized over eleven keys.
  Mutation check: fails when a key is removed from the registry? **yes,
  verified by actually deleting the `cleanup_denoise_enabled` entry and running
  it — exactly one parametrization failed.** Also fails if
  `_cleanup_fields_block()` returned `""`.
- [x] `test_cleanup_key_reaches_the_committed_bundle` —
  `tests/test_settings_ui_coverage.py:72`, parametrized over eleven keys.
  Mutation check: fails on a stale bundle? **yes, verified against the real
  pre-change artifact — the committed `HEAD` `static/rack.min.js` contains zero
  occurrences of every cleanup key checked.**
- [x] `test_unexposed_cleanup_keys_really_are_absent` —
  `tests/test_settings_ui_coverage.py:83`. Fails if `cleanup_demucs_enabled`
  gains a control without the exclusion list being updated, and if the key is
  removed from `DEFAULT_SETTINGS` entirely.
- [x] `test_there_are_cleanup_keys_to_check` —
  `tests/test_settings_ui_coverage.py:55`. Guards the two parametrized tests
  against vacuously passing on an empty key set if the keys were ever renamed
  off the `cleanup_` prefix.

### Red-green

- [x] Reproduced the reported symptom at the layer it lives, against current
  code, before trusting the fix. Method: snapshotted `services/queue.py` to a
  scratch copy AFTER the real edits, applied the inverse of the one change
  (restored the hardcoded `rep_window=3, logprob_cutoff=-2.0,
  no_speech_cutoff=0.6` call), ran the file: **2 failed, 2 passed**. Restored
  from the snapshot, confirmed `git diff --stat` showed only the intended
  change and the fix was still present. No `git checkout` or `git stash` was
  used at any point.

### Full suite

- [x] Ran the FULL suite, not just the new files, before checking any
  test-related box: **823 passed, 22 deselected** (the deselected 22 are the
  `e2e` marker set). No failures, no new warnings.

### Browser tier

- [x] Live drive against a real server on a fresh port (13417) with an isolated
  `WHISPERDECK_DATA_DIR`, serving this worktree's rebuilt bundle. A fresh port
  was used deliberately: reusing one serves a stale bundle out of the app's own
  service worker cache.
- [x] Confirmed the SERVED `rack.min.js` contains the cleanup keys, not just
  the file on disk.
- [x] All eleven controls render with the correct `DEFAULT_SETTINGS` values on
  first load.
- [x] Real mouse click on `#cleanup-loudnorm` (Playwright `locator.click()`)
  flips it and moves the paddle; real mouse click on `#cleanup-save` persists.
- [x] Full round trip: eleven values set, saved, page reloaded, all eleven
  prefill from the persisted values.
- [x] Zero console errors across the whole session.
- [x] Sibling-card regression: the untouched audio-prep card still saves
  (`bitrate_kbps` 192, `auto_correct` flipped) and its save does not clobber
  the cleanup keys, confirming the `json_patch` merge holds across two cards.

## Phase 1.5: completion-race check

- [x] Ran it. Dispatched to a Fable agent (a genuinely different model, per the
  workflow), scoped to `_run_chunk_job` and `_finalize_if_done` in
  `services/queue.py`. Not skipped, not self-reviewed.
- [x] Result: it found a REAL pre-existing bug, unrelated to this change. The
  guard at `services/queue.py:503` tests only `"cancelled"`, so a transcript
  already finalized to `"partial"` gets re-finalized when the automatic retry
  pass flips a failed chunk back to pending. The side-effect block that follows
  re-fires a volley of `enqueue_auto_*` LLM jobs whose only dedupe covers
  pending/running jobs, so once the earlier jobs have finished it creates
  duplicate rows and duplicate paid provider calls on identical text, and
  re-wipes user relabel history. It also flagged that the obvious one-line
  guard swap would break the intended partial-to-completed upgrade.
- [decision] NOT fixed in this PR. It is untouched by this change, it is a
  state-machine correctness question with a non-obvious fix, and folding it into
  a settings-UI PR would make both harder to review. **Filed as issue #328**
  after re-verifying every line number against `master` (Fable's own citations
  were approximate and several were off by a few lines; `_finalize_if_done` is
  at `services/queue.py:486`, its two guards at `:503` and `:576`, the
  side-effect block at `:598`, and the retry-resurrection pass at `:736-737`).

## Pre-Phase-4 checks

- [x] Main repo checkout is clean apart from run artifacts — verified below at
  commit time.
- [x] All four self-report files exist: `investigation.md`, `self-audit.md`,
  `wrong-directions.md`, `token-usage.md`.
- [x] `scripts/verify_self_audit.py` run, before Phase 4, not after. Zero
  citation findings (0 advisory). It reported two build findings, both defects
  in the script itself rather than in this change, each proven rather than
  asserted, and both written up in `wrong-directions.md` section 5:
  - It could not run esbuild at all at first, because it puts the *worktree's*
    `node_modules/.bin` on PATH and a fresh worktree has none. Worked around by
    supplying the main checkout's.
  - With that fixed it reported `STALE BUILD ... committed=228805b,
    fresh=228808b`. The bundle is not stale. The script rebuilds into a
    `NamedTemporaryFile`, and `--sourcemap` makes esbuild write that temp name
    into the output as `//# sourceMappingURL=tmpab3d9x2z.js.map` (18 chars)
    versus the committed `//# sourceMappingURL=rack.min.js.map` (15) — the whole
    3-byte delta. Verified by reproducing that exact temp filename: byte
    identical once the `sourceMappingURL` comment is stripped, and
    `npm run build:js` reproduces the committed bundle at exactly 228805 bytes.
    This fires for any `--sourcemap` bundle on every run.
- [x] Bundle freshness confirmed independently of that broken check: a no-op
  rebuild BEFORE any source edit produced a byte-identical file (so the main
  checkout's esbuild version matches the one that built the committed bundle),
  `npm run build:js` after the change reproduces the committed bytes, the
  SERVED bundle was confirmed to contain the cleanup keys over HTTP, and
  `tests/test_settings_ui_coverage.py:72` asserts every exposed key is in the
  committed bundle.
