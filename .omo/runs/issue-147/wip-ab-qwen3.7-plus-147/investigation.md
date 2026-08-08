# Investigation: Issue #147 — N+1 queries in _serialize_transcript

## Issue summary (verbatim)

> `_serialize_transcript()` (app.py:229) runs 5-8 separate `latest_job()` queries per transcript row. For the tape library listing 100 transcripts, that's 500-800 individual DB queries per request.

## What the issue gets wrong

**The list endpoint does NOT use `_serialize_transcript`.** It uses `_serialize_transcript_summary` (app.py:456-486), a lightweight variant that omits `latest_job()` calls entirely.

- `GET /api/transcripts` (list) → `_build_recent_transcripts` (line 489) → `_serialize_transcript_summary` (line 498)
- `GET /api/transcripts/{id}` (detail) → `_serialize_transcript` (line 1191)
- Mutation endpoints (rename, retag, undo) → `_serialize_transcript` with `include_relabel=True`

So the "500-800 queries for 100 transcripts" claim is wrong. The list endpoint's per-transcript cost is `compute_queue_status()` only (line 484), which short-circuits to `None` for non-"processing" transcripts.

## What the issue gets right

`_serialize_transcript` (used for single-transcript detail) DOES run 5-8 `latest_job()` queries:
- Line 303: `latest_job(db, t.id, "correction")`
- Line 304: `latest_job(db, t.id, "summary")`
- Line 305: `latest_job(db, t.id, "voice_match")`
- Lines 326-330 (dictation only): `latest_job()` for `classify_intent`, `format_markdown`, `format_email`, `format_coding_prompt`

For a single transcript detail view, 5-8 queries is not catastrophic, but batching them into 1 query is still a win.

## Actual N+1 locations

### 1. `_serialize_transcript` (app.py:262-313)
- **Callers:** detail endpoint (line 1191), update endpoint (line 1442), rename (1629), retag (1670), undo (1710), upload pipeline (1059, 1109)
- **N+1:** 5-8 `latest_job()` calls per invocation
- **Fix:** Batch into single query, accept pre-fetched data

### 2. `_dictation_job_fields` (app.py:316-339)
- **Callers:** only `_serialize_transcript` (line 306)
- **N+1:** 4 `latest_job()` calls for dictation transcripts
- **Fix:** Part of the batch in `_serialize_transcript`

### 3. `_serialize_transcript_summary` (app.py:456-486)
- **Callers:** list endpoint via `_build_recent_transcripts` (line 498)
- **N+1:** `compute_queue_status(db, t)` per transcript (line 484)
- **Impact:** Only matters for "processing" transcripts (short-circuits otherwise)
- **Fix:** Batch `compute_queue_status` calls, or accept pre-fetched data

### 4. `_transcription_queue_entry` (app.py:2111-2133)
- **Callers:** `_build_jobs_payload` (line 525) → `GET /api/jobs`
- **N+1:** `compute_queue_status(db, t)` per transcript (line 2113)
- **Impact:** Only for transcripts with `status == "processing"` or `t.jobs`
- **Fix:** Batch `compute_queue_status` calls

## Call site enumeration (Complement Rule)

### `latest_job` callers (all in app.py):
| Line | Function | Context |
|------|----------|---------|
| 303 | `_serialize_transcript` | correction_job |
| 304 | `_serialize_transcript` | summary_job |
| 305 | `_serialize_transcript` | voice_match_job |
| 326 | `_dictation_job_fields` | classify_intent |
| 328 | `_dictation_job_fields` | format_markdown |
| 329 | `_dictation_job_fields` | format_email |
| 330 | `_dictation_job_fields` | format_coding_prompt |

**All `latest_job` callers are within `_serialize_transcript` or its helper `_dictation_job_fields`.** No external callers.

### `compute_queue_status` callers (all in app.py):
| Line | Function | Context |
|------|----------|---------|
| 300 | `_serialize_transcript` | detail view |
| 484 | `_serialize_transcript_summary` | list view |
| 2113 | `_transcription_queue_entry` | jobs payload |

**`compute_queue_status` has callers outside `_serialize_transcript`.** Batching must cover all three.

