# Self-audit, issue #109

> **Revision 2, after the independent audit of PR #336 (gpt-5.6-luna) returned BLOCK,
> and after rebasing onto a master that had moved 6 commits.** The audit was right.
> See "Response to the independent audit" at the bottom for what changed. Test counts
> in the older sections below were re-run and are restated there.

Branch `worktree-issue-109-voiceid-fallback`. Every `[x]` below was re-confirmed by opening the
cited file at the cited line, not from memory of what I intended to write.

**This workflow runs no independent-model audit pass.** Everything below is self-review, plus one
Sonnet verification agent that ran under my own instructions (which is not independent review).
Independent review happens separately via opencode's `/audit-pr` after this PR is opened. Do not
read a clean self-audit as a completed review.

## Promises made in investigation.md

- [x] `_extract_embedding()`'s silent MFCC substitution is now detectable by callers, via
  `_MFCC_MODEL_ID` and `_is_degraded_model()` at `services/voice_id.py:28` and `services/voice_id.py:125`
- [x] The librosa-only install is not treated as degraded (MFCC is its primary model) — the
  `_is_degraded_model` check at `services/voice_id.py:125`, derived from `_BACKEND_MODEL_IDS`;
  pinned by `test_is_degraded_model_depends_on_the_selected_backend`, `tests/test_voice_id.py:846`
- [x] `identify()` keeps its `list[dict]` contract, so `/api/voices/identify` still serializes
  `"matches": []` and not `null` — thin wrapper `identify` at `services/voice_id.py:346`, asserted at
  `tests/test_voice_id.py:890`
- [x] The three causes of an empty match list are now distinguishable — `identify_detailed()` at
  `services/voice_id.py:353` returns `degraded`, `compared`, `skipped_model_mismatch`, `warning`
- [x] The warning text is phrased in exactly one place so the route and the job cannot drift —
  `_identify_warning()` at `services/voice_id.py:422`
- [x] The enroll-side mirror bug (investigation.md section 5c: a profile silently persisted on the
  MFCC fallback while the roster is on speechbrain, permanently unmatchable) is closed —
  `_ensure_not_orphan_model()` at `services/voice_id.py:147`, called from `enroll()` at
  `services/voice_id.py:217` and `add_clip()` at `services/voice_id.py:261`
- [x] The enroll guard runs before the `VoiceProfile` row is created, so a refusal leaves no orphan
  row — `_ensure_not_orphan_model` at `services/voice_id.py:217` sits above the profile creation;
  asserted at `tests/test_voice_id.py:928`
- [x] The voice_match job reports degradation instead of completing clean — `degraded` /
  `unmatchable` counters at `services/llm_jobs.py:735` and `services/llm_jobs.py:736`, incremented at
  `services/llm_jobs.py:780` and `services/llm_jobs.py:783`, folded into `error` at
  `services/llm_jobs.py:818`
- [x] The existing skip-count wording is byte-identical, so the pre-existing assertion on it still
  holds — `services/llm_jobs.py:807`, still `f"{skipped} segment(s) skipped (extraction/embedding failed)"`
- [x] Every entry point that stores a clip through the fallback surfaces the warning (the Complement
  Rule; four routes, not just the one the issue named) — `/api/voices/enroll` at `app.py:3361`,
  `/api/voices/{profile_id}/clips` at `app.py:3441`, enroll-speaker-from-transcript at
  `app.py:2471`, `/api/voices/identify` warning at `app.py:3406`
- [x] The frontend actually renders it — `toastVoiceWarning()` at `static/rack.js:214`, called at
  `static/rack.js:4175`, `static/rack.js:5746`, `static/rack.js:5785`; identify modal appends
  `r.warning` at `static/rack.js:5856`
- [x] `static/rack.min.js` rebuilt from the edited `static/rack.js` with the `package.json`
  `build:js` command

## Issue acceptance criteria, walked one by one

The issue states no numbered acceptance criteria; it names a symptom and two candidate fixes.

- [x] Symptom "Job completes successfully with 0 relabeled segments, no error, no warning" — no
  longer possible when the probe degraded: `job.error` is non-null and names MFCC.
  `test_voice_match_reports_a_degraded_probe_in_job_error`, `tests/test_voice_match_job.py:547`
