"""Tests for the 6 new auth/admin endpoints added in the account-recovery PR:
forgot-username, forgot-password, reset-password, admin/users, admin/promote,
admin/demote. Also covers reset-token hashing at rest and CSRF enforcement."""
import datetime

import pytest

from database import User
from services.auth import hash_reset_token


# ── Helpers ────────────────────────────────────────────────────────────────


def _fresh_client():
    """A new TestClient with no auth, but with a CSRF token already fetched
    and attached — every /api/* mutation requires one, including
    /api/register and /api/login themselves (issue #36)."""
    from fastapi.testclient import TestClient
    import app as app_module
    fresh = TestClient(app_module.app)
    csrf_token = fresh.get("/api/csrf-token").json()["token"]
    fresh.headers["X-CSRF-Token"] = csrf_token
    return fresh


def _register(client, username="other", password="pass123"):
    """Register a second user (the conftest client already has 'testuser')."""
    # Need a fresh client without auth to register a new user
    fresh = _fresh_client()
    resp = fresh.post("/api/register", json={"username": username, "password": password})
    return resp


def _make_admin(db_session, username="testuser"):
    """Promote the test user to admin directly via the DB."""
    user = db_session.query(User).filter(User.username == username).first()
    user.is_admin = True
    db_session.commit()
    return user


# ── forgot-username ───────────────────────────────────────────────────────


class TestForgotUsername:
    def test_returns_all_usernames(self, client, db_session):
        _register(client, "alice", "pass1")
        _register(client, "bob", "pass2")
        resp = client.post("/api/forgot-username")
        assert resp.status_code == 200
        names = resp.json()["usernames"]
        assert "testuser" in names
        assert "alice" in names
        assert "bob" in names

    def test_empty_db_returns_empty(self, client, db_session):
        # testuser is always registered by conftest
        resp = client.post("/api/forgot-username")
        assert resp.status_code == 200
        assert "testuser" in resp.json()["usernames"]


# ── forgot-password ───────────────────────────────────────────────────────


class TestForgotPassword:
    def test_admin_can_generate_token(self, client, db_session):
        _make_admin(db_session)
        _register(client, "target_user", "oldpass")
        resp = client.post("/api/forgot-password", json={"username": "target_user"})
        assert resp.status_code == 200
        data = resp.json()
        assert "reset_token" in data
        assert data["expires_at"] is not None

    def test_non_admin_rejected(self, client, db_session):
        # testuser is auto-admin (first user). Create a second user who is NOT
        # admin, log in as them, and verify the admin-only gate.
        _register(client, "nonadmin", "pass")
        nonadmin_client = _fresh_client()
        nonadmin_client.post("/api/login", json={"username": "nonadmin", "password": "pass"})
        resp = nonadmin_client.post("/api/forgot-password", json={"username": "testuser"})
        assert resp.status_code == 403

    def test_missing_username_field(self, client, db_session):
        _make_admin(db_session)
        resp = client.post("/api/forgot-password", json={})
        assert resp.status_code == 400

    def test_unknown_user_returns_404(self, client, db_session):
        _make_admin(db_session)
        resp = client.post("/api/forgot-password", json={"username": "nobody"})
        assert resp.status_code == 404

    def test_token_stored_hashed_not_plaintext(self, client, db_session):
        """The DB must contain the SHA-256 hash, not the raw token."""
        _make_admin(db_session)
        _register(client, "hashcheck", "pass")
        resp = client.post("/api/forgot-password", json={"username": "hashcheck"})
        plaintext = resp.json()["reset_token"]
        user = db_session.query(User).filter(User.username == "hashcheck").first()
        assert user.reset_token == hash_reset_token(plaintext)
        assert user.reset_token != plaintext

    def test_csrf_required(self, client, db_session):
        _make_admin(db_session)
        # Remove the CSRF header that conftest sets
        old = client.headers.pop("X-CSRF-Token", None)
        try:
            resp = client.post("/api/forgot-password", json={"username": "testuser"})
            assert resp.status_code == 403
        finally:
            if old:
                client.headers["X-CSRF-Token"] = old


# ── reset-password ────────────────────────────────────────────────────────


class TestResetPassword:
    def _get_token(self, client, db_session, target="target_user"):
        _make_admin(db_session)
        _register(client, target, "oldpass")
        resp = client.post("/api/forgot-password", json={"username": target})
        return resp.json()["reset_token"]

    def test_valid_token_resets_password(self, client, db_session):
        token = self._get_token(client, db_session)
        resp = client.post("/api/reset-password", json={
            "token": token, "new_password": "newpass123"
        })
        assert resp.status_code == 200
        assert resp.json()["username"] == "target_user"
        # Verify login with new password works
        login = client.post("/api/login", json={"username": "target_user", "password": "newpass123"})
        assert login.status_code == 200

    def test_invalid_token_rejected(self, client, db_session):
        resp = client.post("/api/reset-password", json={
            "token": "deadbeef" * 8, "new_password": "newpass"
        })
        assert resp.status_code == 400

    def test_token_single_use(self, client, db_session):
        token = self._get_token(client, db_session)
        # First use succeeds
        resp = client.post("/api/reset-password", json={
            "token": token, "new_password": "newpass1"
        })
        assert resp.status_code == 200
        # Second use fails (token cleared)
        resp2 = client.post("/api/reset-password", json={
            "token": token, "new_password": "newpass2"
        })
        assert resp2.status_code == 400

    def test_expired_token_rejected(self, client, db_session):
        token = self._get_token(client, db_session, target="expired_user")
        # Manually expire the token
        user = db_session.query(User).filter(User.username == "expired_user").first()
        user.reset_token_expires_at = datetime.datetime(2020, 1, 1)
        db_session.commit()
        resp = client.post("/api/reset-password", json={
            "token": token, "new_password": "newpass"
        })
        assert resp.status_code == 400

    def test_missing_fields(self, client, db_session):
        resp = client.post("/api/reset-password", json={"token": ""})
        assert resp.status_code == 400

    def test_csrf_required(self, client, db_session):
        token = self._get_token(client, db_session, target="csrf_user")
        old = client.headers.pop("X-CSRF-Token", None)
        try:
            resp = client.post("/api/reset-password", json={
                "token": token, "new_password": "newpass"
            })
            assert resp.status_code == 403
        finally:
            if old:
                client.headers["X-CSRF-Token"] = old


