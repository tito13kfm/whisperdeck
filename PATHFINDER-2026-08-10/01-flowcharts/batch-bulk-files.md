# Feature: batch-bulk-files

## Sources consulted
- `app.py:1546-1690` (bulk_transcribe, full), `1692-1850` (batch management: list_batches/get_batch/cancel_batch, full), `1852-2153` (search/transcripts/files routes, full)
- `app.py:1111-1250` (_run_transcription_pipeline, confirmed shared not batch-specific)
- `app.py:327-354` (_batch_latest_jobs, _serialize_transcript signatures), `3290-3335` (_build_jobs_payload / GET /api/jobs)
- `backends/__init__.py:37` (LOCAL_PROVIDERS)
- `static/batch_aggregate.js` full file (34 lines)
- `static/rack.js:3418-3480` (loadQueue), `3000-3067` (bulk-upload submit handler), `3578` (only frontend caller of /api/batches*), `5938-5958` (renderFilesPage)
- Grep across static/*.js for api/batches — no caller of GET /api/batches or GET /api/batches/{id}

## Definitive verdict: inline in app.py, no hidden service module
Confirmed: all three entry points fully inline in app.py, no services/batch*.py exists. The "feature" is really three bolted-together concerns:

1. **Bulk upload + per-file transcription** (bulk_transcribe, app.py:1546): thin loop around the SAME shared `_run_transcription_pipeline` (app.py:1111) used by single-file transcribe/retranscribe. No batch-specific transcription logic — batching only means generating one batch_id and stamping it on each Transcript row, plus per-file try/except so one failure doesn't abort the rest.
2. **Batch status aggregation is duplicated across two paths that never talk to each other**:
   - Backend: GET /api/batches / /api/batches/{id} (app.py:1695,1770), SQL GROUP BY batch_id aggregate. **Confirmed dead from the UI** — grep of all static/*.js shows no fetch to plain /api/batches or /api/batches/{id}. Only /api/batches/{id}/cancel is called (rack.js:3578). Exercised only by tests/test_batch_api.py (docstring calls them "Backend-only").
   - Frontend: `computeBatchAggregate` (static/batch_aggregate.js), invoked from loadQueue() (rack.js:3425-3467), groups the LIVE job list from GET /api/jobs client-side and computes counts/badges independently in JS. This is what actually renders the progress UI users see.
   - **Batch progress the user sees comes from client-side aggregation over /api/jobs, not the backend's /api/batches aggregate.** Two mirrored implementations (SQL case/sum vs JS for-loop) compute the same rollup; one is unused. Flagged as duplication (not an active bug — no divergence risk today since only one path is live).
3. **File management** (list_files/delete_files, app.py:2033/2084): unrelated concern sharing the route range — reconciles UPLOAD_DIR disk contents against Transcript/TranscriptionJob DB rows for linked vs orphaned files, no relation to batches.

## Side effects
- DB writes: bulk_transcribe -> each pipeline call creates/commits Transcript row (+TranscriptionJob rows for chunked audio) tagged with shared batch_id. cancel_batch calls cancel_transcript_jobs per transcript (db.rollback() on per-transcript failure). delete_files nulls out audio_path/video_path/stereo_audio_path columns and commits.
- File storage: uploads land in UPLOAD_DIR (DATA_DIR/uploads, flat), pipeline transcode/cleanup may rewrite save_path to a new file in same dir.
- Call into transcription-pipeline per batch item confirmed identical to single-file path, batch_id passed through only.

## Error/fallback branches
- Per-file: HTTPException/Exception from pipeline caught (1672-1681), appended to errors[] keyed by index/filename, loop continues.
- Whole-batch fail only if ALL files failed (all_failed and not transcripts, 1683) -> 500.
- Partial success: response always includes batch_id + transcripts; errors key only if non-empty.
- cancel_batch: per-transcript try/except + rollback, already_terminal counted separately from cancelled.
- delete_files: validates all filenames up front (rejects whole batch on bad name before any deletion), then per-file skip reasons (in_use, shared, not_found_or_forbidden, remove_failed).

## Mermaid flowchart

```mermaid
flowchart TD
  U["User selects multiple files<br/>static/rack.js:3023 (bulk-start click)"] --> POST["POST /api/bulk-transcribe<br/>app.py:1546 bulk_transcribe"]
  POST --> VAL["Parse+validate settings/file_settings,<br/>kind, provider<br/>app.py:1561-1605"]
  VAL --> SIZE{"provider in LOCAL_PROVIDERS?<br/>app.py:1608"}
  SIZE -->|yes, >500MB combined| REJ["400 combined size exceeds limit<br/>app.py:1614-1618"]
  SIZE -->|no / within limit| BID["Generate batch_id<br/>app.py:1621"]
  BID --> LOOP["For each file: merge per-file overrides<br/>over global settings<br/>app.py:1636-1646"]
  LOOP --> SAVE["Write upload to UPLOAD_DIR<br/>app.py:1648-1653 (DATA_DIR/uploads)"]
  SAVE --> PIPE["_run_transcription_pipeline(batch_id=...)<br/>app.py:1656 -> app.py:1111"]
  PIPE -->|success| OK["Append to transcripts[]<br/>app.py:1670"]
  PIPE -->|HTTPException / Exception| ERR["Append to errors[], continue loop<br/>app.py:1672-1681"]
  OK --> MORE{"More files?"}
  ERR --> MORE
  MORE -->|yes| LOOP
  MORE -->|no| ALLFAIL{"all_failed and<br/>no transcripts?<br/>app.py:1683"}
  ALLFAIL -->|yes| FAIL500["500 All files failed<br/>app.py:1684"]
  ALLFAIL -->|no| RESP["Return {batch_id, transcripts, errors?}<br/>app.py:1686-1689"]
  RESP --> NAV["Frontend navigates to Queue page<br/>rack.js:3060"]

  NAV --> POLL["loadQueue() polls GET /api/jobs<br/>rack.js:3425 -> app.py:3322 list_jobs"]
  POLL --> BUILD["_build_jobs_payload<br/>app.py:3290-3319 per-transcript job entry incl. batch_id"]
  BUILD --> GROUP["Group jobs by batch_id client-side<br/>rack.js:3437-3443"]
  GROUP --> AGG["computeBatchAggregate(group)<br/>static/batch_aggregate.js:8"]
  AGG --> RENDER["Render batch-group progress bar/badge<br/>rack.js:3446-3480"]

  RENDER -.->|user clicks Cancel batch| CANCELPOST["POST /api/batches/{batch_id}/cancel<br/>app.py:1809 cancel_batch"]
  CANCELPOST --> CANCELLOOP["For each pending/processing transcript:<br/>cancel_transcript_jobs<br/>app.py:1831-1840"]
  CANCELLOOP -->|per-item exception| CROLLBACK["db.rollback(), record error, continue<br/>app.py:1836-1838"]
  CANCELLOOP --> CANCELRESP["Return {cancelled, already_terminal, errors?}<br/>app.py:1842-1849"]

  subgraph DEAD["Unused by frontend (backend-only, exercised only by tests/test_batch_api.py)"]
    GETBATCHES["GET /api/batches<br/>app.py:1695 list_batches<br/>SQL GROUP BY batch_id aggregate"]
    GETBATCHDETAIL["GET /api/batches/{batch_id}<br/>app.py:1770 get_batch"]
  end

  F1["GET /api/files<br/>app.py:2033 list_files"] --> F2["Scan UPLOAD_DIR,<br/>cross-ref Transcript/TranscriptionJob paths<br/>app.py:2040-2076 _transcript_refs_by_realpath / _live_job_paths"]
  F2 --> F3["Return {linked[], orphaned[] (admin-only), totals}<br/>app.py:2080-2081"]
  F3 --> F4["renderFilesPage()<br/>rack.js:5938"]
  F4 -.->|user selects + deletes| F5["POST /api/files/delete<br/>app.py:2084 delete_files"]
  F5 --> F6["Validate names, resolve to real paths<br/>app.py:2092-2099 _resolve_upload_name"]
  F6 --> F7{"in_use / shared /<br/>not_found_or_forbidden?<br/>app.py:2106-2134"}
  F7 -->|skip| F8["Record skip reason, continue<br/>no side effect for that file"]
  F7 -->|ok| F9["os.remove + null out Transcript path field(s)<br/>app.py:2135-2149"]
```

## External dependencies
- transcription-pipeline feature: `_run_transcription_pipeline` (app.py:1111) — the exact same function used by /api/transcribe and retranscribe. Batch adds nothing except passing batch_id through.
- chunked-queue/TranscriptionJob: touched indirectly via pipeline; directly by cancel_batch (cancel_transcript_jobs), file-deletion safety checks (_live_job_paths, _transcript_pipeline_can_resume).
- jobs/queue feature: GET /api/jobs (_build_jobs_payload) is the actual data source for the batch progress UI users see, not GET /api/batches.
- DB models: Transcript, TranscriptionJob, LlmJob (via _batch_latest_jobs, _serialize_transcript).
- backends registry: get_provider, LOCAL_PROVIDERS for provider validation/local-size gating.

## Confidence and gaps
High confidence on inline-vs-service verdict and dead-GET-/api/batches finding (grep-confirmed zero callers). Moderate confidence on _run_transcription_pipeline internals beyond signature/first ~140 lines — full body not read (transcription-pipeline's own territory). No batch_id-conditional branching observed in the portion read, but full ~600-line function not verified for this specifically.
