"""Backfill: pre-existing completed LlmJob rows (from before result_json
existed) get a snapshot filled in from the transcript's current output, so
the run-history picker isn't empty for old data on first upgrade."""
from database import LlmJob, Transcript, User, backfill_llm_job_result_snapshots


def _session_factory(db_session):
    return lambda: db_session


class _NoCloseSession:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def test_backfill_fills_latest_completed_correction_job_only(db_session):
    user = User(username="backfillop", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="f.mp3", status="completed",
        full_text="raw", corrected_text="the final corrected text",
    )
    db_session.add(t)
    db_session.commit()

    older = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction", status="completed", provider="groq", model="m1")
    newer = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction", status="completed", provider="groq", model="m2")
    db_session.add_all([older, newer])
    db_session.commit()

    backfill_llm_job_result_snapshots(lambda: _NoCloseSession(db_session))

    db_session.refresh(older)
    db_session.refresh(newer)
    assert older.result_json is None  # superseded run — no snapshot ever existed for it
    assert newer.result_json == {"corrected_text": "the final corrected text"}


def test_backfill_is_idempotent(db_session):
    user = User(username="backfillop2", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="completed", corrected_text="text")
    db_session.add(t)
    db_session.commit()
    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction", status="completed", provider="groq", model="m1")
    db_session.add(job)
    db_session.commit()

    backfill_llm_job_result_snapshots(lambda: _NoCloseSession(db_session))
    db_session.refresh(job)
    assert job.result_json == {"corrected_text": "text"}

    # Second run must not error and must not touch already-backfilled rows.
    backfill_llm_job_result_snapshots(lambda: _NoCloseSession(db_session))
    db_session.refresh(job)
    assert job.result_json == {"corrected_text": "text"}
