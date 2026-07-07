# Queue Audit: `enqueue_llm_job` Dedupe Race — DB Constraint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Back `enqueue_llm_job`'s check-then-act dedupe with a DB-level partial unique index, so a duplicate-active-job race is impossible even if this app is ever deployed with multiple worker processes, and have the application code degrade gracefully (not crash) if that constraint is ever hit.

**Architecture:** `enqueue_llm_job` (`services/llm_jobs.py`) currently checks for an existing active (`pending`/`running`) `LlmJob` for the same `(transcript_id, kind)` via `get_active_job()`, then inserts a new row if none exists — classic check-then-act, racy only under concurrent writers. Add a SQLite partial unique index on `llm_jobs(transcript_id, kind) WHERE status IN ('pending', 'running')`, created idempotently on every startup via a new `ensure_active_llm_job_unique_index()` migration helper (mirroring the existing `ensure_columns()` additive-migration pattern, but for an index rather than a column — SQLite supports adding an index without a table rebuild). The index deliberately excludes terminal statuses (`completed`/`failed`/`cancelled`), so it does not conflict with the run-history feature's multiple-completed-rows-per-`(transcript_id, kind)` design. `enqueue_llm_job` catches the `IntegrityError` the index would raise on a genuine race and falls back to returning the row that won, matching the existing "already has an active job" behavior instead of propagating a 500.

**Tech Stack:** Python/FastAPI backend, SQLAlchemy 2.0 ORM + raw SQL migrations, SQLite, pytest (sync, in-process — no real threading in this suite).

---

## Global Constraints

- This app runs `uvicorn.run("app:app", ..., reload=False)` with no `workers=` argument (`app.py:1528`) — single process. The race this plan guards against is **not reachable today**; this is defense-in-depth for a future multi-process deployment. Low priority — do not gold-plate beyond what's specified here.
- The index's WHERE clause has exactly one source of truth: the raw SQL in `ensure_active_llm_job_unique_index()` (Task 1). It is **not** also declared on the `LlmJob` model's `__table_args__`. Two hand-maintained declarations of the same constraint (one compiled by SQLAlchemy's DDL layer, one raw SQL) could drift silently — see this codebase's own guidance on mirrored paths. If a future change needs the constraint's shape reflected in the ORM model too, redo this as a single `Index(..., sqlite_where=...)` object and drive the migration from `index_obj.create(engine, checkfirst=True)` — don't add a second SQL string.
- Multiple **completed** `LlmJob` rows per `(transcript_id, kind)` are load-bearing for the run-history feature (Phases 1-4, already merged) — `LlmJob.result_json`, the `/api/transcripts/{id}/runs/{kind}` endpoint, and `tests/test_llm_job_history_backfill.py` all depend on this. The index must never block that. Task 1's tests assert this explicitly.
- Test style: `tests/test_llm_jobs.py` is synchronous and in-process (see its module docstring) — there's no real concurrency in this suite. A true two-connection race isn't practical here, so:
  - The DB constraint itself is tested by inserting two active rows directly (bypassing `enqueue_llm_job`'s Python check) and confirming the second `commit()` raises `IntegrityError`. This is an explicitly-acceptable substitute for a real concurrency test, per this plan's scope.
  - The graceful-fallback code path in `enqueue_llm_job` is tested by monkeypatching `get_active_job` to return `None` (simulating the Python check losing a race), so the insert below it is the thing that actually hits the constraint.

---

### Task 1: Partial unique index migration (`database/__init__.py`)

**Files:**
- Modify: `database/__init__.py:79-97` (`LlmJob` docstring — note only, no schema change here), `database/__init__.py:219-238` (add new functions after `ensure_columns`), `database/__init__.py:282-302` (`init_db` — wire in the new call), `database/__init__.py:305-308` (`__all__`)
- Test: `tests/test_llm_jobs.py` (append new tests)

**Interfaces:**
- Produces: `dedupe_active_llm_jobs(engine) -> int` — collapses any pre-existing duplicate active rows for the same `(transcript_id, kind)` down to one (keeps the lowest `id`, cancels the rest), returns the count cancelled. Exported from `database/__init__.py`.
- Produces: `ensure_active_llm_job_unique_index(engine) -> None` — calls `dedupe_active_llm_jobs()` then creates `ux_llm_jobs_active_transcript_kind`, a `CREATE UNIQUE INDEX IF NOT EXISTS ... WHERE status IN ('pending', 'running')` on `llm_jobs(transcript_id, kind)`. Idempotent; safe on every call to `init_db()`. Exported from `database/__init__.py`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm_jobs.py`, after the existing `test_enqueue_dedupes_active_jobs` (after line 61):

```python
def test_active_llm_job_unique_index_rejects_duplicate_active_rows(db_session):
    """DB-level substitute for a true concurrency test — this suite is
    synchronous/in-process (see module docstring), so two real
    enqueue_llm_job() calls can't race from separate connections. Bypasses
    the Python check-then-act by inserting two active rows directly,
    proving the partial unique index itself (not just the application-level
    check) rejects duplicate active jobs for the same transcript+kind."""
    from sqlalchemy.exc import IntegrityError

    user, t = _make_user_and_transcript(db_session)
    first = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction",
                    provider="groq", model="m1", status="pending")
    db_session.add(first)
    db_session.commit()

    second = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction",
                     provider="groq", model="m2", status="pending")
    db_session.add(second)
    try:
        db_session.commit()
        assert False, "expected IntegrityError from the partial unique index"
    except IntegrityError:
        db_session.rollback()

    active = db_session.query(LlmJob).filter(
        LlmJob.transcript_id == t.id, LlmJob.kind == "correction",
        LlmJob.status.in_(("pending", "running")),
    ).all()
    assert len(active) == 1
    assert active[0].id == first.id


