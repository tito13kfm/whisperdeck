# Issue #104 Investigation

## Real target
Standalone issue #104 — direct target.

## Current code

### `_finish` (services/llm_jobs.py:257-265)
```python
def _finish(db, job: LlmJob, status: str, error: str | None = None) -> None:
    db.refresh(job)
    if job.status == "cancelled":
        return                    # <-- progress counters NOT reset, no commit
    job.status = status
    job.error = error
    job.updated_at = utcnow_naive()
    db.commit()
```

### `cancel_llm_job` (services/llm_jobs.py:235-245)
```python
def cancel_llm_job(db, user_id: int, job_id: int) -> LlmJob:
    job = db.query(LlmJob).filter(LlmJob.id == job_id, LlmJob.user_id == user_id).first()
    if not job:
        raise LookupError("Job not found")
    if job.status not in ACTIVE_STATUSES:
        raise ValueError(f"Cannot cancel a job with status '{job.status}'")
    job.status = "cancelled"
    job.updated_at = utcnow_naive()
    db.commit()
    return job
```

### `serialize_llm_job` (services/llm_jobs.py:48-70)
Reports progress as `{"done": job.progress_done or 0, "total": job.progress_total or 0}` with no status-sensitive filtering. Cancelled jobs report stale progress.

### Correction path (services/llm_jobs.py:298-319)
```python
result = await correct_transcript(...)  # returns 'ok' | 'failed' | 'cancelled'
if result == "ok":
    ...
    _finish(db, job, "completed")
elif result == "failed":
    _finish(db, job, "failed", transcript.correction_error)
# 'cancelled': status already set by cancel_llm_job — leave it.
```
Correction is the only path that returns "cancelled" without calling `_finish` at all.

### Frontend (static/rack.js:3236-3237)
```javascript
function llmJobActive(job) {
  return job && (job.status === 'pending' || job.status === 'running');
}
```
Cancelled jobs are not "active", so polling stops. The stale progress remains visible until user navigates away and back.

## Call sites in scope
- `_finish` has 29 call sites (lines 288, 295, 316, 318, 337, 339, 356, 358, 369, 397, 463, 465, 470, 473, 498, 500, 503, 506, 515, 563, 567, 572, 594, 606, 609, 612, 627, 633) — all single-function, fix at function level covers all.
- `cancel_llm_job` has 1 API call site (`POST /api/jobs/{job_id}/cancel`, app.py:2452) — serializes job immediately after cancel.

## Sibling sweep

### Cancelled status checks in llm_jobs.py
- Line 260: `_finish` early return — **the bug site**
- Line 306: `cancelled()` closure for correction batch check — returns bool, no progress modification
- Line 385: tagging batch check — returns early without calling `_finish`, but tagging doesn't use progress counters (no progress callback)
- Line 422: voice_match batch check — returns early without calling `_finish`, but progress callback already committed last `done` value, and voice_match calls `_finish` on completion (line 463) which would catch cancel

### Transcription jobs (services/queue.py)
Different model class (TranscriptionJob), different progress model (chunk-level, not progress_done/total columns). Cancel mechanism is transcript-level (`cancel_transcript_jobs`), not individual job. No similar bug.

### Other job that might leak progress on cancel
None found. All LLM job progress writes go through `job.progress_done`/`job.progress_total` columns, and all serialization goes through `serialize_llm_job`.

## Issue's suggested fix accuracy
The issue's Option A (fix `_finish` only) misses the correction path (line 319) which doesn't call `_finish` on cancel. It also misses the API endpoint's immediate response — `POST /api/jobs/{job_id}/cancel` returns `serialize_llm_job(job)` before `_finish` runs.

The issue's reasoning against Option B is incorrect: "_finish may have already committed partial progress before cancel lands" — but that's harmless, cancel overwriting progress to zero is the desired behavior.

## Fix plan
Fix BOTH locations:
1. `cancel_llm_job`: zero `progress_done` and `progress_total` before commit (covers immediate API response and correction path)
2. `_finish`: zero progress AND commit before early return on cancel (safety net for all other paths)

## Acceptance criteria (from issue)
The issue doesn't list explicit acceptance criteria, but the fix must:
- [ ] Cancelled jobs show `progress: {done: 0, total: 0}` in all API responses
- [ ] The DB row itself stores zeroed progress on cancel
- [ ] All 7+ job kinds affected
- [ ] No regression on non-cancelled job serialization
