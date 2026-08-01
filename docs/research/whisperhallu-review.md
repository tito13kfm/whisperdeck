# WhisperHallu review: pre-transcription audio cleanup for WhisperDeck

Date: 2026-07-30
Source: https://github.com/EtienneAb3d/WhisperHallu

## What WhisperHallu is

A Python script (not a library API, not a package on PyPI) that wraps `openai-whisper` with a
pre-processing and retry pipeline aimed at one specific failure mode: Whisper hallucinating
text during silence or non-speech audio. Pipeline:

1. **Vocal isolation** — Demucs or Spleeter splits vocals from background noise/music.
2. **Silence trim + loudness normalize** — via ffmpeg.
3. **Silero VAD** — flags which regions are actual speech.
4. **Marker injection** — inserts audible tone markers at speech-region boundaries.
5. **Transcribe**, then check whether the markers survived in the output. If they didn't
   (Whisper "ate" them, a hallucination signature), retry with markers inverted or removed,
   and cross-check the results against each other.

Steps 1-3 are generic audio cleanup. Step 4-5 (the actual "hallucination detection") is a
Whisper-specific hack that only works because the author calls `openai-whisper`'s Python decode
loop directly, across multiple passes, and diffs the results.

## Suitability for direct reuse: poor

- **No license file.** GitHub API reports `"license": null`. Default copyright rules apply —
  nothing here can legally be vendored into WhisperDeck without contacting the author.
- **Stale.** Last push 2024-11-12 (about 14 months old at review time), 13 open issues, no
  releases/tags — a single-maintainer script, not a maintained library.
- **Not packaged.** No `pip install whisperhallu`; it's a script you clone and edit, with a
  loadModel()/transcribePrompt() calling convention, not a clean importable API.
- **Heavy, model-specific dependencies.** Demucs and Spleeter are full source-separation models
  (multi-GB weights, minutes of compute per file on CPU). Spleeter in particular is stuck on
  TensorFlow 1.x-era tooling and is effectively unmaintained upstream too.
- **Architecture mismatch.** The marker-injection/retry trick depends on running
  `openai-whisper`'s Python decode loop directly and inspecting per-segment `no_speech_prob`
  across multiple full passes. WhisperDeck's only in-process Whisper path
  (`backends/builtin.py`) uses **faster-whisper** (CTranslate2), not `openai-whisper` — different
  binding, though `faster_whisper.WhisperModel.transcribe()` segments do expose `no_speech_prob`,
  so the *idea* is portable even though the *code* isn't.
- **Doesn't apply to WhisperDeck's other providers at all.** `groq`, `openai`, `assemblyai`,
  `replicate`, `openrouter` are opaque HTTP APIs (see `backends/`) — WhisperDeck never gets a
  decode loop to instrument, so the marker/retry technique is a non-starter there by
  construction. `moonshine.py` is a different model family entirely (not Whisper), so the same
  applies.

Verdict: don't vendor or wrap this repo. Borrow the *technique*, reimplement narrowly against
what WhisperDeck already has.

## What WhisperDeck already has

- `services/audio_prep.py` already runs every upload through ffmpeg
  (`transcode_for_upload`: mono, 16kHz, mp3) before any provider sees it, and already does
  silence detection (`detect_silence_midpoints`, via ffmpeg's `silencedetect`) — currently only
  used to pick chunk-split points, not to trim or clean audio.
- `backends/builtin.py` (faster-whisper) already passes `vad_filter=True` by default, which is
  faster-whisper's own Silero-VAD-based filter — a chunk of WhisperHallu's step 3 is already
  running for that one provider. It's not exposed as a user-facing setting, and it isn't used
  for anything except the builtin/faster-whisper path.
- No noise reduction, loudness normalization, or vocal isolation exists anywhere in the
  pipeline today. No hallucination-repetition heuristic exists on any provider's output.

## Recommended approach: adopt techniques, not the repo

Ranked by cost/benefit, each independent (can ship any subset):

**A. Generic ffmpeg cleanup filter chain — cheap, provider-agnostic, no new dependencies.**
Add an optional filter stage to `transcode_for_upload()`: `loudnorm` (loudness normalization),
a highpass filter (removes low-frequency rumble/handling noise), and ffmpeg's `afftdn` (a light
built-in denoiser). All ship with ffmpeg already, which WhisperDeck already requires — zero new
install burden. Benefits every provider (cloud and local) since it runs before the file is sent
anywhere. Should be opt-in (a checkbox/setting), since normalization can occasionally hurt
already-clean studio audio and adds a few seconds of ffmpeg processing.

**B. Expose and tune VAD — cheap, faster-whisper (builtin) only.**
`vad_filter=True` is already on by default in `backends/builtin.py` but hardcoded — no way to
tune `min_silence_duration_ms` or the speech-probability threshold, and no visibility that it's
even active. Surface it as an advanced setting for the builtin provider.

**C. Repetition/hallucination heuristic on segment output — cheap-medium, faster-whisper only.**
Whisper's classic hallucination signature is a run of near-identical repeated text with low
`avg_logprob`/high `no_speech_prob` — exactly the fields `builtin.py` already extracts
(`s.avg_logprob`) but doesn't currently act on. A post-hoc filter (flag or drop segments matching
repeated-ngram + low-confidence pattern) gets most of WhisperHallu's hallucination-detection
value without needing the marker-injection/retry machinery or a second full transcription pass.

**D. Demucs vocal isolation — expensive, opt-in only, local provider only.**
Facebook's Demucs is MIT-licensed and pip-installable (`pip install demucs`), and WhisperDeck
already carries `torch`/`torchaudio` as dependencies (for diarization), so it isn't a novel
dependency category. But it's a multi-GB model download and meaningfully slow on CPU — appropriate
only as an explicit "clean up noisy audio" opt-in toggle for local transcription, not a default.
Skip Spleeter (TF1-era, unmaintained, no reason to prefer it over Demucs here).

## Suggested next step

If there's appetite to act on this, A + B are small enough to scope as one plan (both touch
`services/audio_prep.py` and `backends/builtin.py`, both are additive/opt-in, no new deps for A,
one optional-extra dependency for D if pursued later). C is a good pairing with B since both
touch the same builtin-provider segment data. D is worth a separate decision once A-C are in,
since it's the only one with real cost (model download + CPU time) and would want real
before/after accuracy comparisons to justify.
