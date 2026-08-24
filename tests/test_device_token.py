from sqlalchemy import inspect

from database import User
from services.auth import (
    create_user, set_device_token, revoke_device_token, get_user_by_device_token,
    _hash_token,
)


def test_users_table_has_device_token_columns(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {c["name"] for c in inspector.get_columns("users")}
    assert "local_device_token_hash" in columns
    assert "local_device_token_created_at" in columns


def test_set_device_token_stores_hash_not_plaintext(db_session):
    user = create_user(db_session, "devtok_user", "pass1234")
    token = set_device_token(db_session, user)
    assert user.local_device_token_hash == _hash_token(token)
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


class TestDeviceTokenSettingsRoutes:
    def test_generate_returns_plaintext_once(self, client, db_session):
        resp = client.post("/api/settings/device-token")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["token"]) == 64  # secrets.token_hex(32)
        assert data["created_at"] is not None

    def test_status_reflects_generated_state(self, client, db_session):
        status_before = client.get("/api/settings/device-token").json()
        assert status_before["has_token"] is False
        client.post("/api/settings/device-token")
        status_after = client.get("/api/settings/device-token").json()
        assert status_after["has_token"] is True
        assert status_after["created_at"] is not None

    def test_revoke_clears_state(self, client, db_session):
        client.post("/api/settings/device-token")
        resp = client.delete("/api/settings/device-token")
        assert resp.status_code == 200
        status = client.get("/api/settings/device-token").json()
        assert status["has_token"] is False

    def test_generate_requires_csrf(self, client, db_session):
        old = client.headers.pop("X-CSRF-Token", None)
        try:
            resp = client.post("/api/settings/device-token")
            assert resp.status_code == 403
        finally:
            if old:
                client.headers["X-CSRF-Token"] = old

    def test_regenerate_invalidates_previous_token(self, client, db_session):
        first = client.post("/api/settings/device-token").json()["token"]
        second = client.post("/api/settings/device-token").json()["token"]
        assert first != second
        from services.auth import get_user_by_device_token
        assert get_user_by_device_token(db_session, first) is None
        assert get_user_by_device_token(db_session, second) is not None
