# self-audit.md — issue #112

Branch: `worktree-issue-112-voice-match-backend`. Every `[x]` below was re-confirmed by opening the cited file at the cited line after the final edit, not from memory. Full suite (`799 passed, 22 deselected`) was run before any test-related box was checked.

## Promises from investigation.md

[x] Add one registry so the model-id literals aren't hand-maintained in parallel lists — delivered, `_MFCC_MODEL_ID` at `services/voice_id.py:24` and `_BACKEND_MODEL_IDS` at `services/voice_id.py:30`, consumed by `backend_name` (`services/voice_id.py:75-76`), `_extract_embedding` (`services/voice_id.py:332`, `services/voice_id.py:337`), and `_mfcc_fallback` (`services/voice_id.py:345`).

[x] Add `compatible_embedding_models()` returning the set of model ids the current backend can produce — delivered at `services/voice_id.py:78-91`; returns `set()` for `"none"`, otherwise `{primary, _MFCC_MODEL_ID}`.

[x] Keep the "no profiles at all" guard and its exact message untouched — confirmed, `"No enrolled voices with clips — add a clip to a roster profile first"` is unchanged at `services/llm_jobs.py:703` and shows as a context line (no `+`/`-`) in `git diff`.

[x] Add the backend-compatibility guard before the per-segment ffmpeg loop — delivered at `services/llm_jobs.py:715-720`: `compatible = voice_id_service.compatible_embedding_models()` then `if not any(not p.embedding_model or p.embedding_model in compatible for p in enrolled):`.

[x] The guard mirrors `identify()`'s falsy-`embedding_model` wildcard, not just NULL — delivered, `not p.embedding_model` at `services/llm_jobs.py:716` mirrors `if profile.embedding_model and profile.embedding_model != probe_model` at `services/voice_id.py:264`. Tightened from an earlier `p.embedding_model is None` after the Phase 3 verification pass pointed out that `""` would have been treated as a wildcard by `identify()` but not by the guard (unreachable in practice, since no write path assigns `""`, but the asymmetry is now gone).

[x] New failure message names both sides of the mismatch — delivered, `stale` is built at `services/llm_jobs.py:717` and interpolated with `voice_id_service.backend_name` at `services/llm_jobs.py:719-721`.

[x] Cross-reference comments so the mirrored pair can't drift — delivered at `services/voice_id.py:259-263` (points at the llm_jobs pre-flight) and `services/llm_jobs.py:705-714` (points at `identify()`).

[x] Update the test helpers that hardcoded an incompatible `embedding_model` — delivered: `_enrolled_profile` in `tests/test_voice_match_job.py:29-56` (defaults to `voice_id_service.backend_name` via the `_CURRENT_BACKEND` sentinel) and `_enrolled_profile` in `tests/test_relabel_undo.py:161-171`. The `_extract_embedding` monkeypatch in `test_voice_match_runs_real_identify_through_executor` now returns `voice_id_service.backend_name` as the probe model (`tests/test_voice_match_job.py:123-125`).

[x] `tests/test_speaker_naming.py` needed no change — confirmed, its profiles use `"MFCC fingerprint (librosa)"` (`tests/test_speaker_naming.py:155`, `:192`, `:271`), which the superset accepts under any live backend. This is the case the superset exists to protect.

## Tests, with mutation checks

[x] `test_voice_match_fails_fast_when_enrolled_voices_use_a_different_backend` — `tests/test_voice_match_job.py:180`. Red-green confirmed: run against unmodified source before the guard existed, it failed with `AssertionError: assert 'completed' == 'failed'`, and the `fail_if_called` stub for `extract_clips_concat` was swallowed by the branch's per-segment `except Exception: skipped += 1`, which is exactly the wasted-work symptom the issue reports. Passes after the fix. Asserts `job.progress_done == 0` and that the transcript is untouched.
[decision] `test_voice_match_fails_fast_when_enrolled_voices_use_a_different_backend` mutation-check detail, recorded as a decision rather than a checked box because one half of the answer is "no": it DOES fail with the guard body removed (that is the red run cited above, which is the mutation that matters for a bug-fix regression test), but it does NOT fail with `compatible_embedding_models()` replaced by `return set()`, since an empty set still refuses the mismatched roster. That mutation is killed by `test_voice_match_proceeds_when_enrolled_voice_matches_current_backend` instead, which is why that positive test is its required partner. Disclosed rather than papered over.

