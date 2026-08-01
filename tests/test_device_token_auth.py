import io
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as app_module
from database import User
from services.auth import create_user, revoke_device_token, set_device_token


async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello")
    db.add(t)
    db.commit()
    return t


def _device_client(db_session):
    """A bare TestClient sharing the test db but carrying no cookies at
    all, simulating a headless device with no browser session."""
    def _override_get_db():
        yield db_session
    app_module.app.dependency_overrides[app_module.get_db] = _override_get_db
    return TestClient(app_module.app)


def test_valid_device_token_uploads_without_cookie_or_csrf(client, db_session):
    user = db_session.query(User).filter(User.username == "testuser").first()
    token = set_device_token(db_session, user)
    device_client = _device_client(db_session)
    with patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        resp = device_client.post(
            "/api/transcribe",
            files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
            data={"provider": "moonshine", "kind": "voice_note"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


def test_invalid_device_token_rejected(client, db_session):
    device_client = _device_client(db_session)
    resp = device_client.post(
        "/api/transcribe",
        files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
        data={"provider": "moonshine", "kind": "voice_note"},
        headers={"Authorization": "Bearer " + "wrong" * 12},
    )
    assert resp.status_code == 401


def test_neither_token_nor_cookie_rejected(client, db_session):
    """No bearer header and no session cookie means enforce_csrf's blanket
    policy (issue #36, app.py:189) rejects the request before the route's
    auth dependency ever runs -- 403 (missing CSRF token), not 401. This
    is pre-existing behavior, unrelated to the device-token change: the
    same request against the old get_current_user-only route 403s for the
    identical reason. Confirms the failure mode, not which auth path won."""
    device_client = _device_client(db_session)
    resp = device_client.post(
        "/api/transcribe",
        files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
        data={"provider": "moonshine", "kind": "voice_note"},
    )
    assert resp.status_code == 403


def test_device_token_not_honored_on_unscoped_route(client, db_session):
    """A bearer token is only meaningful on /api/transcribe. Any other
    authenticated route must still reject a bearer-only caller with no
    session, proving the token's blast radius stayed narrow."""
    user = db_session.query(User).filter(User.username == "testuser").first()
    token = set_device_token(db_session, user)
    device_client = _device_client(db_session)
    resp = device_client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_bearer_token_does_not_skip_csrf_on_other_mutating_route(client, db_session):
    """enforce_csrf's bearer-header CSRF exemption is scoped to
    /api/transcribe only (app.py enforce_csrf), the one route that
    actually honors a bearer token for auth. POST /api/hotwords is a
    real mutating route on plain get_current_user, unrelated to this
    feature: with a valid device token, no session cookie, and no
    X-CSRF-Token header, it must 403 (CSRF not exempted here) -- not 401
    (would mean CSRF was skipped and the route's own auth rejected it)
    and not 200 (would mean the token leaked into a route it shouldn't
    affect)."""
    user = db_session.query(User).filter(User.username == "testuser").first()
    token = set_device_token(db_session, user)
    device_client = _device_client(db_session)
    resp = device_client.post(
        "/api/hotwords",
        json={"term": "example"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_device_token_upload_rate_limited(client, db_session):
    from services.security import rate_limiter
    user = db_session.query(User).filter(User.username == "testuser").first()
    token = set_device_token(db_session, user)
    rate_limiter._buckets.clear()
    device_client = _device_client(db_session)
    with patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        statuses = []
        for _ in range(31):
            resp = device_client.post(
                "/api/transcribe",
                files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
                data={"provider": "moonshine", "kind": "voice_note"},
                headers={"Authorization": f"Bearer {token}"},
            )
            statuses.append(resp.status_code)
    assert statuses[-1] == 429
    assert statuses.count(200) == 30


def test_session_authenticated_upload_not_subject_to_device_rate_limit(client):
    """The 30/hour device-upload bucket must key off the actual auth
    outcome (request.state.device_authenticated), not merely the presence
    of a bearer header. A normal session-cookie upload with no bearer
    header at all must never brush against that bucket -- prove it by
    exceeding the device limit's count (31) on the session-authenticated
    `client` and confirming every single one still succeeds."""
    from services.security import rate_limiter
    rate_limiter._buckets.clear()
    with patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        statuses = []
        for _ in range(31):
            resp = client.post(
                "/api/transcribe",
                files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
                data={"provider": "moonshine", "kind": "voice_note"},
            )
            statuses.append(resp.status_code)
    assert statuses.count(200) == 31
    assert 429 not in statuses


def test_revoked_device_token_rejected_at_route_level(db_session):
    """revoke_device_token is unit-tested at the service layer elsewhere,
    but nothing proves a revoked token is actually rejected by the real
    route. Generate a token, revoke it, then attempt an upload with the
    now-stale plaintext via the same headless client path used by the
    valid-token test -- must 401, not 200."""
    user = create_user(db_session, "devicerevoke", "testpass123")
    token = set_device_token(db_session, user)
    revoke_device_token(db_session, user)
    device_client = _device_client(db_session)
    resp = device_client.post(
        "/api/transcribe",
        files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
        data={"provider": "moonshine", "kind": "voice_note"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
