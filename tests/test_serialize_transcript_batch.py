"""Batch latest-job lookup used by `_serialize_transcript` to collapse
N×K individual `latest_job()` calls (issue #147) into one query."""
from database import LlmJob, Transcript, User, ProviderConfig
from app import _batch_latest_jobs
from services.llm_jobs import enqueue_llm_job


def _make_user_and_transcript(db_session, username="batchop"):
    user = User(username=username, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="t.mp3", status="completed",
        full_text="raw", segments=[],
    )
    db_session.add(t)
    db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="fake"))
    db_session.commit()
    return user, t


def test_batch_latest_jobs_empty_input_returns_empty(db_session):
    assert _batch_latest_jobs(db_session, []) == {}


def test_batch_latest_jobs_returns_latest_per_kind(db_session):
    user, t = _make_user_and_transcript(db_session)
    # Two rows for the same (transcript, kind). enqueue_llm_job dedupes
    # active jobs, so the first one must be terminal before the second
    # enqueue creates a fresh row.
    older = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m-old")
    older.status = "completed"
    db_session.commit()
    newer = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m-new")
    assert newer.id > older.id
    enqueue_llm_job(db_session, user.id, t.id, "summary", "groq", "s1")

    out = _batch_latest_jobs(db_session, [t.id])

    assert set(out.keys()) == {(t.id, "correction"), (t.id, "summary")}
    assert out[(t.id, "correction")].id == newer.id
    assert out[(t.id, "correction")].model == "m-new"
    assert out[(t.id, "summary")].model == "s1"


def test_batch_latest_jobs_groups_by_transcript(db_session):
    user, t1 = _make_user_and_transcript(db_session, username="batchop-multi1")
    t2 = Transcript(
        user_id=user.id, title="t2", filename="t2.mp3", status="completed",
        full_text="raw2", segments=[],
    )
    db_session.add(t2)
    db_session.commit()
    enqueue_llm_job(db_session, user.id, t1.id, "correction", "groq", "a")
    enqueue_llm_job(db_session, user.id, t2.id, "correction", "groq", "b")

    out = _batch_latest_jobs(db_session, [t1.id, t2.id])

    assert out[(t1.id, "correction")].model == "a"
    assert out[(t2.id, "correction")].model == "b"


def test_batch_latest_jobs_ignores_kinds_outside_serialized_set(db_session):
    """`rediarize` is in VALID_KINDS but not consumed by the serializer, so
    the batch filter excludes it. A rediarize row must not appear in the
    result map (the serializer wouldn't read it)."""
    user, t = _make_user_and_transcript(db_session, username="batchop-red")
    enqueue_llm_job(db_session, user.id, t.id, "rediarize", "groq", "r1")
    enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "c1")

    out = _batch_latest_jobs(db_session, [t.id])

    assert (t.id, "rediarize") not in out
    assert (t.id, "correction") in out

