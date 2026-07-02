# Transcription UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the transcription upload/settings flow trustworthy and controllable — one credential list (providers + HF token), one model-selection flow (default in Settings, override per upload), real progress visibility (including "why is this queued" for rate-limit throttling), and the ability to cancel/resume an in-progress transcription.

**Architecture:** All changes extend the existing chunking/queue system (`services/queue.py`) and the existing per-request `db`/`current_user` route pattern in `app.py` — no new subsystems. `queue_status` is computed on read from existing `TranscriptionJob` rows and budget-accounting functions, never persisted. `cancelled` is a new terminal value on both `Transcript.status` and `TranscriptionJob.status`, handled by extending the existing dispatch/finalize logic in `services/queue.py`, which already treats non-`pending`/`running` job statuses as terminal for finalization purposes.

**Tech Stack:** FastAPI, SQLAlchemy/SQLite (one additive column via the existing `ensure_columns` helper), vanilla JS/single-file HTML frontend (no build step) — same as the rest of this codebase.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-01-transcription-ux-improvements-design.md`. Decisions there (HF token lives inside the Providers card, `default_model` is the single source of truth for model selection, cancel is "stop dispatching, let in-flight finish and discard," resume mirrors retry-failed-chunks, no backend change for navigate-away) are not up for re-litigation during implementation.
- No test suite exists in this repo. Every task's verification is a manual PowerShell/Python/browser check against a running `app.py`, matching this codebase's established pattern. Do not introduce a pytest suite.
- `GET /api/settings` does not mask any field (including `hf_token`) — this is existing, unchanged behavior, not a gap to fix here (see spec's "Explicitly out of scope").
- Do not change chunking, silence-detection, or the worker dispatch loop's core mechanics — only add visibility into and control over the existing system.
- Every new/modified route must use the existing `db: Session = Depends(get_db)`, `current_user: User = Depends(get_current_user)` pattern and ownership filter (`Transcript.user_id == current_user.id`) already used by every other transcript route in `app.py`.

---

## File Structure

- **Modify `database/__init__.py`**: add `Transcript.processed_size_bytes` (nullable Integer, additive column via `ensure_columns`). `TranscriptionJob.status` and `Transcript.status` gain a `cancelled` value — this is a comment/docstring change only (SQLite `String` columns have no enum constraint to update), so no schema migration is needed for the status values themselves.
- **Modify `services/queue.py`**: add `cancel_transcript_jobs`, `resume_cancelled_chunks` (mirrors `retry_failed_chunks`), `compute_queue_status`, `estimate_resume_seconds`, `_oldest_contributing_timestamp`; extend `_finalize_if_done` to handle the `cancelled` case; fix `queue_worker_tick`'s finalize-candidate discovery to cover transcripts with only `running` jobs left (needed so a transcript cancelled while a job is still in flight actually reaches `cancelled` once that job finishes).
- **Modify `app.py`**: `_serialize_transcript` gains a `db` parameter (needed for `compute_queue_status`) and two new response fields (`processed_size_bytes`, `queue_status`); `transcribe_audio` persists `processed_size_bytes` on both the sync and chunked paths; two new routes (`POST /api/transcripts/{id}/cancel`, `POST /api/transcripts/{id}/resume`).
- **Modify `static/index.html`**: HF token row moves into the Providers card; `fetchProviderModels`/`loadTxModels` wire up `default_model` persistence and pre-fill; progress screen shows model/queue-status/processed-size instead of raw upload size, plus a Cancel button; transcript detail page gets a Resume button for `cancelled` transcripts; dashboard list gets a queue-status-aware badge for `processing` transcripts.

---

### Task 1: `processed_size_bytes` column and persistence

**Files:**
- Modify: `database/__init__.py` (add column + `ensure_columns` call)
- Modify: `app.py` (persist the value on both upload paths)

**Interfaces:**
- Produces: `Transcript.processed_size_bytes: int | None`.
- Consumes: nothing new — `file_size` (sync path) and the `chunks` list (chunked path) are already computed in `transcribe_audio` before this task's changes.

- [ ] **Step 1: Add the column**

In `database/__init__.py`, find:
```python
    audio_path = Column(String(512), nullable=True)  # post-transcode, pre-chunk-split file; used by chunked-path diarization
    diarize_requested = Column(Boolean, default=False)
    num_speakers = Column(Integer, nullable=True)  # None = auto-detect (pyannote only; heuristic fallback defaults to 2)
```
Change to:
```python
    audio_path = Column(String(512), nullable=True)  # post-transcode, pre-chunk-split file; used by chunked-path diarization
    diarize_requested = Column(Boolean, default=False)
    num_speakers = Column(Integer, nullable=True)  # None = auto-detect (pyannote only; heuristic fallback defaults to 2)
    processed_size_bytes = Column(Integer, nullable=True)  # post-transcode size (sum of chunk files if chunked) — NOT the raw upload size
```

Find:
```python
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER"})
```
Change to:
```python
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER", "processed_size_bytes": "INTEGER"})
```

- [ ] **Step 2: Verify against a throwaway copy of the real database**

```powershell
cd C:\Claude\whisperdesk
Copy-Item data\whisperdesk.db data\_test_ux_migrate.db
.venv\Scripts\python.exe -c "
from database import init_db, Transcript
engine, SessionLocal, migrated = init_db('data/_test_ux_migrate.db')
print('migrated tables (expect empty, already-migrated db):', migrated)
db = SessionLocal()
t = db.query(Transcript).first()
print('processed_size_bytes column readable:', t.processed_size_bytes if t else 'no rows')
"
Remove-Item data\_test_ux_migrate.db
```
Expected: `migrated tables (expect empty, already-migrated db): []`, `processed_size_bytes column readable: None`.

- [ ] **Step 3: Persist the value on both upload paths in `app.py`**

Find (the chunked-path branch, right after chunk creation):
```python
        try:
            chunks = await chunk_audio(str(save_path), str(UPLOAD_DIR), target_chunk_bytes=threshold_bytes)
        except AudioPrepError as e:
            raise HTTPException(status_code=500, detail=str(e))

        transcript = transcription_service.create_transcript_stub(
            db,
            current_user.id,
            filename=file.filename or "audio.mp3",
            provider_name=provider,
            model=model or provider_config.get("default_model") or "",
            language=language,
            audio_path=str(save_path),
            diarize_requested=diarize,
            title=title or file.filename,
            num_speakers=num_speakers,
        )
        create_chunk_jobs(db, transcript.id, chunks)
        return _serialize_transcript(transcript)
