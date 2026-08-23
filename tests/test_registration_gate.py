"""Registration gate (issue #395) and first-admin election (issue #301).

Modes: 'open' | 'invite' | 'closed'. Zero users always means 'open' (the
first registration must be possible); once any user exists the default is
'invite' and REGISTRATION_MODE overrides. The suite-wide conftest sets
REGISTRATION_MODE=open so the rest of the tests keep registering tokenless
second users; every test here monkeypatches the env explicitly.

Mutation checks: test_default_invite_rejects_tokenless_register fails if
the register-route gate is deleted (registration would succeed);
test_invite_happy_path_consumes_token fails if consume_invite_token's body
is replaced with `return True` (used_at stays NULL); the two concurrency
tests fail against a count()-based create_user / a non-CAS consume.
"""
import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

import app as app_module
from database import InviteToken, User, init_db
from services.auth import (
    create_user,
    generate_invite_token,
    _hash_token,
    registration_mode,
    utcnow,
)
from services.security import rate_limiter


def _fresh_client():
    """Unauthenticated client with a CSRF token attached (same pattern as
    tests/test_auth_admin.py). Inherits whatever get_db override is active —
    use alongside `client` or `gated_db`, never bare (a bare TestClient hits
    the app's own default database)."""
    fresh = TestClient(app_module.app)
    fresh.headers["X-CSRF-Token"] = fresh.get("/api/csrf-token").json()["token"]
    return fresh


@pytest.fixture
def gated_db(db_session):
    """get_db override for tests that need an anonymous client over the
    per-test DB *without* the conftest `client` fixture (which would
    pre-register testuser and destroy the zero-users state)."""
    def _override():
        yield db_session
    app_module.app.dependency_overrides[app_module.get_db] = _override
    yield db_session
    app_module.app.dependency_overrides.clear()


def _register(client_, username, password="valid1234", invite_token=None):
    payload = {"username": username, "password": password}
    if invite_token is not None:
        payload["invite_token"] = invite_token
    return client_.post("/api/register", json=payload)


# ── registration_mode() resolution ─────────────────────────────────────────


class TestModeResolution:
    def test_zero_users_is_always_open(self, db_session, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "closed")
        assert registration_mode(db_session) == "open"
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        assert registration_mode(db_session) == "open"

    def test_default_is_invite_once_user_exists(self, db_session, monkeypatch):
        create_user(db_session, "someone", "valid1234")
        monkeypatch.delenv("REGISTRATION_MODE", raising=False)
        assert registration_mode(db_session) == "invite"

    def test_env_overrides(self, db_session, monkeypatch):
        create_user(db_session, "someone", "valid1234")
        for mode in ("open", "invite", "closed"):
            monkeypatch.setenv("REGISTRATION_MODE", mode)
            assert registration_mode(db_session) == mode

    def test_invalid_env_falls_back_to_invite(self, db_session, monkeypatch):
        create_user(db_session, "someone", "valid1234")
        monkeypatch.setenv("REGISTRATION_MODE", "banana")
        assert registration_mode(db_session) == "invite"


# ── the /api/register gate ─────────────────────────────────────────────────