- [x] Symptom "zero indication that the embedding backend silently degraded" — indicated on the
  Queue screen (`job.error` renders red at `static/rack.js:3479`/`3509`, an existing path), in the
  identify modal (`static/rack.js:5856`), and as a toast on every enrollment route
- [ ] Proposed fix (a), "propagate the fallback as a warning to the job `result_json`" — NOT
  delivered as literally worded. `result_json` is absent from `serialize_llm_job()`
  (`services/llm_jobs.py:48-70`) and `voice_match` is not in the `/runs/{kind}` allowlist
  (`app.py:2775`), so it would be unreadable data. The warning goes to `job.error` instead, which
  is the codebase's existing rendered channel for "completed but degraded". See wrong-directions.md #2.
- [ ] Proposed fix (b), "don't fallback during `identify()`, return `None`" — NOT delivered as
  literally worded. `identify()` never returned `None`, and deleting the fallback would break
  librosa-only installs outright. Delivered the intent (distinguish degraded from no-match) without
  the type change. See wrong-directions.md #1.

## Decisions the issue did not ask for

- [decision] Fixed the enroll/add_clip side too, not just `identify()` — not specified by the issue,
  because `_extract_embedding()` has three callers and investigation.md section 5c confirmed the
  first clip on a fresh profile can silently persist on MFCC, producing a profile voice match can
  never find. Fixing only the read side would have left the write side generating new instances of
  the same bug.
- [decision] `_ensure_not_orphan_model()` refuses only when the roster already holds a *different*
  embedding model, rather than refusing every degraded clip — not specified by the issue, because a
  speechbrain install whose every clip degrades stays internally consistent and matches fine today;
  a blanket refusal would break that working configuration. Pinned in both directions by
  `test_enroll_rejects_a_degraded_clip_when_the_roster_uses_another_model`
  (`tests/test_voice_id.py:928`) and `test_enroll_allows_a_fallback_clip_when_the_roster_is_empty`
  (`tests/test_voice_id.py:946`).
- [decision] Did not fix the stale `_last_backend_error` singleton state found in the sibling sweep
  — not specified by the issue, and it is already filed as issue #110, so fixing it here would
  collide with that work.
- [decision] Did not fix `_finish()`'s cancelled-only guard (`services/llm_jobs.py:341`) found by
  the Phase 1.5 completion-race check — not specified by the issue, not reachable for voice_match
  before or after this change. Reported in wrong-directions.md #5; it deserves its own issue
  because the `correction` branch carries the live version of the shape.
- [decision] Used toast type `'info'` rather than `'error'` for the enrollment warning — not
  specified by the issue, because the operation genuinely succeeded and `toast()`
  (`static/rack.js:201`) has no warn variant; styling it as an error would misreport the outcome.

## Mutation checks

Every mutation was run for real in a detached scratch worktree seeded with the fixed code, not
reasoned about. Each box pastes the observed runner output: the unmutated run, then the same
selection with the named function's body replaced by the trivial constant of its declared return.

- [x] `test_is_degraded_model_depends_on_the_selected_backend` — mutation check: `_is_degraded_model`
  body replaced by `return False`, and separately by `return True`, so it pins the backend
  dependency rather than one constant
  $ python -m pytest tests/test_voice_id.py -q -p no:warnings -k test_is_degraded_model_depends_on_the_selected_backend
  1 passed, 46 deselected in 0.14s
  # mutated: _is_degraded_model -> return False
  1 failed, 46 deselected in 0.79s
  # mutated: _is_degraded_model -> return True
  1 failed, 46 deselected in 0.67s
  # restored from the pristine copy after each mutation
- [x] `test_degraded_model_warning_only_fires_for_a_real_degradation` — mutation check:
  `degraded_model_warning` body replaced by `return None`, the trivial constant of its declared
  `Optional[str]` return
  $ python -m pytest tests/test_voice_id.py -q -p no:warnings -k test_degraded_model_warning_only_fires_for_a_real_degradation
  1 passed, 46 deselected in 0.15s
  # mutated: degraded_model_warning -> return None
  1 failed, 46 deselected in 0.65s
  # restored from the pristine copy after each mutation
