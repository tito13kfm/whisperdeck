# Live conversational capture: streaming STT, gap-triggered turns, spoken responses

> One-line status: Draft plan, exploratory. Not scheduled, no code written. Split out of `docs/plans/12-voice-dump-multi-item-capture.md`, whose Phase 1 is post-hoc/text-only and explicitly defers everything here.

## Motivation

Doc 12 locked its Phase 1 to post-hoc: record the whole dump, then split/review/finalize once, after the fact. The natural next step the user raised is a live mode — the app listens continuously, notices when the user has paused (finished a thought), and can interject a clarifying question or read one back, rather than only reviewing everything at the end. This doc captures what that would take, split into three genuinely separate pieces: detecting the pause, transcribing incrementally, and talking back.

None of this is designed to be built yet. It's recorded because the underlying capability turned out to already exist in more places than expected, and losing that context would mean re-discovering it later.

## Streaming STT: the model already supports it, WhisperDeck doesn't use it

`backends/moonshine.py:8` — the default local model is literally `medium-streaming`; `SUPPORTED_MODELS` (`moonshine.py:32-38` as of `58e8711`) lists `tiny-streaming`/`small-streaming`/`medium-streaming` alongside non-streaming `tiny`/`base`. But `MoonshineProvider.transcribe()` calls `transcriber.transcribe_without_streaming(audio_data, sample_rate=sample_rate)` (`moonshine.py:114`) on a fully-decoded in-memory buffer — the method name implies the `moonshine-voice` library has a real streaming counterpart that WhisperDeck simply never calls today.

**Lemonade's Whisper endpoint does not help here.** `docs/LEMONADE.md` — `POST /v1/audio/transcriptions` is standard batch OpenAI format, the same shape as every other hosted Whisper wrapper in `backends/`. It does not solve streaming. Moonshine's `-streaming` variants, reached through the library's actual streaming API (not `transcribe_without_streaming`), remain the only in-repo path to real incremental transcription — worth stating explicitly so a future reader doesn't assume Lemonade already covers this.

**Current capture pipeline is record-then-upload-once, not incremental, at all.** `static/rack.js` `startLiveCapture()` (`rack.js:2546` as of `58e8711`, `CAP` at `rack.js:2532`) buffers `MediaRecorder` output into `CAP.chunks` and only assembles/uploads the full `Blob` after Stop (`rack.js:2637`, `finishLiveCapture`). The existing "chunked upload" feature (`chunk_threshold_mb` in settings) splits an already-*complete* file for parallel transcription of long recordings — a different problem, don't conflate it with live streaming.

Getting to real live transcription means: (1) swap `transcribe_without_streaming` for the library's actual streaming call, (2) build a continuous ingest path from browser to server (a WebSocket is the natural fit — a one-shot POST doesn't model a live session), (3) restructure whatever consumes the transcript (the voice-dump split/clarify chain) to read a growing partial transcript instead of one finished file.

## Silence-gap detection as the turn-end trigger

The user's own framing: a configurable silence gap (5-10s, adjustable) marks "the user finished this item," rather than the app ever interrupting mid-utterance. This piece is cheap:

- `rack.js:2534` `analyserLevel()` / `rack.js:2589-2599` — `CAP` already holds live `AnalyserNode`s (`CAP.micAn`, `CAP.sysAn`, `fftSize = 256`, `CAP.buf`), sampled every frame via `analyserLevel(CAP.micAn)` — today purely to drive a VU meter (`INST.driveMic`/`driveSys` at `rack.js:1545`). A rolling "N consecutive low-amplitude samples → fire gap event" on top of that existing per-frame read is a small addition, not a new audio subsystem.
- `services/audio_prep.py:208` `detect_silence_midpoints()` — already exists, using ffmpeg's `silencedetect` filter (`noise_db="-30dB"`, `min_duration=0.5` as the existing conventions). It operates on a **completed file**, though, to find good post-hoc split points for chunked upload — not directly reusable for a live buffer (wrong input shape), but worth mirroring its threshold conventions for consistency if a live version is built.
- Once the gap fires, the decision of "does this warrant an interjected clarifying question, or just silently mark a turn boundary" is a separate, softer design question — not resolved here, deferred alongside the rest of live mode.

## Spoken responses: two real options

