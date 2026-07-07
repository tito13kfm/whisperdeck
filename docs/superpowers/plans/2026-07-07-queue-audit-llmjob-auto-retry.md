# LlmJob Auto-Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a failed `LlmJob` (correction/summary/rediarize/voice_match) the same automatic exponential-backoff resurrection `TranscriptionJob` already has, instead of sitting `failed` forever until a user manually hits `/api/jobs/{id}/rerun`.

**Architecture:** Add an `attempts` column to `LlmJob` (mirroring `TranscriptionJob.attempts`), increment it at the existing claim-then-commit point in `llm_worker_tick`, and add a resurrection sweep at the top of the same tick that flips eligible `failed` rows back to `pending` using `services.queue`'s existing `MAX_ATTEMPTS` constant and `_retry_eligible` backoff formula verbatim (no new backoff curve). The sweep is guarded so it never resurrects a dismissed job, a job that never actually ran (a precondition failure like "no API key saved"), a job of a kind not opted into auto-retry, or a job whose transcript+kind lane already has a fresher active job from a manual rerun. The manual `/api/jobs/{id}/rerun` path is untouched — it still creates a brand-new row and still requires the target job be `failed`/`cancelled`, so a permanently-failed (MAX_ATTEMPTS-exhausted) job stays rerunnable exactly as today.

**Tech Stack:** Python, SQLAlchemy (SQLite), asyncio, pytest.

---

## Open Design Questions

**Which `LlmJob` kinds should auto-retry?** This plan defaults `AUTO_RETRY_KINDS = ("correction", "summary")` — provider API calls, where a failure is plausibly transient (network blip, rate limit, momentary provider 5xx). It deliberately **excludes** `rediarize` and `voice_match`: both are local CPU-bound compute (pyannote diarization clustering, voice-embedding extraction over every segment), where a failure is far more likely to be deterministic (bad/missing audio file, no diarization backend installed, no enrolled voice profiles) than transient — blindly retrying 3x would just re-run expensive local inference against the same failure with no chance of a different outcome, burning real CPU time for zero benefit. This needs a human call: if you disagree (e.g. a `voice_match` per-segment failure could plausibly be a transient extraction hiccup under load), the fix is a one-line edit to `AUTO_RETRY_KINDS` in `services/llm_jobs.py` — the sweep query filters on this single constant, so widening or narrowing it doesn't touch any other logic in this plan.

Two related sub-decisions were **not** left open (made here, with rationale, revisit if you disagree):
- **Jobs that never actually ran (`attempts == 0`) are excluded from the sweep.** `enqueue_llm_job` can create a job directly in `status="failed"` with `attempts=0` when a precondition fails at creation time (e.g. `enqueue_auto_correction`'s "no groq API key saved" path) — `TranscriptionJob` never has this shape (it's only ever `failed` after `_run_chunk_job` increments `attempts` first). Auto-retrying a job that failed before ever running would just re-fail the same precondition check on a timer for no reason.
- **Dismissed jobs are excluded from the sweep.** `TranscriptionJob`'s own resurrection sweep in `services/queue.py` has no such guard (dismissal there is transcript-level, decided only once a transcript is fully terminal), so this is a deliberate improvement rather than strict parity — `LlmJob.dismissed` is per-job, so a user explicitly hiding one failed job should not see it silently come back to life and re-run in the background.

---

## Global Constraints

