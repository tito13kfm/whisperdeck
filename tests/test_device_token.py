from sqlalchemy import inspect

from database import User
from services.auth import (
    create_user, set_device_token, revoke_device_token, get_user_by_device_token,
    hash_device_token,
)


def test_users_table_has_device_token_columns(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {c["name"] for c in inspector.get_columns("users")}
    assert "local_device_token_hash" in columns
    assert "local_device_token_created_at" in columns


def test_set_device_token_stores_hash_not_plaintext(db_session):
    user = create_user(db_session, "devtok_user", "pass1234")
    token = set_device_token(db_session, user)
    assert user.local_device_token_hash == hash_device_token(token)
    assert user.local_device_token_hash != token
    assert user.local_device_token_created_at is not None


def test_get_user_by_device_token_finds_matching_user(db_session):
    user = create_user(db_session, "devtok_user2", "pass1234")
    token = set_device_token(db_session, user)
    found = get_user_by_device_token(db_session, token)
    assert found is not None
    assert found.id == user.id


def test_get_user_by_device_token_rejects_wrong_token(db_session):
    user = create_user(db_session, "devtok_user3", "pass1234")
    set_device_token(db_session, user)
    assert get_user_by_device_token(db_session, "wrong" * 8) is None


def test_get_user_by_device_token_rejects_empty_token(db_session):
    assert get_user_by_device_token(db_session, "") is None


def test_revoke_device_token_clears_lookup(db_session):
    user = create_user(db_session, "devtok_user4", "pass1234")
    token = set_device_token(db_session, user)
    revoke_device_token(db_session, user)
    assert user.local_device_token_hash is None
    assert get_user_by_device_token(db_session, token) is None