def test_active_llm_job_unique_index_allows_multiple_completed_rows(db_session):
    """The index must NOT block the run-history case: many completed rows
    per (transcript_id, kind) are expected (correction/summary/rediarize
    rerun history — see tests/test_llm_job_history_backfill.py). Also
    covers a completed+pending pair for the same transcript+kind, which is
    exactly the rerun_llm_job() flow (rerun a failed/cancelled job while its
    prior completed siblings are still in the table)."""
    user, t = _make_user_and_transcript(db_session)

    older = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction",
                    provider="groq", model="m1", status="completed")
    newer = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction",
                    provider="groq", model="m2", status="completed")
    db_session.add_all([older, newer])
    db_session.commit()  # must not raise

    pending = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction",
                      provider="groq", model="m3", status="pending")
    db_session.add(pending)
    db_session.commit()  # must not raise — completed rows are unconstrained

    rows = db_session.query(LlmJob).filter(
        LlmJob.transcript_id == t.id, LlmJob.kind == "correction",
    ).all()
    assert len(rows) == 3


def test_dedupe_and_index_creation_on_preexisting_duplicates(tmp_path):
    """Simulates upgrading a database that predates this migration: llm_jobs
    exists without the partial unique index, and — hypothetically, since
    today's single-process app never triggers the check-then-act race —
    already has two active rows for the same transcript+kind.
    ensure_active_llm_job_unique_index() must clean these up before
    CREATE UNIQUE INDEX runs, not crash startup."""
    from sqlalchemy import create_engine, text
    from database import Base, ensure_active_llm_job_unique_index

    db_path = tmp_path / "pre_migration.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)  # llm_jobs exists; the index is not part of the ORM model

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, username, password_hash, password_salt) "
            "VALUES (1, 'u', 'x', 'y')"
        ))
        conn.execute(text(
            "INSERT INTO transcripts (id, user_id, title, filename, status, full_text, segments) "
            "VALUES (1, 1, 't', 'f.mp3', 'completed', '', '[]')"
        ))
        conn.execute(text(
            "INSERT INTO llm_jobs (id, user_id, transcript_id, kind, status, provider, model) "
            "VALUES (1, 1, 1, 'correction', 'pending', 'groq', 'm1')"
        ))
        conn.execute(text(
            "INSERT INTO llm_jobs (id, user_id, transcript_id, kind, status, provider, model) "
            "VALUES (2, 1, 1, 'correction', 'running', 'groq', 'm2')"
        ))

    ensure_active_llm_job_unique_index(engine)

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, status FROM llm_jobs WHERE transcript_id = 1 AND kind = 'correction' ORDER BY id"
        )).fetchall()
        assert [r[1] for r in rows] == ["pending", "cancelled"]

        idx = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = 'ux_llm_jobs_active_transcript_kind'"
        )).fetchone()
        assert idx is not None
    engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_active_llm_job_unique_index_rejects_duplicate_active_rows tests/test_llm_jobs.py::test_active_llm_job_unique_index_allows_multiple_completed_rows tests/test_llm_jobs.py::test_dedupe_and_index_creation_on_preexisting_duplicates -v`

Expected: `test_active_llm_job_unique_index_rejects_duplicate_active_rows` FAILs on the `assert False, "expected IntegrityError..."` line (no constraint exists yet, so the second `commit()` succeeds). `test_active_llm_job_unique_index_allows_multiple_completed_rows` passes vacuously today (nothing to break yet) — that's expected, it becomes a real regression guard after Step 3. `test_dedupe_and_index_creation_on_preexisting_duplicates` FAILs with `ImportError: cannot import name 'ensure_active_llm_job_unique_index'`.

- [ ] **Step 3: Implement the migration functions**

In `database/__init__.py`, update the `LlmJob` docstring (lines 80-81):

```python
class LlmJob(Base):
    """Background LLM work (correction / summary) against a transcript.
    Powers the Queue screen: status, batch progress, cancel/rerun.

    At most one active (pending/running) row per (transcript_id, kind) is
    enforced by a partial unique index — see
    ensure_active_llm_job_unique_index() below, applied on every startup.
    Deliberately does not cover completed/failed/cancelled rows: multiple
    historical rows per (transcript_id, kind) are expected and required for
    run-history/compare (result_json, backfill_llm_job_result_snapshots).
    """