- Run tests with this project's virtualenv: `.venv\Scripts\python.exe -m pytest <args>` (per `tests/conftest.py`'s interpreter guard).
- Preserve the existing "claim lands before any await" invariant in `llm_worker_tick`: the `attempts` increment happens in the *same* loop that sets `job.status = "running"`, before the one `db.commit()`, before `db.close()`, before `asyncio.gather` starts awaiting `run_llm_job`. Never increment `attempts` inside `run_llm_job` itself (that runs in its own per-job session, after the await boundary, and would defeat the whole point of counting attempts at the claim checkpoint).
- Reuse `services.queue.MAX_ATTEMPTS` (currently `3`) and `services.queue._retry_eligible` verbatim — both are duck-typed on `.attempts`/`.updated_at` only, with no `TranscriptionJob`-specific logic, so they work unmodified against `LlmJob` rows. Do not invent a separate backoff formula or a separate `MAX_ATTEMPTS` constant for `LlmJob`.
- `services/queue.py` has no top-level import of `services.llm_jobs` (only a deferred local import inside `_finalize_if_done`, specifically to avoid a module-load cycle), so `services/llm_jobs.py` importing `from services.queue import MAX_ATTEMPTS, _retry_eligible` at module top level is safe — no circular import.
- **Cross-reference:** a sibling plan, `docs/superpowers/plans/2026-07-07-queue-audit-split-concurrent-job-pools.md`, also modifies `llm_worker_tick` (splitting the single `_MAX_CONCURRENT_JOBS` cap into IO/CPU pools). As of this writing that plan is unexecuted — the current code still has the single global cap this plan's diffs are written against. If that plan lands first, the resurrection sweep (Task 3) still goes at the very top of `llm_worker_tick`, before whatever cap logic exists; the `attempts` increment (Task 2) still goes in whichever loop(s) set `job.status = "running"` for a claimed job, applied identically regardless of which pool claimed it. Neither task's logic depends on how the concurrency cap is structured.

---

### Task 1: Add `attempts` column to `LlmJob`

**Files:**
- Modify: `database/__init__.py:79-97` (`LlmJob` model), `database/__init__.py:298` (`ensure_columns` call)
- Test: `tests/test_llm_jobs.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm_jobs.py`:

```python
def test_llm_job_attempts_defaults_to_zero(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    assert job.attempts == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k test_llm_job_attempts_defaults_to_zero`
Expected: FAIL with `AttributeError: 'LlmJob' object has no attribute 'attempts'`

- [ ] **Step 3: Implement**

In `database/__init__.py`, in the `LlmJob` class, insert an `attempts` column right after `status` (currently line 88):

```python
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(32), nullable=False)  # correction | summary
    status = Column(String(32), default="pending")  # pending, running, completed, failed, cancelled
    attempts = Column(Integer, default=0)  # incremented at claim time in llm_worker_tick — powers auto-retry backoff
    progress_done = Column(Integer, default=0)
```

At line 298, add `attempts` to the `ensure_columns` migration call so existing (pre-upgrade) databases get the column too. `INTEGER DEFAULT 0` (not bare `INTEGER`) is required here — SQLite's `ALTER TABLE ADD COLUMN` backfills existing rows with `NULL` otherwise, and `_retry_eligible`'s `job.attempts >= MAX_ATTEMPTS` (added in Task 3) would raise `TypeError` comparing `None >= 3` the first time the sweep hits a legacy row:

```python
    ensure_columns(engine, "llm_jobs", {"dismissed": "BOOLEAN DEFAULT 0", "result_json": "JSON", "attempts": "INTEGER DEFAULT 0"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k test_llm_job_attempts_defaults_to_zero`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database/__init__.py tests/test_llm_jobs.py
git commit -m "feat: add attempts column to LlmJob for auto-retry tracking"
```

---

### Task 2: Increment `attempts` at claim time

**Files:**
- Modify: `services/llm_jobs.py:348-351` (claim loop inside `llm_worker_tick`)
- Test: `tests/test_llm_jobs.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm_jobs.py`:

```python
def test_worker_tick_increments_attempts_on_claim(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    assert job.attempts == 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_FakeResponse("S: fixed"))):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.attempts == 1
    assert job.status == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k test_worker_tick_increments_attempts_on_claim`
Expected: FAIL — `assert job.attempts == 1` fails (actual: `0`); the job does complete (existing dispatch logic already works), but nothing increments `attempts` yet.

- [ ] **Step 3: Implement**

In `services/llm_jobs.py`, in `llm_worker_tick`, change the claim loop from:

```python
        for job in claimed:
            job.status = "running"
            job.updated_at = utcnow_naive()
        db.commit()  # claim lands before any await — same invariant as the chunk queue
```

to:

```python
        for job in claimed:
            job.status = "running"
            job.attempts = (job.attempts or 0) + 1
            job.updated_at = utcnow_naive()
        db.commit()  # claim lands before any await — same invariant as the chunk queue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k test_worker_tick_increments_attempts_on_claim`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/llm_jobs.py tests/test_llm_jobs.py
