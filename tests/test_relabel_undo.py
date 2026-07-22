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


def test_rename_then_undo_restores_segments_and_corrected_text(client, db_session):
    t = _transcript(db_session, corrected_text="SPEAKER_00: hello there\n\nSPEAKER_01: general kenobi")
    r = client.post(f"/api/transcripts/{t.id}/speakers/rename",
                    json={"from": "SPEAKER_00", "to": "Alice"})
    assert r.status_code == 200

    r = client.post(f"/api/transcripts/{t.id}/relabel-undo")
    assert r.status_code == 200
    assert r.json()["undone"] == "rename"

    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert [s["speaker"] for s in t2.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert t2.corrected_text.startswith("SPEAKER_00: hello there")


def test_retag_then_undo(client, db_session):
    t = _transcript(db_session)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0, 2], "speaker": "Bob"})
    assert r.status_code == 200
    r = client.post(f"/api/transcripts/{t.id}/relabel-undo")
    assert r.status_code == 200
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert [s["speaker"] for s in t2.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


def test_undo_with_no_history_is_404(client, db_session):
    t = _transcript(db_session)
    assert client.post(f"/api/transcripts/{t.id}/relabel-undo").status_code == 404


def test_two_undos_walk_back_two_actions(client, db_session):
    t = _transcript(db_session)
    client.post(f"/api/transcripts/{t.id}/segments/retag", json={"indices": [0], "speaker": "A"})
    client.post(f"/api/transcripts/{t.id}/segments/retag", json={"indices": [0], "speaker": "B"})
    client.post(f"/api/transcripts/{t.id}/relabel-undo")
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t2.segments[0]["speaker"] == "A"
    client.post(f"/api/transcripts/{t.id}/relabel-undo")
    db_session.expire_all()
    t3 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t3.segments[0]["speaker"] == "SPEAKER_00"
