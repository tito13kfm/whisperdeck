"""Correction pass: provider routing, speaker-labeled chunked prompts,
auto-correct key resolution, and the curated model catalog."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from database import Transcript, User, ProviderConfig
from services.correction import correct_transcript, run_auto_correction, _batch_lines, _transcript_lines
from services.model_catalog import get_correction_models, _openrouter_cache
from services.settings import DEFAULT_SETTINGS, get_user_settings, resolve_provider_key


def _make_user_and_transcript(db_session, segments=None, full_text="raw text"):
    user = User(username="router", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="f.mp3", status="completed",
        full_text=full_text, segments=segments or [],
    )
    db_session.add(t)
    db_session.commit()
    return user, t


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


def _chat_response(content):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


# ── provider routing ──────────────────────────────────────────────────────

def test_openrouter_hits_openrouter_base_url(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_response("fixed"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(
            db_session, transcript, api_key="sk-or-key",
            provider_name="openrouter", model="deepseek/deepseek-v4-flash",
        ))
    assert transcript.correction_error is None
    called_url = fake_post.await_args.args[0]
    assert called_url.startswith("https://openrouter.ai/api/v1")


def test_unknown_provider_records_clear_error_not_groq_fallback(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_response("should never be called"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(
            db_session, transcript, api_key="key", provider_name="replicate",
        ))
    fake_post.assert_not_awaited()
    assert "does not support provider 'replicate'" in transcript.correction_error


def test_local_provider_uses_saved_api_url(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_response("fixed"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(
            db_session, transcript, api_key="", provider_name="local", model="llama3",
            provider_config={"api_url": "http://box:8080/v1"},
        ))
    assert fake_post.await_args.args[0].startswith("http://box:8080/v1")


# ── speaker-labeled chunked prompt ────────────────────────────────────────

def test_prompt_carries_speaker_labels_and_preserve_instruction(db_session):
    segs = [
        {"start": 0, "end": 5, "speaker": "Sarah Chen", "text": "netsweet cutover is on track"},
        {"start": 5, "end": 9, "speaker": "Raj Patel", "text": "vendor sync after the first"},
    ]
    user, transcript = _make_user_and_transcript(db_session, segments=segs)
    fake_post = AsyncMock(return_value=_chat_response(
        "Sarah Chen: NetSuite cutover is on track\n\nRaj Patel: Vendor sync after the first"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))
    prompt = fake_post.await_args.kwargs["json"]["messages"][1]["content"]
    assert "Sarah Chen: netsweet cutover is on track" in prompt
    assert "reproduce each 'Speaker Name:' prefix exactly" in prompt
    assert transcript.corrected_text.startswith("Sarah Chen: NetSuite")


def test_long_transcripts_are_chunked_into_multiple_calls(db_session):
    segs = [
        {"start": i, "end": i + 1, "speaker": f"Speaker {i % 3}", "text": "word " * 60}
        for i in range(40)
    ]
    user, transcript = _make_user_and_transcript(db_session, segments=segs)
    fake_post = AsyncMock(side_effect=[_chat_response(f"part {i}") for i in range(10)])
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))
    assert fake_post.await_count > 1
    assert transcript.correction_error is None
    assert transcript.corrected_text == "\n\n".join(
        f"part {i}" for i in range(fake_post.await_count))


def test_batch_lines_respects_budget():
    lines = ["x" * 100] * 10
    batches = _batch_lines(lines, budget=350)
    assert len(batches) > 1
    assert [ln for b in batches for ln in b] == lines


def test_transcript_lines_falls_back_to_full_text(db_session):
    user, transcript = _make_user_and_transcript(db_session, segments=[], full_text="plain words")
    assert _transcript_lines(transcript) == ["plain words"]


# ── auto-correct key resolution ───────────────────────────────────────────

def test_auto_correction_skips_with_reason_when_provider_key_missing(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    settings = {**DEFAULT_SETTINGS, "correction_provider": "openrouter", "correction_model": "deepseek/deepseek-v4-flash"}
    fake_post = AsyncMock(return_value=_chat_response("never"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_auto_correction(db_session, transcript, settings))
    fake_post.assert_not_awaited()
    assert "auto-correct skipped: no openrouter API key" in transcript.correction_error


def test_auto_correction_uses_settings_provider_and_pool_key(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    db_session.add(ProviderConfig(user_id=user.id, name="openrouter", api_key="sk-or-pool"))
    db_session.commit()
    settings = {**DEFAULT_SETTINGS, "correction_provider": "openrouter", "correction_model": "deepseek/deepseek-v4-flash"}
    fake_post = AsyncMock(return_value=_chat_response("fixed"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_auto_correction(db_session, transcript, settings))
    assert transcript.correction_error is None
    assert fake_post.await_args.args[0].startswith("https://openrouter.ai")
    assert fake_post.await_args.kwargs["headers"]["Authorization"] == "Bearer sk-or-pool"


def test_resolve_provider_key_returns_empty_when_unsaved(db_session):
    user = User(username="poolless", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    key, cfg = resolve_provider_key(db_session, user.id, "openrouter")
    assert key == "" and cfg == {}


def test_new_settings_defaults_present(db_session):
    user = User(username="defaults", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    settings = get_user_settings(db_session, user.id)
    assert settings["correction_provider"] == "groq"
    assert settings["summary_provider"] == "groq"
    assert settings["correction_model"] and settings["summary_model"]


# ── model catalog ─────────────────────────────────────────────────────────

def _reset_openrouter_cache():
    _openrouter_cache.update(at=0.0, models=None)


def test_catalog_curated_for_groq_no_network():
    fake_get = AsyncMock(side_effect=AssertionError("no network for groq"))
    with patch("httpx.AsyncClient.get", fake_get):
        models = asyncio.run(get_correction_models("groq"))
    assert any(m["id"] == "llama-3.3-70b-versatile" for m in models)


def test_catalog_openrouter_merges_pricing_and_drops_dead_models():
    _reset_openrouter_cache()
    live = _FakeResponse(200, {"data": [
        {"id": "deepseek/deepseek-v4-flash", "pricing": {"prompt": "0.00000014", "completion": "0.00000028"}},
    ]})
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=live)):
        models = asyncio.run(get_correction_models("openrouter"))
    _reset_openrouter_cache()
    ids = [m["id"] for m in models]
    assert ids == ["deepseek/deepseek-v4-flash"]  # others not live → dropped
    assert "$0.14/M in" in models[0]["label"]


def test_catalog_openrouter_network_failure_falls_back_to_curated():
    _reset_openrouter_cache()
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=RuntimeError("offline"))):
        models = asyncio.run(get_correction_models("openrouter"))
    _reset_openrouter_cache()
    assert any(m["id"] == "deepseek/deepseek-v4-flash" for m in models)
