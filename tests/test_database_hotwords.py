from database import HotwordEntry, Transcript, User
from services.settings import DEFAULT_SETTINGS, get_user_settings


def test_hotword_entry_table_exists_and_scopes_to_user(db_session):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    entry = HotwordEntry(user_id=user.id, term="Groq", source="manual")
    db_session.add(entry)
    db_session.commit()

    fetched = db_session.query(HotwordEntry).filter(HotwordEntry.user_id == user.id).all()
    assert len(fetched) == 1
    assert fetched[0].term == "Groq"
    assert fetched[0].source == "manual"
    assert fetched[0].created_at is not None


def test_transcript_has_correction_columns(db_session):
    user = User(username="bob", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    t = Transcript(user_id=user.id, title="t", filename="f.mp3")
    db_session.add(t)
    db_session.commit()

    assert t.corrected_text is None
    assert t.correction_error is None
    assert t.correction_model is None

    t.corrected_text = "cleaned up text"
    t.correction_model = "groq/llama-3.3-70b-versatile"
    db_session.commit()
    db_session.refresh(t)
    assert t.corrected_text == "cleaned up text"
    assert t.correction_model == "groq/llama-3.3-70b-versatile"


def test_auto_correct_defaults_to_true(db_session):
    assert DEFAULT_SETTINGS["auto_correct"] is True

    user = User(username="carol", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    settings = get_user_settings(db_session, user.id)
    assert settings["auto_correct"] is True