git commit -m "feat: increment LlmJob.attempts at claim time"
```

---

### Task 3: Add the auto-retry resurrection sweep (timing, dismissed, never-ran, and kind guards)

**Files:**
- Modify: `services/llm_jobs.py:1-20` (imports and constants), `services/llm_jobs.py:332` (top of `llm_worker_tick`)
- Test: `tests/test_llm_jobs.py` (append)

**Interfaces:**
- Produces: `AUTO_RETRY_KINDS` constant, consumed by the sweep added in this task and referenced by the Open Design Questions section above (nothing later depends on it beyond this task, so nothing here is front-loaded).
- Imports `MAX_ATTEMPTS` and `_retry_eligible` from `services.queue` — both already exist and are exported (module-level, no `__all__` restricting them) as of `services/queue.py:243` and `services/queue.py:391-396`.

- [ ] **Step 1: Write the failing tests**

First, extend the imports at the top of `tests/test_llm_jobs.py`. Change:

```python
import asyncio
import io
import json
from unittest.mock import AsyncMock, patch

from database import LlmJob, Transcript, User, ProviderConfig
from services.llm_jobs import (
    enqueue_llm_job, run_llm_job, cancel_llm_job, rerun_llm_job, llm_worker_tick,
    reset_stuck_llm_jobs, dismiss_llm_job, clear_finished_llm_jobs,
)
```

to:

```python
import asyncio
import datetime
import io
import json
from unittest.mock import AsyncMock, patch

from database import LlmJob, Transcript, User, ProviderConfig, utcnow_naive
from services.llm_jobs import (
    enqueue_llm_job, run_llm_job, cancel_llm_job, rerun_llm_job, llm_worker_tick,
    reset_stuck_llm_jobs, dismiss_llm_job, clear_finished_llm_jobs,
)
from services.queue import MAX_ATTEMPTS
```

Then append these six tests:

```python
def test_worker_tick_resurrects_failed_job_past_backoff_window(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = 1
    job.error = "transient network blip"
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=100)  # backoff for attempts=1 is 10s
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_FakeResponse("S: fixed"))):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.attempts == 2
    assert job.error is None


