"""Regression tests for issue #391: a write endpoint racing another writer
under sqlite's WAL busy_timeout used to escape as a bare 500. app.py now
registers a single @app.exception_handler(OperationalError) (see the seam
right after the middleware setup, before the first route) that maps a
"database is locked" OperationalError to 409 with a Retry-After header, and
re-raises anything else unchanged.

Test 1 proves the underlying exception this handler depends on: real lock
contention against cancel_llm_job raises sqlalchemy.exc.OperationalError
with "is locked" in its message. Tests 2-4 exercise the app-level mapping
through the actual routes, via a monkeypatched raiser rather than real
contention -- real cross-connection contention against the routes would be
slow and flaky (PRAGMA busy_timeout defaults to 5000ms), and test 1 already
pins the exception shape that makes the monkeypatched raiser realistic.
"""
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from database import LlmJob, Transcript, User
from services.llm_jobs import cancel_llm_job


def _user(db_session, name="locker"):
    user = User(username=name, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def test_lock_contention_raises_operational_error(db_session):
    """Issue #391: pins the exception contract the app-level handler relies
    on. Without a second writer holding sqlite's write lock, cancel_llm_job's
    UPDATE would just succeed -- there would be nothing for the handler to
    map, and reverting the fix would leave this test passing but the bug
    unfixed. The contention below is what makes it a real regression test."""
    user = _user(db_session)
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction",
                 provider="groq", model="m", status="running")
    db_session.add(job)
    db_session.commit()

    # Lower the wait on db_session's own connection so the test fails fast
    # instead of waiting out the real 5000ms default.
    db_session.connection().exec_driver_sql("PRAGMA busy_timeout=100")

    engine = db_session.get_bind()
    conn = engine.connect()
    try:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        conn.exec_driver_sql(
            f"UPDATE llm_jobs SET updated_at=updated_at WHERE id={job.id}"
        )
        # Held open, uncommitted -- this connection now owns sqlite's one
        # write lock, so db_session's UPDATE below must contend for it.

        with pytest.raises(OperationalError) as ei:
            cancel_llm_job(db_session, user.id, job.id)

        assert "is locked" in str(ei.value)
    finally:
        conn.rollback()
        conn.close()
        db_session.rollback()


def test_cancel_route_maps_locked_db_to_409(client, monkeypatch):
    """Mutation check: without the handle_db_locked exception handler in
    app.py, TestClient (default raise_server_exceptions=True) re-raises the
    OperationalError instead of letting the route return a response, and
    this test errors rather than failing a plain assertion."""
    def raiser(*args, **kwargs):
        raise OperationalError(
            "UPDATE llm_jobs SET status=?", (1,),
            sqlite3.OperationalError("database is locked"),
        )

    monkeypatch.setattr("app.cancel_llm_job", raiser)

    r = client.post("/api/jobs/1/cancel")

    assert r.status_code == 409
    assert "retry" in r.json()["detail"].lower()
    assert r.headers.get("retry-after") == "1"


def test_rerun_route_maps_locked_db_to_409(client, monkeypatch):
    """Same mapping as the cancel route, for /api/jobs/{id}/rerun -- pins
    that the handler is registered app-wide, not bolted onto cancel_job
    alone."""
    def raiser(*args, **kwargs):
        raise OperationalError(
            "UPDATE llm_jobs SET status=?", (1,),
            sqlite3.OperationalError("database is locked"),
        )

    monkeypatch.setattr("app.rerun_llm_job", raiser)

    r = client.post("/api/jobs/1/rerun")

    assert r.status_code == 409
    assert "retry" in r.json()["detail"].lower()
    assert r.headers.get("retry-after") == "1"


def test_non_lock_operational_error_still_raises(client, monkeypatch):
    """The handler must not blanket-convert every OperationalError to 409 --
    only the ones sqlite raises for lock contention. A disk I/O error (or any
    other OperationalError without "is locked" in its message) must still
    propagate as an unhandled error, which TestClient's default
    raise_server_exceptions=True re-raises into the test rather than turning
    into a response."""
    def raiser(*args, **kwargs):
        raise OperationalError(
            "UPDATE llm_jobs SET status=?", (1,),
            sqlite3.OperationalError("disk I/O error"),
        )

    monkeypatch.setattr("app.cancel_llm_job", raiser)

    with pytest.raises(OperationalError) as ei:
        client.post("/api/jobs/1/cancel")

    assert "disk I/O error" in str(ei.value)
