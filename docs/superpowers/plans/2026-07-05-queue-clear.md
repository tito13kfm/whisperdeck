# Queue Clear/Dismiss Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users dismiss (hide) completed/failed/cancelled entries from the Queue screen, individually or in bulk by status, without destroying any underlying job/chunk data.

**Architecture:** Two new non-destructive boolean flags (`LlmJob.dismissed`, `Transcript.queue_dismissed`) hide entries from `GET /api/jobs`. Three new endpoints set those flags (single LlmJob dismiss, single transcription-entry dismiss, bulk clear-by-status). Resume/Retry on a transcript clear its dismissed flag so re-activated work reappears in the queue. The frontend gets a per-row dismiss button plus header "Clear completed/failed/cancelled" buttons.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), vanilla JS (`static/rack.js`), pytest + FastAPI TestClient.

## Global Constraints

- Clearable statuses are exactly `completed`, `failed`, `cancelled` — NOT `partial` (spec: `docs/superpowers/specs/2026-07-05-queue-clear-design.md`).
- Dismissal must be reversible/non-destructive: never delete `LlmJob` or `TranscriptionJob` rows as part of this feature.
- New boolean columns follow this repo's existing migration convention: `ensure_columns(engine, table, {"col": "BOOLEAN"})` in `database/__init__.py`'s `init_db()` — no SQL `DEFAULT` clause, matching the existing `diarize_requested` column. Because of this, **pre-existing rows get `NULL`, not `False`**, after the migration runs — every read-side filter on these columns MUST use `.isnot(True)`, never `== False`, or old rows will silently vanish from query results.
- Run tests with this project's virtualenv: `.venv\Scripts\python.exe -m pytest <args>` (per `tests/conftest.py`'s interpreter guard).

---

### Task 1: Schema — add `dismissed` / `queue_dismissed` columns

**Files:**
- Modify: `database/__init__.py:36-55` (`Transcript` model), `database/__init__.py:77-93` (`LlmJob` model), `database/__init__.py:249-250` (`init_db` migration calls)
- Test: `tests/test_queue_clear.py` (new file)

**Interfaces:**
- Produces: `Transcript.queue_dismissed` (bool, default `False`), `LlmJob.dismissed` (bool, default `False`) — both consumed by Tasks 2 and 3.

- [ ] **Step 1: Write the failing test**

Create `tests/test_queue_clear.py`:

```python
"""Queue clear/dismiss: hide finished/failed/cancelled entries from the
Queue screen without deleting the underlying job/chunk data (issue #13)."""
from database import LlmJob, Transcript, TranscriptionJob, User


def _make_user(db_session):
    user = User(username="clearop", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def test_new_columns_default_false(db_session):
    user = _make_user(db_session)
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="completed")
    db_session.add(t)
    db_session.commit()
    assert t.queue_dismissed is False

    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction", status="completed")
    db_session.add(job)
    db_session.commit()
    assert job.dismissed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_clear.py -v`
Expected: FAIL — `AttributeError` (or similar) because `queue_dismissed`/`dismissed` don't exist on the models yet.

- [ ] **Step 3: Add the columns**

In `database/__init__.py`, add to the `Transcript` model (right after `correction_model`, line 50):

```python
    correction_model = Column(String(128), nullable=True)  # e.g. "groq/llama-3.3-70b-versatile"
    queue_dismissed = Column(Boolean, default=False)  # hides this transcript's Queue-screen entry
```

Add to the `LlmJob` model (right after `error`, line 91):

```python
    error = Column(Text, nullable=True)
    dismissed = Column(Boolean, default=False)  # hides this job's Queue-screen entry
```

In `init_db()` (around line 249-250), extend the existing `ensure_columns` call for `transcripts` and add a new one for `llm_jobs`:

```python
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER", "processed_size_bytes": "INTEGER", "corrected_text": "TEXT", "correction_error": "TEXT", "correction_model": "TEXT", "queue_dismissed": "BOOLEAN"})
    ensure_columns(engine, "llm_jobs", {"dismissed": "BOOLEAN"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_clear.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database/__init__.py tests/test_queue_clear.py
git commit -m "feat: add dismissed/queue_dismissed columns for queue clear"
```

---

### Task 2: Service-layer dismiss functions + resume/retry reset

**Files:**
- Modify: `services/llm_jobs.py:108-115` (after `rerun_llm_job`)
- Modify: `services/queue.py:260-277` (`retry_failed_chunks`), `services/queue.py:311-339` (`resume_cancelled_chunks`), and add a new `dismiss_transcript_queue_entry` function
- Test: `tests/test_queue_clear.py`

**Interfaces:**
- Consumes: `LlmJob`, `Transcript`, `utcnow_naive` (already imported in both files); `TranscriptionJob` (already imported in `services/queue.py`).
- Produces: `dismiss_llm_job(db, user_id: int, job_id: int) -> LlmJob` (raises `LookupError` if not found, `ValueError` if status not in completed/failed/cancelled). `dismiss_transcript_queue_entry(db, transcript_id: int) -> Transcript` (same raise contract). Both consumed by Task 3's endpoints.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_queue_clear.py`:

```python
import pytest

from services.llm_jobs import dismiss_llm_job, enqueue_llm_job
from services.queue import dismiss_transcript_queue_entry, retry_failed_chunks, resume_cancelled_chunks


def _make_transcript(db_session, user, status="completed"):
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status=status)
    db_session.add(t)
    db_session.commit()
    return t


def test_dismiss_llm_job_requires_terminal_status(db_session):
    user = _make_user(db_session)
    t = _make_transcript(db_session, user)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    with pytest.raises(ValueError):
        dismiss_llm_job(db_session, user.id, job.id)  # still 'pending'

    job.status = "completed"
    db_session.commit()
    dismissed = dismiss_llm_job(db_session, user.id, job.id)
    assert dismissed.dismissed is True


def test_dismiss_llm_job_not_found_raises_lookup_error(db_session):
    user = _make_user(db_session)
    with pytest.raises(LookupError):
        dismiss_llm_job(db_session, user.id, 99999)


def test_dismiss_transcript_queue_entry_requires_terminal_status(db_session):
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, status="processing")
    with pytest.raises(ValueError):
        dismiss_transcript_queue_entry(db_session, t.id)

    t.status = "failed"
    db_session.commit()
    dismissed = dismiss_transcript_queue_entry(db_session, t.id)
    assert dismissed.queue_dismissed is True


def test_retry_failed_chunks_resets_dismissed_flag(db_session):
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, status="failed")
    t.queue_dismissed = True
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="failed",
    ))
    db_session.commit()

    retry_failed_chunks(db_session, t.id)
    db_session.refresh(t)
    assert t.queue_dismissed is False
    assert t.status == "processing"


def test_resume_cancelled_chunks_resets_dismissed_flag(db_session):
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, status="cancelled")
    t.queue_dismissed = True
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="cancelled",
    ))
    db_session.commit()

    resume_cancelled_chunks(db_session, t.id)
    db_session.refresh(t)
    assert t.queue_dismissed is False
    assert t.status == "processing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_clear.py -v`
