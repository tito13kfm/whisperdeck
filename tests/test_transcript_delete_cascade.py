"""Issue #300: deleting a transcript must cascade to LlmJob and TranscriptTag rows.

SQLite reuses rowids. Without ORM cascade, orphaned rows reattach to a
future transcript that happens to be assigned the deleted row's id.
The FK CASCADE is inert because foreign_keys pragma is off repo-wide."""
import pytest

from database import LlmJob, Transcript, TranscriptTag, User


def _make_transcript(db_session, user_id, **kw):
    kw.setdefault("title", "t")
    kw.setdefault("filename", "t.mp3")
    kw.setdefault("status", "completed")
    t = Transcript(user_id=user_id, **kw)
    db_session.add(t)
    db_session.commit()
    return t


def test_delete_transcript_cascades_llm_jobs(db_session):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    t = _make_transcript(db_session, user.id)

    for kind in ("correction", "summary", "voice_match"):
        db_session.add(LlmJob(
            user_id=user.id, transcript_id=t.id, kind=kind,
            status="completed", provider="groq", model="llama-3.3-70b",
        ))
    db_session.add(LlmJob(
        user_id=user.id, transcript_id=None, kind="assistant",
        status="completed",
    ))
    db_session.commit()
    tid = t.id

    assert db_session.query(LlmJob).filter(LlmJob.transcript_id == tid).count() == 3

    db_session.delete(t)
    db_session.commit()

    assert db_session.query(LlmJob).filter(LlmJob.transcript_id == tid).count() == 0
    assert db_session.query(LlmJob).filter(LlmJob.kind == "assistant").count() == 1


def test_delete_transcript_cascades_transcript_tags(db_session):
    user = User(username="bob", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    t = _make_transcript(db_session, user.id)
    db_session.add_all([
        TranscriptTag(transcript_id=t.id, tag="python"),
        TranscriptTag(transcript_id=t.id, tag="meeting"),
    ])
    db_session.commit()
    tid = t.id

    assert db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == tid).count() == 2

    db_session.delete(t)
    db_session.commit()

    assert db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == tid).count() == 0


def test_delete_transcript_cascades_both_children_together(db_session):
    user = User(username="carol", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    t = _make_transcript(db_session, user.id)
    db_session.add(LlmJob(user_id=user.id, transcript_id=t.id, kind="summary", status="pending"))
    db_session.add(TranscriptTag(transcript_id=t.id, tag="alpha"))
    db_session.commit()
    tid = t.id

    t2 = _make_transcript(db_session, user.id, title="sibling", filename="s.mp3")
    db_session.add(LlmJob(user_id=user.id, transcript_id=t2.id, kind="summary", status="pending"))
    db_session.add(TranscriptTag(transcript_id=t2.id, tag="beta"))
    db_session.commit()
    t2_id = t2.id

    db_session.delete(t)
    db_session.commit()

    assert db_session.query(LlmJob).filter(LlmJob.transcript_id == tid).count() == 0
    assert db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == tid).count() == 0
    assert db_session.query(LlmJob).filter(LlmJob.transcript_id == t2_id).count() == 1
    assert db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == t2_id).count() == 1


def test_delete_transcript_without_children_succeeds(db_session):
    user = User(username="dave", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    t = _make_transcript(db_session, user.id)
    tid = t.id

    db_session.delete(t)
    db_session.commit()

    assert db_session.query(Transcript).filter(Transcript.id == tid).first() is None