# ── admin/users ───────────────────────────────────────────────────────────


class TestAdminUsers:
    def test_admin_can_list_users(self, client, db_session):
        _make_admin(db_session)
        resp = client.get("/api/admin/users")
        assert resp.status_code == 200
        users = resp.json()
        assert len(users) >= 1
        assert any(u["username"] == "testuser" for u in users)

    def test_non_admin_rejected(self, client, db_session):
        _register(client, "nonadmin_users", "pass")
        nonadmin_client = _fresh_client()
        nonadmin_client.post("/api/login", json={"username": "nonadmin_users", "password": "pass"})
        resp = nonadmin_client.get("/api/admin/users")
        assert resp.status_code == 403


# ── admin/promote ─────────────────────────────────────────────────────────


class TestAdminPromote:
    def test_promote_user(self, client, db_session):
        _make_admin(db_session)
        _register(client, "promotee", "pass")
        resp = client.post("/api/admin/promote", json={"username": "promotee"})
        assert resp.status_code == 200
        assert resp.json()["is_admin"] is True
        # Verify in DB
        user = db_session.query(User).filter(User.username == "promotee").first()
        assert user.is_admin is True

    def test_non_admin_rejected(self, client, db_session):
        _register(client, "nonadmin_promote", "pass")
        nonadmin_client = _fresh_client()
        nonadmin_client.post("/api/login", json={"username": "nonadmin_promote", "password": "pass"})
        resp = nonadmin_client.post("/api/admin/promote", json={"username": "testuser"})
        assert resp.status_code == 403

    def test_unknown_user_returns_404(self, client, db_session):
        _make_admin(db_session)
        resp = client.post("/api/admin/promote", json={"username": "nobody"})
        assert resp.status_code == 404

    def test_missing_username(self, client, db_session):
        _make_admin(db_session)
        resp = client.post("/api/admin/promote", json={})
        assert resp.status_code == 400

    def test_csrf_required(self, client, db_session):
        _make_admin(db_session)
        old = client.headers.pop("X-CSRF-Token", None)
        try:
            resp = client.post("/api/admin/promote", json={"username": "testuser"})
            assert resp.status_code == 403
        finally:
            if old:
                client.headers["X-CSRF-Token"] = old


# ── admin/demote ──────────────────────────────────────────────────────────


class TestAdminDemote:
    def test_demote_user(self, client, db_session):
        _make_admin(db_session)
        _register(client, "demotee", "pass")
        # First promote
        client.post("/api/admin/promote", json={"username": "demotee"})
        # Then demote
        resp = client.post("/api/admin/demote", json={"username": "demotee"})
        assert resp.status_code == 200
        assert resp.json()["is_admin"] is False

    def test_cannot_self_demote(self, client, db_session):
        _make_admin(db_session)
        resp = client.post("/api/admin/demote", json={"username": "testuser"})
        assert resp.status_code == 400

    def test_non_admin_rejected(self, client, db_session):
        _register(client, "nonadmin_demote", "pass")
        nonadmin_client = _fresh_client()
        nonadmin_client.post("/api/login", json={"username": "nonadmin_demote", "password": "pass"})
        resp = nonadmin_client.post("/api/admin/demote", json={"username": "testuser"})
        assert resp.status_code == 403

    def test_csrf_required(self, client, db_session):
        _make_admin(db_session)
        old = client.headers.pop("X-CSRF-Token", None)
        try:
            resp = client.post("/api/admin/demote", json={"username": "testuser"})
            assert resp.status_code == 403
        finally:
            if old:
                client.headers["X-CSRF-Token"] = old


# ── Env var isolation (regression test for the WHISPERDECK/DESK typo) ────


class TestEnvVarIsolation:
    def test_conftest_data_dir_is_used(self):
        """app.py's DATA_DIR must point at the test data dir set by conftest,
        not the production data/ folder. This is the exact bug the audit found."""
        import app as app_module
        import os
        # The conftest sets WHISPERDECK_DATA_DIR to a temp dir
        expected = os.environ.get("WHISPERDECK_DATA_DIR", "")
        assert expected, "conftest should have set WHISPERDECK_DATA_DIR"
        assert str(app_module.DATA_DIR) == expected
        # Must NOT be the production data dir — conftest uses tempfile.mkdtemp
        # which produces paths like /tmp/whisperdesk-test-XXXXXX, never ./data
        assert app_module.DATA_DIR != app_module.BASE_DIR / "data"