[x] `test_voice_match_proceeds_when_enrolled_voice_matches_current_backend` — `tests/test_voice_match_job.py:219`. Mutation check: fails with `compatible_embedding_models()`'s body replaced by `return set()`? yes, verified by actually applying that mutation (6 tests failed, including this one) and reverting with the exact inverse edit.

[x] `test_voice_match_proceeds_for_legacy_profile_with_no_embedding_model` — `tests/test_voice_match_job.py:248`. Mutation check: fails with the wildcard clause dropped from the guard (`not any(p.embedding_model in compatible ...)`)? yes, verified by applying that mutation (this test was the single failure) and reverting.

[x] `test_compatible_embedding_models_covers_primary_and_mfcc_fallback` — `tests/test_voice_id.py:392`. Asserts all four backends with `==` on the exact set, not membership. Mutation check: fails with the method body replaced by `return set()`? yes (part of the 6-test kill above). Also fails for a body returning only `{primary}`, since three of the four assertions require the MFCC id.

[x] `test_identify_still_matches_legacy_profile_with_no_recorded_model` (pre-existing, repaired) — `tests/test_voice_id.py:435`. Mutation check: fails with the `profile.embedding_model and` short-circuit removed from `services/voice_id.py:264`? yes, now that the row actually stores NULL and the probe uses a different model id. Before the repair it did not, so it was vacuous.

[x] Full suite run before checking any test box — `799 passed, 22 deselected, 1 warning in 85.81s`, using `C:\Claude\whisperdesk\.venv\Scripts\python.exe -m pytest <worktree>\tests -q`. Independently re-run by the Phase 3 verification agent: `799 passed, 22 deselected, 1 warning in 77.29s`.

[x] Both temporary mutations reverted with the inverse of that one edit (never `git checkout` / `git stash`) — confirmed: `grep -rn "MUTATION-CHECK-TEMP"` returns nothing, and `git diff --stat` shows only the five intended files.

## Acceptance criteria

Issue #112 states no numbered acceptance criteria. Walked against its Problem / Impact / Proposed Fix:

[x] "It doesn't verify that the profile's `embedding_model` matches the current backend" — met, `services/llm_jobs.py:715-716`.
[x] "The job runs, extracts all segments (spawning ffmpeg per segment), and matches nothing, wasting CPU" — met, the guard returns before the segment loop; the mismatch test asserts `extract_clips_concat` is never called and `job.progress_done == 0`.
[x] "The user sees the job 'complete' with no relabeled segments and no explanation" — met, the job now fails with a message naming the enrolled model(s) and the current backend. It reaches the UI through the existing `job.error` channel (`serialize_llm_job`, `services/llm_jobs.py:57`), rendered at `static/rack.js:3473`, `:3502`, and toasted at `static/rack.js:912`; `humanizeJobError` (`static/rack.js:214-224`) matches neither of its two special-cased patterns, so the text passes through verbatim.
[x] "Filter by embedding_model in the early-exit check" (proposed fix, option 1) — met, but deliberately NOT as literally written; see `[decision]` below and `wrong-directions.md` item 2.
[ ] "or add a warning to the job result when no matches were found despite enrolled profiles existing" (proposed fix, option 2) — NOT delivered. The issue offers it as an alternative ("or"), and only option 1 addresses the stated Impact (wasted ffmpeg); a post-hoc warning fires after the CPU is already spent. See `[decision]` below.

## Decisions the issue did not ask for

[decision] Implemented the guard as a permissive superset (`compatible_embedding_models()` returns primary + MFCC fallback) rather than `embedding_model == backend_name` — not specified by the issue, because `_extract_embedding` (`services/voice_id.py:328-341`) degrades to MFCC when the primary backend is installed but throws at runtime, so an equality filter would hard-fail jobs whose profiles were enrolled during such a fallback. A false-negative refusal is worse than the wasted CPU being fixed.

[decision] One wasted-CPU case therefore survives by design: profiles tagged `"MFCC fingerprint (librosa)"` while the primary backend is installed and healthy will pass the guard and still match nothing. Excluded because blocking them requires knowing the probe model before extraction, which is impossible. The fix is partial on purpose.

[decision] The embedding-dimension skip (`services/voice_id.py:267-268`, `len(stored) != len(probe_embedding)`) is not pre-flighted — not specified by the issue, because the probe's length is unknown until a clip is extracted. A legacy NULL-model profile with a wrong-length vector still passes the guard and matches nothing.