```
Change to:
```python
        try:
            chunks = await chunk_audio(str(save_path), str(UPLOAD_DIR), target_chunk_bytes=threshold_bytes)
        except AudioPrepError as e:
            raise HTTPException(status_code=500, detail=str(e))

        transcript = transcription_service.create_transcript_stub(
            db,
            current_user.id,
            filename=file.filename or "audio.mp3",
            provider_name=provider,
            model=model or provider_config.get("default_model") or "",
            language=language,
            audio_path=str(save_path),
            diarize_requested=diarize,
            title=title or file.filename,
            num_speakers=num_speakers,
        )
        # Real processed size, not the raw upload size — the sum of all
        # chunk files, since that's what actually gets sent to the provider.
        transcript.processed_size_bytes = sum(os.path.getsize(c["path"]) for c in chunks)
        db.commit()
        create_chunk_jobs(db, transcript.id, chunks)
        return _serialize_transcript(transcript)
```
(This task deliberately leaves the `_serialize_transcript(transcript)` call signature untouched — Task 2 is what changes that function to take `db` as well, and updates every call site including this one. Changing it here would reference a parameter the function doesn't accept yet.)

Find (the sync-path branch, right after the threshold check that decides NOT to chunk):
```python
    try:
        transcript = await transcription_service.transcribe(
            db,
            current_user.id,
            audio_path=str(save_path),
            provider_name=provider,
            provider_config=provider_config,
            title=title or file.filename,
            language=language,
            model=model or provider_config.get("default_model"),
            temperature=temperature,
        )
```
Change to:
```python
    try:
        transcript = await transcription_service.transcribe(
            db,
            current_user.id,
            audio_path=str(save_path),
            provider_name=provider,
            provider_config=provider_config,
            title=title or file.filename,
            language=language,
            model=model or provider_config.get("default_model"),
            temperature=temperature,
        )
        transcript.processed_size_bytes = file_size
        db.commit()