Expected: FAIL — `ImportError` (`dismiss_llm_job`/`dismiss_transcript_queue_entry` don't exist yet).

- [ ] **Step 3: Implement `dismiss_llm_job`**

In `services/llm_jobs.py`, add right after `rerun_llm_job` (line 115):

```python
def dismiss_llm_job(db, user_id: int, job_id: int) -> LlmJob:
    """Hide a finished job from the Queue screen. Non-destructive — the row
    (and its transcript's corrected_text/summary, which live elsewhere)
    stays untouched."""
    job = db.query(LlmJob).filter(LlmJob.id == job_id, LlmJob.user_id == user_id).first()
    if not job:
        raise LookupError("Job not found")
    if job.status not in ("completed", "failed", "cancelled"):
        raise ValueError(f"Cannot dismiss a job with status '{job.status}'")
    job.dismissed = True
    job.updated_at = utcnow_naive()
    db.commit()
    return job
```

- [ ] **Step 4: Implement `dismiss_transcript_queue_entry` and the resume/retry reset**

In `services/queue.py`, add a new function after `retry_failed_chunks` (line 277):

```python
def dismiss_transcript_queue_entry(db, transcript_id: int) -> Transcript:
    """Hide a finished transcription pipeline's Queue-screen entry.
    Non-destructive — TranscriptionJob rows are untouched, so Resume/Retry
    still work if the flag is later cleared."""
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        raise LookupError("Transcript not found")
    if transcript.status not in ("completed", "failed", "cancelled"):
        raise ValueError(f"Cannot dismiss a queue entry with status '{transcript.status}'")
    transcript.queue_dismissed = True
    db.commit()
    return transcript
```

Modify `retry_failed_chunks` (lines 272-276) to reset the flag:

```python
    if failed:
        transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
        if transcript:
            transcript.status = "processing"
            transcript.queue_dismissed = False
        db.commit()
    return len(failed)
```

Modify `resume_cancelled_chunks` (lines 336-339) to reset the flag:

```python
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if transcript and transcript.status == "cancelled":
        transcript.status = "processing"
        transcript.queue_dismissed = False
    db.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_clear.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add services/llm_jobs.py services/queue.py tests/test_queue_clear.py
git commit -m "feat: add dismiss functions for LLM jobs and transcription queue entries"
```

---

### Task 3: API endpoints + `list_jobs` filtering

**Files:**
- Modify: `app.py:25-39` (imports), `app.py:1148-1183` (`list_jobs`), `app.py:1186-1205` (add new endpoints near existing `cancel`/`rerun` job endpoints)
- Test: `tests/test_queue_clear.py`

**Interfaces:**
- Consumes: `dismiss_llm_job`, `dismiss_transcript_queue_entry` from Task 2; `serialize_llm_job` (already imported in `app.py`).
- Produces: `POST /api/jobs/{job_id}/dismiss`, `POST /api/transcripts/{transcript_id}/dismiss-queue-entry`, `POST /api/jobs/clear-by-status` — consumed by Task 4's frontend.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_queue_clear.py`:

```python
import io
from unittest.mock import AsyncMock, patch


def _upload(client):
    async def _stub_transcribe(db, user_id, **kwargs):
        t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello")
        db.add(t)
        db.commit()
        return t
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("m.mp3", io.BytesIO(b"x"), "audio/mpeg")},
            data={"provider": "groq"},
        )


def test_dismiss_job_route_hides_it_from_listing(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    _upload(client)
    job_id = [j for j in client.get("/api/jobs").json()["jobs"] if j["kind"] == "correction"][0]["id"]

    client.post(f"/api/jobs/{job_id}/cancel")
    r = client.post(f"/api/jobs/{job_id}/dismiss")
    assert r.status_code == 200
    assert r.json()["job"]["status"] == "cancelled"

    jobs = client.get("/api/jobs").json()["jobs"]
    assert job_id not in [j["id"] for j in jobs if j["kind"] == "correction"]


def test_dismiss_job_route_rejects_active_status(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    _upload(client)
    job_id = [j for j in client.get("/api/jobs").json()["jobs"] if j["kind"] == "correction"][0]["id"]

    r = client.post(f"/api/jobs/{job_id}/dismiss")
    assert r.status_code == 400


def test_dismiss_transcript_queue_entry_route(db_session, client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    transcript_id = _upload(client).json()["id"]
    # a bare completed transcript has no TranscriptionJob rows, so it never
    # shows up in the queue in the first place — give it one chunk row so it
    # actually appears, matching how a real chunked transcription looks once done.
    db_session.add(TranscriptionJob(
        transcript_id=transcript_id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="completed",
    ))
    db_session.commit()
    assert transcript_id in [j["transcript_id"] for j in client.get("/api/jobs").json()["jobs"] if j["kind"] == "transcription"]

    r = client.post(f"/api/transcripts/{transcript_id}/dismiss-queue-entry")
    assert r.status_code == 200

    jobs = client.get("/api/jobs").json()["jobs"]
    assert transcript_id not in [j["transcript_id"] for j in jobs if j["kind"] == "transcription"]


def test_clear_by_status_route(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    _upload(client)
    job_id = [j for j in client.get("/api/jobs").json()["jobs"] if j["kind"] == "correction"][0]["id"]
    client.post(f"/api/jobs/{job_id}/cancel")

    r = client.post("/api/jobs/clear-by-status", json={"status": "cancelled"})
    assert r.status_code == 200
    assert r.json()["cleared"] >= 1

    jobs = client.get("/api/jobs").json()["jobs"]
    assert job_id not in [j["id"] for j in jobs]


def test_clear_by_status_rejects_bad_status(client):
    r = client.post("/api/jobs/clear-by-status", json={"status": "running"})
    assert r.status_code == 400


def test_processing_transcript_never_hidden_even_if_dismissed(db_session, client):
    """Guard against a future re-activation path (or a dismiss racing a
    resume) leaving queue_dismissed=True on a transcript that's actively
    processing — active work must never disappear from the queue."""
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    transcript_id = _upload(client).json()["id"]
    t = db_session.query(Transcript).filter(Transcript.id == transcript_id).first()
    t.queue_dismissed = True
    t.status = "processing"
    db_session.add(TranscriptionJob(
        transcript_id=transcript_id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="pending",
    ))
    db_session.commit()

    jobs = client.get("/api/jobs").json()["jobs"]
    assert transcript_id in [j["transcript_id"] for j in jobs if j["kind"] == "transcription"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_clear.py -v`
Expected: FAIL — 404s (routes don't exist yet).

- [ ] **Step 3: Update imports**

In `app.py`, update the two import lines (25 and 36-39):

```python
from services.queue import create_chunk_jobs, retry_failed_chunks, queue_worker_loop, compute_queue_status, cancel_transcript_jobs, resume_cancelled_chunks, dismiss_transcript_queue_entry
from services.llm_jobs import (
    enqueue_llm_job, enqueue_auto_correction, serialize_llm_job, latest_job,
    cancel_llm_job, rerun_llm_job, dismiss_llm_job, llm_worker_loop,
)
```

- [ ] **Step 4: Filter dismissed entries out of `list_jobs`**

In `app.py`, modify `list_jobs` (lines 1148-1183):

```python
@app.get("/api/jobs")
async def list_jobs(limit: int = Query(50, le=200), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Master queue: newest-first LLM jobs + transcription pipelines that
    are active or ran through the chunk queue."""
    llm = (
        db.query(LlmJob)
        .filter(LlmJob.user_id == current_user.id, LlmJob.dismissed.isnot(True))
        .order_by(LlmJob.id.desc())
        .limit(limit)
        .all()
    )
    transcripts = (
        db.query(Transcript)
        .filter(Transcript.user_id == current_user.id)
        .order_by(Transcript.created_at.desc())
        .limit(limit)
        .all()
    )
    titles = {t.id: (t.title or t.filename) for t in transcripts}
    missing = [j.transcript_id for j in llm if j.transcript_id not in titles]
    if missing:
        for t in db.query(Transcript).filter(Transcript.id.in_(missing)).all():
            titles[t.id] = t.title or t.filename

    entries = []
    for t in transcripts:
        if t.status != "processing" and t.queue_dismissed:
            continue
        if t.status == "processing" or t.jobs:
            entries.append(_transcription_queue_entry(db, t))
    for j in llm:
        e = serialize_llm_job(j)
        e["title"] = titles.get(j.transcript_id, f"Transcript {j.transcript_id}")
        entries.append(e)

    entries.sort(key=lambda e: e["created_at"] or "", reverse=True)
    active = sum(1 for e in entries if e["status"] in ("pending", "running", "queued", "waiting"))
    return {"jobs": entries[:limit], "active": active}
```

(Note: `transcripts` still queries every transcript, unfiltered — it doubles as the source for `titles`, which LLM-job entries need even when that transcript's own queue entry is dismissed. The `queue_dismissed` check is applied only where transcription entries get appended, and is gated on `t.status != "processing"` so actively-processing work is never hidden — this makes the feature robust even if some future re-activation path forgets to reset the flag, e.g. `/api/transcripts/{id}/retranscribe`, which is actually safe today since it creates a brand-new `Transcript` row rather than reusing the dismissed one.

Known limitation, acceptable for this issue's scope: LLM jobs filter `dismissed` in SQL before `.limit(limit)`, but transcripts filter `queue_dismissed` in Python after the limit is applied — dismissing transcription entries won't free a limit slot to reveal older ones. Don't "fix" this by filtering the `transcripts` query itself; that would break the `titles` lookup LLM-job entries depend on.)

- [ ] **Step 5: Add the three new endpoints**

In `app.py`, add after the existing `rerun_job` endpoint (line 1205), before `get_summary`:

```python
QUEUE_CLEARABLE_STATUSES = ("completed", "failed", "cancelled")


@app.post("/api/jobs/{job_id}/dismiss")
async def dismiss_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        job = dismiss_llm_job(db, current_user.id, job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "job": serialize_llm_job(job)}


@app.post("/api/transcripts/{transcript_id}/dismiss-queue-entry")
async def dismiss_transcript_entry(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    try:
        dismiss_transcript_queue_entry(db, transcript_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.post("/api/jobs/clear-by-status")
async def clear_jobs_by_status(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    status = data.get("status")
    if status not in QUEUE_CLEARABLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {QUEUE_CLEARABLE_STATUSES}")
    llm_count = (
        db.query(LlmJob)
        .filter(LlmJob.user_id == current_user.id, LlmJob.status == status, LlmJob.dismissed.isnot(True))
        .update({"dismissed": True}, synchronize_session=False)
    )
    transcript_count = (
        db.query(Transcript)
        .filter(Transcript.user_id == current_user.id, Transcript.status == status, Transcript.queue_dismissed.isnot(True))
        .update({"queue_dismissed": True}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "cleared": llm_count + transcript_count}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_clear.py -v`
Expected: PASS (12 tests total)

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (all tests, including the pre-existing `tests/test_llm_jobs.py` routes)

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_queue_clear.py
git commit -m "feat: add dismiss/clear-by-status API endpoints"
```

---

### Task 4: Frontend — dismiss button + bulk clear buttons

**Files:**
- Modify: `static/rack.js:1569-1583` (`jobActions`), `static/rack.js:1587-1650` (`loadQueue`)

**Interfaces:**
- Consumes: `POST /api/jobs/{job_id}/dismiss`, `POST /api/transcripts/{transcript_id}/dismiss-queue-entry`, `POST /api/jobs/clear-by-status` from Task 3; existing `api()` helper (`static/rack.js:164-174`), existing `btn` closure inside `jobActions`.

- [ ] **Step 1: Add a dismiss button to `jobActions`**

In `static/rack.js`, modify `jobActions` (lines 1569-1583):

```javascript
function jobActions(j) {
  const acts = [];
  const btn = (act, label, red = false) =>
    `<button class="btn${red ? ' btn--red' : ''}" style="font-size:12px;padding:6px 12px;${red ? '' : 'border-color:var(--inset-edge)'}" data-jact="${act}" data-jid="${j.id}" data-tid="${j.transcript_id}">${label}</button>`;
  if (j.kind === 'transcription') {
    if (['running', 'queued', 'waiting'].includes(j.status)) acts.push(btn('t-cancel', 'Cancel — resumable'));
    if (j.status === 'cancelled') acts.push(btn('t-resume', 'Resume'));
    if (j.status === 'failed' || j.status === 'partial') acts.push(btn('t-retry', 'Retry'));
    if (['completed', 'failed', 'cancelled'].includes(j.status)) acts.push(btn('t-dismiss', 'Dismiss'));
  } else {
    if (j.status === 'pending' || j.status === 'running') acts.push(btn('j-cancel', 'Cancel'));
    if (j.status === 'failed' || j.status === 'cancelled') acts.push(btn('j-rerun', 'Rerun'));
    if (['completed', 'failed', 'cancelled'].includes(j.status)) acts.push(btn('j-dismiss', 'Dismiss'));
  }
  acts.push(btn('open', 'Open transcript'));
  return acts.join('');
}
```

- [ ] **Step 2: Handle the dismiss actions and add bulk-clear buttons in `loadQueue`**

In `static/rack.js`, modify `loadQueue` (lines 1587-1650). First, add a `clearBtn` helper and wire it into the header — replace the `root.innerHTML = ...` block (lines 1624-1630):

```javascript
  const active = data.active || 0;
  const clearBtn = (status, label) => jobs.some(j => j.status === status)
    ? `<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-clear="${status}">${label}</button>`
    : '';
  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Queue</h1>
      <div style="display:flex;align-items:center;gap:10px">
        ${clearBtn('completed', 'Clear completed')}${clearBtn('failed', 'Clear failed')}${clearBtn('cancelled', 'Clear cancelled')}
        <div class="page-status" style="color:${active ? AMBER : GREEN}">${ledDot(active ? AMBER : GREEN, true, 9)}${jobs.length} jobs · ${active} active</div>
      </div>
    </div>
    ${jobs.length ? rows : '<div class="empty-unit">Queue idle — jobs appear here when the machine is working</div>'}`;
```

Inside the existing `[data-jact]` listener block, the `jid`/`tid` extraction line is unchanged:

```javascript
  root.querySelectorAll('[data-jact]').forEach(b => b.addEventListener('click', async (e) => {
    e.preventDefault();
    const act = b.dataset.jact, jid = b.dataset.jid, tid = Number(b.dataset.tid);
```

Add two new `if` branches inside that same try block, directly after the existing `t-retry` branch and before `loadQueue();` (line 1641):

```javascript
      if (act === 't-retry') { const r = await api('/api/transcripts/' + tid + '/retry-failed-chunks', { method: 'POST' }); toast('Retrying ' + r.retried + ' sections', 'info'); }
      if (act === 'j-dismiss') { await api('/api/jobs/' + jid + '/dismiss', { method: 'POST' }); toast('Dismissed', 'info'); }
      if (act === 't-dismiss') { await api('/api/transcripts/' + tid + '/dismiss-queue-entry', { method: 'POST' }); toast('Dismissed', 'info'); }
      loadQueue();
```

Then add a new listener block right after the closing `}));` of the `[data-jact]` block, before `clearTimeout(queuePollTimer);` (line 1646):

```javascript
  root.querySelectorAll('[data-clear]').forEach(b => b.addEventListener('click', async () => {
    try {
      const r = await api('/api/jobs/clear-by-status', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: b.dataset.clear }) });
      toast('Cleared ' + r.cleared + ' item(s)', 'info');
      loadQueue();
    } catch (err) { toast(err.message, 'error'); }
  }));
```

- [ ] **Step 3: Manual verification**

There is no JS unit-test harness in this repo (backend logic is fully covered by Task 2/3's pytest suite). Verify by hand:

1. Run the app (`run` skill or existing dev-server workflow).
2. Upload/transcribe a file, let a correction job complete.
3. Open the Queue screen — confirm the completed correction job shows a "Dismiss" button; click it, confirm the row disappears and does not reappear on refresh.
4. Cancel another job so a "cancelled" entry exists; confirm the "Clear cancelled" header button appears, click it, confirm the entry disappears.
5. Confirm "Clear completed" / "Clear failed" buttons only render when at least one entry of that status is present.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: add queue dismiss/clear UI"
```