- [x] `test_identify_detailed_warns_when_a_fallback_probe_cannot_reach_the_roster` — mutation check:
  `_identify_warning` replaced by `return None`, and separately `identify_detailed` replaced by a
  constant neutral outcome dict, the trivial constant of its declared `dict` return
  $ python -m pytest tests/test_voice_id.py -q -p no:warnings -k test_identify_detailed_warns_when_a_fallback_probe_cannot_reach_the_roster
  1 passed, 46 deselected in 0.23s
  # mutated: _identify_warning -> return None. The selection also covers the extraction-failure
  # test, which survives because that warning is set directly in identify_detailed, not via the helper
  1 failed, 1 passed, 45 deselected in 0.81s
  # mutated: identify_detailed -> constant neutral outcome dict, selection -k identify_detailed
  3 failed, 39 deselected in 1.09s
  # restored from the pristine copy after each mutation
- [x] `test_enroll_rejects_a_degraded_clip_when_the_roster_uses_another_model` and
  `test_add_clip_rejects_a_degraded_first_clip_on_an_empty_profile` — mutation check:
  `_ensure_not_orphan_model` replaced by a no-op `return None`, the trivial constant of its declared
  `None` return
  $ python -m pytest tests/test_voice_id.py -q -p no:warnings -k "test_enroll_rejects_a_degraded_clip_when_the_roster_uses_another_model or test_add_clip_rejects_a_degraded_first_clip_on_an_empty_profile"
  2 passed, 45 deselected in 0.31s
  # mutated: _ensure_not_orphan_model -> no-op, selection widened to include the route-level pair
  # FAILED tests/test_voice_id.py::test_enroll_rejects_a_degraded_clip_when_the_roster_uses_another_model
  # FAILED tests/test_voice_id.py::test_add_clip_rejects_a_degraded_first_clip_on_an_empty_profile
  3 failed, 4 passed, 58 deselected in 2.32s
  # restored from the pristine copy after each mutation
- [x] `test_enroll_speaker_accepts_a_fallback_clip_for_a_brand_new_name` and
  `test_enroll_speaker_still_refuses_a_fallback_clip_against_an_enrolled_roster` — the audit's
  blocking scenario and its complement. Mutation check: restoring the buggy
  `VoiceProfile.embedding.isnot(None)` SQL filter, which is exactly what the audit caught
  $ python -m pytest tests/test_speaker_naming.py -q -p no:warnings -k fallback_clip
  2 passed, 16 deselected in 1.11s
  # mutated: _ensure_not_orphan_model -> SQL embedding.isnot(None) filter (the pre-audit bug)
  # FAILED tests/test_speaker_naming.py::test_enroll_speaker_accepts_a_fallback_clip_for_a_brand_new_name
  1 failed, 1 passed, 16 deselected in 1.77s
  # the refuse-test keeps passing under that mutation, so the pair distinguishes the two states
  # rather than both asserting one. Restored from the pristine copy afterwards
- [x] `test_voice_match_reports_a_degraded_probe_in_job_error` — mutation check: the job's error
  assembly at `services/llm_jobs.py:818` replaced by `error = None`
  $ python -m pytest tests/test_voice_match_job.py -q -p no:warnings -k test_voice_match_reports_a_degraded_probe_in_job_error
  1 passed, 17 deselected in 0.27s
  # mutated: error = "; ".join(notes) if notes else None  ->  error = None
  1 failed, 17 deselected in 0.91s
  # restored from the pristine copy after each mutation
- [x] `test_voice_match_stays_error_free_when_the_probe_model_matches`
  (`tests/test_voice_match_job.py:580`) — the control. It passes under every mutation above, so a
  `1 failed` line there means a pinned behavior rather than a broken import or a collection error
  $ python -m pytest tests/test_voice_match_job.py -q -p no:warnings -k test_voice_match_stays_error_free_when_the_probe_model_matches
  1 passed, 17 deselected in 0.25s
  # under the job error-assembly mutation above it stayed green while the degraded test went
  # 1 failed, which is the point of keeping it

After every mutation the file was restored from its pristine copy, the sandbox worktree was
removed, and the four affected test files were re-run in the real worktree: `91 passed, 1 skipped`.
The full-suite result is in the Test runs section below.

## Red-green

- [x] `test_voice_match_reports_a_degraded_probe_in_job_error` reproduces the reported symptom
  against unmodified HEAD code: run in a detached HEAD worktree it failed at
  `assert job.error is not None` -> `assert None is not None`, with the job `completed` and the
  segment unrelabeled. Exactly the issue's "Job completes successfully with 0 relabeled segments,
  no error". Its control test passed on HEAD in the same run.