```

Then, immediately after `ensure_columns()` (after line 237, before `backfill_llm_job_result_snapshots`), add:

```python
def dedupe_active_llm_jobs(engine) -> int:
    """Defensive cleanup run immediately before creating the partial unique
    index below: collapses any pre-existing duplicate active (pending/
    running) LlmJob rows for the same (transcript_id, kind) down to one,
    cancelling the extras. In practice this should find nothing — the
    check-then-act race in enqueue_llm_job() is only reachable under a
    multi-process deployment, and this app always runs uvicorn.run() with
    no `workers=` arg — but CREATE UNIQUE INDEX fails outright if
    violating rows already exist, so this keeps a dirty DB from crashing
    startup instead of silently never getting the new protection.
    Returns the number of rows cancelled.
    """
    inspector = inspect(engine)
    if "llm_jobs" not in inspector.get_table_names():
        return 0
    with engine.begin() as conn:
        dupes = conn.execute(text(
            "SELECT transcript_id, kind FROM llm_jobs "
            "WHERE status IN ('pending', 'running') "
            "GROUP BY transcript_id, kind HAVING COUNT(*) > 1"
        )).fetchall()
        cancelled = 0
        for transcript_id, kind in dupes:
            rows = conn.execute(text(
                "SELECT id FROM llm_jobs WHERE transcript_id = :tid AND kind = :kind "
                "AND status IN ('pending', 'running') ORDER BY id ASC"
            ), {"tid": transcript_id, "kind": kind}).fetchall()
            for (row_id,) in rows[1:]:  # keep the oldest (lowest id), cancel the rest
                conn.execute(text(
                    "UPDATE llm_jobs SET status = 'cancelled', "
                    "error = 'Superseded by duplicate-active-job cleanup (schema migration)' "
                    "WHERE id = :id"
                ), {"id": row_id})
                cancelled += 1
    return cancelled


