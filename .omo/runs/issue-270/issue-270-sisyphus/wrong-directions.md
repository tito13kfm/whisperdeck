# Wrong directions — Issue #270

## 1. Oracle found VAD kwarg API mismatch
BuiltinProvider originally passed `vad_filter=vad` (a dict) to faster-whisper's `model.transcribe()`. Oracle flagged that faster-whisper expects `vad_parameters` for the tuning dict, not `vad_filter`. Fixed: tuned params go to `vad_parameters`, `vad_filter` stays bool.

## 2. Oracle found mirror-path VAD gap
Inline transcribe (`app.py`) passed VAD settings, but chunked path (`queue.py:_run_chunk_job`) did not. Long recordings (>300s) on builtin would ignore VAD settings while short recordings would honor them. Fixed: chunk job now fetches user_settings and passes VAD params to `provider.transcribe()`.

## 3. Oracle found dead code: filter_hallucinations + cleanup_demucs
Both functions were defined in `services/audio_cleanup.py` and imported in `app.py` but never called. Settings existed but were dead. Fixed: `filter_hallucinations` wired into both inline and chunked transcribe paths. `cleanup_demucs` remains import-guarded stub — Demucs was explicitly "needs a deeper dive before implementation" per child issue #239.

## 4. Missing research doc
All four child issues (#236-#239) reference `docs/research/whisperhallu-review.md` which does not exist on disk. Current thresholds are conservative defaults marked as provisional. Recommendation: audit the referenced research, derive empirical thresholds from real recordings, update defaults before enabling cleanup features by default.

## 5. `_clean.mp3` file naming collision
Oracle noted that `base_clean.mp3` in UPLOAD_DIR can collide on concurrent same-name uploads. Same issue exists for `transcode_for_upload` (`base_16k.mp3`). Should use a UUID or hash-based suffix. Not fixed in this issue — pre-existing pattern, out of scope.
