from database import User
from services.hotwords import add_hotword, delete_hotword, list_hotwords


def _make_user(db_session, username="alice"):
    user = db_session.query(User).filter(User.username == username).first()
    if user:
        return user
    user = User(username=username, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def test_add_and_list_hotwords(db_session):
    user = _make_user(db_session)
    add_hotword(db_session, user.id, "Groq")
    add_hotword(db_session, user.id, "Moonshine", source="extracted")

    entries = list_hotwords(db_session, user.id)
    assert {(e.term, e.source) for e in entries} == {("Groq", "manual"), ("Moonshine", "extracted")}


def test_add_hotword_dedups_case_insensitively(db_session):
    user = _make_user(db_session)
    first = add_hotword(db_session, user.id, "Groq")
    second = add_hotword(db_session, user.id, "groq", source="extracted")

    assert second.id == first.id
    assert len(list_hotwords(db_session, user.id)) == 1
    # original source is preserved, not overwritten by the dup attempt
    assert list_hotwords(db_session, user.id)[0].source == "manual"


def test_add_hotword_scopes_dedup_per_user(db_session):
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")
    add_hotword(db_session, alice.id, "Groq")
    add_hotword(db_session, bob.id, "Groq")

    assert len(list_hotwords(db_session, alice.id)) == 1
    assert len(list_hotwords(db_session, bob.id)) == 1


def test_delete_hotword_removes_owned_entry(db_session):
    user = _make_user(db_session)
    entry = add_hotword(db_session, user.id, "Groq")

    assert delete_hotword(db_session, user.id, entry.id) is True
    assert list_hotwords(db_session, user.id) == []


def test_delete_hotword_refuses_other_users_entry(db_session):
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")
    entry = add_hotword(db_session, alice.id, "Groq")

    assert delete_hotword(db_session, bob.id, entry.id) is False
    assert len(list_hotwords(db_session, alice.id)) == 1


def test_delete_hotword_returns_false_for_missing_id(db_session):
    user = _make_user(db_session)
    assert delete_hotword(db_session, user.id, 9999) is False


def test_add_hotword_treats_percent_and_underscore_as_literal_characters(db_session):
    user = _make_user(db_session)
    aws = add_hotword(db_session, user.id, "AWS")
    literal = add_hotword(db_session, user.id, "A%S")

    assert literal.id != aws.id
    assert {e.term for e in list_hotwords(db_session, user.id)} == {"AWS", "A%S"}