- [x] The same test passes against the fixed code.
- The symptom lives at the job/service layer, so red-green was done at that layer. No browser was
  needed for it.

## Test runs

- [x] FULL Python suite, run by me in the worktree after the last edit:
  `835 passed, 1 skipped in 256.12s` (`pytest tests -q -p no:warnings --ignore=tests/e2e`)
- [x] JavaScript suite: `npm run test:js` -> `25 pass, 0 fail`
- [x] Live check of the changed API surface (FastAPI `TestClient`, real decodable WAV):
  `/api/voices/enroll` returned the new `warning` key; `/api/voices/identify` returned
  `probe_model`, `degraded`, `skipped_model_mismatch`, `warning`, with `matches` still a list.
  The check ran on a librosa-only backend, so `warning` was `None` there, which is the correct
  non-degraded result; the degraded branch is covered by the unit and job tests above.
- [x] Bundle freshness: re-running the esbuild command produced no further change to
  `static/rack.min.js` / `.map`.
- [x] Toolchain match proven rather than assumed: rebuilding HEAD's *unmodified* `static/rack.js`
  with the same command reproduced the committed `static/rack.min.js` byte-for-byte (`cmp` clean).

## Complement Rule sweep

- [x] All three callers of `_extract_embedding()` accounted for: `enroll()`
  (`services/voice_id.py:151`), `add_clip()` (`services/voice_id.py:195`), `identify()` via
  `identify_detailed()` (`services/voice_id.py:283`)
- [x] Both production callers of `identify()` accounted for: `app.py:3385` and
  `services/llm_jobs.py:736`
- [x] All four routes that can store or probe a clip surface the warning (listed above)
- [ ] **Missed on my first pass, caught by the full suite:** `tests/test_relabel_undo.py:215` also
  stubbed the old `identify`, so it fell through to the real MFCC path once the job switched to
  `identify_detailed`. Fixed (`tests/test_relabel_undo.py:196-215`) and re-verified by me by
  reading the diff, not just by the green run. Recording this as a `[ ]` rather than hiding it:
  my sibling sweep covered the file I was editing and the production callers, but not every test
  that mocks the changed method. A grep for the old method name across `tests/` belongs in the
  sweep, not just in `services/` and `app.py`.
- [x] Post-fix grep for `voice_id_service.identify` (exact, non-`_detailed`) across `*.py`: no
  remaining hits. The four `svc.identify(...)` calls left in `tests/test_voice_id.py:387-442` are
  deliberate direct calls exercising the preserved wrapper contract.

## Pre-Phase-4 checks

- [x] Main repo checkout clean apart from run artifacts: `git -C C:\Claude\WhisperDeck diff --stat`
  shows no tracked changes (the `.omo/runs/` files and `.skill-observations/` are untracked or
  gitignored)
- [x] All four self-report files present in
  `.omo/runs/issue-109/worktree-issue-109-voiceid-fallback/`: `investigation.md`, `self-audit.md`,
  `wrong-directions.md`, `token-usage.md`
- [x] `python scripts/verify_self_audit.py .omo/runs/issue-109/worktree-issue-109-voiceid-fallback/self-audit.md`
  — ran it from the main checkout before Phase 4. Its `file:line` citation check passed clean: zero
  citation findings across every citation in this file. Its build check reported two blocking
  findings, both environmental, not defects in this change:

  ```
  - BUILD [build:js]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
  - BUILD [build:css]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
  ```

  The script shells out to bare `esbuild`, which requires a local `node_modules/.bin` on PATH.
  Neither the worktree nor the main checkout has `node_modules` (gitignored, never installed on this
  machine), so the checker cannot rebuild either bundle here. This is a pre-existing condition of
  the environment, not something this change introduced — `build:css` fails identically and this
  change does not touch `static/rack.css` at all.

  I did not let that stand as an unverified box. The same check was performed by hand with a
  stronger test than the script's: `npx --yes esbuild@0.25` (the version pinned in `package.json`)
  rebuilt `static/rack.min.js` from the current `static/rack.js` with no resulting diff, and
  rebuilding HEAD's *unmodified* `static/rack.js` with that command reproduced the committed
  `static/rack.min.js` byte-for-byte under `cmp`, proving the toolchain matches the one the
  committed bundle came from. Recommended script fix is in wrong-directions.md #3.

  **Re-run after the audit response was written**, against a master that now includes #332 and
  #338 (which tightened this checker): `2 blocking finding(s), 0 advisory`, both of them the same
  `'esbuild' is not recognized` BUILD findings, with zero citation findings and zero mutation
  findings. #338 added a rule that a mutation claim must paste an observed transcript rather than
  state an outcome, and the first run under it reported four `MUTATION CLAIM NOT EVIDENCED`
  findings against this file. That was a fair hit: the claims were true but written as conclusions.
  Every mutation box above now carries the runner invocation, the unmutated pass count and the
  mutated failure count as observed. One line in my first draft of those boxes quoted a
  control-test run I had not actually performed; I ran it before writing it down
  (`1 passed, 17 deselected in 0.25s`) rather than leave an invented number in a document whose
  whole purpose is to be checkable.

