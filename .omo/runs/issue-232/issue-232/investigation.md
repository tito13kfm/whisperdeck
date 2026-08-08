# Investigation: Issue #232 — Batch Management API

**Target**: `C:\Claude\whisperdesk-issue-232` (worktree, branch `issue-232`)
**Reports**: `C:\Claude\whisperdesk\.omo\runs\issue-232\issue-232\`
**Base**: `7125b90` (origin/master, includes #231)

## Issue summary

Three endpoints:
1. `GET /api/batches` — list batches with aggregate stats
2. `GET /api/batches/{batch_id}` — detail for one batch
3. `POST /api/batches/{batch_id}/cancel` — cancel active transcripts in batch

## Codebase inventory

### Transcript model
- `database/__init__.py:60` — `batch_id = Column(String(64), nullable=True, index=True)` (added by #231)
- `database/__init__.py:31` — `Transcript` class, 28+ columns

### Serialization
- `app.py:305-357` — `_serialize_transcript()` already includes `"batch_id": t.batch_id or None` on line 317 (added by #231)

### Existing cancel infrastructure
- `app.py:1798-1808` — `cancel_transcript` route: validates status == "processing", calls `cancel_transcript_jobs`
- `services/queue.py:281-309` — `cancel_transcript_jobs()`: cancels pending TranscriptionJob rows, flips transcript status to "cancelled". Does NOT validate transcript status — that guard is in the route handler (line 1805).

### Existing list pattern
- `app.py:1512-1522` — `GET /api/transcripts` with optional `batch_id` filter
- `app.py:611-642` — `_build_recent_transcripts()` with batch_id filtering

### Imports available in app.py (line 17-30)
- FastAPI: UploadFile, File, Form, HTTPException, Query, Body, Depends, Request
- SQLAlchemy: or_, func, Session
- Database models: Transcript, User, plus others
- `utcnow_naive` imported at line 30

## Call sites and entry points in scope

### GET /api/batches
- New route in `app.py` — no existing call sites
- Uses `Transcript.batch_id` column, `Transcript.user_id`, status counts via SQL GROUP BY

### GET /api/batches/{batch_id}
- New route in `app.py`
- Reuses `_serialize_transcript()` (app.py:305) and `_batch_latest_jobs()` (app.py:278)
- Pattern: same as `get_transcript` (app.py:1525-1532)

### POST /api/batches/{batch_id}/cancel
- New route in `app.py`
- Reuses `cancel_transcript_jobs()` (services/queue.py:281)
- Targets transcripts with status IN ("pending", "processing") — unlike the single `cancel_transcript` route which checks status == "processing"

### Nothing else in scope
- No other batch_id operations exist beyond #231's additions (bulk_transcribe, list_transcripts filter)
- No UI changes needed for this backend-only issue

## Sibling sweep

- `batch_id` column — used in 3 places: serializer (line 317), list filter (line 633), bulk_transcribe (line 1424). All three already handle it correctly from #231. No missing call sites.
- Timer/poller check — no timers or pollers reference batch_id. N/A.
- Guard sweep — `cancel_transcript` route (line 1805) checks `t.status != "processing"`. The batch cancel will need a different guard (allow pending + processing, reject already-terminal). This is an intentional divergence, not a missed sibling — the batch cancel has different semantics from single-transcript cancel.
- Status enum sweep — transcript status values used across the codebase: pending, processing, completed, failed, partial, cancelled. The batch cancel touches two statuses (pending, processing). No new status values introduced.

## Issue's approach — accuracy check

### GET /api/batches
Issue says: "Query Transcript grouped by batch_id, count statuses with SQL GROUP BY. Skip transcripts where batch_id IS NULL."
**Verdict**: Correct. SQLAlchemy pattern for group-by counts is standard.

Issue says: "first_title from the transcript with the lowest id in that batch"
**Verdict**: Correct but imprecise. Lowest `id` is a reasonable proxy for upload order since #231 processes files sequentially. We'll use `MIN(id)` in a subquery.

Issue says: "limit (default 20, max 100), offset (default 0)"
**Verdict**: Standard. Matches existing patterns.

### GET /api/batches/{batch_id}
Issue says: "Query all transcripts WHERE batch_id = $batch_id AND user_id = $user_id. Serialize each with _serialize_transcript()."
**Verdict**: Correct. _serialize_transcript needs a jobs_map from _batch_latest_jobs — we need to batch that call, same pattern as existing routes.

Issue says: "Return 404 if no transcripts found for that batch."
**Verdict**: Correct.

### POST /api/batches/{batch_id}/cancel
Issue says: "Query transcripts WHERE batch_id = $batch_id AND user_id = $user_id AND status IN ('pending', 'processing'). For each, call the existing cancel_transcript() or equivalent logic from services/queue.py."
**Verdict**: Needs refinement. `cancel_transcript()` is the route handler (validates status == "processing"), not the right function. The correct function is `cancel_transcript_jobs()` from services/queue.py, which does the actual work. The route-level status check would reject pending transcripts.

Issue says: "If some cancel operations fail, continue with remaining and report partial success"
**Verdict**: Reasonable. `cancel_transcript_jobs` doesn't raise on failure (it does a simple query + update). But for robustness, we'll wrap each call in try/except.

Issue says: "Return 404 if batch not found"
**Verdict**: Correct.

Issue's response shape for cancel:
```json
{"batch_id": "...", "cancelled": 2, "already_terminal": 3}
```
**Verdict**: Good. But the issue also mentions an optional `errors` field for partial failures — I'll include this.

## Implementation plan

All changes in `app.py` only (backend routes). New test file `tests/test_batch_api.py`.

### Routes to add (app.py):
1. `GET /api/batches` — around line 1495-1510 area (after bulk_transcribe, before /api/search)
2. `GET /api/batches/{batch_id}` — right after #1
3. `POST /api/batches/{batch_id}/cancel` — right after #2

### Tests to write (tests/test_batch_api.py):
1. test_list_batches — 2 batches, verify counts, null batch_id excluded
2. test_list_batches_empty — no batch transcripts
3. test_get_batch_detail — full detail with transcript list
4. test_get_batch_not_found — 404
5. test_cancel_batch — 3 pending transcripts, cancel all
6. test_cancel_batch_mixed — mix of pending/processing/completed
7. test_cancel_batch_not_found — 404

### No Phase 1.5 needed
No job/state completion path with side effects is being changed. `cancel_transcript_jobs` is called as-is; we're only adding new callers, not modifying its internals.

## Acceptance criteria from issue #232
1. `GET /api/batches` returns batches with aggregate stats ✓ (targeting)
2. `GET /api/batches/{batch_id}` returns transcripts in batch ✓ (targeting)
3. `POST /api/batches/{batch_id}/cancel` cancels active transcripts ✓ (targeting)
4. All tests green including #231 tests ✓ (targeting)
5. Null batch_id transcripts excluded from batch listing ✓ (targeting)
