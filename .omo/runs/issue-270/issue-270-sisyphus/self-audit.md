# Self-audit — Issue #270 (Studio pipeline: unified audio cleanup stage)

## Investigation.md promises

[x] `cleanup_loudnorm_enabled` in `DEFAULT_SETTINGS` — delivered, confirmed at services/settings.py:37 (first of 12 new cleanup keys)
[x] `services/audio_cleanup.py`: Create `cleanup_audio()` with loudnorm/denoise ffmpeg chain — delivered, confirmed at services/audio_cleanup.py:46-113
[x] `services/audio_cleanup.py`: `filter_hallucinations()` post-hoc filter — delivered, confirmed at services/audio_cleanup.py:116-162
[x] `services/audio_cleanup.py`: `cleanup_demucs()` stub with import guard — delivered, confirmed at services/audio_cleanup.py:165-199
[x] `cleanup_audio()` threaded into `_run_transcription_pipeline()` — delivered, confirmed at app.py:1139
[x] `app.py`: Thread VAD settings through `transcription_service.transcribe()` kwargs — delivered, confirmed at app.py:1269-1271
[x] `backends/builtin.py`: Construct `vad_filter` dict from `vad_threshold`/`vad_min_silence_duration_ms` kwargs — delivered, confirmed at backends/builtin.py:109-122
[x] `tests/test_audio_cleanup.py`: 23 tests for cleanup, hallucination, demucs, repeat detection — delivered at tests/test_audio_cleanup.py

## Mutation checks

[x] `test_find_longest_repeat_mutation_empty` — mutation check: fails with function body replaced by `return None`? yes (asserts non-None result for input with repeats)
[x] `test_filter_hallu_mutation_removes` — mutation check: fails with function body replaced by `return segments`? yes (asserts segment removed from result)
[x] `test_cleanup_mutation_changes_path` — mutation check: fails with function body replaced by early return of original path? yes (covered by `test_cleanup_loudnorm_applied` which asserts path ends in `_clean.mp3`)

## Acceptance criteria walk (from issue #270)

[x] "decision note records why each of #236-#239 is included, deferred, or remains independent" — investigation.md covers this: loudnorm/denoise/highpass unified in cleanup_audio (ffmpeg chain), VAD surfaced as kwarg settings (builtin-only), hallucination as post-hoc filter, Demucs as opt-in stub with import guard
[x] "cleanup stage has an explicit contract and deterministic fallback" — `CleanupResult` dataclass defines contract; all error paths return original audio_path
[x] "no provider receives a local-only option accidentally" — VAD/hallucination params flow through kwargs ignored by non-builtin providers; Demucs is import-guarded (ImportError returns original)
[ ] "measurable benchmark evidence supports defaults and thresholds" — NOT delivered: the referenced whisperhallu-review.md does not exist on disk; thresholds are provisional conservative defaults documented as such
[decision] Demucs implemented as import-guarded stub, not full pipeline — design doc says "separate opt-in pre-step" and child issue #239 says "needs a deeper dive before implementation"; investigation.md notes research doc is missing

## Full test suite

[x] Full suite run — 720 passed, 0 failed (pytest tests/ --ignore=tests/e2e)

## Main repo cleanliness

[x] `git -C C:/Claude/whisperdesk diff --stat` — no output, main checkout is clean

## Self-report files

[x] investigation.md at .omo/runs/issue-270/issue-270-sisyphus/investigation.md
[x] self-audit.md at .omo/runs/issue-270/issue-270-sisyphus/self-audit.md
[x] wrong-directions.md at .omo/runs/issue-270/issue-270-sisyphus/wrong-directions.md
[x] token-usage.md at .omo/runs/issue-270/issue-270-sisyphus/token-usage.md

## Oracle Phase 3.75

**Verdict: NEEDS-DISCUSSION** → fixed before PR. Oracle identified:

1. `vad_filter` kwarg should be `vad_parameters` dict for faster-whisper → fixed at backends/builtin.py:117-122
2. Chunk path (`queue.py:_run_chunk_job`) not receiving VAD settings → fixed at services/queue.py:413-423
3. `filter_hallucinations` imported but never called → wired at app.py:1286-1296 (inline) and services/queue.py:448-456 (chunked)
4. `no_speech_prob` not propagated through Segment dataclass → added to backends/base.py:20, backends/builtin.py:150, services/transcription.py:120, services/queue.py:449

All Oracle findings resolved. Full suite re-run: 720 passed, 0 failed.
