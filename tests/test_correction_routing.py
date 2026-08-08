"""Correction pass: provider routing, speaker-labeled chunked prompts,
auto-correct key resolution, and the curated model catalog."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from database import Transcript, User
from services.correction import correct_transcript, _batch_lines, _transcript_lines
from services.model_catalog import get_correction_models, _local_llm_cache, _openrouter_cache
from services.settings import get_user_settings, resolve_provider_key


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


def _json_records(*id_text_pairs):
    return json.dumps([{"id": rid, "text": text} for rid, text in id_text_pairs])


# ── provider routing ──────────────────────────────────────────────────────

def test_openrouter_hits_openrouter_base_url(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_response(_json_records(("L0000", "fixed"))))
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
    fake_post = AsyncMock(return_value=_chat_response(_json_records(("L0000", "fixed"))))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(
            db_session, transcript, api_key="", provider_name="local", model="llama3",
            provider_config={"api_url": "http://box:8080/v1"},
        ))
    assert fake_post.await_args.args[0].startswith("http://box:8080/v1")


def test_local_provider_omits_auth_header_when_no_key(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_response(_json_records(("L0000", "fixed"))))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(
            db_session, transcript, api_key="", provider_name="local", model="llama3",
            provider_config={"api_url": "http://box:8080/v1"},
        ))
    headers = fake_post.await_args.kwargs["headers"]
    assert "Authorization" not in headers


def test_local_llm_provider_uses_its_own_saved_api_url(db_session):
    """local_llm is a separate slot from local (transcription) — different
    port, e.g. Ollama on 11434 while the STT server sits on 8080."""
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_response(_json_records(("L0000", "fixed"))))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(
            db_session, transcript, api_key="", provider_name="local_llm", model="llama3",
            provider_config={"api_url": "http://box:11434/v1"},
        ))
    assert fake_post.await_args.args[0].startswith("http://box:11434/v1")
    headers = fake_post.await_args.kwargs["headers"]
    assert "Authorization" not in headers


def test_local_llm_provider_defaults_to_ollama_port_when_unset(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_response(_json_records(("L0000", "fixed"))))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(
            db_session, transcript, api_key="", provider_name="local_llm", model="llama3",
        ))
    assert fake_post.await_args.args[0].startswith("http://localhost:11434/v1")


# ── speaker-labeled chunked prompt ────────────────────────────────────────

def test_prompt_carries_speaker_labels_and_preserve_instruction(db_session):
    segs = [
        {"start": 0, "end": 5, "speaker": "Sarah Chen", "text": "netsweet cutover is on track"},
        {"start": 5, "end": 9, "speaker": "Raj Patel", "text": "vendor sync after the first"},
    ]
    user, transcript = _make_user_and_transcript(db_session, segments=segs)
    fake_post = AsyncMock(return_value=_chat_response(_json_records(
        ("L0000", "Sarah Chen: NetSuite cutover is on track"),
        ("L0001", "Raj Patel: Vendor sync after the first"))))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))
    prompt = fake_post.await_args.kwargs["json"]["messages"][1]["content"]
    assert "Sarah Chen: netsweet cutover is on track" in prompt
    assert "never rename, merge, or drop speakers" in prompt
    assert transcript.corrected_text.startswith("Sarah Chen: NetSuite")


def test_long_transcripts_are_chunked_into_multiple_calls(db_session):
    """Multi-batch correction with overlapping IDs: dedup keeps first occurrence,
    output is sorted by ID regardless of LLM reordering within batches."""
    from services.correction import _BATCH_OVERLAP_LINES, _batch_lines, _id_line
    segs = [{"start": i, "end": i + 1, "speaker": f"S{i}", "text": "word " * 60} for i in range(40)]
    user, transcript = _make_user_and_transcript(db_session, segments=segs)
    id_lines = [_id_line(i, s["speaker"] + ": " + s["text"]) for i, s in enumerate(segs)]
    batches = _batch_lines(id_lines, overlap=_BATCH_OVERLAP_LINES)
    records: dict[str, str] = {}
    mock_responses = []
    for batch in batches:
        batch_ids = [line[:6] for line in batch]
        batch_pairs = [(rid, f"out: {rid}") for rid in batch_ids]
        for rid, text in batch_pairs:
            if rid not in records:
                records[rid] = text
        mock_responses.append(_chat_response(_json_records(*batch_pairs)))
    fake_post = AsyncMock(side_effect=mock_responses)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))
    assert fake_post.await_count > 1
    assert transcript.correction_error is None
    sorted_texts = [text for _, text in sorted(records.items())]
    assert transcript.corrected_text == "\n\n".join(sorted_texts)


def test_batch_lines_respects_budget():
    lines = ["x" * 100] * 10
    batches = _batch_lines(lines, budget=350)
    assert len(batches) > 1
    assert [ln for b in batches for ln in b] == lines


def test_transcript_lines_falls_back_to_full_text(db_session):
    user, transcript = _make_user_and_transcript(db_session, segments=[], full_text="plain words")
    assert _transcript_lines(transcript) == ["plain words"]


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
    assert settings["correction_provider"] == "local_llm"
    assert settings["summary_provider"] == "local_llm"
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


def _reset_local_llm_cache():
    _local_llm_cache.update(at=0.0, base=None, models=None)


def test_catalog_local_llm_lists_live_models_and_stars_recommended():
    _reset_local_llm_cache()
    live = _FakeResponse(200, {"data": [
        {"id": "gemma2"},
        {"id": "llama3.2:latest"},
    ]})
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=live)):
        models = asyncio.run(get_correction_models("local_llm", "http://box:1234/v1"))
    _reset_local_llm_cache()
    ids = [m["id"] for m in models]
    # recommended (tag-matched) model sorts first with a starred label
    assert ids == ["llama3.2:latest", "gemma2"]
    assert "★" in models[0]["label"] and "Llama 3.2" in models[0]["label"]
    assert models[1]["label"] == "gemma2"


def test_catalog_local_llm_unreachable_returns_empty_for_free_text_fallback():
    _reset_local_llm_cache()
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=RuntimeError("offline"))):
        models = asyncio.run(get_correction_models("local_llm", "http://box:1234/v1"))
    _reset_local_llm_cache()
    assert models == []


def test_catalog_local_llm_unconfigured_queries_chat_default_endpoint():
    _reset_local_llm_cache()
    fake_get = AsyncMock(return_value=_FakeResponse(200, {"data": []}))
    with patch("httpx.AsyncClient.get", fake_get):
        asyncio.run(get_correction_models("local_llm", None))
    _reset_local_llm_cache()
    assert fake_get.call_args.args[0] == "http://localhost:11434/v1/models"


def test_catalog_local_llm_sends_bearer_when_key_configured():
    _reset_local_llm_cache()
    fake_get = AsyncMock(return_value=_FakeResponse(200, {"data": []}))
    with patch("httpx.AsyncClient.get", fake_get):
        asyncio.run(get_correction_models("local_llm", "http://box:1234/v1", "sekrit"))
    _reset_local_llm_cache()
    assert fake_get.call_args.kwargs["headers"] == {"Authorization": "Bearer sekrit"}
