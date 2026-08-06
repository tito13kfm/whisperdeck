# Self-audit — issue #110

Branch: `worktree-polished-swinging-alpaca`. Target issue: **#110**, `voice_id:
_last_backend_error is shared mutable state across threads` (standalone issue, not a tracking
issue, not a PR number).

**Scope of this audit: self-review only.** This Claude Code workflow does not run an
independent-model audit pass (opencode's `/issue` does, via Oracle in its own Phase 3.75).
Independent review of this change happens via opencode's `/audit-pr` as a separate step after the
PR is opened. Nothing below should be read as independent review.

## Promises from investigation.md

[x] `services/voice_id.py`: `_last_backend_error` is no longer shared across threads, it is backed by per-thread `threading.local()` storage -- delivered, confirmed at services/voice_id.py:43
[x] `services/voice_id.py`: the `_last_backend_error` property getter defaults to `None` for a thread that never wrote, so no `AttributeError` -- delivered, confirmed at services/voice_id.py:46
[x] `services/voice_id.py`: the `_last_backend_error` setter writes to per-thread storage, so all three existing writers (`_embed_speechbrain`, `_embed_pyannote`, `_embed_mfcc`) become thread-safe without changing their bodies -- delivered, confirmed at services/voice_id.py:60
[x] `services/voice_id.py`: every extraction starts with a clean error slot via `self._last_backend_error = None`, so a failure can no longer be reported as the reason for a later call on the same thread -- delivered, confirmed at services/voice_id.py:334
[x] `services/voice_id.py`: sibling race 1, `_classifier` lazy init is now double-checked under `self._model_lock` -- delivered, confirmed at services/voice_id.py:363
[x] `services/voice_id.py`: sibling race 2, `_pyannote_inference` lazy init is now double-checked under the same `self._model_lock` -- delivered, confirmed at services/voice_id.py:396
[x] `services/voice_id.py`: `self._model_lock` is created once in `__init__` so the singleton shares one lock -- delivered, confirmed at services/voice_id.py:36
[x] `services/voice_id.py`: `import threading` added -- delivered, confirmed at services/voice_id.py:11

### Call sites in scope (the Complement Rule)

investigation.md enumerated five entry points that reach this state: four `async def` routes on the
event loop and one ThreadPoolExecutor worker. The fix corrects the state inside the service rather
than at any call site, so all five are covered by construction and none needed an edit. Verified by
the Phase 3 agent grepping `app.py` and `services/llm_jobs.py` for `voice_id_service.` and
confirming every call lands on `enroll`, `add_clip`, `identify`, `list_profiles`, `delete_profile`,
`remove_clip`, `backend_name`, or `_backend` -- no external caller touches `_last_backend_error`,
`_extract_embedding`, `_get_classifier`, or `_get_pyannote_inference`.

Citations below point at each route's `async def` line, since that is where the route's name is
literally written; the `voice_id_service` call itself sits further down each handler body (at
`app.py:3350`, `:2460`, `:3417`, `:3381` respectively).

[x] `app.py`: `enroll_voice` (event loop) covered, no edit needed, still surfaces `str(e)` unchanged -- delivered, confirmed at app.py:3332
[x] `app.py`: `enroll_speaker_from_transcript` (event loop, the one route the issue named) covered, no edit needed -- delivered, confirmed at app.py:2413
[x] `app.py`: `add_voice_clip` (event loop, unnamed by the issue) covered, no edit needed -- delivered, confirmed at app.py:3401
[x] `app.py`: `identify_speaker` (event loop, writes the field as a side effect) covered, no edit needed -- delivered, confirmed at app.py:3364
[x] `services/llm_jobs.py`: the `voice_match` job's `run_in_executor` worker call to `identify` covered, no edit needed -- delivered, confirmed at services/llm_jobs.py:731

### Sibling sweep result

The sweep found two racy attributes the issue never named, both fixed above. It also cleared two:
`self.voices_dir` and `self._backend` are read-only after `__init__` in production code, so
concurrent reads are safe and neither was changed. No module-level mutable globals in the file.
Nothing is pickled or deep-copied, so introducing a `threading.Lock` and a `threading.local` as
instance attributes is safe (grepped for `deepcopy`/`pickle` against `voice_id`: no hits).

## Tests

[x] `tests/test_voice_id.py`: new test proves an executor thread's failure cannot become the reason an event-loop `enroll` reports, via `test_enroll_error_reason_is_not_contaminated_by_a_concurrent_worker_thread` -- delivered, confirmed at tests/test_voice_id.py:677
[x] `tests/test_voice_id.py`: new test proves a second extraction reports its own failure, not a stale one, via `test_extract_embedding_reports_the_current_failure_not_a_stale_one` -- delivered, confirmed at tests/test_voice_id.py:717
[x] `tests/test_voice_id.py`: new test proves two racing threads build exactly one classifier, via `test_get_classifier_builds_one_instance_when_two_threads_race` -- delivered, confirmed at tests/test_voice_id.py:739
[x] `tests/test_voice_id.py`: new test proves two racing threads build exactly one pyannote inference, via `test_get_pyannote_inference_builds_one_instance_when_two_threads_race` -- delivered, confirmed at tests/test_voice_id.py:770
[x] `tests/test_voice_id.py`: changed test `test_enroll_error_includes_underlying_reason_when_all_backends_fail` now sets the reason from inside the `_embed_speechbrain` mock instead of pre-seeding the attribute -- delivered, confirmed at tests/test_voice_id.py:202

### Mutation checks

Every new and changed test was checked by temporarily reverting the fix and by replacing the
function under test with a trivial constant of its declared return type. Each temporary mutation
was applied to a scratch copy and restored by copy-back, never by `git checkout`/`stash`/`restore`/
`reset`, and `git diff --stat` was confirmed to return to baseline after each one (final diffstat
matched the pre-mutation baseline exactly: 207 insertions, 16 deletions across the same two files).

[x] test_enroll_error_reason_is_not_contaminated_by_a_concurrent_worker_thread -- mutation check: fails with `threading.local()` replaced by a plain shared object? yes (asserted the worker's text appeared instead of its own). Fails with the `_last_backend_error` getter body replaced by `return None`? yes.
[x] test_extract_embedding_reports_the_current_failure_not_a_stale_one -- mutation check: fails with the `self._last_backend_error = None` reset line deleted? yes (read back `librosa boom 1` on the second call). Fails with the getter body replaced by `return None`? yes (`TypeError: argument of type 'NoneType' is not iterable`).
[x] test_get_classifier_builds_one_instance_when_two_threads_race -- mutation check: fails with `with self._model_lock:` and the inner re-check removed? yes (`assert 2 == 1`). Fails with `_get_classifier` body replaced by `return None`? yes.
[x] test_get_pyannote_inference_builds_one_instance_when_two_threads_race -- mutation check: fails with `with self._model_lock:` and the inner re-check removed? yes (`assert 2 == 1`). Fails with `_get_pyannote_inference` body replaced by `return None`? yes.
[x] test_enroll_error_includes_underlying_reason_when_all_backends_fail -- mutation check: fails with the `_last_backend_error` getter body replaced by `return None`? yes (reason text absent from the raised message). Also confirmed by running, not assumed, that it still passes with the reset line deleted, so it is not silently duplicating the staleness test's coverage.

### Red-green

The reported symptom is a wrong error message across threads, so the red-green layer is the unit
layer where that state lives; no browser is involved. Each of the four new tests was run against a
build with its specific fix reverted, and each failed with the pre-fix symptom (see the mutation
lines above). None of them is a "doesn't raise" test: each asserts a specific string presence or
absence, or an exact instantiation count with `==`.

### Suites

- `tests/test_voice_id.py`: **37 passed, 1 skipped, 0 failed** (the skip is a pre-existing
  `importorskip("torch")`; torch is absent in this environment).
- `tests/test_voice_match_job.py`: 10 passed. `tests/test_speaker_naming.py`: 16 passed.
  `tests/test_relabel_undo.py`: 9 passed.
- **Full suite** (`pytest -q`, worktree root): **829 passed, 1 skipped, 22 deselected, 0 failed.**
  Run in full before any test-related box above was checked.

### Testing tier

`AGENTS.md:171-181` puts this at tier 1 (unit/integration for the touched path): a backend fix
scoped to one module with no request/response contract change. Per the tier-2 rule's own
instruction to substitute a static contract check rather than silently skip, the Phase 3 agent
confirmed in source that all three error-surfacing handlers still forward `str(e)` from the
`ValueError` raised by `enroll`/`add_clip`, so what the frontend receives is byte-identical in
shape to before: `app.py:3332-3360` (`enroll_voice`, 500 + `detail=str(e)`), `app.py:3401-3426`
(`add_voice_clip`, 400 + `detail=str(e)`), `app.py:2413-2481`
(`enroll_speaker_from_transcript`, 400 + `detail=str(e)`). No server or browser was started, by
design for this tier, and that is stated here rather than left implicit.

## Acceptance criteria

Issue #110 has no acceptance-criteria list; it has a "Proposed Fix" block offering three options.
Walking them:

[x] Option 1, "Make `_last_backend_error` thread-local (`threading.local()`)" -- taken, at services/voice_id.py:43
[ ] Option 2, "Return the error as part of the `_extract_embedding()` return value" -- NOT taken: see the first decision line below.
[ ] Option 3, "Use a lock around reads/writes" -- NOT taken for the error text, deliberately: a lock makes the read consistent, not correct. Thread A would still read thread B's error, just without tearing, so it does not fix the issue's own stated impact ("Wrong error message shown to user"). A lock IS used, but for the two lazy model caches, where mutual exclusion is the right tool.

## Decisions the issue did not ask for

[decision] Chose the issue's option 1 (thread-local) over its option 2 (return value) -- not specified by the issue, because option 2 changes `_extract_embedding`'s return shape, which ~25 existing monkeypatch sites in `tests/test_voice_id.py` and `tests/test_voice_match_job.py:102` construct as a 2-tuple. Option 1 fixes the same race with no signature change anywhere, keeps those sites honest, and leaves a much smaller conflict surface against the in-progress work on neighbouring issue #109, which edits the same `_extract_embedding` function. Option 2 remains the more explicit design if someone later wants to delete the diagnostic channel outright.

[decision] Added a per-call reset of `_last_backend_error` at the top of `_extract_embedding` -- not specified by the issue, because `threading.local()` alone does not fix the whole reported symptom. `_embed_mfcc` guards its write with `if not self._last_backend_error` so it won't clobber a more specific primary-backend error, and nothing ever reset the field between calls, so on a `librosa_mfcc`-only backend the first failure's text stuck permanently and every later failure reported the first call's reason. That is a wrong-error-message bug on a single thread, which the issue never mentions and which thread-local storage would not have touched.

[decision] Fixed the two lazy-init races on `_classifier` and `_pyannote_inference` -- not specified by the issue, because the mandated sibling sweep found them on the exact same singleton reached from the exact same two threads, and a change that hardened one piece of that object's shared state while leaving two others unguarded would be arbitrary. They are also the more damaging of the three: two threads could both run the model loader against the same on-disk `savedir`. Disclosed here rather than filed separately because they are the same hazard on the same call graph, in the same five-line neighbourhood of the file.

[decision] Rewrote the pre-existing test `test_enroll_error_includes_underlying_reason_when_all_backends_fail` instead of weakening the fix -- not specified by the issue, because the per-call reset made that test fail: it pre-seeded `svc._last_backend_error` before calling `enroll()`, which bypassed extraction entirely and is not how production ever sets that field. The rewrite drives the real write path (the `_embed_speechbrain` mock records the reason, as the real one does in its `except` block) and keeps the assertion identical, so it is strictly stronger coverage than before, not weaker. This was found by the Phase 3 agent's full-file run and is called out because a fix that breaks a previously-green test is exactly the kind of thing that must not be discovered by a reviewer.

[decision] Did not fix two adjacent defects found in passing -- not specified by the issue, and out of scope: (a) the three `async def` enrollment routes call the blocking embedding extraction directly on the event loop, stalling it for the duration; (b) `enroll_speaker_from_transcript` does not catch `ValueError` from `add_clip`, so an extraction failure there returns 500 rather than a clean 400 like the other two routes do. Both are recorded here so they are not lost, neither is touched by this PR.

## Phase 1.5 (completion-race check): not triggered

The Fable completion-race check applies when Phase 1 surfaces code that marks a job/state
`"completed"` and then fires a further side effect in the same try block or handler. Phase 1
surfaced no such shape, verified by reading rather than assumed:

- In the `voice_match` branch (`services/llm_jobs.py:690-752`), `_finish(db, job, "completed",
  error)` at line 752 is the terminal statement of the branch. The dependent writes
  (`record_relabel`, `transcript.segments`) happen at lines 744-750, before it, not after.
- `_finish` itself (`services/llm_jobs.py:319-330`) only sets the terminal state behind a
  cancel-wins guard (`if job.status == "cancelled": ... return`) and commits. It does not enqueue a
  job, fire a callback, or write a dependent record.
- Nothing in this PR touches a job or state completion path; the whole diff is inside
  `services/voice_id.py` and `tests/test_voice_id.py`.

The Fable call budget was therefore not spent, and this is a documented non-trigger, not a silent
skip.

## Pre-Phase-4 checks

[x] Main repo checkout clean: `git -C C:\Claude\WhisperDeck diff --stat` reports no changes; `git status --porcelain` shows only the pre-existing untracked `.skill-observations/`, which predates this run (it was present in the session's opening git status) and no `.omo/runs/` file appears in `diff` because `.omo/` is gitignored.
[x] All four self-report files present in `.omo/runs/issue-110/worktree-polished-swinging-alpaca/`: `investigation.md`, `self-audit.md`, `wrong-directions.md`, `token-usage.md`.
[x] `scripts/verify_self_audit.py` run against this file before Phase 4 (result recorded in the PR summary).
