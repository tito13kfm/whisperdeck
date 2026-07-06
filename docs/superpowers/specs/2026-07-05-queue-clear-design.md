# Queue clear/dismiss — design (Issue #13)

## Problem

The Queue screen (`/api/jobs`, `rack.js` `loadQueue()`) lists two kinds of
entries — `LlmJob` rows (correction/summary/voice_match/rediarize) and
transcription pipeline entries derived from `Transcript` + its `TranscriptionJob`
chunk rows. Neither source is ever removed once terminal, so completed,
failed, and cancelled jobs accumulate forever with no way to dismiss them.

## Constraint that shapes the design

Cancelled/failed transcription jobs still need their `TranscriptionJob` chunk
rows around, because Resume (`/api/transcripts/{id}/resume`) and Retry
(`/api/transcripts/{id}/retry-failed-chunks`) read those rows to know which
chunks to redo. Hard-deleting on clear would silently forfeit that ability.
So clearing must be non-destructive: a hide flag, not a delete.

## Schema changes

Two new boolean columns, added via the existing lightweight ALTER-TABLE
migration helper in `database/__init__.py` (default `False`, nullable):

- `LlmJob.dismissed`
- `Transcript.queue_dismissed`

## Scope: which statuses are clearable

Only `completed`, `failed`, `cancelled`. `partial` (some transcription chunks
failed, entry still has a live Retry action) is excluded — it stays visible
until its constituent chunks resolve to a terminal state.

## Backend changes (`app.py`, `services/llm_jobs.py`, `services/queue.py`)

- `GET /api/jobs` (`list_jobs`): exclude `LlmJob.dismissed == True` rows and
  `Transcript.queue_dismissed == True` transcripts from the entries list.
- `POST /api/jobs/{job_id}/dismiss`: set `LlmJob.dismissed = True`. 400 if the
  job's status isn't completed/failed/cancelled.
- `POST /api/transcripts/{transcript_id}/dismiss-queue-entry`: set
  `Transcript.queue_dismissed = True`. 400 if the transcript's derived queue
  status isn't completed/failed/cancelled.
- `POST /api/jobs/clear-by-status`: body `{"status": "completed"|"failed"|"cancelled"}`.
  Bulk-sets the dismiss flag on every matching `LlmJob` and `Transcript` row
  for `current_user`. Uses the same status derivation `_transcription_queue_entry`
  already does (transcript's own `status` column when not `processing`).
- `resume_transcript` and `retry_failed_chunks` (`services/queue.py` /
  `app.py`): reset `Transcript.queue_dismissed = False` as part of the
  resume/retry action, so a dismissed-then-resumed transcription reappears in
  the queue instead of silently vanishing.
- `rerun_llm_job` needs no change — it already creates a fresh `LlmJob` row
  (undismissed by default); the old dismissed row stays hidden.

## Frontend changes (`static/rack.js`)

- `jobActions()`: add a dismiss button for both `LlmJob`-kind and
  transcription-kind entries when status ∈ {completed, failed, cancelled}.
  Calls the appropriate dismiss endpoint based on `j.kind`, then reloads the
  queue.
- `loadQueue()` page header: three buttons — "Clear completed", "Clear
  failed", "Clear cancelled" — each POSTs to `clear-by-status` and reloads.
  A button is omitted (or disabled) when no jobs of that status currently
  exist in the loaded page.

## Out of scope

Issues #12 (queue audit / concurrency) and #11 (provider/model metadata +
comparison) are separate specs, not touched here.

## Testing

- Unit: dismiss endpoints reject non-terminal statuses; bulk clear only
  touches the targeted status and only the current user's rows.
- Unit: resume/retry clears `queue_dismissed`.
- Integration: `GET /api/jobs` omits dismissed rows; a fresh rerun's new
  `LlmJob` row still appears even though the prior dismissed row for the
  same transcript does not.
