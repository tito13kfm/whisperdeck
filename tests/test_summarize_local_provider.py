"""Summarize: keyless local providers must not receive an empty Bearer header.

Also covers issue: consecutive local-model summarize runs sometimes returned
raw/truncated JSON text as the displayed summary instead of failing clearly.
Root cause: local models aren't put in response_format json_object (many
OpenAI-compatible local servers don't support it), and the summarize() call
never checked finish_reason nor treated invalid JSON as a real failure — it
silently wrapped whatever garbage text came back into a fake "summary" that
looked like success. See docs/superpowers/specs/... (none yet — small
targeted fix, not a new feature)."""
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, patch

from backends import ProviderError
from database import Transcript, User
from services.transcription import TranscriptionService


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _chat_response(content, finish_reason="stop"):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]})


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


def test_summarize_requests_json_mode_for_local_provider_too(db_session, tmp_path):
    """response_format json_object is safe to send unconditionally per this
    module's own comment (unsupported OpenAI-compatible endpoints just
    ignore unknown fields) — local shouldn't be excluded from a mode that,
    when honored, eliminates the malformed-JSON failure mode outright."""
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
    body = fake_post.await_args.kwargs["json"]
    assert body["response_format"] == {"type": "json_object"}


def test_summarize_raises_clear_error_when_response_was_cut_off(db_session, tmp_path):
    """A local model that hits its context/output limit mid-generation
    returns finish_reason='length' — this must surface as a clear failure,
    not a fake completed summary built from truncated text."""
    user, transcript = _make_user_and_transcript(db_session)
    svc = TranscriptionService(str(tmp_path))
    truncated = '{"short_summary": "The team discussed refunds and ca'
    fake_post = AsyncMock(return_value=_chat_response(truncated, finish_reason="length"))
    with patch("httpx.AsyncClient.post", fake_post):
        with pytest.raises(ProviderError, match="cut off"):
            asyncio.run(svc.summarize(
                db_session, user.id, transcript.id, api_key="", provider_name="local",
                provider_config={"api_url": "http://box:8080/v1"}, model="llama3",
            ))


def test_summarize_raises_clear_error_on_invalid_json(db_session, tmp_path):
    """A local model without JSON-mode enforcement sometimes returns text
    that isn't valid JSON at all (not truncated, just malformed) — this must
    also surface as a clear failure instead of silently displaying the raw
    text as if it were a real summary."""
    user, transcript = _make_user_and_transcript(db_session)
    svc = TranscriptionService(str(tmp_path))
    fake_post = AsyncMock(return_value=_chat_response("Sure, here's the summary you asked for: ...", finish_reason="stop"))
    with patch("httpx.AsyncClient.post", fake_post):
        with pytest.raises(ProviderError, match="valid JSON"):
            asyncio.run(svc.summarize(
                db_session, user.id, transcript.id, api_key="", provider_name="local",
                provider_config={"api_url": "http://box:8080/v1"}, model="llama3",
            ))


def test_summarize_stores_provider_on_summary_row(db_session, tmp_path):
    user, transcript = _make_user_and_transcript(db_session)
    svc = TranscriptionService(str(tmp_path))
    fake_post = AsyncMock(return_value=_chat_response(
        '{"short_summary": "s", "key_points": [], "action_items": [], "decisions": []}'
    ))
    with patch("httpx.AsyncClient.post", fake_post):
        summary = asyncio.run(svc.summarize(
            db_session, user.id, transcript.id, api_key="", provider_name="local",
            provider_config={"api_url": "http://box:8080/v1"}, model="llama3",
        ))
    assert summary.provider == "local"