```

- [ ] **Step 4: Commit**

```powershell
git add database/__init__.py app.py
git commit -m "Add processed_size_bytes column, persist real audio size on both upload paths"
```

---

### Task 2: `_serialize_transcript` gains `db` and `queue_status`; budget/finalize helpers in `services/queue.py`

**Files:**
- Modify: `services/queue.py` (new functions: `_oldest_contributing_timestamp`, `estimate_resume_seconds`, `compute_queue_status`)
- Modify: `app.py` (`_serialize_transcript` signature change and all 5 call sites)

**Interfaces:**
- Produces: `compute_queue_status(db, transcript) -> dict | None` — the three-state shape from the spec (`transcribing`/`queued`/`rate_limited`). `_serialize_transcript(db, t) -> dict` (signature changed from `_serialize_transcript(t)`).
- Consumes: `compute_audio_seconds_used`, `has_budget`, `PROVIDER_LIMITS`, `DEFAULT_LIMITS` (all exist already in `services/queue.py`).

**Why `estimate_resume_seconds` is a new function rather than reusing `has_budget` directly:** `has_budget` answers "is there room right now" — it doesn't tell you *when* there will be room. Answering that means finding the oldest row currently counted in the binding window and computing how long until it ages out of that window (a completely different query shape), which is why this needs its own helper.

- [ ] **Step 1: Add `_oldest_contributing_timestamp`, `estimate_resume_seconds`, and `compute_queue_status` to `services/queue.py`**

Add these functions right after `has_budget` (before `def _normalize`):
```python
def _oldest_contributing_timestamp(db, user_id: int, provider: str, window_seconds: int):
    """Return the earliest updated_at among the rows compute_audio_seconds_used
    would count for this user+provider within the trailing window_seconds —
    i.e. the row whose usage will be the next to age out. Returns None if
    nothing is currently contributing (budget isn't actually constrained)."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=window_seconds)

    transcript_times = [
        t.updated_at for t in db.query(Transcript).filter(
            Transcript.user_id == user_id,
            Transcript.provider == provider,
            Transcript.status.in_(["completed", "partial"]),
            Transcript.updated_at >= cutoff,
        ).all()
    ]
    job_times = [
        j.updated_at for j in (
            db.query(TranscriptionJob)
            .join(Transcript, TranscriptionJob.transcript_id == Transcript.id)
            .filter(
                Transcript.user_id == user_id,
                Transcript.provider == provider,
                Transcript.status.notin_(["completed", "partial"]),
                TranscriptionJob.status.in_(["running", "completed"]),
                TranscriptionJob.updated_at >= cutoff,
            )
            .all()
        )
    ]
    all_times = transcript_times + job_times
    return min(all_times) if all_times else None


def estimate_resume_seconds(db, user_id: int, provider: str, additional_seconds: float) -> float:
    """Best-effort estimate of when has_budget would next return True for a
    job needing additional_seconds. Checks both the hourly (ash) and daily
    (asd) windows — whichever is actually blocking — and returns how long
    until the oldest row counted in that window ages out. Approximate: it
    doesn't account for other jobs adding new usage before then, since this
    is a UI estimate ("resuming in ~Nm"), not a scheduling guarantee."""
    limits = PROVIDER_LIMITS.get(provider, DEFAULT_LIMITS)
    now = datetime.datetime.utcnow()
    candidates = []
    for window_seconds, cap_key in ((3600, "ash"), (86400, "asd")):
        used = compute_audio_seconds_used(db, user_id, provider, window_seconds)
        if used + additional_seconds > limits[cap_key]:
            oldest = _oldest_contributing_timestamp(db, user_id, provider, window_seconds)
            if oldest:
                expiry = oldest + datetime.timedelta(seconds=window_seconds)
                candidates.append((expiry - now).total_seconds())
    if not candidates:
        return 0.0
    return max(0.0, max(candidates))


def compute_queue_status(db, transcript) -> Optional[dict]:
    """Live status for a 'processing' transcript, computed on read (never
    persisted) — tells the frontend WHY an upload looks like it's waiting:
    actively transcribing a chunk, queued behind concurrency, or blocked on
    the provider's rate-limit budget. Returns None once status isn't
    'processing' anymore (terminal states carry their own meaning)."""
    if transcript.status != "processing":
        return None

    jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == transcript.id).all()
    if not jobs:
        return None  # single-shot sync path never has job rows

    chunks_done = sum(1 for j in jobs if j.status == "completed")
    chunks_total = len(jobs)

    if any(j.status == "running" for j in jobs):
        return {"state": "transcribing", "chunks_done": chunks_done, "chunks_total": chunks_total}

    pending = sorted((j for j in jobs if j.status == "pending"), key=lambda j: j.chunk_index)
    if not pending:
        # Everything left is "failed" awaiting its backoff window — not
        # rate-limited, just waiting on the retry timer.
        return {"state": "queued", "chunks_done": chunks_done, "chunks_total": chunks_total}

    next_job = pending[0]
    job_duration = next_job.end_time - next_job.start_time
    if has_budget(db, transcript.user_id, transcript.provider, job_duration):
        return {"state": "queued", "chunks_done": chunks_done, "chunks_total": chunks_total}

    resume_in = estimate_resume_seconds(db, transcript.user_id, transcript.provider, job_duration)
    return {
        "state": "rate_limited",
        "chunks_done": chunks_done,
        "chunks_total": chunks_total,
        "resume_in_seconds": round(resume_in),
    }
```

- [ ] **Step 2: Update `_serialize_transcript`'s signature and all 5 call sites in `app.py`**

Find:
```python
def _serialize_transcript(t: Transcript) -> dict:
    jobs = t.jobs or []
    job_progress = None
    if jobs:
        job_progress = {
            "total": len(jobs),
            "completed": sum(1 for j in jobs if j.status == "completed"),
            "failed": sum(1 for j in jobs if j.status == "failed"),
        }
    return {
        "id": t.id,
        "title": t.title,
        "filename": t.filename,
        "duration_seconds": t.duration_seconds,
        "provider": t.provider,
        "model": t.model,
        "language": t.language,
        "status": t.status,
        "full_text": t.full_text,
        "segments": t.segments or [],
        "speaker_count": t.speaker_count,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "has_summary": t.summary is not None,
        "job_progress": job_progress,
    }
```
Change to:
```python
def _serialize_transcript(db: Session, t: Transcript) -> dict:
    jobs = t.jobs or []
    job_progress = None
    if jobs:
        job_progress = {
            "total": len(jobs),
            "completed": sum(1 for j in jobs if j.status == "completed"),
            "failed": sum(1 for j in jobs if j.status == "failed"),
        }
    return {
        "id": t.id,
        "title": t.title,
        "filename": t.filename,
        "duration_seconds": t.duration_seconds,
        "provider": t.provider,
        "model": t.model,
        "language": t.language,
        "status": t.status,
        "full_text": t.full_text,
        "segments": t.segments or [],
        "speaker_count": t.speaker_count,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "has_summary": t.summary is not None,
        "job_progress": job_progress,
        "processed_size_bytes": t.processed_size_bytes,
        "queue_status": compute_queue_status(db, t),
    }
```

Add the import at the top of `app.py`, alongside the existing `services.queue` imports:
```python
from services.queue import create_chunk_jobs, retry_failed_chunks, queue_worker_loop, compute_queue_status
```
(This replaces whatever the current import line for `services.queue` reads — check the existing line first; it likely already imports `create_chunk_jobs, retry_failed_chunks, queue_worker_loop` and just needs `compute_queue_status` appended to that same line.)

Update every call site to pass `db` as the first argument (there are 5 in total; `transcribe_audio` alone has two — one in its chunked branch right after `create_chunk_jobs(db, transcript.id, chunks)`, one in its sync-path branch after the diarization block):
- Both `return _serialize_transcript(transcript)` lines inside `transcribe_audio` (chunked branch and sync-path branch) → `return _serialize_transcript(db, transcript)`.
- `list_transcripts`: `return [_serialize_transcript(t) for t in transcripts]` → `return [_serialize_transcript(db, t) for t in transcripts]`.
- `get_transcript`: `return _serialize_transcript(t)` → `return _serialize_transcript(db, t)`.
- `update_transcript`: `return _serialize_transcript(t)` → `return _serialize_transcript(db, t)`.

- [ ] **Step 3: Verify `compute_queue_status` against a throwaway database**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe -c "
import datetime
from database import init_db, Transcript, TranscriptionJob
from services.auth import create_user
from services.queue import compute_queue_status

engine, SessionLocal, _ = init_db('data/_test_queue_status.db')
db = SessionLocal()
u = create_user(db, 'qstatustest', 'pw')

# Case 1: one job running -> transcribing
t1 = Transcript(user_id=u.id, title='a', filename='a.mp3', provider='groq', status='processing')
db.add(t1); db.commit()
db.add(TranscriptionJob(transcript_id=t1.id, chunk_index=0, start_time=0, end_time=100, audio_path='x', status='running'))
db.add(TranscriptionJob(transcript_id=t1.id, chunk_index=1, start_time=100, end_time=200, audio_path='y', status='pending'))
db.commit()
print('case 1 (expect transcribing):', compute_queue_status(db, t1))

# Case 2: rate-limited -- push usage near the 7200s/hr groq cap, then a pending job that would exceed it
t2 = Transcript(user_id=u.id, title='b', filename='b.mp3', provider='groq', status='completed', duration_seconds=7000.0, updated_at=datetime.datetime.utcnow())
db.add(t2); db.commit()
t3 = Transcript(user_id=u.id, title='c', filename='c.mp3', provider='groq', status='processing')
db.add(t3); db.commit()
db.add(TranscriptionJob(transcript_id=t3.id, chunk_index=0, start_time=0, end_time=500, audio_path='z', status='pending'))
db.commit()
print('case 2 (expect rate_limited):', compute_queue_status(db, t3))

# Case 3: no prior usage, pending job well under budget -> queued
t4 = Transcript(user_id=u.id, title='d', filename='d.mp3', provider='groq', status='processing')
db.add(t4); db.commit()
db.add(TranscriptionJob(transcript_id=t4.id, chunk_index=0, start_time=0, end_time=50, audio_path='w', status='pending'))
db.commit()
print('case 3 (expect queued):', compute_queue_status(db, t4))
"
Remove-Item data\_test_queue_status.db
```
Expected: case 1 prints `{'state': 'transcribing', 'chunks_done': 0, 'chunks_total': 2}`; case 2 prints a dict with `'state': 'rate_limited'` and a positive `resume_in_seconds`; case 3 prints `{'state': 'queued', 'chunks_done': 0, 'chunks_total': 1}`.

- [ ] **Step 4: Confirm the app still imports and boots**

```powershell
.venv\Scripts\python.exe -c "import app; print('OK')"
```
Expected: `OK`.

- [ ] **Step 5: Commit**

```powershell
git add services/queue.py app.py
git commit -m "Add queue_status computation and thread db through _serialize_transcript"
```

---

### Task 3: Cancel and resume — data model, `services/queue.py`, and routes

**Files:**
- Modify: `services/queue.py` (`cancel_transcript_jobs`, `resume_cancelled_chunks`, `_finalize_if_done` extension, `queue_worker_tick` finalize-discovery fix)
- Modify: `app.py` (two new routes)

**Interfaces:**
- Produces: `cancel_transcript_jobs(db, transcript_id: int) -> int` (returns count of jobs cancelled), `resume_cancelled_chunks(db, transcript_id: int) -> int` (returns count re-queued).
- Consumes: existing `Transcript`, `TranscriptionJob` models; `_finalize_if_done`, `queue_worker_tick` (both modified in this task).

**Why `queue_worker_tick`'s finalize-discovery needs fixing here:** today, `_finalize_if_done` is only ever called for transcript IDs that had at least one `pending` job *at the start of the current tick* (see the `by_transcript` dict built from the `pending`-status query). If `cancel_transcript_jobs` cancels every `pending` job on a transcript while one of its jobs is still `running` (dispatched in an earlier tick, awaiting the provider), that transcript will never again appear in any future tick's `by_transcript` dict — it has zero `pending` jobs from that point on. Its `_finalize_if_done` call would never fire once the running job finishes, leaving the transcript stuck at `status="processing"` forever. This task fixes that by also checking finalize-eligibility for every `Transcript` currently `processing`, not only ones with jobs pending this tick.

- [ ] **Step 1: Add `cancel_transcript_jobs` and `resume_cancelled_chunks` to `services/queue.py`**

Add these functions right after `retry_failed_chunks`:
```python
def cancel_transcript_jobs(db, transcript_id: int) -> int:
    """Mark every still-pending job for this transcript as cancelled, so
    the worker stops dispatching new work for it. Jobs already 'running'
    are left alone — they're mid-flight to the provider and will finish
    naturally; _finalize_if_done discards their result once they do (see
    that function's cancelled-transcript branch). Returns how many
    pending jobs were cancelled.

    If nothing is left running after this call, the transcript is marked
    cancelled immediately — otherwise the next tick's finalize pass
    (queue_worker_tick, fixed in this same task to check ALL processing
    transcripts) picks it up once the running job(s) finish."""
    pending = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "pending")
        .all()
    )
    for job in pending:
        job.status = "cancelled"
    db.commit()

    still_running = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "running")
        .count()
    )
    if still_running == 0:
        transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
        if transcript:
            transcript.status = "cancelled"
            transcript.updated_at = datetime.datetime.utcnow()
            db.commit()
    return len(pending)


def resume_cancelled_chunks(db, transcript_id: int) -> int:
    """Reset every cancelled job for this transcript back to pending so
    the worker picks it up again. Returns how many were reset."""
    cancelled = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "cancelled")
        .all()
    )
    for job in cancelled:
        job.status = "pending"
        job.attempts = 0
        job.error = None
    if cancelled:
        transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
        if transcript:
            transcript.status = "processing"
        db.commit()
    return len(cancelled)
```

- [ ] **Step 2: Extend `_finalize_if_done` to handle a cancelled transcript**

Find:
```python
async def _finalize_if_done(db, transcript_id: int, diarization_service) -> None:
    jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == transcript_id).all()
    if not jobs or any(j.status in ("pending", "running") for j in jobs):
        return  # still work outstanding

    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        return

    segments, full_text = merge_chunk_results(jobs)
```
Change to:
```python
async def _finalize_if_done(db, transcript_id: int, diarization_service) -> None:
    jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == transcript_id).all()
    if not jobs or any(j.status in ("pending", "running") for j in jobs):
        return  # still work outstanding

    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        return

    if any(j.status == "cancelled" for j in jobs):
        # Cancellation always wins: discard whatever any job produced
        # (per the design's "result is simply discarded rather than
        # merged") rather than partially merging results from jobs that
        # happened to finish after cancel was requested.
        transcript.status = "cancelled"
        transcript.updated_at = datetime.datetime.utcnow()
        db.commit()
        return

    segments, full_text = merge_chunk_results(jobs)
```

- [ ] **Step 3: Fix `queue_worker_tick`'s finalize-candidate discovery**

Find:
```python
        pending = db.query(TranscriptionJob).filter(TranscriptionJob.status == "pending").all()
        by_transcript = {}
        for job in pending:
            by_transcript.setdefault(job.transcript_id, []).append(job)

        for transcript_id, jobs in by_transcript.items():
```
Change to:
```python
        pending = db.query(TranscriptionJob).filter(TranscriptionJob.status == "pending").all()
        by_transcript = {}
        for job in pending:
            by_transcript.setdefault(job.transcript_id, []).append(job)

        # Finalize-check every processing transcript, not just ones with
        # pending jobs this tick. Needed for cancel: cancelling every
        # pending job on a transcript that still has a job 'running'
        # leaves it with zero pending jobs from then on, so it would never
        # appear in by_transcript again — without this, it would never
        # reach _finalize_if_done once that running job completes and
        # would stay stuck at status='processing' forever.
        processing_ids = {
            row[0] for row in db.query(Transcript.id).filter(Transcript.status == "processing").all()
        }
        finalize_candidate_ids = set(by_transcript.keys()) | processing_ids

        for transcript_id, jobs in by_transcript.items():
```

Find (the end of that same `for transcript_id, jobs in by_transcript.items():` loop body):
```python
            await _finalize_if_done(db, transcript_id, diarization_service)
    finally:
        db.close()
```
Change to:
```python
            await _finalize_if_done(db, transcript_id, diarization_service)

        for transcript_id in finalize_candidate_ids - set(by_transcript.keys()):
            # Transcripts with no pending jobs this tick (e.g. everything
            # still running from a prior tick, or already fully terminal
            # apart from a status flip cancel_transcript_jobs deferred to
            # here) still need a finalize check — see the comment above
            # processing_ids for why this is necessary.
            await _finalize_if_done(db, transcript_id, diarization_service)
    finally:
        db.close()
```

- [ ] **Step 4: Add the cancel and resume routes to `app.py`**

Find:
```python
from services.queue import create_chunk_jobs, retry_failed_chunks, queue_worker_loop, compute_queue_status
```
Change to:
```python
from services.queue import create_chunk_jobs, retry_failed_chunks, queue_worker_loop, compute_queue_status, cancel_transcript_jobs, resume_cancelled_chunks
```

Find:
```python
@app.post("/api/transcripts/{transcript_id}/retry-failed-chunks")
async def retry_transcript_chunks(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    count = retry_failed_chunks(db, transcript_id)
    return {"ok": True, "retried": count}
```
Add immediately after it:
```python
@app.post("/api/transcripts/{transcript_id}/cancel")
async def cancel_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if t.status != "processing":
        raise HTTPException(status_code=400, detail=f"Cannot cancel a transcript with status '{t.status}'")
    count = cancel_transcript_jobs(db, transcript_id)
    return {"ok": True, "cancelled": count}


@app.post("/api/transcripts/{transcript_id}/resume")
async def resume_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if t.status != "cancelled":
        raise HTTPException(status_code=400, detail=f"Cannot resume a transcript with status '{t.status}'")
    count = resume_cancelled_chunks(db, transcript_id)
    return {"ok": True, "resumed": count}
```

- [ ] **Step 5: Verify cancel-while-running reaches `cancelled`, and resume works — end to end against a real Groq key**

```powershell
cd C:\Claude\whisperdesk
$groqKey = & ".venv\Scripts\python.exe" -c 'import sqlite3; print(sqlite3.connect("data/whisperdesk.db").execute("select api_key from provider_configs where user_id=1 and name=" + chr(39) + "groq" + chr(39)).fetchone()[0])'
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=90" -af "volume=0.02" -c:a libmp3lame -b:a 192k test_cancel.mp3 2>&1 | Out-Null

Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -eq '' } | Out-Null  # no-op, just a readable checkpoint
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" -NoNewWindow -RedirectStandardOutput "run_out.log" -RedirectStandardError "run_err.log" -PassThru
Start-Sleep -Seconds 3

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"canceltest","password":"pw"}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/settings -Method Put -Body '{"chunk_threshold_mb":1}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null
$body2 = @{ api_key = $groqKey } | ConvertTo-Json
Invoke-WebRequest -Uri http://localhost:9781/api/providers/groq -Method Put -Body $body2 -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null

$form = @{ file = Get-Item "test_cancel.mp3"; provider = "groq"; language = "en"; model = "whisper-large-v3" }
$r = Invoke-WebRequest -Uri http://localhost:9781/api/transcribe -Method Post -Form $form -WebSession $session -UseBasicParsing
$body = $r.Content | ConvertFrom-Json
$tid = $body.id
Write-Host "created transcript $tid, job_progress:" ($body.job_progress | ConvertTo-Json -Compress)

# Cancel almost immediately -- some jobs may already be running
$cancelResp = Invoke-WebRequest -Uri "http://localhost:9781/api/transcripts/$tid/cancel" -Method Post -WebSession $session -UseBasicParsing
Write-Host "cancel response:" $cancelResp.Content

Start-Sleep -Seconds 10
$afterCancel = (Invoke-WebRequest -Uri "http://localhost:9781/api/transcripts/$tid" -WebSession $session -UseBasicParsing).Content | ConvertFrom-Json
Write-Host "status after cancel + wait (expect cancelled):" $afterCancel.status

$resumeResp = Invoke-WebRequest -Uri "http://localhost:9781/api/transcripts/$tid/resume" -Method Post -WebSession $session -UseBasicParsing
Write-Host "resume response:" $resumeResp.Content

Start-Sleep -Seconds 15
$afterResume = (Invoke-WebRequest -Uri "http://localhost:9781/api/transcripts/$tid" -WebSession $session -UseBasicParsing).Content | ConvertFrom-Json
Write-Host "status after resume + wait (expect completed or partial):" $afterResume.status

Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.Id -eq $p.Id } | Stop-Process -Force
Remove-Item run_out.log,run_err.log,test_cancel.mp3 -ErrorAction SilentlyContinue
python -c "
import sqlite3
c = sqlite3.connect('data/whisperdesk.db')
c.execute(\"delete from users where username='canceltest'\")
c.commit()
"
```
Expected: `status after cancel + wait (expect cancelled): cancelled` (proving the finalize-discovery fix works — this transcript had zero pending jobs after cancel, so without Task 3 Step 3's fix it would still show `processing`); `status after resume + wait (expect completed or partial): completed` or `partial`.

Note the `Stop-Process` targets `$p.Id` specifically (the PID this script itself started) — never stop a process by name/image, since other Python processes on the machine are unrelated to this test.

- [ ] **Step 6: Commit**

```powershell
git add services/queue.py app.py
git commit -m "Add cancel/resume for in-progress transcriptions"
```

---

### Task 4: Frontend — HF token into Providers card, model-selection unification

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `GET/PUT /api/settings` (existing), `GET/PUT /api/providers/{name}` (existing, `default_model` field).

- [ ] **Step 1: Remove the standalone Diarization settings card**

Find:
```html
        <div class="set-card">
          <h4>Diarization</h4>
          <div class="cfg-f" style="margin-bottom:6px">
            <label>HuggingFace token</label>
            <input type="password" id="setHfToken" placeholder="hf_..." style="width:100%;padding:7px 10px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-family:var(--font);outline:none">
          </div>
          <p style="font-size:11px;color:var(--text-muted);margin-bottom:10px">Required for pyannote speaker diarization. Accept the terms on <a href="https://huggingface.co/pyannote/speaker-diarization-3.1" target="_blank">pyannote/speaker-diarization-3.1</a> and <a href="https://huggingface.co/pyannote/segmentation-3.0" target="_blank">pyannote/segmentation-3.0</a>, then create a read token at <a href="https://huggingface.co/settings/tokens" target="_blank">huggingface.co/settings/tokens</a>.</p>
          <button class="btn btn-sm btn-primary" onclick="saveHfToken()">Save</button>
        </div>
        <div class="set-card">
          <h4>Account</h4>
```
Change to:
```html
        <div class="set-card">
          <h4>Account</h4>
```

- [ ] **Step 2: Add the HF token row inside the Providers card**

Find:
```html
        <div class="set-card">
          <h4><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>Providers</h4>
          <p>Configure API keys for transcription backends. Keys are stored locally.</p>
          <div id="providerList"></div>
        </div>
```
Change to:
```html
        <div class="set-card">
          <h4><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>Providers</h4>
          <p>Configure API keys for transcription backends. Keys are stored locally.</p>
          <div id="providerList"></div>
          <div class="prov-item">
            <div class="pv-icon" style="background:linear-gradient(135deg,#f59e0b,#d97706)">HF</div>
            <div class="pv-info">
              <div class="pv-name">HuggingFace</div>
              <div class="pv-desc">Required for pyannote speaker diarization. Accept terms on <a href="https://huggingface.co/pyannote/speaker-diarization-3.1" target="_blank">pyannote/speaker-diarization-3.1</a> and <a href="https://huggingface.co/pyannote/segmentation-3.0" target="_blank">pyannote/segmentation-3.0</a>, then create a token at <a href="https://huggingface.co/settings/tokens" target="_blank">huggingface.co/settings/tokens</a>.</div>
            </div>
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
              <div class="pv-key">
                <input type="password" id="setHfToken" placeholder="hf_..." onchange="saveHfToken()" style="width:150px">
                <span class="ks off" id="ks-hftoken"></span>
              </div>
            </div>
          </div>
        </div>
```

- [ ] **Step 3: Update `loadAudioSettings`/`saveHfToken` to drive the new indicator**

Find:
```javascript
async function loadAudioSettings() {
  try {
    const r = await fetch(API + '/api/settings');
    if (!r.ok) return;
    const s = await r.json();
    document.getElementById('setBitrate').value = s.bitrate_kbps;
    document.getElementById('setChunkThreshold').value = s.chunk_threshold_mb;
    document.getElementById('setMaxConcurrent').value = s.max_concurrent_chunks;
    document.getElementById('setHfToken').value = s.hf_token || '';
  } catch (e) {}
}
```
Change to:
```javascript
async function loadAudioSettings() {
  try {
    const r = await fetch(API + '/api/settings');
    if (!r.ok) return;
    const s = await r.json();
    document.getElementById('setBitrate').value = s.bitrate_kbps;
    document.getElementById('setChunkThreshold').value = s.chunk_threshold_mb;
    document.getElementById('setMaxConcurrent').value = s.max_concurrent_chunks;
    document.getElementById('setHfToken').value = s.hf_token || '';
    document.getElementById('ks-hftoken').className = 'ks ' + (s.hf_token ? 'on' : 'off');
  } catch (e) {}
}
```

Find:
```javascript
async function saveHfToken() {
  try {
    const r = await fetch(API + '/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hf_token: document.getElementById('setHfToken').value.trim() }),
    });
    if (!r.ok) throw new Error(await r.text());
    toast('HuggingFace token saved', 'success');
  } catch (e) {
    toast('Failed to save token: ' + (e.message || e), 'error');
  }
}
```
Change to:
```javascript
async function saveHfToken() {
  const val = document.getElementById('setHfToken').value.trim();
  try {
    const r = await fetch(API + '/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hf_token: val }),
    });
    if (!r.ok) throw new Error(await r.text());
    document.getElementById('ks-hftoken').className = 'ks ' + (val ? 'on' : 'off');
    toast('HuggingFace token saved', 'success');
  } catch (e) {
    toast('Failed to save token: ' + (e.message || e), 'error');
  }
}
```

- [ ] **Step 4: Repurpose the provider row's "Fetch models" button to persist `default_model`**

Find:
```html
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
          ${p.needs_key ? `<div class="pv-key">
            <input type="password" id="key-${p.id}" placeholder="${p.key_prefix || ''}..." value="" data-provider="${p.id}" onchange="saveProviderKey('${p.id}')" style="width:150px">
            <span class="ks ${p.configured ? 'on' : 'off'}" id="ks-${p.id}"></span>
          </div>` : ''}
          <button class="btn btn-xs btn-ghost" onclick="fetchProviderModels('${p.id}')" title="Fetch available models" style="font-size:10px">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:10px;height:10px"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
            ${zeroSetup ? 'Select model' : 'Fetch models'}
          </button>
        </div>
      </div>`;
    }).join('');
```
Change to:
```html
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
          ${p.needs_key ? `<div class="pv-key">
            <input type="password" id="key-${p.id}" placeholder="${p.key_prefix || ''}..." value="" data-provider="${p.id}" onchange="saveProviderKey('${p.id}')" style="width:150px">
            <span class="ks ${p.configured ? 'on' : 'off'}" id="ks-${p.id}"></span>
          </div>` : ''}
          <button class="btn btn-xs btn-ghost" onclick="fetchProviderModels('${p.id}')" title="Fetch available models" style="font-size:10px">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:10px;height:10px"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
            ${zeroSetup ? 'Select model' : 'Fetch models'}
          </button>
          <div id="model-wrap-${p.id}" style="font-size:10px;color:var(--text-muted)"></div>
        </div>
      </div>`;
    }).join('');
```

Find:
```javascript
    // Load existing key values and URLs
    const r2 = await Promise.all(provs.map(p => fetch(API + '/api/providers/' + p.id).then(r => r.json())));
    r2.forEach(cfg => {
      const input = document.getElementById('key-' + cfg.name);
      if (input && cfg._has_key) {
        input.value = cfg.api_key || '••••••••';
        input.dataset.hasKey = 'true';
      }
      const urlInput = document.getElementById('url-' + cfg.name);
      if (urlInput && cfg.api_url) {
        urlInput.value = cfg.api_url;
      }
    });
  } catch (e) {}
}
```
Change to:
```javascript
    // Load existing key values and URLs
    const r2 = await Promise.all(provs.map(p => fetch(API + '/api/providers/' + p.id).then(r => r.json())));
    r2.forEach(cfg => {
      const input = document.getElementById('key-' + cfg.name);
      if (input && cfg._has_key) {
        input.value = cfg.api_key || '••••••••';
        input.dataset.hasKey = 'true';
      }
      const urlInput = document.getElementById('url-' + cfg.name);
      if (urlInput && cfg.api_url) {
        urlInput.value = cfg.api_url;
      }
      const modelWrap = document.getElementById('model-wrap-' + cfg.name);
      if (modelWrap && cfg.default_model) {
        modelWrap.textContent = 'Default: ' + cfg.default_model;
      }
    });
  } catch (e) {}
}
```

Find:
```javascript
async function fetchProviderModels(name) {
  const btn = event.target.closest('button') || event.target;
  btn.disabled = true;
  btn.textContent = 'Loading...';
  try {
    const r = await fetch(API + '/api/providers/' + name + '/models');
    const data = await r.json();
    if (data.models && data.models.length > 0) {
      toast(name + ': ' + data.models.length + ' models available', 'success');
      // Show them in the transcribe model dropdown
      const modelSelect = document.getElementById('txModel');
      modelSelect.innerHTML = data.models.map(m =>
        '<option value="' + escapeHtml(m) + '">' + escapeHtml(m) + '</option>'
      ).join('');
      navigate('transcribe');
    } else {
      toast(name + ': no models found', 'info');
    }
  } catch (e) {
    toast('Failed to fetch models: ' + (e.message || e), 'error');
  }
  btn.disabled = false;
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:10px;height:10px"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Fetch models';
}
```
Change to:
```javascript
async function fetchProviderModels(name) {
  const btn = event.target.closest('button') || event.target;
  btn.disabled = true;
  btn.textContent = 'Loading...';
  try {
    const r = await fetch(API + '/api/providers/' + name + '/models');
    const data = await r.json();
    const wrap = document.getElementById('model-wrap-' + name);
    if (data.models && data.models.length > 0) {
      const cfgR = await fetch(API + '/api/providers/' + name);
      const cfg = await cfgR.json();
      wrap.innerHTML = '<select onchange="saveDefaultModel(\'' + name + '\', this.value)" style="font-size:10px;padding:2px 4px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary)">' +
        data.models.map(m =>
          '<option value="' + escapeHtml(m) + '"' + (m === cfg.default_model ? ' selected' : '') + '>' + escapeHtml(m) + '</option>'
        ).join('') + '</select>';
      toast(name + ': ' + data.models.length + ' models available', 'success');
    } else {
      toast(name + ': no models found', 'info');
    }
  } catch (e) {
    toast('Failed to fetch models: ' + (e.message || e), 'error');
  }
  btn.disabled = false;
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:10px;height:10px"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Fetch models';
}

async function saveDefaultModel(name, model) {
  try {
    await fetch(API + '/api/providers/' + name, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ default_model: model }),
    });
    toast(name + ' default model set to ' + model, 'success');
  } catch (e) {
    toast('Failed to save default model: ' + (e.message || e), 'error');
  }
}
```

- [ ] **Step 5: Pre-fill the Transcribe page's model dropdown from the provider's default**

Find (in `loadTxModels`):
```javascript
  try {
    const r = await fetch(API + '/api/providers/' + provider + '/models');
    const data = await r.json();
    modelSelect.innerHTML = data.models.map(m =>
      '<option value="' + escapeHtml(m) + '">' + escapeHtml(m) + '</option>'
```
Change to:
```javascript
  try {
    const r = await fetch(API + '/api/providers/' + provider + '/models');
    const data = await r.json();
    const cfgForDefault = await fetch(API + '/api/providers/' + provider).then(r => r.json()).catch(() => ({}));
    modelSelect.innerHTML = data.models.map(m =>
      '<option value="' + escapeHtml(m) + '"' + (m === cfgForDefault.default_model ? ' selected' : '') + '>' + escapeHtml(m) + '</option>'
```
(The line after this, `).join('');`, and everything below it in `loadTxModels` stays unchanged — this only touches the two lines shown.)

- [ ] **Step 6: Verify in a real browser**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe app.py
```
Open `http://localhost:9781`, log in, go to Settings:
1. Confirm the Providers card now shows an "HuggingFace" row at the bottom of the list with the same masked-input + dot style as Groq/OpenAI, and the standalone "Diarization" card is gone.
2. Type a token, confirm the dot turns on (green) and stays on after a page reload.
3. Click "Fetch models" on Groq (or another configured provider) — confirm a `<select>` of models appears where the button used to just navigate away, and confirm the previously-saved default (if any) is pre-selected.
4. Pick a different model in that dropdown, confirm the "Default: X" label updates and persists across reload.
5. Go to Transcribe, pick that provider — confirm the model dropdown there pre-fills with the same default, but can still be changed for this one upload.

- [ ] **Step 7: Commit**

```powershell
git add static/index.html
git commit -m "Move HF token into Providers card, unify model selection via default_model"
```

---

### Task 5: Frontend — progress screen (real size, queue status, model, cancel), detail-page resume, dashboard badge

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `processed_size_bytes`, `queue_status`, `model` (all on the transcript response, from Tasks 1–2), `POST /api/transcripts/{id}/cancel`, `POST /api/transcripts/{id}/resume` (Task 3).

- [ ] **Step 1: Add a Cancel button to the progress page markup**

Find:
```html
        <h3 id="progTitle">Starting...</h3>
        <p id="progDesc">Preparing your transcription</p>
        <div class="stages">
```
Change to:
```html
        <h3 id="progTitle">Starting...</h3>
        <p id="progDesc">Preparing your transcription</p>
        <button class="btn btn-sm" id="progCancelBtn" onclick="cancelCurrentUpload()" style="display:none;margin-bottom:10px">Cancel</button>
        <div class="stages">
```

- [ ] **Step 2: Update `pollTranscript` to show queue status, model, and real processed size; track the in-flight transcript ID for cancel**

Find:
```javascript
async function pollTranscript(id) {
  while (true) {
    const r = await fetch(API + '/api/transcripts/' + id);
    if (!r.ok) throw new Error('Lost track of transcript ' + id);
    const data = await r.json();
    if (data.job_progress) {
      const p = data.job_progress;
      document.getElementById('progDesc').textContent = p.completed + ' of ' + p.total + ' sections done';
    }
    if (['completed', 'failed', 'partial'].includes(data.status)) {
      if (data.status === 'failed') throw new Error(data.error || 'Transcription failed');
      return data;
    }
    await new Promise(res => setTimeout(res, 2000));
  }
}
```
Change to:
```javascript
let currentUploadTranscriptId = null;

async function pollTranscript(id) {
  currentUploadTranscriptId = id;
  document.getElementById('progCancelBtn').style.display = 'inline-flex';
  try {
    while (true) {
      const r = await fetch(API + '/api/transcripts/' + id);
      if (!r.ok) throw new Error('Lost track of transcript ' + id);
      const data = await r.json();
      document.getElementById('progTitle').textContent = 'Transcribing with ' + (data.model || 'Whisper') + '...';

      if (data.processed_size_bytes) {
        document.getElementById('progDesc').textContent = (data.processed_size_bytes / (1024*1024)).toFixed(1) + ' MB processed';
      }

      if (data.queue_status) {
        const q = data.queue_status;
        if (q.state === 'transcribing') {
          document.getElementById('progDesc').textContent = q.chunks_done + ' of ' + q.chunks_total + ' sections done';
        } else if (q.state === 'rate_limited') {
          const mins = Math.max(1, Math.round(q.resume_in_seconds / 60));
          document.getElementById('progDesc').textContent = 'Waiting on ' + data.provider + '\'s rate limit — resuming in ~' + mins + 'm';
        } else if (q.state === 'queued') {
          document.getElementById('progDesc').textContent = 'Queued — ' + q.chunks_done + ' of ' + q.chunks_total + ' sections done';
        }
      }

      if (['completed', 'failed', 'partial', 'cancelled'].includes(data.status)) {
        if (data.status === 'failed') throw new Error(data.error || 'Transcription failed');
        return data;
      }
      await new Promise(res => setTimeout(res, 2000));
    }
  } finally {
    document.getElementById('progCancelBtn').style.display = 'none';
    currentUploadTranscriptId = null;
  }
}

async function cancelCurrentUpload() {
  if (!currentUploadTranscriptId) return;
  try {
    await fetch(API + '/api/transcripts/' + currentUploadTranscriptId + '/cancel', { method: 'POST' });
    toast('Cancelling...', 'info');
  } catch (e) {
    toast('Failed to cancel: ' + (e.message || e), 'error');
  }
}
```

- [ ] **Step 3: Handle the new `cancelled` terminal status in `startTx`**

Find:
```javascript
    setStageActive('stg-done');
    const failedMsg = finalData.status === 'partial' ? ' (some sections failed — retry from the transcript page)' : '';
    document.getElementById('progTitle').textContent = finalData.status === 'partial' ? 'Transcription partially complete' : 'Transcription complete!';
    document.getElementById('progDesc').textContent = (finalData.segments ? finalData.segments.length + ' segments · ' + finalData.provider : '') + failedMsg;
    document.getElementById('progCircle').style.strokeDashoffset = '0';
    document.getElementById('progCircle').style.stroke = finalData.status === 'partial' ? 'var(--warning)' : 'var(--success)';
    document.getElementById('progCheck').style.display = 'block';

    toast(finalData.status === 'partial' ? 'Transcription partially complete' : 'Transcription complete!', finalData.status === 'partial' ? 'error' : 'success');
    selectedFile = null;
    document.getElementById('txStartBtn').disabled = true;

    setTimeout(() => navigate('detail', finalData.id), 1200);
```
Change to:
```javascript
    setStageActive('stg-done');
    const isPartial = finalData.status === 'partial';
    const isCancelled = finalData.status === 'cancelled';
    const failedMsg = isPartial ? ' (some sections failed — retry from the transcript page)' : '';
    document.getElementById('progTitle').textContent = isCancelled ? 'Transcription cancelled' : isPartial ? 'Transcription partially complete' : 'Transcription complete!';
    document.getElementById('progDesc').textContent = isCancelled ? 'Resume from the transcript page to finish it later.' : (finalData.segments ? finalData.segments.length + ' segments · ' + finalData.provider : '') + failedMsg;
    document.getElementById('progCircle').style.strokeDashoffset = '0';
    document.getElementById('progCircle').style.stroke = isCancelled ? 'var(--text-muted)' : isPartial ? 'var(--warning)' : 'var(--success)';
    document.getElementById('progCheck').style.display = 'block';

    toast(isCancelled ? 'Transcription cancelled' : isPartial ? 'Transcription partially complete' : 'Transcription complete!', isCancelled ? 'info' : isPartial ? 'error' : 'success');
    selectedFile = null;
    document.getElementById('txStartBtn').disabled = true;

    setTimeout(() => navigate('detail', finalData.id), 1200);
```

- [ ] **Step 4: Add a Resume button to the transcript detail page for `cancelled` transcripts**

Find:
```javascript
    document.getElementById('hdrActions').innerHTML = `
      ${t.status === 'partial' ? `<button class="btn btn-sm" onclick="retryFailedChunks(${t.id})">Retry failed sections</button>` : ''}
```
Change to:
```javascript
    document.getElementById('hdrActions').innerHTML = `
      ${t.status === 'partial' ? `<button class="btn btn-sm" onclick="retryFailedChunks(${t.id})">Retry failed sections</button>` : ''}
      ${t.status === 'cancelled' ? `<button class="btn btn-sm" onclick="resumeCancelledTranscript(${t.id})">Resume</button>` : ''}
```

Add near `retryFailedChunks`:
```javascript
async function resumeCancelledTranscript(id) {
  try {
    const r = await fetch(API + '/api/transcripts/' + id + '/resume', { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const body = await r.json();
    toast('Resuming ' + body.resumed + ' section(s)...', 'success');
    setTimeout(() => navigate('detail', id), 500);
  } catch (e) {
    toast('Resume failed: ' + (e.message || e), 'error');
  }
}
```

- [ ] **Step 5: Add a queue-status-aware badge to the dashboard's recent-transcripts list**

Find:
```javascript
      c.innerHTML = list.map(t => {
        const statusClass = t.status === 'completed' ? 'status-done' : t.status === 'processing' ? 'status-processing' : 'status-error';
        const statusLabel = t.status === 'completed' ? 'Done' : t.status === 'processing' ? 'Processing' : 'Failed';
```
Change to:
```javascript
      c.innerHTML = list.map(t => {
        const statusClass = t.status === 'completed' ? 'status-done' : t.status === 'processing' ? 'status-processing' : 'status-error';
        let statusLabel = t.status === 'completed' ? 'Done' : t.status === 'processing' ? 'Processing' : t.status === 'cancelled' ? 'Cancelled' : 'Failed';
        if (t.status === 'processing' && t.queue_status) {
          if (t.queue_status.state === 'transcribing') statusLabel = t.queue_status.chunks_done + '/' + t.queue_status.chunks_total;
          else if (t.queue_status.state === 'rate_limited') statusLabel = 'Rate limited';
          else if (t.queue_status.state === 'queued') statusLabel = 'Queued';
        }
```

- [ ] **Step 6: Verify in a real browser**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe app.py
```
Open `http://localhost:9781`, log in:
1. Upload a file large enough to chunk (or lower the chunk threshold in Settings first). Confirm the progress screen's title shows the real model name, the description switches from raw upload MB to processed MB, and a "Cancel" button is visible while it's `processing`.
2. Click Cancel mid-upload. Confirm the progress screen settles on "Transcription cancelled" and the transcript detail page shows a "Resume" button.
3. Click Resume, confirm it completes normally afterward.
4. From the dashboard, start another upload, navigate away to a different page mid-transcription, confirm the dashboard's recent list shows a live status (chunk count, "Queued", or "Rate limited" — whichever applies) instead of a generic "Processing" label, and clicking into the transcript resumes the same progress view.

- [ ] **Step 7: Commit**

```powershell
git add static/index.html
git commit -m "Add cancel/resume UI, real progress status, and dashboard queue-status badge"
```

---

## Post-implementation note

Task 3's `queue_worker_tick` fix (finalize-checking all `processing` transcripts, not just ones with pending jobs) is a real latent bug fix in the existing chunking/queue system, surfaced by designing cancel — it existed before this plan and would have caused any transcript whose jobs happened to reach an all-`running`-then-naturally-settle state outside the dispatching tick's own synchronous flow to never finalize. Under the system's actual current behavior (dispatch and await-completion happen synchronously within one tick), this was latent and never triggered in practice — cancel is what makes it a live, easily-triggered path — but it is a correctness improvement to the underlying queue regardless of whether cancel existed, not a new-feature-only side effect.
