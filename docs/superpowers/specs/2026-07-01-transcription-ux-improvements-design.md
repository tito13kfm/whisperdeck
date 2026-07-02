# Transcription upload/settings UX improvements — design

## Context

Real usage of WhisperDeck after the audio-chunking/queue feature shipped surfaced seven usability problems, several tied to the same underlying gap: the frontend has no visibility into what the background job queue is actually doing.

1. The just-added HuggingFace token (for pyannote diarization) lives in its own "Diarization" settings card with no saved/configured indicator, unlike provider API keys which show a masked value and an on/off dot.
2. The upload progress screen shows the raw browser `File` object's size throughout — for a video upload, this is the full video size (e.g. 1.5GB), not the much smaller size of the extracted/transcoded audio actually being processed, which is misleading.
3. Model selection exists in two disconnected places: a "Fetch models"/"Select model" button in Settings (which just populates the Transcribe page's dropdown and navigates there — it doesn't persist anything) and the Transcribe page's own model dropdown. The backend already has an unused `default_model` field on `ProviderConfig` that neither UI writes to.
4. The "Transcribing with Whisper..." progress stage gives no live signal. In real use, a chunked upload that was actually being correctly throttled by the new rate-limit-aware queue (waiting for Groq's hourly audio-second budget to free up) looked indistinguishable from a hung request.
5. The progress screen doesn't show which model is actually being used for a given transcription.
6. There's no way to cancel an in-progress transcription.
7. Undefined behavior when navigating away from the progress page mid-transcription — the backend job queue already runs fully server-side regardless of any open browser tab, but the UI doesn't make that clear or let you find your way back to watching.

## Decisions

1. **HF token placement**: moves into the existing "Providers" settings card as one more credential row, styled identically to the Groq/OpenAI/Replicate key rows (masked input + on/off configured indicator). It is not a real `ProviderConfig` row in the backend — it stays a field on `user.settings` — but visually it belongs in the same list since it's conceptually the same kind of thing (a credential unlocking a capability).
2. **Model selection unification**: one underlying value, `ProviderConfig.default_model` (already exists in the backend, currently unwritten by any UI). The Settings page's per-provider model picker is repurposed to actually save this value via the existing `PUT /api/providers/{name}` endpoint. The Transcribe page's model dropdown pre-fills from that default when a provider is selected, but stays fully editable per-upload — satisfying "pick at transcription time, default set in settings" as one flow instead of two disconnected pickers.
3. **Real processed size, not raw upload size**: the progress screen shows the browser's raw `selectedFile.size` only during the initial "Uploading..." stage (before the server has done anything). Once the immediate `/api/transcribe` response returns, the screen switches to a new `processed_size_bytes` field in that response — the server-computed post-transcode file size (already computed today for the chunk-threshold check, just not returned to the frontend). For chunked uploads this is the sum of all chunk file sizes.
4. **Live progress + rate-limit visibility**: a new `queue_status` field, computed at request time (not persisted) from the same budget logic `services/queue.py` already has, returned alongside every transcript response while status is `processing`:
   - `{"state": "transcribing", "chunks_done": N, "chunks_total": M}` — at least one job is `running`.
   - `{"state": "queued"}` — all jobs `pending`, and dispatch isn't currently blocked by rate-limit budget (just waiting for a worker tick or a concurrency slot).
   - `{"state": "rate_limited", "resume_in_seconds": N}` — all jobs `pending` and the provider's hourly/daily audio-second budget is currently exhausted. `resume_in_seconds` is computed by finding when the oldest transcript contributing to the current budget usage will age out of the trailing window — the same accounting `compute_audio_seconds_used` already performs, applied in reverse (when does *this* usage expire, not is there room *now*).
   The progress screen shows the model in use (from `Transcript.model`, already stored) plus state-specific text: a chunk counter when `transcribing`, "Waiting on Groq's rate limit — resuming in ~Nm" when `rate_limited`, instead of a silent, indistinguishable spinner in every case.
5. **Cancel**: `POST /api/transcripts/{id}/cancel` marks every `pending` `TranscriptionJob` row as a new `cancelled` status and sets `Transcript.status = "cancelled"` once no jobs remain `running`. Jobs already in flight to the provider are left to finish naturally (an in-flight HTTP request to Groq can't be un-sent, and letting it complete costs nothing extra since the result is simply discarded rather than merged) — this is a "stop dispatching more work" cancel, not an attempted hard-abort. A "Cancel" button appears on the progress screen any time status is `processing`.
6. **Resume**: `POST /api/transcripts/{id}/resume`, structurally identical to the existing `retry-failed-chunks` endpoint but resetting `cancelled` jobs back to `pending` instead of `failed` ones. Appears on the transcript detail page whenever status is `cancelled`, in the same spot the existing "Retry failed sections" button appears for `partial` transcripts.
7. **Navigate-away behavior**: no backend change — the job queue is already fully independent of any open browser tab. The transcript list/dashboard view gains a small status badge driven by the same `queue_status` states (transcribing / queued / rate_limited), so an in-progress transcript is visually distinguishable from the list, and clicking into it re-enters the existing polling loop and picks up exactly where it left off. No special "resume watching" mechanism is needed since nothing was ever paused.

## Data model changes

`TranscriptionJob.status` gains a `cancelled` value alongside `pending/running/completed/failed`.
`Transcript.status` gains a `cancelled` value alongside `pending/processing/completed/failed/partial`.

No new tables. `queue_status` is computed on read, not stored, since it's derived entirely from existing `TranscriptionJob` rows and the existing budget-accounting functions.

## API changes

- `GET /api/settings` — no schema change to the existing fields; the HF token continues to round-trip in plaintext as it already does for the other settings values (consistent with how this endpoint already behaves — it does not mask any field today, unlike `/api/providers/{name}` which does mask `api_key`). The frontend's "configured" dot is computed client-side from whether the returned value is non-empty, mirroring the provider-key display pattern visually without requiring a new masking convention on this endpoint.
- `PUT /api/providers/{name}` — no schema change; `default_model` already exists and is already writable, just not currently exercised by any UI.
- Transcript serialization (`GET /api/transcripts/{id}`, and the immediate response from `POST /api/transcribe`) gains two fields: `processed_size_bytes: int | null` and `queue_status: {state, ...} | null` (null when status isn't `processing`, e.g. already `completed`/`failed`/`partial`/`cancelled`).
- New: `POST /api/transcripts/{id}/cancel` → `{"ok": true, "cancelled": <count>}`.
- New: `POST /api/transcripts/{id}/resume` → `{"ok": true, "resumed": <count>}`.

## Explicitly out of scope

- Any change to how chunking, silence-detection, or the worker's dispatch loop itself works — this spec only adds visibility into and control over that existing system, not changes to its mechanics.
- A true hard-abort of an in-flight provider HTTP request — not attempted, since it isn't reliably possible and wouldn't save real cost or time over letting it finish and discarding the result.
- Masking the HF token value in `GET /api/settings` the way provider API keys are masked in `GET /api/providers/{name}` — the existing settings endpoint has never masked any field, and introducing masking there is a separate, broader change to that endpoint's contract, not specific to this feature.

## Verification plan

No test suite exists for this app; verification is manual, following the established pattern:
1. HF token round-trips through the Providers card UI with a masked/configured indicator, same as an existing provider key.
2. Setting a provider's default model in Settings actually persists (`GET /api/providers/{name}` reflects it after reload) and the Transcribe page's dropdown pre-fills with it on provider selection, while still being editable per-upload.
3. Upload a large video file; confirm the progress screen switches from the raw upload size to a materially smaller `processed_size_bytes` value once transcoding completes.
4. Force a rate-limit-exhausted scenario (as was hit live during testing) and confirm the progress screen shows `"rate_limited"` with a resume estimate instead of an indistinguishable spinner; confirm a normal `"transcribing"` chunked upload shows real chunk counts and the correct model name.
5. Cancel an in-progress chunked transcription; confirm not-yet-dispatched jobs stop being picked up, the transcript reaches `cancelled`, and any job already in flight when cancel was pressed still completes and its result is discarded (not merged).
6. Resume a cancelled transcript; confirm cancelled jobs reset to pending and the transcript reaches a normal terminal state again.
7. Start a chunked upload, navigate away from the progress page, confirm the dashboard/list view shows a live status badge, and navigating back into the transcript resumes polling correctly.