def ensure_active_llm_job_unique_index(engine) -> None:
    """Partial unique index: at most one active (pending/running) LlmJob
    per (transcript_id, kind) — defense-in-depth against the check-then-act
    race in enqueue_llm_job() (services/llm_jobs.py), which would only be
    live under a multi-process deployment. Deliberately does NOT cover
    'completed'/'failed'/'cancelled' rows: multiple historical rows per
    (transcript_id, kind) are expected and required for run-history/compare
    (see backfill_llm_job_result_snapshots below).

    This is the single source of truth for the index's shape — it is not
    also declared on the LlmJob model's __table_args__, so there is only
    one place the WHERE clause can drift. Idempotent (CREATE ... IF NOT
    EXISTS), safe to call on every startup, including against a freshly
    created table (create_all() does not create this index itself).
    """
    inspector = inspect(engine)
    if "llm_jobs" not in inspector.get_table_names():
        return
    dedupe_active_llm_jobs(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_llm_jobs_active_transcript_kind "
            "ON llm_jobs (transcript_id, kind) "
            "WHERE status IN ('pending', 'running')"
        ))
```

In `init_db()`, change:

```python
    ensure_columns(engine, "llm_jobs", {"dismissed": "BOOLEAN DEFAULT 0", "result_json": "JSON"})
    ensure_columns(engine, "summaries", {"provider": "TEXT"})
```

to:

```python
    ensure_columns(engine, "llm_jobs", {"dismissed": "BOOLEAN DEFAULT 0", "result_json": "JSON"})
    ensure_active_llm_job_unique_index(engine)
    ensure_columns(engine, "summaries", {"provider": "TEXT"})
