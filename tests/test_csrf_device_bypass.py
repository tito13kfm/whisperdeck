"""Regression for #303: cookie-authenticated POST /api/transcribe must
still require CSRF even when an Authorization: Bearer header is present."""

import io

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.auth import set_device_token


def _fresh_client(db_session):
    """Helper local to this file to build a TestClient without conftest's authenticated fixture."""
    def _override_get_db():
        yield db_session

    app_module.app.dependency_overrides[app_module.get_db] = _override_get_db
    app_module.rate_limiter._buckets.clear()
    client = TestClient(app_module.app)
    # Do NOT auto-register; tests that need auth will register/capture state explicitly.
    return client


def test_cookie_plus_bearer_without_csrf_is_rejected(client, db_session):
    """The exact bug: cookie auth + any Bearer header previously skipped CSRF entirely."""
    # `client` is the conftest-authenticated session (has csrf_token + user_id).
    csrf = client.headers.get("X-CSRF-Token")
    assert csrf

    # With valid CSRF, even cookie+bearer should succeed past the CSRF gate if file is valid
    # (but we test gate separately: without CSRF, cookie+bearer must 403)
    bare = TestClient(app_module.app)
    # Re-wire bare to same DB/user context but share cookies? Use the same client with bearer+no-CSRF.
    # Simpler: use the authenticated client, drop CSRF header, add Bearer.
    old = client.headers.pop("X-CSRF-Token", None)
    try:
        resp = client.post(
            "/api/transcribe",
            headers={"Authorization": "Bearer totally-bogus-token"},
            files={"file": ("a.wav", io.BytesIO(b"fake audio"), "audio/wav")},
            data={"provider": "moonshine", "language": "en", "kind": "meeting"},
        )
        assert resp.status_code == 403
        assert "CSRF" in resp.json().get("detail", "")
    finally:
        if old is not None:
            client.headers["X-CSRF-Token"] = old


def test_cookie_plus_bearer_with_csrf_passes_gate(client, db_session):
    """When both cookie and bearer are present but CSRF is valid, the CSRF
    gate passes (downstream auth still decides who is authenticated; may 200/400 not 403)."""
    # Use valid CSRF + valid device token to ensure the gate is not 403.
    # Generate a device token for this user.
    user = db_session.query(app_module.User).filter(app_module.User.username == "testuser").first()
    assert user is not None
    token = set_device_token(db_session, user)

    # Keep CSRF header (conftest sets it); attach bearer as well.
    resp = client.post(
        "/api/transcribe",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("a.wav", io.BytesIO(b"fake audio"), "audio/wav")},
        data={"provider": "moonshine", "language": "en", "kind": "meeting"},
    )
    # CSRF gate passed, so status must NOT be 403 CSRF. Accept 200/400/422 from downstream validation.
    assert resp.status_code != 403 or "CSRF" not in resp.json().get("detail", ""), resp.text


def test_pure_bearer_without_csrf_passes_csrf_gate(db_session):
    """A pure device caller (no session cookie, no CSRF) must skip CSRF.
    Downstream auth decides; gate itself must not 403 on CSRF."""
    # Build a no-cookie client and mint a device token for a registered user.
    # Register a user directly via DB to get a token without a cookie pair.
    from services.auth import create_user

    user2 = create_user(db_session, "deviceonly", "devicepass123")
    token = set_device_token(db_session, user2)

    fresh = _fresh_client(db_session)
    try:
        resp = fresh.post(
            "/api/transcribe",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("a.wav", io.BytesIO(b"fake audio"), "audio/wav")},
            data={"provider": "moonshine", "language": "en", "kind": "meeting"},
        )
        # CSRF gate must not reject pure bearer; allowed statuses are 200/400/422 etc, not CSRF 403.
        detail = resp.json().get("detail", "") if resp.headers.get("content-type", "").startswith("application/json") else ""
        if resp.status_code == 403:
            assert "CSRF" not in detail, resp.text
        else:
            assert resp.status_code in (200, 400, 422, 500), resp.text
    finally:
        app_module.app.dependency_overrides.clear()


def test_cookie_without_bearer_without_csrf_still_rejected(client):
    """Baseline: cookie auth without bearer and without CSRF still 403s (existing behavior)."""
    old = client.headers.pop("X-CSRF-Token", None)
    try:
        resp = client.post(
            "/api/transcribe",
            files={"file": ("a.wav", io.BytesIO(b"fake audio"), "audio/wav")},
            data={"provider": "moonshine", "language": "en", "kind": "meeting"},
        )
        assert resp.status_code == 403
        assert "CSRF" in resp.json().get("detail", "")
    finally:
        if old is not None:
            client.headers["X-CSRF-Token"] = old


def test_anonymous_session_bearer_with_cookie_still_needs_csrf(db_session):
    """An anonymous session (only csrf_token, no user_id) that also sends a
    Bearer header must still present CSRF — same rule as for authenticated sessions."""
    fresh = _fresh_client(db_session)
    try:
        # Establish an anonymous session (fetching csrf creates a session cookie).
        anon_csrf = fresh.get("/api/csrf-token").json()["token"]
        # Now POST to transcribe with a bearer header but without X-CSRF-Token.
        resp = fresh.post(
            "/api/transcribe",
            headers={"Authorization": "Bearer any-token"},
            files={"file": ("a.wav", io.BytesIO(b"fake audio"), "audio/wav")},
            data={"provider": "moonshine", "language": "en", "kind": "meeting"},
        )
        # Must be CSRF 403 even though Bearer header present, because session is non-empty.
        assert resp.status_code == 403
        assert "CSRF" in resp.json().get("detail", "")

        # With the correct CSRF, the CSRF gate passes (user will still 401 as anonymous).
        resp2 = fresh.post(
            "/api/transcribe",
            headers={"Authorization": "Bearer any-token", "X-CSRF-Token": anon_csrf},
            files={"file": ("b.wav", io.BytesIO(b"fake audio"), "audio/wav")},
            data={"provider": "moonshine", "language": "en", "kind": "meeting"},
        )
        if resp2.status_code == 403:
            assert "CSRF" not in resp2.json().get("detail", ""), resp2.text
    finally:
        app_module.app.dependency_overrides.clear()