---

# Response to the independent audit (gpt-5.6-luna, verdict BLOCK)

## Blocking finding: transcript enrollment rejected its own placeholder

**Confirmed, and the audit's diagnosis was exactly right.** `app.py`'s
enroll-from-transcript route creates the `VoiceProfile` row stamped with
`voice_id_service.backend_name` *before* any extraction happens, so on an otherwise
empty roster the only row carrying a model id was the one being enrolled into. My
orphan guard counted it, and a fallback clip collided with its own placeholder. The
route returned 400 on a flow that should have succeeded.

- [x] Fixed by skipping profiles that have no embedding, at `services/voice_id.py:147`.
  A row with no embedding has no clips, so its `embedding_model` is a placeholder
  rather than a fact.
- [decision] Chose the audit's option "exclude the target profile" in its more general
  form (skip *every* embedding-less profile) rather than "avoid pre-seeding the model".
  Not pre-seeding would change a route behavior this issue does not own, and the same
  collision would still be reachable through `enroll()` by name against a placeholder
  row. Skipping embedding-less profiles closes both doors with one predicate.
- [x] **A second, deeper defect surfaced while fixing it, which the audit could not have
  seen from the diff.** My first attempt used `VoiceProfile.embedding.isnot(None)` as a
  SQL filter and the new regression test still failed. `embedding` is a `Column(JSON)`,
  and SQLAlchemy's JSON type persists Python `None` as JSON `null` rather than SQL NULL,
  so `IS NOT NULL` is true for placeholder rows. The filter now runs in Python
  (`services/voice_id.py:170`), which additionally makes it the *same expression*
  `identify()` uses (`if profile.embedding is None: continue`), so the guard and the
  matcher cannot disagree.
- [x] Regression test the audit asked for:
  `test_enroll_speaker_accepts_a_fallback_clip_for_a_brand_new_name`
  (`tests/test_speaker_naming.py:187`) posts transcript enrollment for a new name under
  MFCC fallback and asserts 200, a non-null `warning`, the persisted profile's
  `embedding_model`, and exactly one persisted `VoiceClip`.
- [x] Paired complement so the fix cannot be a no-op:
  `test_enroll_speaker_still_refuses_a_fallback_clip_against_an_enrolled_roster`
  (`tests/test_speaker_naming.py:219`) still expects 400 when a genuinely enrolled
  speechbrain profile is present.
- [x] Red-green on the audit's exact condition: restoring the SQL-filter version in a
  detached sandbox worktree makes the accept-test fail and the refuse-test pass. Mutating
  `_ensure_not_orphan_model` to a no-op kills 3 tests including the refuse-test, while the
  accept-test correctly survives.

## Should-fix: validation failures returned 500

- [x] Fixed at `app.py:3363`. `/api/voices/enroll` now catches `ValueError` and returns
  400 with the message, cleaning up the uploaded file first, matching what the add-clip
  route already did. The catch-all 500 remains for genuine faults.
- [x] Verified live in the browser: the refused enrollment returns `400 (Bad Request)`
  from `/api/voices/enroll`, recorded in the captured console log.

## Nit: broad exception could misreport a malformed outcome as an extraction skip

- [x] Fixed at `services/llm_jobs.py:750`. The per-segment `try` now covers only the
  extraction and identify calls; `outcome` is initialized to `None` before it and read
  after it, so a missing key would raise rather than being absorbed into `skipped`.

## Nit: frontend warning behavior was not browser-tested

