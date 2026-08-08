# Issue #147 Investigation

## Issue Summary
Title: "Fix N+1 queries in _serialize_transcript — batch LlmJob fetching"

## What the Issue Claims
- `_serialize_transcript()` (app.py:229) runs 5-8 `latest_job()` queries per transcript row
- For the tape library listing 100 transcripts, that's 500-800 individual DB queries per request
- Proposes `_batch_latest_jobs()` to fetch all latest jobs in one query

## Actual Current State (vs. Issue Claims)

### Line number drift
`_serialize_transcript` is at line 262, not 229. The code has been refactored since the issue was filed.

### False claim: list endpoint calls _serialize_transcript
The list endpoint `/api/transcripts` calls `_build_recent_transcripts` (line 489), which calls `_serialize_transcript_summary` (line 456), NOT `_serialize_transcript`. The summary serializer is a lightweight version that omits full_text, segments, corrected_text, and per-kind LLM job details.

`_serialize_transcript_summary` does NOT call `latest_job()` at all. Zero LlmJob queries.

### All _serialize_transcript callers are single-transcript
Grep shows 7 call sites, all returning exactly ONE transcript:
- Line 1059: transcribe endpoint (chunked path) — returns single new transcript
- Line 1109: transcribe endpoint (non-chunked path) — returns single new transcript
- Line 1191: `GET /api/transcripts/{id}` — single transcript by ID
- Line 1442: `PATCH /api/transcripts/{id}` — single transcript update
- Line 1629: `POST .../speakers/rename` — single transcript rename response
- Line 1670: `POST .../segments/retag` — single transcript retag response
- Line 1711: `POST .../voice-match` apply — single transcript response

No N+1 scenario exists. 3-8 `latest_job()` queries for ONE transcript is not the problem described.

### The actual remaining N+1: compute_queue_status
`_serialize_transcript_summary` calls `compute_queue_status(db, t)` per row (line 484). For 100 transcripts, that's 100 queries. However:
- `compute_queue_status` early-returns `None` for non-"processing" transcripts (line 156)
- Processing transcripts are rare in practice — most are "completed" or "failed"
- The query impact is bounded by how many transcripts happen to be mid-processing

### The issue's proposed fix is still useful
Even though the list N+1 it targets doesn't exist, batch infrastructure for LlmJob fetching is reusable and would clean up `_serialize_transcript`'s 3-8 individual queries (even for single transcripts). Batching `compute_queue_status` would also help `_serialize_transcript_summary`.

## Scope for Fix

### In scope
1. Add `batch_latest_jobs(transcript_ids)` to `services/llm_jobs.py` — returns `{(transcript_id, kind): LlmJob}` dict
2. Modify `_serialize_transcript` to accept optional `jobs_cache` parameter — falls back to individual `latest_job()` calls when not provided (backward compat)
3. Add `batch_queue_status(transcripts)` to combine `compute_queue_status` lookups
4. Modify `_serialize_transcript_summary` to accept optional `queue_status_cache`
5. Modify `_build_recent_transcripts` to batch-fetch before the loop

### Out of scope
- Changing any single-transcript endpoint callers — they can continue with no cache param (backward compat)
- Pre-fetching `latest_relabel` — only used when `include_relabel=True`, which is not in the list path

### Call sites that need updating
- `_build_recent_transcripts` (line 489): pass pre-fetched queue_status to `_serialize_transcript_summary`
- No changes needed to single-transcript callers of `_serialize_transcript`

## Risk
The batch query returns the same data as individual queries but in different order. Must ensure:
- `_serialize_transcript` produces identical JSON shape with or without cache
- `_serialize_transcript_summary` produces identical JSON shape with or without cache
- Existing tests pass unchanged (no behavioral change)

## Files to Modify
1. `services/llm_jobs.py` — add `batch_latest_jobs()` function
2. `services/queue.py` — add `batch_queue_status()` function (or batch helper)
3. `app.py` — modify `_serialize_transcript`, `_serialize_transcript_summary`, `_build_recent_transcripts`