def test_worker_tick_leaves_failed_job_within_backoff_window(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = 1
    job.error = "transient network blip"
    job.updated_at = utcnow_naive()  # just failed — backoff for attempts=1 is 10s, not elapsed
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1


def test_worker_tick_never_resurrects_job_at_max_attempts(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = MAX_ATTEMPTS
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"


def test_worker_tick_never_resurrects_dismissed_job(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = 1
    job.dismissed = True
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"


def test_worker_tick_never_resurrects_a_job_that_never_ran(db_session):
    """attempts stays 0 for jobs enqueue_llm_job pre-fails immediately (e.g.
    'no API key saved') — retrying a precondition failure would just fail
    identically, so these are excluded from the auto-retry sweep."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(
        db_session, user.id, t.id, "correction", "openrouter", "m1",
        error="auto-correct skipped: no openrouter API key saved (see service panel)",
    )
    assert job.status == "failed" and job.attempts == 0
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"


def test_worker_tick_never_resurrects_non_auto_retry_kinds(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    job.status = "failed"
    job.attempts = 1
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None, diarization_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
```

- [ ] **Step 2: Run tests to verify they fail (and note which ones don't, and why)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k "resurrects or never_resurrects or backoff_window"`

Against the still-unmodified code (no sweep exists at all yet, `AUTO_RETRY_KINDS` doesn't exist):
- `test_worker_tick_resurrects_failed_job_past_backoff_window` — FAIL. Nothing resurrects a `failed` job today, so it stays `failed`/`attempts=1` instead of reaching `completed`/`attempts=2`. This is the real red/green test proving the sweep mechanism works.
- `test_worker_tick_leaves_failed_job_within_backoff_window` — PASSES even before this change. With no sweep at all, the job trivially stays `failed`. Kept as a pin: once the sweep exists, this is what actually proves the *timing* guard (not just "no sweep exists") is correct — if you implement the sweep without checking `_retry_eligible`'s elapsed-time condition, this test goes red.
- `test_worker_tick_never_resurrects_job_at_max_attempts` — PASSES even before this change, same reasoning. Pins the `attempts >= MAX_ATTEMPTS` guard.
- `test_worker_tick_never_resurrects_dismissed_job` — PASSES even before this change, same reasoning. Pins the `dismissed.is_(False)` guard.
- `test_worker_tick_never_resurrects_a_job_that_never_ran` — PASSES even before this change, same reasoning. Pins the `attempts >= 1` guard.
- `test_worker_tick_never_resurrects_non_auto_retry_kinds` — PASSES even before this change, same reasoning. Pins the `kind.in_(AUTO_RETRY_KINDS)` guard.

The intent: write all six now, then implement the sweep with all four guards baked into one query from the start (Step 3) rather than adding them one at a time — the five "trivially passing" tests exist to catch a *naive* implementation (e.g. one that copies `services/queue.py`'s `pending_or_retry` pattern verbatim, which has none of these four guards) rather than to catch "no sweep at all".

- [ ] **Step 3: Implement**

In `services/llm_jobs.py`, add the import and constant. Change the top-of-file imports from:

```python
import asyncio
import datetime

from database import LlmJob, Transcript, VoiceProfile, utcnow_naive
from services.audio_prep import extract_clips_concat
from services.voice_id import voice_id_service
```

to:

```python
import asyncio
import datetime

from database import LlmJob, Transcript, VoiceProfile, utcnow_naive
from services.audio_prep import extract_clips_concat
from services.queue import MAX_ATTEMPTS, _retry_eligible
from services.voice_id import voice_id_service
```

Change `VALID_KINDS` (currently line 20) from:

```python
VALID_KINDS = ("correction", "summary", "rediarize", "voice_match")
```

to:

```python
VALID_KINDS = ("correction", "summary", "rediarize", "voice_match")
# Auto-retry (issue #14) is scoped to network-dependent kinds only —
# correction/summary call a provider API and can fail transiently.
# rediarize/voice_match are local CPU-bound compute (diarization clustering,
# voice-embedding extraction); a failure there is far more likely to be
# deterministic (bad audio, missing backend, no enrolled voices) than
# transient, so blindly retrying up to MAX_ATTEMPTS would just re-run
# expensive local inference against the same failure. See "Open Design
# Questions" in docs/superpowers/plans/2026-07-07-queue-audit-llmjob-auto-retry.md
# if reconsidering.
AUTO_RETRY_KINDS = ("correction", "summary")
```

Then, at the top of `llm_worker_tick` (currently starting at line 332), insert the sweep before the existing `running = ...` count query:

```python
async def llm_worker_tick(SessionLocal, transcription_service, diarization_service=None) -> None:
    db = SessionLocal()
    try:
        eligible_failed = (
            db.query(LlmJob)
            .filter(
                LlmJob.status == "failed",
                LlmJob.dismissed.is_(False),
                LlmJob.attempts >= 1,
                LlmJob.kind.in_(AUTO_RETRY_KINDS),
            )
            .all()
        )
        for job in eligible_failed:
            if _retry_eligible(job):
                job.status = "pending"
                job.error = None
        db.commit()

        running = db.query(LlmJob).filter(LlmJob.status == "running").count()
        slots = max(0, _MAX_CONCURRENT_JOBS - running)
        if slots == 0:
            return
        claimed = (
            db.query(LlmJob)
            .filter(LlmJob.status == "pending")
            .order_by(LlmJob.id.asc())
            .limit(slots)
            .all()
        )
        if not claimed:
            return
        for job in claimed:
            job.status = "running"
            job.attempts = (job.attempts or 0) + 1
            job.updated_at = utcnow_naive()
        db.commit()  # claim lands before any await — same invariant as the chunk queue
        job_ids = [job.id for job in claimed]
    finally:
        db.close()

    await asyncio.gather(*(run_llm_job(SessionLocal, jid, transcription_service, diarization_service) for jid in job_ids))
```

(Only the new `eligible_failed` block at the top is new here — the rest of the function is unchanged from Task 2.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v`
Expected: PASS — all pre-existing tests plus all new tests from Tasks 1-3.

- [ ] **Step 5: Commit**

```bash
git add services/llm_jobs.py tests/test_llm_jobs.py
git commit -m "feat: auto-retry failed correction/summary LlmJobs on the existing backoff timer"
```

---

### Task 4: Guard against double-dispatch when a manual rerun already created an active sibling job

**Files:**
- Modify: `services/llm_jobs.py` (sweep loop added in Task 3, inside `llm_worker_tick`)
- Test: `tests/test_llm_jobs.py` (append)

**Why this is its own task:** manual rerun (`rerun_llm_job`) creates a brand-new `LlmJob` row rather than reusing the failed one — `enqueue_llm_job`'s dedup (`get_active_job`, which only looks at `pending`/`running` rows) never even sees the old failed row, since "failed" isn't an active status. If a user reruns a job manually, and the *original* failed row is independently still eligible for the auto-retry sweep (attempts < MAX_ATTEMPTS, backoff elapsed, not dismissed), the next tick would resurrect the old row too — producing two jobs for the same transcript+kind dispatched concurrently, both mutating the same transcript. There is no DB-level uniqueness constraint on `(transcript_id, kind)` to catch this; the only dedup is the app-level `get_active_job` check at enqueue time, which the sweep bypasses entirely unless it's taught to check it too.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm_jobs.py`:

```python
def test_worker_tick_skips_resurrection_when_a_fresh_job_already_active(db_session):
    """A manual rerun creates a NEW row rather than reusing the failed one
    (see rerun_llm_job) — if the auto-retry sweep resurrected the old failed
    row too, both would dispatch and write the same transcript concurrently."""
    user, t = _make_user_and_transcript(db_session)
    stale = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    stale.status = "failed"
    stale.attempts = 1
    stale.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    fresh = rerun_llm_job(db_session, user.id, stale.id)
    assert fresh.status == "pending"

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_FakeResponse("S: fixed"))):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(stale)
    db_session.refresh(fresh)
    assert stale.status == "failed"      # left alone — fresh sibling already covers this lane
    assert fresh.status == "completed"   # the manually-created job dispatched normally
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k test_worker_tick_skips_resurrection_when_a_fresh_job_already_active`
Expected: FAIL — `assert stale.status == "failed"` fails (actual: `"completed"`). Against Task 3's code, the sweep has no sibling check, so it resurrects `stale` to `pending` too; both `stale` and `fresh` are then claimed and dispatched by the same tick (2 slots, `_MAX_CONCURRENT_JOBS` is 2), and both complete against the mocked provider call — demonstrating the double-dispatch this task exists to prevent.

- [ ] **Step 3: Implement**

In `services/llm_jobs.py`, change the sweep loop added in Task 3 from:

```python
        for job in eligible_failed:
            if _retry_eligible(job):
                job.status = "pending"
                job.error = None
        db.commit()
```

to:

```python
        for job in eligible_failed:
            if not _retry_eligible(job):
                continue
            if get_active_job(db, job.transcript_id, job.kind) is not None:
                # A manual rerun already created a fresh pending/running job
                # for this transcript+kind — resurrecting this stale failed
                # row too would dispatch two jobs writing the same
                # transcript concurrently. Leave it failed; it's still
                # manually rerunnable if the fresh sibling later fails too.
                continue
            job.status = "pending"
            job.error = None
        db.commit()
```

(`get_active_job` is already defined earlier in this same module — no new import needed.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v`
Expected: PASS — all tests from Tasks 1-4.

- [ ] **Step 5: Commit**

```bash
git add services/llm_jobs.py tests/test_llm_jobs.py
git commit -m "fix: don't auto-resurrect a stale failed LlmJob if a manual rerun already created a fresh one"
```

---

### Task 5: Update `reset_stuck_llm_jobs`'s docstring and confirm attempts aren't double-counted

**Files:**
- Modify: `services/llm_jobs.py:79-88` (`reset_stuck_llm_jobs`)
- Test: `tests/test_llm_jobs.py` (append)

**Note:** `reset_stuck_llm_jobs`'s actual behavior needs no code change — it already only flips `status` and `error`, never touching `attempts`, and Task 2 already made `attempts` get incremented at claim time (before the crash that leaves a job stuck `running`), so a restart-interrupted job's `attempts` is already correct by construction. This task is a documentation fix (the docstring currently says "LlmJob has no auto-retry", which becomes false after Task 3) plus a regression test pinning that correctness so a future change to this function can't silently double-count attempts.

- [ ] **Step 1: Write the test**

Append to `tests/test_llm_jobs.py`:

```python
def test_reset_stuck_llm_jobs_preserves_attempts_count(db_session):
    """attempts was already incremented at claim time (llm_worker_tick) before
    the crashed await — reset_stuck_llm_jobs must not increment it again, so
    a crash-interrupted job doesn't burn an extra retry it never used."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    job.attempts = 1
    db_session.commit()

    reset_stuck_llm_jobs(db_session)

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1
```

- [ ] **Step 2: Run test to verify it passes already**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k test_reset_stuck_llm_jobs_preserves_attempts_count`
Expected: PASS immediately — no code change is needed for this test to pass, since `reset_stuck_llm_jobs` never touched `attempts` to begin with. This step exists to confirm the pin is correct before moving on, not to drive a code change.

- [ ] **Step 3: Update the docstring**

In `services/llm_jobs.py`, change:

```python
def reset_stuck_llm_jobs(db) -> int:
    """Startup reconciliation: an LlmJob left 'running' means the process
    died mid-job. LlmJob has no auto-retry, so this matches its existing
    failure UX — the user reruns it manually via /api/jobs/{id}/rerun."""
```

to:

```python
def reset_stuck_llm_jobs(db) -> int:
    """Startup reconciliation: an LlmJob left 'running' means the process
    died mid-job. attempts was already incremented before the crashed
    await (see the claim loop in llm_worker_tick), so land it on 'failed'
    and let the normal sweep + _retry_eligible backoff resurrect it (for
    AUTO_RETRY_KINDS) — never straight back to 'pending'. Mirrors
    reset_stuck_transcription_jobs' identical reasoning for TranscriptionJob."""
```

- [ ] **Step 4: Run the full file to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/llm_jobs.py tests/test_llm_jobs.py
git commit -m "docs: update reset_stuck_llm_jobs docstring for auto-retry, pin attempts not double-counted"
```

---

### Task 6: Regression test — manual rerun still works after auto-retry exhausts MAX_ATTEMPTS

**Files:**
- Test: `tests/test_llm_jobs.py` (append)

**Note:** No code change — `rerun_llm_job`'s guard (`job.status not in ("failed", "cancelled")`) already treats a MAX_ATTEMPTS-exhausted job identically to any other `failed` job, since both are just `status == "failed"` rows. This task exists purely to lock in that the auto-retry feature never regresses the manual rerun path, both at the service-function level and the route level.

- [ ] **Step 1: Write the tests**

Append to `tests/test_llm_jobs.py`:

```python
def test_rerun_still_works_after_auto_retry_exhausts_max_attempts(db_session):
    """A job that auto-retried up to MAX_ATTEMPTS and landed permanently
    'failed' must remain manually rerunnable — auto-retry must never lock
    out the existing /api/jobs/{id}/rerun path."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = MAX_ATTEMPTS
    job.error = "boom"
    db_session.commit()

    fresh = rerun_llm_job(db_session, user.id, job.id)

    assert fresh.id != job.id
    assert fresh.status == "pending"
    assert fresh.attempts == 0


def test_rerun_route_works_on_permanently_failed_job(client, db_session):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    transcript_id = _upload(client).json()["id"]

    jobs = client.get("/api/jobs").json()["jobs"]
    job_id = next(j["id"] for j in jobs if j["kind"] == "correction")

    job = db_session.query(LlmJob).filter(LlmJob.id == job_id).first()
    job.status = "failed"
    job.attempts = MAX_ATTEMPTS  # simulates auto-retry having exhausted its budget
    db_session.commit()

    rerun = client.post(f"/api/jobs/{job_id}/rerun").json()
    assert rerun["job"]["status"] == "pending"
    assert rerun["job"]["id"] != job_id
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k "rerun_still_works or rerun_route_works_on_permanently_failed"`
Expected: PASS — both should pass immediately, with no production code change needed, confirming the manual rerun path was never broken by Tasks 1-5.

- [ ] **Step 3: Run the entire suite for a final regression check**

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: PASS across the whole suite — confirms nothing in `database/__init__.py` or `services/llm_jobs.py` broke any other test file (e.g. `tests/test_llm_job_history_backfill.py`, `tests/test_voice_match_job.py`, route tests in `tests/test_llm_jobs.py` itself).

- [ ] **Step 4: Commit**

```bash
git add tests/test_llm_jobs.py
git commit -m "test: confirm manual rerun still works after LlmJob auto-retry exhausts MAX_ATTEMPTS"
```