**Fair, and now closed with a real browser run, not an assertion that it should work.**
The machine only has librosa installed, where MFCC is the primary model and nothing is
degraded, so a stock server can never render this state. I ran the app on an isolated
`WHISPERDECK_DATA_DIR` with `_backend` forced to `speechbrain` and `_extract_embedding`
forced onto the MFCC fallback, then drove it through Playwright:

- [x] Enrolled a speaker on an empty roster: succeeded, and the roster row reads
  `1 clip · MFCC fingerprint (librosa)`. This is the audit's blocking scenario, working.
- [x] Ran identify against that MFCC profile: matched at 100%, and the result box carried
  "This audio could not be processed by the speechbrain/spkrec-ecapa-voxceleb backend, so
  a lower-accuracy MFCC fingerprint was used instead. Matching is limited to profiles
  enrolled with the same fallback."
- [x] Replaced the roster with a speechbrain-model profile and re-ran identify. This is
  the literal issue #109 scenario. Before this PR the UI said only "No match above 65% /
  1 profiles checked". It now adds: "... 1 enrolled profile(s) use a different embedding
  model and were skipped, so no match was possible."
- [x] Attempted to enroll a fallback clip against that speechbrain roster: refused with
  400, and the roster count stayed at 1, so no orphan profile was left behind.
- [ ] The enroll-side toast itself was not captured in a snapshot: `toast()` removes the
  node after 4.2s and each MCP round trip is slower than that. What was verified is the
  server response and the resulting roster state, plus the same `warning` field rendering
  through the identify path. Recording this as not-verified rather than claiming it.
- [x] Teardown: browser closed, uvicorn on port 9787 killed and confirmed `DEAD` by port
  check, isolated data dir and all scratch files removed, worktree `git status` clean.

## Rebase onto current master

The branch was 6 commits behind and `CONFLICTING` by the time the audit came back.
Rebased onto `85eb576`. Conflicts and how they were resolved:

- [x] #327 had introduced `_MFCC_MODEL_ID` and a `_BACKEND_MODEL_IDS` registry,
  duplicating the `MFCC_MODEL_ID` / `_STRONG_BACKENDS` constants I had added. Dropped mine
  and derived `_is_degraded_model` from the registry (`services/voice_id.py:125`), so
  adding a backend cannot make the two disagree, which is what that registry exists for.
- [x] #327's pre-flight guard (`compatible_embedding_models()`) refuses jobs whose roster
  no live backend can match, which my degraded-probe job test would have tripped. That
  test now enrolls under speechbrain so the job reaches the segment loop and the degraded
  probe is what stops the match, not the pre-flight.
- [x] **Five more stale mocks, same class as the one the first review caught.** #327 and
  #331 added tests patching `voice_id_service.identify`, which the job no longer calls.
  All converted to `identify_detailed` via the shared `_outcome()` helper. Post-rebase
  grep for `voice_id_service.identify` (exact, non-`_detailed`) across `*.py`: no hits.

## Test runs after all of the above

- [x] FULL Python suite: **865 passed, 1 skipped** (`pytest tests -q -p no:warnings
  --ignore=tests/e2e`). Supersedes the 835 figure in the section above, which predates the
  rebase.
- [x] JavaScript suite: `npm run test:js` gives **25 pass, 0 fail**.
- [x] Bundle rebuilt after the rebase; `git status --porcelain static/` clean afterwards,
  so the committed bundle matches a fresh build of the current `rack.js`.
- [x] Mutation checks re-run after the rebase, since `_is_degraded_model` changed shape:
  `_is_degraded_model` to `False` KILLED, to `True` KILLED, `degraded_model_warning` to
  `None` KILLED, `_identify_warning` to `None` KILLED, job error assembly to `None`
  KILLED, `_ensure_not_orphan_model` to a no-op KILLED.

## On the audit's honesty check finding "artifact absent"

Both audits reported `self-audit.md [x] lines verified: 0/0. No self-report artifacts
found.` That is correct and expected, not a missing file: `.gitignore:55` ignores
`.omo/*`, so run artifacts are deliberately never committed and cannot appear in the PR.
They live at
`C:\Claude\WhisperDeck\.omo\runs\issue-109\worktree-issue-109-voiceid-fallback\`.
A reviewer wanting to verify `[x]` lines has to read them from that path. Added to the PR
body so the next audit can find them.
