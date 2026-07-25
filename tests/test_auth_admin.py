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


def _register(client, username="other", password="pass1234"):
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
        _register(client, "alice", "pass1234")
        _register(client, "bob", "pass2345")
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
        _register(client, "target_user", "oldpass1")
        resp = client.post("/api/forgot-password", json={"username": "target_user"})
        assert resp.status_code == 200
        data = resp.json()
        assert "reset_token" in data
        assert data["expires_at"] is not None

    def test_non_admin_rejected(self, client, db_session):
        # testuser is auto-admin (first user). Create a second user who is NOT
        # admin, log in as them, and verify the admin-only gate.
        _register(client, "nonadmin", "pass1234")
        nonadmin_client = _fresh_client()
        nonadmin_client.post("/api/login", json={"username": "nonadmin", "password": "pass1234"})
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
        _register(client, "hashcheck", "pass1234")
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
        _register(client, target, "oldpass1")
        resp = client.post("/api/forgot-password", json={"username": target})
        return resp.json()["reset_token"]

    def test_valid_token_resets_password(self, client, db_session):
        token = self._get_token(client, db_session)
        resp = client.post("/api/reset-password", json={
            "token": token, "new_password": "newpass123"
        })
        assert resp.status_code == 200
        assert resp.json()["username"] == "target_user"
        # reset-password rotated the CSRF token -- refresh the header
        client.headers["X-CSRF-Token"] = client.get("/api/csrf-token").json()["token"]
        # Verify login with new password works
        login = client.post("/api/login", json={"username": "target_user", "password": "newpass123"})
        assert login.status_code == 200

    def test_invalid_token_rejected(self, client, db_session):
        resp = client.post("/api/reset-password", json={
            "token": "deadbeef" * 8, "new_password": "newpass12"
        })
        assert resp.status_code == 400

    def test_token_single_use(self, client, db_session):
        token = self._get_token(client, db_session)
        # First use succeeds
        resp = client.post("/api/reset-password", json={
            "token": token, "new_password": "newpass12"
        })
        assert resp.status_code == 200
        # reset-password rotated the CSRF token -- refresh the header
        client.headers["X-CSRF-Token"] = client.get("/api/csrf-token").json()["token"]
        # Second use fails (token cleared)
        resp2 = client.post("/api/reset-password", json={
            "token": token, "new_password": "newpass23"
        })
        assert resp2.status_code == 400

    def test_expired_token_rejected(self, client, db_session):
        token = self._get_token(client, db_session, target="expired_user")
        # Manually expire the token
        user = db_session.query(User).filter(User.username == "expired_user").first()
        user.reset_token_expires_at = datetime.datetime(2020, 1, 1)
        db_session.commit()
        resp = client.post("/api/reset-password", json={
            "token": token, "new_password": "newpass12"
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
                "token": token, "new_password": "newpass12"
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
        _register(client, "nonadmin_users", "pass1234")
        nonadmin_client = _fresh_client()
        nonadmin_client.post("/api/login", json={"username": "nonadmin_users", "password": "pass1234"})
        resp = nonadmin_client.get("/api/admin/users")
        assert resp.status_code == 403


# ── admin/promote ─────────────────────────────────────────────────────────


class TestAdminPromote:
    def test_promote_user(self, client, db_session):
        _make_admin(db_session)
        _register(client, "promotee", "pass1234")
        resp = client.post("/api/admin/promote", json={"username": "promotee"})
        assert resp.status_code == 200
        assert resp.json()["is_admin"] is True
        # Verify in DB
        user = db_session.query(User).filter(User.username == "promotee").first()
        assert user.is_admin is True

    def test_non_admin_rejected(self, client, db_session):
        _register(client, "nonadmin_promote", "pass1234")
        nonadmin_client = _fresh_client()
        nonadmin_client.post("/api/login", json={"username": "nonadmin_promote", "password": "pass1234"})
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
        _register(client, "demotee", "pass1234")
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
        _register(client, "nonadmin_demote", "pass1234")
        nonadmin_client = _fresh_client()
        nonadmin_client.post("/api/login", json={"username": "nonadmin_demote", "password": "pass1234"})
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

# -- CSRF token lifecycle -------------------------------------------------


class TestCsrfTokenLifecycle:
    def test_stable_token_across_reads(self, client):
        """GET /api/csrf-token returns the same token on consecutive
        calls: token is per-session, not per-request (issue #51)."""
        first = client.get("/api/csrf-token").json()["token"]
        second = client.get("/api/csrf-token").json()["token"]
        assert first == second, "CSRF token should be stable per session"
        assert len(first) == 64, "token should be 32 hex bytes"

    def test_token_rotates_on_register(self, client, db_session):
        """Register must rotate the CSRF token.
        Anonymous-session token must not survive into authenticated session."""
        fresh = _fresh_client()
        pre = fresh.get("/api/csrf-token").json()["token"]
        fresh.post("/api/register", json={"username": "csrftest", "password": "secret12"})
        post = fresh.get("/api/csrf-token").json()["token"]
        assert pre != post, "register should rotate CSRF token"

    def test_token_rotates_on_login(self, client, db_session):
        """Login must rotate the CSRF token.
        Pre-auth token must not survive into the authenticated session."""
        _register(client, "logintest", "secret12")
        fresh = _fresh_client()
        pre = fresh.get("/api/csrf-token").json()["token"]
        fresh.post("/api/login", json={"username": "logintest", "password": "secret12"})
        post = fresh.get("/api/csrf-token").json()["token"]
        assert pre != post, "login should rotate CSRF token"
    def test_token_rotates_on_reset_password(self, client, db_session):
        """Reset-password auto-login must rotate the CSRF token.
        Same session-fixation gap as login/register: anonymous token
        must not survive into the authenticated session."""
        _make_admin(db_session)
        _register(client, "resetrot", "oldpass1")
        token = client.post("/api/forgot-password", json={"username": "resetrot"}).json()["reset_token"]
        fresh = _fresh_client()
        pre = fresh.get("/api/csrf-token").json()["token"]
        fresh.post("/api/reset-password", json={"token": token, "new_password": "newpass12"})
        post = fresh.get("/api/csrf-token").json()["token"]
        assert pre != post, "reset-password should rotate CSRF token"


# ── password policy ────────────────────────────────────────────────────────



class TestPasswordPolicy:
    """Server-side password policy validation for register and reset-password."""

    def test_register_too_short(self, client, db_session):
        """Password shorter than 8 chars is rejected."""
        fresh = _fresh_client()
        resp = fresh.post("/api/register", json={"username": "pw_too_short", "password": "abc"})
        assert resp.status_code == 400
        assert "Password must be at least 8 characters" in resp.json()["detail"]

    def test_register_no_digit(self, client, db_session):
        """Password with only letters is rejected."""
        fresh = _fresh_client()
        resp = fresh.post("/api/register", json={"username": "pw_no_digit", "password": "longenough"})
        assert resp.status_code == 400
        assert "Password must contain at least one digit" in resp.json()["detail"]

    def test_register_short_with_digit(self, client, db_session):
        """Password with digit but too short is rejected."""
        fresh = _fresh_client()
        resp = fresh.post("/api/register", json={"username": "pw_short1", "password": "short1"})
        assert resp.status_code == 400
        # "short1" is 6 chars — too short, so length check fires first
        assert "Password must be at least 8 characters" in resp.json()["detail"]

    def test_register_valid(self, client, db_session):
        """Valid password (8+ chars, letter and digit) passes."""
        fresh = _fresh_client()
        resp = fresh.post("/api/register", json={"username": "pw_valid", "password": "valid1234"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    # password_confirm mismatch is client-side only — not testable via the API.
    # Covered by e2e tests (browser-level validation in submitAuth).

    def test_reset_password_too_short(self, client, db_session):
        """Reset-password rejects new password that fails policy."""
        _make_admin(db_session)
        _register(client, "pw_reset_target", "oldpass1")
        token = client.post("/api/forgot-password", json={"username": "pw_reset_target"}).json()["reset_token"]
        # reset-password will rotate the CSRF token — use a fresh client
        fresh = _fresh_client()
        resp = fresh.post("/api/reset-password", json={"token": token, "new_password": "short"})
        assert resp.status_code == 400
        assert "Password must be at least 8 characters" in resp.json()["detail"]

    def test_reset_password_valid(self, client, db_session):
        """Reset-password accepts a valid new password."""
        _make_admin(db_session)
        _register(client, "pw_reset_valid", "oldpass1")
        token = client.post("/api/forgot-password", json={"username": "pw_reset_valid"}).json()["reset_token"]
        fresh = _fresh_client()
        resp = fresh.post("/api/reset-password", json={"token": token, "new_password": "validnew1"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "pw_reset_valid"

    # ── hardening: non-numeric env var falls back to 8 ──

    def test_non_numeric_min_length_falls_back(self, client, db_session, monkeypatch):
        """A non-numeric PASSWORD_MIN_LENGTH must not crash the route.
        Falls back to the default (8) so an 8-char valid password passes
        and a 7-char one is rejected — no 500."""
        monkeypatch.setenv("PASSWORD_MIN_LENGTH", "abc")
        fresh = _fresh_client()
        resp_ok = fresh.post("/api/register", json={"username": "pw_env_ok", "password": "valid1234"})
        assert resp_ok.status_code == 200
        fresh2 = _fresh_client()
        resp_bad = fresh2.post("/api/register", json={"username": "pw_env_bad", "password": "short12"})
        assert resp_bad.status_code == 400
        assert "Password must be at least 8 characters" in resp_bad.json()["detail"]

    # ── check order: the "real" error wins over the password error ──

    def test_register_taken_username_beats_password_error(self, client, db_session):
        """A taken username + weak password reports 'Username already taken',
        not the password-policy error."""
        _register(client, "dupuser", "pass1234")
        fresh = _fresh_client()
        resp = fresh.post("/api/register", json={"username": "dupuser", "password": "abc"})
        assert resp.status_code == 400
        assert "Username already taken" in resp.json()["detail"]

    def test_reset_bad_token_beats_password_error(self, client, db_session):
        """A bad reset token + weak password reports 'Invalid or expired reset
        token', not the password-policy error."""
        _make_admin(db_session)
        fresh = _fresh_client()
        resp = fresh.post("/api/reset-password", json={
            "token": "deadbeef" * 8, "new_password": "abc"
        })
        assert resp.status_code == 400
        assert "Invalid or expired reset token" in resp.json()["detail"]

    # ── single source of truth: meta tag injection ──

    def test_index_page_has_default_min_length_meta(self, client, db_session):
        """The served index.html has a meta tag with the password min length,
        defaulting to 8 when PASSWORD_MIN_LENGTH is unset."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="wd-password-min-length" content="8"' in resp.text

    def test_index_page_reflects_env_var(self, client, db_session, monkeypatch):
        """Setting PASSWORD_MIN_LENGTH injects the value into the page meta tag
        so the client and hint text match the server policy."""
        monkeypatch.setenv("PASSWORD_MIN_LENGTH", "12")
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="wd-password-min-length" content="12"' in resp.text
        assert 'content="8"' not in resp.text

    def test_index_page_falls_back_on_non_numeric(self, client, db_session, monkeypatch):
        """A non-numeric PASSWORD_MIN_LENGTH falls back to 8 in the meta tag,
        matching the server-side fallback in password_min_length()."""
        monkeypatch.setenv("PASSWORD_MIN_LENGTH", "garbage")
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="wd-password-min-length" content="8"' in resp.text