- **Kokoro via Lemonade** (`docs/LEMONADE.md`) — `POST /v1/audio/speech`, model `kokoro-v1`, 10 documented working voices (`af_bella`, `af_sky`, `af_nicole`, `af_sarah`, `am_adam`, `am_michael`, `bf_emma`, `bf_isabella`, `bm_george`, `bm_lewis`). Already tested and working in this repo — `scripts/generate_test_audio.py` uses it today to synthesize test meeting audio, not as a user-facing feature. **Known silent-failure gotcha, already caught in the doc**: voice names must include the region+number suffix; a bare prefix like `af` returns HTTP 200 with 0 bytes, not an error — a naive integration would look like it worked and produce silence.
- **Browser-native `SpeechSynthesis`** (Web Speech API) — zero server round-trip, no Lemonade dependency, works even without Lemonade running. Lower voice quality, no cloning, but a legitimate cheap default with Kokoro as an opt-in upgrade path — present both, not an either/or.
- Frontend playback either way reuses the existing `new Audio(...)` pattern already used for recorded-audio playback (`rack.js:4038`/`5833` as of `58e8711`) — no new playback component needed once audio bytes exist.

## Known constraint: running two local models at once

`backends/moonshine.py:10` documents `medium-streaming` at ~980MB resident in the Python process. Running Moonshine (STT) and Lemonade (LLM + TTS) simultaneously on one machine means both footprints resident at once. Per doc 12's resource-contention gotcha, `services/queue.py`'s `local_provider_lock` only serializes Moonshine STT calls against each other and has no reach into `services/llm_jobs.py` — so a live pipeline running STT and an LLM/TTS call concurrently is genuinely unserialized, contending for the same CPU. Worth flagging for anyone running this on a lower-RAM box, same spirit as `moonshine.py`'s own "<8GB, consider the smaller base model" note.

## Explicitly not designed here

- The actual turn-taking UX (when exactly to interject vs. stay silent, how the clarifying question is surfaced during a live session) — needs its own design pass once the three capability pieces above are validated individually.
- Any change to the Phase 1 (post-hoc) voice-dump chain in doc 12 — this is additive, doc 12 stands on its own regardless of whether live mode ever gets built.

## Competitor precedents

- **Dual-source capture (mic + system audio)**: `docs/research/meeting-notetaker-competitor-review.md` (meetily, 2026-08-06) credits **Screenpipe** as prior art for cross-platform system-audio taps and flags Bluetooth-output edge cases. Relevant to the gap this doc does not yet solve: capturing the remote side of a call, not just the mic.
- **Live captions as a phased slice**: same review (anarlog) separates "Live" (streaming captions during recording) from "After recording" models — precedent for shipping live-caption display as a first slice before the harder spoken-response/TTS half. See also issue #263 comment.

## Verification

### 2026-08-12 re-check (base `58e8711`, PR for #263)

All capability claims re-verified against current code; no live-mode runtime code exists (by design). Drift vs original line numbers:

| Claim | Doc cited | Current | Drift |
|---|---|---|---|
| `SUPPORTED_MODELS` | `moonshine.py:32-38` | `moonshine.py:32-38` | none, same range |
| `transcribe_without_streaming` | `moonshine.py:114` | `moonshine.py:114` | none |
| `CAP` / `startLiveCapture` | `rack.js:2456` / `2516` / `2549` | `rack.js:2532` / `2546` / `2637` | +76–90 lines, same functions |
| `AnalyserNode` / `analyserLevel` | `rack.js:2442` / `2499-2509` / `1456` | `rack.js:2534` / `2589-2599` / `1545` | +90 lines |
| `detect_silence_midpoints` | `audio_prep.py:168` | `audio_prep.py:208` | +40 lines |
| `new Audio(...)` | `rack.js:3850`/`5337` | `rack.js:4038`/`5833` | +188–496 lines |
| `local_provider_lock` | `services/queue.py` | `services/queue.py:461`/`535`/`887` | same, still not covering `llm_jobs` |
| `kokoro-v1` voices / gotcha | `docs/LEMONADE.md` | `docs/LEMONADE.md:18-32` | none |

Sibling sweep: `transcribe_without_streaming` (1 file), `WebSocket` (0 hits), `SpeechSynthesis` (0 hits), `audio/speech` (docs + `scripts/generate_test_audio.py` only) — no sibling implementation found.

Not yet buildable — each of the three pieces (streaming STT, gap detection, TTS) still needs its own prototype/spike to confirm the library-level streaming API actually behaves as its naming implies, since nothing in this repo has called it yet. When those spikes land, update this section with results.
