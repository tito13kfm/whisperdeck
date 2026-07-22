"""Relabel history: record helper, pruning, undo endpoint."""
import pytest

from database import RelabelHistory, Transcript, User


def _test_user(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _transcript(db_session, **overrides):
    user = _test_user(db_session)
    fields = dict(
        user_id=user.id, title="mtg", filename="mtg.mp3", status="completed",
        full_text="hello there general",
        segments=[
            {"start": 0.0, "end": 2.0, "text": "hello there", "speaker": "SPEAKER_00"},
            {"start": 2.0, "end": 4.0, "text": "general kenobi", "speaker": "SPEAKER_01"},
            {"start": 4.0, "end": 6.0, "text": "you are bold", "speaker": "SPEAKER_00"},
        ],
    )
    fields.update(overrides)
    t = Transcript(**fields)
    db_session.add(t)
    db_session.commit()
    return t


def test_record_relabel_stores_inverse_and_prunes(client, db_session):
    # `client` is unused directly but its fixture setup registers "testuser"
    # via /api/register (see conftest.py) -- db_session alone never creates
    # any user, so _test_user() would return None without it.
    from services.relabel import record_relabel, MAX_HISTORY
    t = _transcript(db_session)
    for n in range(MAX_HISTORY + 5):
        record_relabel(db_session, t, "retag", [(0, f"OLD_{n}")], description=f"run {n}")
        db_session.commit()
    rows = (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == t.id)
            .order_by(RelabelHistory.id).all())
    assert len(rows) == MAX_HISTORY
    assert rows[-1].description == f"run {MAX_HISTORY + 4}"
    assert rows[-1].inverse["segments"] == [{"index": 0, "speaker": f"OLD_{MAX_HISTORY + 4}"}]