class TestRegisterGate:
    def test_zero_users_register_succeeds_even_env_closed(self, gated_db, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "closed")
        resp = _register(_fresh_client(), "firstuser")
        assert resp.status_code == 200

    def test_default_invite_rejects_tokenless_register(self, client, monkeypatch):
        # `client` registered testuser, so a user exists; unset env = default.
        monkeypatch.delenv("REGISTRATION_MODE", raising=False)
        resp = _register(_fresh_client(), "stranger")
        assert resp.status_code == 400
        assert "invite token is required" in resp.json()["detail"]

    def test_closed_rejects_even_valid_invite(self, client, db_session, monkeypatch):
        admin = db_session.query(User).filter(User.username == "testuser").first()
        token, _ = generate_invite_token(db_session, admin)
        monkeypatch.setenv("REGISTRATION_MODE", "closed")
        resp = _register(_fresh_client(), "stranger", invite_token=token)
        assert resp.status_code == 403
        assert "closed" in resp.json()["detail"].lower()

    def test_open_ignores_supplied_token(self, client, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "open")
        resp = _register(_fresh_client(), "walkin", invite_token="garbage")
        assert resp.status_code == 200

    def test_invite_happy_path_consumes_token(self, client, db_session, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        admin = db_session.query(User).filter(User.username == "testuser").first()
        token, expires_at = generate_invite_token(db_session, admin)
        assert expires_at > utcnow()
        resp = _register(_fresh_client(), "invited")
        assert resp.status_code == 400  # tokenless still rejected
        resp = _register(_fresh_client(), "invited", invite_token=token)
        assert resp.status_code == 200
        db_session.expire_all()
        row = db_session.query(InviteToken).filter(
            InviteToken.token_hash == _hash_token(token)
        ).first()
        new_user = db_session.query(User).filter(User.username == "invited").first()
        assert row.used_at is not None
        assert row.used_by == new_user.id

    def test_invite_single_use(self, client, db_session, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        admin = db_session.query(User).filter(User.username == "testuser").first()
        token, _ = generate_invite_token(db_session, admin)
        assert _register(_fresh_client(), "first", invite_token=token).status_code == 200
        resp = _register(_fresh_client(), "second", invite_token=token)
        assert resp.status_code == 400
        assert "Invalid or expired invite token" in resp.json()["detail"]

    def test_invite_expired(self, client, db_session, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        admin = db_session.query(User).filter(User.username == "testuser").first()
        token, _ = generate_invite_token(db_session, admin)
        row = db_session.query(InviteToken).filter(
            InviteToken.token_hash == _hash_token(token)
        ).first()
        row.expires_at = utcnow() - datetime.timedelta(hours=1)
        db_session.commit()
        resp = _register(_fresh_client(), "latecomer", invite_token=token)
        assert resp.status_code == 400
        assert "Invalid or expired invite token" in resp.json()["detail"]

    def test_invite_not_burned_by_username_collision(self, client, db_session, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        admin = db_session.query(User).filter(User.username == "testuser").first()
        token, _ = generate_invite_token(db_session, admin)
        # 'testuser' is taken; the 400 must not consume the token.
        resp = _register(_fresh_client(), "testuser", invite_token=token)
        assert resp.status_code == 400
        assert "Username already taken" in resp.json()["detail"]
        db_session.expire_all()
        row = db_session.query(InviteToken).filter(
            InviteToken.token_hash == _hash_token(token)
        ).first()
        assert row.used_at is None
        # And a weak password mustn't either (policy check precedes consume).
        resp = _register(_fresh_client(), "weakling", password="short", invite_token=token)
        assert resp.status_code == 400
        db_session.expire_all()
        assert row.used_at is None
        # Token still works for a clean registration.
        assert _register(_fresh_client(), "cleanuser", invite_token=token).status_code == 200

    def test_bad_token_reported_before_password_policy(self, client, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        resp = _register(_fresh_client(), "someone", password="short", invite_token="wrong")
        assert resp.status_code == 400
        assert "Invalid or expired invite token" in resp.json()["detail"]

    def test_token_hashed_at_rest(self, client, db_session):
        admin = db_session.query(User).filter(User.username == "testuser").first()
        token, _ = generate_invite_token(db_session, admin)
        row = db_session.query(InviteToken).first()
        assert row.token_hash != token
        assert row.token_hash == _hash_token(token)


# ── mint endpoint ───────────────────────────────────────────────────────────


class TestMintEndpoint:
    def test_mint_requires_admin(self, client, db_session, monkeypatch):
        monkeypatch.setenv("REGISTRATION_MODE", "open")
        # Second, non-admin user on a fresh client.
        fresh = _fresh_client()
        assert _register(fresh, "peon").status_code == 200
        fresh.headers["X-CSRF-Token"] = fresh.get("/api/csrf-token").json()["token"]
        resp = fresh.post("/api/admin/invites", json={})
        assert resp.status_code == 403

    def test_admin_mint_returns_token_once(self, client):
        # conftest's testuser is the first user and therefore admin (#301
        # election by id == 1).
        resp = client.post("/api/admin/invites", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["invite_token"]) == 64  # hex of 32 bytes
        expires = datetime.datetime.fromisoformat(body["expires_at"])
        assert expires > utcnow()


# ── bootstrap flag ──────────────────────────────────────────────────────────


class TestBootstrapFlag:
    def test_anonymous_bootstrap_reports_mode(self, gated_db, monkeypatch):
        monkeypatch.delenv("REGISTRATION_MODE", raising=False)
        fresh = _fresh_client()
        assert fresh.get("/api/bootstrap").json()["registration_mode"] == "open"  # zero users
        create_user(gated_db, "someone", "valid1234")
        assert fresh.get("/api/bootstrap").json()["registration_mode"] == "invite"
        monkeypatch.setenv("REGISTRATION_MODE", "closed")
        assert fresh.get("/api/bootstrap").json()["registration_mode"] == "closed"


# ── issue #301: first-admin election ────────────────────────────────────────


class TestFirstAdminElection:
    def test_first_user_is_admin_second_is_not(self, db_session):
        first = create_user(db_session, "alpha", "valid1234")
        second = create_user(db_session, "beta", "valid1234")
        assert first.id == 1 and first.is_admin
        assert not second.is_admin

    def test_concurrent_first_registrations_single_admin(self, tmp_path, monkeypatch):
        """N concurrent first-ever registrations with distinct usernames:
        all succeed, exactly one admin, and it is id 1."""
        monkeypatch.setenv("REGISTRATION_MODE", "open")
        db_path = tmp_path / "adminrace.db"
        engine, SessionLocal, _ = init_db(str(db_path))

        def _override_get_db():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app_module.app.dependency_overrides[app_module.get_db] = _override_get_db
        rate_limiter._buckets.clear()
        try:
            N = 5  # fits the register:{ip} 5/300s bucket
            barrier = Barrier(N)

            def _attempt(i):
                c = TestClient(app_module.app)
                tok = c.get("/api/csrf-token").json()["token"]
                barrier.wait(timeout=10)
                return c.post(
                    "/api/register",
                    json={"username": f"racer{i}", "password": "valid1234"},
                    headers={"X-CSRF-Token": tok},
                )

            with ThreadPoolExecutor(max_workers=N) as pool:
                results = [f.result(timeout=30) for f in
                           [pool.submit(_attempt, i) for i in range(N)]]

            statuses = [r.status_code for r in results]
            assert statuses == [200] * N, f"non-200s in first-admin race: {statuses}"

            db = SessionLocal()
            try:
                admins = db.query(User).filter(User.is_admin.is_(True)).all()
                assert len(admins) == 1, (
                    f"expected exactly one admin, got "
                    f"{[(u.id, u.username) for u in admins]}"
                )
                assert admins[0].id == 1
            finally:
                db.close()
        finally:
            app_module.app.dependency_overrides.clear()
            engine.dispose()


# ── invite-token consumption race ───────────────────────────────────────────


class TestInviteRace:
    def test_concurrent_registers_same_token_one_winner(self, tmp_path, monkeypatch):
        """N concurrent registrations sharing one invite token, distinct
        usernames: exactly one 200, the rest 400, no 500s."""
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        db_path = tmp_path / "inviterace.db"
        engine, SessionLocal, _ = init_db(str(db_path))

        setup_db = SessionLocal()
        try:
            admin = create_user(setup_db, "adminuser", "valid1234")
            token, _ = generate_invite_token(setup_db, admin)
        finally:
            setup_db.close()

        def _override_get_db():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app_module.app.dependency_overrides[app_module.get_db] = _override_get_db
        rate_limiter._buckets.clear()
        try:
            N = 4  # + the admin setup register stays under 5/300s... admin was created via create_user (no HTTP), so 4 is headroom
            barrier = Barrier(N)

            def _attempt(i):
                c = TestClient(app_module.app)
                tok = c.get("/api/csrf-token").json()["token"]
                barrier.wait(timeout=10)
                return c.post(
                    "/api/register",
                    json={"username": f"guest{i}", "password": "valid1234",
                          "invite_token": token},
                    headers={"X-CSRF-Token": tok},
                )

            with ThreadPoolExecutor(max_workers=N) as pool:
                results = [f.result(timeout=30) for f in
                           [pool.submit(_attempt, i) for i in range(N)]]

            statuses = sorted(r.status_code for r in results)
            assert 500 not in statuses, f"500s in invite race: {statuses}"
            assert statuses.count(200) == 1, (
                f"expected exactly one winner for a single-use token, "
                f"statuses: {statuses}"
            )
            assert statuses.count(400) == N - 1, f"statuses: {statuses}"

            db = SessionLocal()
            try:
                registered = db.query(User).filter(User.username.like("guest%")).count()
                assert registered == 1
            finally:
                db.close()
        finally:
            app_module.app.dependency_overrides.clear()
            engine.dispose()
