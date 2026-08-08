# Investigation — Issue #270: Unified audio cleanup stage

**Target**: Issue #270 (resolved from tracking issue #264, first open child in execution order)
**Worktree**: C:/Claude/whisperdesk-issue-270-sisyphus (branch `issue-270-sisyphus`, base `origin/master` at 4295264)
**Main checkout**: C:/Claude/whisperdesk (branch `tooling-verify-gate`)

## Design doc

`docs/superpowers/specs/2026-08-01-studio-classification-design.md`, decision 10 (line 140-147):

> The four audio-cleanup issues ship as one coherent pipeline stage with a
> defined order: loudnorm/denoise (#236) → VAD (#237) → chunking → transcribe
> → post-hoc hallucination filter (#238), with Demucs vocal isolation (#239)
> as a separate opt-in pre-step for noisy local recordings. Each step keeps its
> own on/off setting and a safe fallback to the original audio if it fails —
> this is architectural coherence, not a forced always-on bundle.

## Current pipeline flow (file:line)

`_run_transcription_pipeline()` at `app.py:1020-1283`:

1. **Pre-transcode guards** (app.py:1054-1057): diarize forced off for dictation/voice_note, capture_source normalization
2. **Transcode** (app.py:1088-1106): `transcode_for_upload()` converts to 16kHz mono mp3 (for cloud, long local, or non-native container)
3. **Stereo copy** (app.py:1108-1115): live-stereo gets a 2ch FLAC via `transcode_stereo_for_diarization()`
4. **Chunk vs inline** (app.py:1169-1171): hosted_chunked (cloud + big file) or local_chunked (local + >300s)
5. **Chunked path** (app.py:1188-1218): `chunk_audio()` → stub → `create_chunk_jobs()`
6. **Inline path** (app.py:1220-1283): `transcription_service.transcribe()` → diarization → correction/classify/voice-note/tagging

### Where the cleanup stage plugs in

The design doc says: loudnorm/denoise → VAD → chunking → transcribe → hallucination filter.

Looking at the code, there are **two natural insertion points**:

**A. Pre-transcode cleanup** (before or as part of `transcode_for_upload()`):
- loudnorm/denoise (#236): ffmpeg filters `loudnorm`, highpass, `afftdn`
- This is a ffmpeg filter chain — fits naturally into `transcode_for_upload()`'s existing ffmpeg invocation
- Benefits every provider (cloud + local) since it runs before the file is sent anywhere

**B. Pre-chunk/resume analysis** (after transcode, before chunking):
- VAD tuning (#237): this is currently a builtin-only faster-whisper parameter (`vad_filter=True`)
- The issue wants "surface VAD as an advanced setting" — this is about exposing the parameter, not adding a new processing step
- The VAD silero filter runs during transcription, not pre-transcription; surfacing its settings means adding them to user_settings and passing them through to `BuiltinProvider.transcribe()`

**C. Demucs separation** (#239): separate opt-in pre-step before even the transcode (needs raw audio, runs vocal separation first)
- Local only (demucs torch model, needs GPU or fast CPU)
- Produces a cleaned audio file that then goes through the normal transcode path

**D. Post-hoc hallucination filter** (#238): after transcription, over segment output
- Analyzes segment `avg_logprob` and text repetition patterns
- Builtin-only (faster-whisper exposes these fields; cloud providers mostly don't)
- Must run before diarization (segments are used by diarization) or after (segments already assigned to speakers)

### Sibling sweep

**Callers of `transcode_for_upload()`** (audio_prep.py:44):
1. `app.py:1102` — `_run_transcription_pipeline()` for fresh uploads
2. `app.py:1102` same site for retranscription (re-enters same pipeline via `_run_transcription_pipeline()` called from both `/api/transcribe` and `/api/transcripts/{id}/retranscribe`)

**Callers of `chunk_audio()`** (audio_prep.py:271):
1. `app.py:1180` — `_run_transcription_pipeline()` chunked path only
No other callers. Chunking only happens in the chunked-path branch of the pipeline.

**Callers of `detect_silence_midpoints()`** (audio_prep.py:168):
1. `chunk_audio()` @ audio_prep.py:295 — the only caller
If loudnorm/denoise changes the noise floor, silence detection thresholds in `chunk_audio()` may need retuning. The `noise_db` default is `-30dB` (hardcoded in `detect_silence_midpoints`).

**Callers of `BuiltinProvider.transcribe()`**:
- Accessed through `PROVIDER_REGISTRY["builtin"]` → `get_provider()` → called by `TranscriptionService.transcribe()` in `services/transcription.py`
- `vad_filter` parameter is a kwarg passed through `kwargs.get("vad_filter", True)` — no caller currently passes it, so it always defaults to True

**Retranscription path**: `app.py:1988-2029` — calls `_run_transcription_pipeline()` with the stored audio_path. The cleanup stage would automatically apply to retranscription since both paths converge in `_run_transcription_pipeline()`.

**No other entry points found.** The pipeline has exactly one convergence point (`_run_transcription_pipeline()`) for both initial upload and retranscription. The chunked and inline paths diverge inside this function but both go through the same transcode step first.

### Missing research doc

The child issues #236-#239 all reference `docs/research/whisperhallu-review.md` but this file does not exist on disk (`docs/research/` directory is empty). The research referenced parameters and benchmarks for loudnorm targets, afftdn strength, VAD thresholds, hallucination repetition windows, and Demucs model behavior. Without it, derived thresholds should use conservative defaults documented as provisional.

## Settings design

Following the existing pattern from `services/settings.py` (correction_provider/correction_model at lines 25-26):

```python
# Audio cleanup defaults (provisional — research doc missing)
"cleanup_loudnorm": False,        # opt-in loudnorm + highpass + denoise chain
"cleanup_loudnorm_target": -23.0,  # LUFS target
"cleanup_highpass": False,         # rumble/handling-noise filter (80Hz)
"cleanup_denoise": False,          # afftdn denoiser

"cleanup_vad_enabled": True,       # Silero VAD (builtin-only, already default-on)
"cleanup_vad_min_silence_ms": 100, # ms
"cleanup_vad_threshold": 0.5,      # speech probability threshold

"cleanup_hallu_enabled": False,    # post-hoc hallucination filter (builtin-only)
"cleanup_hallu_rep_window": 3,     # n-gram repetition window size
"cleanup_hallu_logprob_cutoff": -2.0, # avg_logprob below which a segment is suspect
"cleanup_hallu_no_speech_cutoff": 0.6, # no_speech_prob above which suspect

"cleanup_demucs_enabled": False,   # Demucs vocal isolation (local-only, expensive)
```

## Stage contract

```
CleanupResult = {
    "audio_path": str,          # path to processed audio (may == input if no-op)
    "applied_steps": [str],     # e.g. ["loudnorm", "denoise"]
    "skipped_steps": [str],     # e.g. ["demucs"] (disabled or non-local provider)
    "failed_steps": [str],      # steps that errored, fell back to original
    "warnings": [str],          # non-fatal diagnostics
}
```

Key design constraint: if any optional processor fails, the pipeline continues with the original (pre-cleanup) audio. The cleanup stage never blocks transcription.

## What the issue's plan misses

1. **VAD settings aren't a processing stage** — they're transcription parameters passed to `BuiltinProvider.transcribe()`. The design doc places VAD between loudnorm and chunking, but Silero VAD runs during transcription, not before. The settings should be threaded through to the backend, not run as a separate pre-step.

2. **Hallucination filter needs Segment schema extension** — `base.Segment` has `confidence` (avg_logprob) but not `no_speech_prob`. faster-whisper segments have this field; the builtin backend currently doesn't extract it (builtin.py:142-151). Either add it to the Segment dataclass or run the filter inside the backend before converting to Segment.

3. **Chunked-path cleanup**: cleanup must apply to each chunk individually (or to the whole file before chunking). loudnorm/denoise is pre-chunk (applied once). VAD settings apply per-chunk during transcription. Hallucination filter applies per-chunk segment output. Demucs is pre-everything.

4. **Retranscription threading**: the design doc says "thread the same behavior through retranscription" but retranscription already goes through `_run_transcription_pipeline()` — it's automatically covered. No separate work needed.

## Test plan

Per the issue's test section:
1. **Mocked ffmpeg filter construction and cleanup ordering** — verify filters are composed correctly in the ffmpeg command
2. **VAD and silence-detection regression tests** — confirm changed audio levels don't break chunk boundaries
3. **Hallucination heuristic tests** with repeated speech and legitimate repetition
4. **Demucs cache/download/failure tests** if selected
5. **Pipeline tests** proving cleanup failure preserves transcription
6. **Retranscription parity tests** — same cleanup applied

## Scope decision

The implementation scope for this issue is:
1. Add settings to `DEFAULT_SETTINGS` in `services/settings.py`
2. Create a `services/audio_cleanup.py` module with `cleanup_audio()` function
3. Thread the cleanup call into `_run_transcription_pipeline()` between transcode and chunk/inline decision
4. Thread VAD settings through to `BuiltinProvider.transcribe()`
5. Add hallucination filter as post-hoc step in the pipeline
6. Wire Demucs as optional pre-step (stub or full implementation)
7. Write tests

Given the research doc is missing and benchmarks can't be run in this environment, thresholds will be set to conservative defaults marked as provisional.
