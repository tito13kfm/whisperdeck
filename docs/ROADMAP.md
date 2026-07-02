# WhisperDesk Roadmap

**Keep this current.** Update this file whenever a plan lands (move it to Done)
or a new idea gets parked (add it to Parked). If a plan gets merged and this
file isn't touched in the same session, that's a bug — fix it before moving on.

## Done

- Per-user auth (`docs/superpowers/specs/2026-06-30-per-user-auth-design.md`)
- Audio chunking + queue (`docs/superpowers/specs/2026-07-01-audio-chunking-and-queue-design.md`)
- Transcription UX: cancel/resume, queue status, HF token consolidation, unified model selection (`docs/superpowers/specs/2026-07-01-transcription-ux-improvements-design.md`)
- Diarization torchcodec bypass (pyannote reads via soundfile, not torchaudio)

## In Progress

- Cheap/quick pre-transcription pass that optimizes the prompt per-meeting (working name: "whisper-tiny pre-pass" or an alternative cheap approach) — brainstorming started 2026-07-02.

## Parked (not designed yet)

- **Moonshine local-provider swap** (HIGH PRIORITY) — swap the weak built-in faster-whisper-tiny provider for Moonshine; English-only fits actual usage.
- **LLM transcript correction pass** — hotword-aware LLM pass to fix likely-mistranscribed words post-transcription.
- **Windows ML / native app pivot** — DirectML could unlock real AMD GPU use; needs ONNX conversion or a native-app rewrite, not a quick win.

## Known accepted gaps

- Cancel/resume: a few-Python-instructions race window between the diarization-await re-check and the final commit in `_finalize_if_done` is inert under the current single-process deployment (no `await` point in the gap). Needs a guarded `UPDATE ... WHERE status != 'cancelled'` if the app ever goes multi-worker.
- Detail page for an in-progress transcript doesn't live-poll (only the dashboard recents badge does). Spec called for both; only the dashboard got built. Not blocking, not fixed.