### `_serialize_transcript` callers (all in app.py):
| Line | Function | Endpoint |
|------|----------|----------|
| 1059 | `_run_transcription_pipeline` | POST /api/transcribe (chunked) |
| 1109 | `_run_transcription_pipeline` | POST /api/transcribe (inline) |
| 1191 | `get_transcript` | GET /api/transcripts/{id} |
| 1442 | `update_transcript` | PATCH /api/transcripts/{id} |
| 1629 | `rename_transcript_speaker` | POST .../speakers/rename |
| 1670 | `retag_transcript_segments` | POST .../segments/retag |
| 1710 | `undo_last_relabel` | POST .../relabel-undo |

## Proposed fix

### 1. Add `batch_latest_jobs` in `services/llm_jobs.py`

```python
def batch_latest_jobs(db: Session, transcript_ids: list[int]) -> dict:
    """Return {(transcript_id, kind): LlmJob} for the latest job of each kind.
    Single query instead of N*len(kinds) queries."""
    if not transcript_ids:
        return {}
    from sqlalchemy import func
    subq = (
        db.query(
            LlmJob.transcript_id,
            LlmJob.kind,
            func.max(LlmJob.id).label("max_id")
        )
        .filter(LlmJob.transcript_id.in_(transcript_ids))
        .group_by(LlmJob.transcript_id, LlmJob.kind)
        .subquery()
    )
    jobs = (
        db.query(LlmJob)
        .join(subq, LlmJob.id == subq.c.max_id)
        .all()
    )
    return {(j.transcript_id, j.kind): j for j in jobs}
```

### 2. Modify `_serialize_transcript` to accept pre-fetched jobs

Add optional `latest_jobs: dict | None = None` parameter. If provided, use it instead of calling `latest_job()`. If not provided, call `batch_latest_jobs([t.id])` to fetch in one query.

### 3. Modify `_dictation_job_fields` to accept pre-fetched jobs

Same pattern: accept `latest_jobs` dict, look up by `(t.id, kind)`.

### 4. Update callers of `_serialize_transcript`

- **List endpoint** (`_build_recent_transcripts`): Already uses `_serialize_transcript_summary`, no change needed for `latest_job` batching. But could batch `compute_queue_status` if needed.
- **Detail endpoint** (`get_transcript`): Call `batch_latest_jobs([t.id])` once, pass to `_serialize_transcript`.
- **Mutation endpoints** (rename, retag, undo): Same pattern.
- **Upload pipeline** (`_run_transcription_pipeline`): Same pattern.

### 5. `compute_queue_status` batching (optional, lower priority)

`compute_queue_status` is more complex: it queries TranscriptionJob, then potentially calls `has_budget` → `compute_audio_seconds_used` → more queries. Batching this is harder because it depends on transcript state. For now, leave it as-is since it short-circuits for non-"processing" transcripts.

## Acceptance criteria mapping

- [ ] **List endpoint uses batch job fetching** — N/A, list endpoint doesn't use `latest_job()`. But detail endpoint will.
- [ ] **`_serialize_transcript` accepts pre-fetched job data** — Yes, add `latest_jobs` param.
- [ ] **No behavioral change in the API response** — Verified by comparing serialized output.
- [ ] **Existing tests still pass** — Run test suite.

## Testing strategy

1. **Static check:** Read changed code, verify `batch_latest_jobs` returns same results as individual `latest_job()` calls.
2. **Unit tests:** Run existing test suite (`test.bat` or `./test.sh`).
3. **E2E (if browser available):** `e2e-regression-http` to verify no behavioral change in transcript detail/list views.

## Files to modify

- `services/llm_jobs.py` — add `batch_latest_jobs`
- `app.py` — modify `_serialize_transcript`, `_dictation_job_fields`, update callers

## Risk assessment

- **Low risk:** The fix is a pure optimization, no behavioral change.
- **Complement Rule:** All `latest_job` callers are within `_serialize_transcript` / `_dictation_job_fields`. No external callers to miss.
- **Edge cases:** Empty transcript list (batch returns `{}`), dictation vs meeting kind (dictation has 4 extra job kinds), no jobs for a transcript (lookup returns `None`).
