"""Summarize: keyless local providers must not receive an empty Bearer header."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from database import Transcript, User
from services.transcription import TranscriptionService


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _chat_response(content):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def _make_user_and_transcript(db_session):
    user = User(username="summarizer", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="f.mp3", status="completed",
        full_text="raw meeting text", segments=[],
    )
    db_session.add(t)
    db_session.commit()
    return user, t


def test_summarize_local_provider_omits_auth_header_when_no_key(db_session, tmp_path):
    user, transcript = _make_user_and_transcript(db_session)
    svc = TranscriptionService(str(tmp_path))
    fake_post = AsyncMock(return_value=_chat_response(
        '{"short_summary": "s", "key_points": [], "action_items": [], "decisions": []}'
    ))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(svc.summarize(
            db_session, user.id, transcript.id, api_key="", provider_name="local",
            provider_config={"api_url": "http://box:8080/v1"}, model="llama3",
        ))
    headers = fake_post.await_args.kwargs["headers"]
    assert "Authorization" not in headers
