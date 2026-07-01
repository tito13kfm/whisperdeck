# Audio chunking, upload queue, and rate-limit awareness — design

## Context

WhisperDeck transcodes uploads to 16kHz mono MP3 before sending them to cloud providers (`services/audio_prep.py`), but sends the whole file in one request. A 1.5hr meeting at the current 64kbps produces ~42MB, over Groq's 25MB free-tier upload cap — the request fails outright. Confirmed live against Groq's docs (2026-07-01):

- File size cap: **25MB free tier / 100MB dev tier**.
- Rate limits (free tier, `whisper-large-v3`/`whisper-large-v3-turbo`): **20 requests/min, 2,000 requests/day, 7,200 audio-seconds/hour, 28,800 audio-seconds/day**.

A single 1.5hr (5,400s) meeting alone uses ~75% of the hourly audio-second budget on free tier — rate-limit awareness matters as much as the size cap once chunking is in place, since chunking turns one request into several.

## Decisions

1. **Chunk trigger**: size-based. After transcoding, if the file exceeds a (user-configurable) threshold, split it. Short/medium meetings stay single-shot with no added latency.
2. **Chunk boundaries**: silence-aware via `ffmpeg silencedetect` (cheap — single-pass amplitude scan, no ML, adds seconds not minutes even on long files), cutting at the quiet gap closest to the target chunk size. Each chunk carries a few seconds of overlap with its neighbor as a safety net for stretches with no clean silence gap.
3. **Reassembly**: per-chunk segments get their absolute time offset added back in; overlapping text between adjacent chunks is deduped by matching tail/head text before merging into one `Transcript.segments` list and rebuilding `full_text`.
4. **Bitrate**: raised from 64kbps to 128kbps (still 16kHz mono — sample rate is the real ceiling since Whisper resamples to 16kHz internally regardless of input; bitrate governs compression-artifact loss, which matters more for the noisy/accented audio this app targets). Chunking removes the size pressure that justified the old low bitrate.
5. **Stereo (dual-channel Zoom/room-mic recordings)**: explicitly **not** addressed by this spec. Groq's docs confirm speech-to-text models downsample to mono server-side regardless of upload format, so sending stereo through buys nothing — the channel separation is destroyed either way, and naive downmixing averages both channels together (potentially worse than a deliberate choice). Real channel-based diarization requires splitting channels client-side into two mono files and transcribing each independently — that's its own follow-up project, not a toggle. This spec ships plain forced mono, matching current behavior at the new bitrate.
6. **Concurrency**: upload queue dispatches multiple chunk jobs at once, capped by a configurable concurrency limit (default 4 — safely under Groq's 20 RPM even accounting for retries).
7. **Failure handling**: partial results. If a chunk fails after retries, the rest of the transcript still completes; the failed chunk's time range is tracked for later retry, and the transcript's status reflects the gap (new `partial` status) rather than discarding completed work.
8. **Queue mechanics**: a durable, in-process job queue — a new DB table of chunk jobs, worked by a background `asyncio` loop already running inside `app.py`. No new infrastructure (no Celery/Redis/external cron) — appropriate for a personal app on one machine, while still being durable across restarts and genuinely rate-limit-aware (unlike a bare in-memory queue).
9. **Rate-limit tracking**: computed from real usage, not a separate counter table — the worker sums `duration_seconds` from this user's `Transcript` rows for the given provider within the trailing hour/day and compares against that provider's published ASH/ASD before dispatching new jobs. If dispatching would exceed budget, jobs stay queued and are retried next tick rather than firing and hitting 429s.
10. **Upload UX**: `/api/transcribe` becomes asynchronous. It transcodes, chunks, creates the `Transcript` row (`status="processing"`), enqueues one job per chunk, and returns immediately with the transcript id — it does not wait for chunks to finish. The frontend's progress screen polls `GET /api/transcripts/{id}` every few seconds until status settles, showing real job-completion counts instead of the current staged-timeout animation. This replaces the old single blocking `fetch()` call, since a queued job may legitimately wait minutes for rate-limit budget and holding one HTTP request open that long is fragile.
11. **Settings**: all four tunables below are per-user settings (same place/pattern as provider API keys), not hardcoded constants:
    - Bitrate (default 128kbps)
    - Chunk size threshold (default ~20MB, safely under the free-tier 25MB cap)
    - Max concurrent chunk uploads (default 4)
    - (Rate-limit budget per provider is derived from Groq's published numbers, not user-set, but the design keeps it provider-configurable in code since paid tiers differ.)

## Data model changes

New table:
```
TranscriptionJob
  id             INTEGER PRIMARY KEY
  transcript_id  INTEGER FK -> transcripts.id
  chunk_index    INTEGER
  start_time     FLOAT   -- offset into the full transcript, seconds
  end_time       FLOAT
  audio_path     TEXT    -- path to this chunk's transcoded file
  status         TEXT    -- pending, running, completed, failed
  attempts       INTEGER default 0
  error          TEXT nullable
  created_at     DATETIME
  updated_at     DATETIME
```

`Transcript.status` gains a `partial` value (some chunks completed, at least one chunk permanently failed after retries) alongside the existing `pending/processing/completed/failed`.

## Architecture / flow

1. `POST /api/transcribe`: save upload, transcode (existing `transcode_for_upload`, now at the user's configured bitrate), check resulting size against the user's chunk threshold.
   - Under threshold: existing single-shot path, unchanged, still synchronous.
   - Over threshold: run `ffmpeg silencedetect` once, compute chunk boundaries at silence points nearest the target size (with overlap), write chunk files, create `Transcript` (`status="processing"`), insert one `TranscriptionJob` row per chunk, return `{id: transcript.id}` immediately.
2. Background worker (asyncio loop in `app.py`, ticking every few seconds): for each user+provider with pending jobs, compute trailing-hour/day audio-seconds already submitted from `Transcript` history; dispatch up to the concurrency cap worth of pending jobs whose provider call would keep the user under budget; mark dispatched jobs `running`.
3. Each dispatched job calls the existing provider `transcribe()` on its chunk file. On success: mark `completed`, store the returned segments (with `start_time` offset applied) on the job row or directly merge into the parent `Transcript`. On failure: increment `attempts`, retry with backoff up to a cap, then mark `failed` and record `error`.
4. When all jobs for a transcript reach a terminal state: merge completed chunks' segments in order (offset + overlap-dedup), rebuild `full_text`, set `Transcript.status` to `completed` (all succeeded) or `partial` (at least one permanently failed).
5. Frontend polls `GET /api/transcripts/{id}` until status is terminal; on `partial`, the transcript detail page shows a "Retry failed sections" action that re-enqueues just the `failed` `TranscriptionJob` rows (resetting them to `pending`, `attempts=0`) for the worker to pick up again.

## Explicitly out of scope

- Channel-based diarization (splitting stereo into two mono streams and transcribing each independently) — separate follow-up project; see "Decisions" item 5.
- The hotword/context correction LLM pass and the local Whisper-tiny pre-pass for topic/vocabulary intel — separate ideas noted for future design, unrelated to chunking mechanics.
- Provider fallback routing (switching providers automatically on rate-limit exhaustion) — this spec queues and waits for budget on the *same* provider; switching providers is a larger decision (cost, different API shapes) left to a future project, consistent with the original per-user-auth spec's deferred "Future work" note.
- External/OS-level cron or a separate worker process — the queue runs in-process via `asyncio`, per Decision 8.

## Verification plan

No test suite exists for this app; verification is manual, following the same pattern as prior work in this codebase:
1. Single-shot path unaffected: a short recording under the chunk threshold still transcribes via the existing synchronous flow.
2. Chunking triggers correctly: a long recording over the threshold gets split, and the merged transcript's `full_text`/segments read correctly across chunk boundaries (spot-check a boundary for duplicated or missing words).
3. Concurrency respected: multiple chunks dispatch up to the configured cap, not all at once.
4. Rate-limit awareness: with a low chunk threshold and a large file (many chunks) against a real Groq key, confirm the worker throttles dispatch rather than firing all chunks immediately, and no 429s occur.
5. Partial failure + retry: simulate a chunk failure (e.g. temporarily bad API key mid-run), confirm the transcript reaches `partial` status with the failed range identifiable, then confirm "Retry failed sections" successfully re-processes just that chunk and the transcript reaches `completed`.
6. Settings take effect: changing bitrate/threshold/concurrency in Settings changes behavior on the next transcription (spot-check via logs or file size).
