import io
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as app_module
from database import User
from services.auth import set_device_token


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