```

In `__all__`, change:

```python
__all__ = [
    "Base", "User", "Transcript", "Summary", "VoiceProfile", "VoiceClip", "ProviderConfig", "TranscriptionJob", "LlmJob", "HotwordEntry",
    "init_db", "migrate_schema", "backfill_user_id", "ensure_columns", "backfill_llm_job_result_snapshots",
]
```

to:

```python
__all__ = [
    "Base", "User", "Transcript", "Summary", "VoiceProfile", "VoiceClip", "ProviderConfig", "TranscriptionJob", "LlmJob", "HotwordEntry",
    "init_db", "migrate_schema", "backfill_user_id", "ensure_columns", "backfill_llm_job_result_snapshots",
    "dedupe_active_llm_jobs", "ensure_active_llm_job_unique_index",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_active_llm_job_unique_index_rejects_duplicate_active_rows tests/test_llm_jobs.py::test_active_llm_job_unique_index_allows_multiple_completed_rows tests/test_llm_jobs.py::test_dedupe_and_index_creation_on_preexisting_duplicates -v`

Expected: all three PASS.

- [ ] **Step 5: Regression check — run-history tests still pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py tests/test_llm_job_history_backfill.py -v`

Expected: all PASS, including the pre-existing `tests/test_llm_job_history_backfill.py` tests that create two `completed` `LlmJob` rows for the same `(transcript_id, kind)` (e.g. `older`/`newer` at lines 28-29 and 79-80) — this is direct proof the new index doesn't touch that codepath, since those rows are never `pending`/`running`.

- [ ] **Step 6: Commit**

```bash
git add database/__init__.py tests/test_llm_jobs.py
git commit -m "feat: partial unique index guards against duplicate active LlmJob rows"
```

---

### Task 2: Graceful `IntegrityError` handling in `enqueue_llm_job`

**Files:**
- Modify: `services/llm_jobs.py:1-20` (imports), `services/llm_jobs.py:59-76` (`enqueue_llm_job`)
- Test: `tests/test_llm_jobs.py` (append new test)

**Interfaces:**
- Modifies: `enqueue_llm_job(db, user_id, transcript_id, kind, provider, model, error=None) -> LlmJob` — same signature and return contract as today (returns the existing active job if one is already active), now also correct when the "no active job" read was stale due to a race that the Task 1 index catches.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm_jobs.py`, after `test_dedupe_and_index_creation_on_preexisting_duplicates` (the last test added in Task 1):

```python
def test_enqueue_llm_job_survives_race_past_the_python_check(db_session, monkeypatch):
    """Simulates a second process winning the check-then-act race: the
    Python-level get_active_job() check is monkeypatched to miss an
    already-active row on its FIRST call only (as it would under a true
    multi-process race), so enqueue_llm_job()'s insert hits the DB
    constraint instead of the Python check. The second call (in the
    except-branch recovery re-query) is passed through to the real
    get_active_job(), since a real recovery query would see the row the
    other process actually committed — a lambda that always returns None
    would defeat the recovery path too, not just the initial check.
    Confirms the resulting IntegrityError is caught and the caller gets
    the existing active job back, not a crash."""
    import services.llm_jobs as llm_jobs_module

    user, t = _make_user_and_transcript(db_session)
    existing = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")

    real_get_active_job = llm_jobs_module.get_active_job
    calls = {"n": 0}

    def fake_get_active_job(db, tid, kind):
        calls["n"] += 1
        # Miss only on the first call (the pre-insert check inside the
        # second enqueue_llm_job() below) — pass every later call (the
        # recovery re-query after the IntegrityError) through to the real
        # implementation.
        return None if calls["n"] == 1 else real_get_active_job(db, tid, kind)

    monkeypatch.setattr(llm_jobs_module, "get_active_job", fake_get_active_job)

    result = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m2")

    assert calls["n"] == 2  # 1 missed check + 1 real recovery re-query
    assert result.id == existing.id
    active = db_session.query(LlmJob).filter(
        LlmJob.transcript_id == t.id, LlmJob.kind == "correction",
        LlmJob.status.in_(("pending", "running")),
    ).all()
    assert len(active) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_enqueue_llm_job_survives_race_past_the_python_check -v`

Expected: FAIL with `sqlalchemy.exc.IntegrityError` raised out of `enqueue_llm_job`'s `db.commit()` (uncaught), not the graceful return the test asserts.

- [ ] **Step 3: Implement the fix**

In `services/llm_jobs.py`, add the import above the existing `database`/`services` imports (after line 8, the `datetime` import), keeping the third-party import separated from the local ones:

```python
from sqlalchemy.exc import IntegrityError

from database import LlmJob, Transcript, VoiceProfile, utcnow_naive
from services.audio_prep import extract_clips_concat
from services.voice_id import voice_id_service
```

Then replace `enqueue_llm_job` (lines 59-76):

```python
def enqueue_llm_job(db, user_id: int, transcript_id: int, kind: str,
                    provider: str, model: str, error: str | None = None) -> LlmJob:
    """One active job per transcript+kind — returns the existing one instead
    of stacking duplicates. `error` pre-fails the job (e.g. 'no key saved')
    so the skip is visible and rerunnable in the queue.

    The check-then-act above (get_active_job then insert) is racy under a
    multi-process deployment — this app doesn't run one today (see
    ensure_active_llm_job_unique_index() in database/__init__.py for the
    defense-in-depth DB constraint that backs this). If a concurrent insert
    ever wins the race, the commit below raises IntegrityError instead of
    the Python check catching it; the except clause re-queries and returns
    the row that won, so callers see the same "already has an active job"
    behavior as the non-racy path instead of a 500.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"Unknown LLM job kind: {kind}")
    existing = get_active_job(db, transcript_id, kind)
    if existing:
        return existing
    job = LlmJob(
        user_id=user_id, transcript_id=transcript_id, kind=kind,
        provider=provider, model=model,
        status="failed" if error else "pending", error=error,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = get_active_job(db, transcript_id, kind)
        if existing:
            return existing
        raise
    return job
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_enqueue_llm_job_survives_race_past_the_python_check -v`

Expected: PASS.

- [ ] **Step 5: Full regression check**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py tests/test_llm_job_history_backfill.py tests/test_posthoc_reprocess.py tests/test_voice_match_job.py tests/test_queue_dismiss_routes.py -v`

Expected: all PASS. These are the other suites that construct `LlmJob` rows directly or call `enqueue_llm_job` — confirms the new `try`/`except` and the Task 1 index don't change behavior for any existing caller (`enqueue_auto_correction`, `rerun_llm_job`, the `/api/transcripts/{id}/{summarize,correct,rediarize,voice-match}` routes — none of these hold uncommitted, non-`LlmJob` changes on `db` at the point they call `enqueue_llm_job`, except the rediarize route, which explicitly commits `t.num_speakers`/`t.diarize_requested` at `app.py:1079` before calling it — so a rollback on the rare race path never discards unrelated caller state).

- [ ] **Step 6: Commit**

```bash
git add services/llm_jobs.py tests/test_llm_jobs.py
git commit -m "fix: catch IntegrityError from the active-job unique index in enqueue_llm_job"
```