[decision] Skipped option 2 of the proposed fix (a warning on zero matches) — not specified as required by the issue (it says "or"), because after the early exit the remaining zero-match cases are the two above, and surfacing them would need `identify()` to report per-profile skip reasons back to the job: a wider API change than this issue's scope. Noted as a follow-up rather than silently dropped.

[decision] Excluded `static/rack.js:5074-5079`, the "N enrolled voice(s) might match unlabeled speakers here" nudge that gates the "Match now" CTA on `voices.length` alone — not specified by the issue (which names only the backend check), and a client-side mirror would have to replicate the compatible *set*; a one-value comparison against `backend_name` would diverge from the server and hide the CTA in cases the server accepts. The server guard is authoritative (AGENTS.md Complement Rule item 2), and the nudge spawns no ffmpeg. Worth a follow-up issue.

[decision] Repaired the pre-existing vacuous test `test_identify_still_matches_legacy_profile_with_no_recorded_model` (`tests/test_voice_id.py:435`) — not asked for by the issue, because this change's NULL-wildcard clause depends on that branch being genuinely covered, and the test as written passed via the equality branch (the column default at `database/__init__.py:250` overwrote the intended NULL). Details in `wrong-directions.md` item 3.

[decision] Did NOT fix the write-after-cancel defect the Phase 1.5 (Fable) check found at `services/llm_jobs.py:744-750` — out of scope. `record_relabel` and the `transcript.segments` overwrite commit with no cancellation check, then `_finish` downgrades to `"cancelled"`, so a cancelled voice_match job still rewrites every speaker label. Real, pre-existing, and independent of this change (which only adds pre-work early exits). Recorded in `wrong-directions.md` item 5 with a recommended issue title.

## Review status

[x] **No independent-model audit pass ran in this workflow.** Claude Code's `/issue-claude` has no equivalent of opencode's `/issue` Oracle phase, so everything above is self-review. The Phase 1.5 (Fable) and Phase 3 (Sonnet) agents are part of this same run and do not constitute independent review. Independent review happens separately via opencode's `/audit-pr` after this PR is opened. Self-audit-only must not be mistaken for a full review.

[x] Main repo checkout clean before Phase 4 — `git -C C:\Claude\whisperdesk diff --stat` returns empty and `git status --porcelain` shows nothing (`.omo/runs/` is gitignored, so the run artifacts do not appear at all).

[x] All four self-report files exist in `.omo/runs/issue-112/worktree-issue-112-voice-match-backend/`: `investigation.md`, `self-audit.md`, `wrong-directions.md`, `token-usage.md`.

[x] No `static/` file touched, so no `esbuild` rebuild is implicated and no e2e selector changes are needed. Confirmed no test in `tests/` or `tests/e2e` selects on the new message text.

[x] `scripts/verify_self_audit.py` run before Phase 4 — **0 citation findings**, and after the checker's own bugs were fixed, **0 findings of any kind**: `OK: no stale builds, no suspect line citations.`

[correction] An earlier version of this line reported a leftover blocking "stale `static/rack.min.js`" finding and called it a pre-existing condition on `origin/master`. **That diagnosis was wrong** and the PR body carried it for a while. The bundle was never stale; the checker rebuilds to a temp filename while `build:js` passes `--sourcemap`, so the emitted `sourceMappingURL` basename differed, which was the whole 3-byte delta. Building with the real basename gives an identical md5 and size. Both checker bugs (that one, plus the missing-esbuild failure in `wrong-directions.md` item 6) are fixed with tests in **PR #332**. Full account in `wrong-directions.md` item 7, including what I got wrong about the process.

[x] CI green on the exact PR head, not just locally — check `tests` **pass** in 1m45s on run `30874007005`, `headSha fabfda96d03e9815f2c3f0c338a18b59e5b5f6cc`, which equals local `HEAD`. This matters because every local run shows `22 deselected` (`pytest.ini`'s `-m "not e2e"`), so CI is the only layer that exercised the e2e tier against the new guard.

[x] Rebased onto current `origin/master` (`7fd6911`, PR #325 landed mid-run) before opening the PR, and re-ran the full suite on the new base: **802 passed, 22 deselected** (the extra three over the earlier 799 are #325's own tests). Clean rebase, no conflicts. See `wrong-directions.md` item 8.
