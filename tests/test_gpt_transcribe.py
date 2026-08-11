"""Tests for gpt-transcribe provider support (issue #369).

Covers: keyword sanitization, model list filtering, duration/language handling
for no-segments responses, keywords/languages forwarding, prompt forwarding,
pricing rates, and diarization skip on empty segments.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from services.hotwords import sanitize_keywords
from services.pricing import get_stt_rate
from backends.openai import OpenAIProvider
from backends.openrouter import OpenRouterProvider
from services.diarization import DiarizationService


# -- sanitize_keywords -------------------------------------------------------

def test_sanitize_keywords_drops_angle_brackets():
    result = sanitize_keywords(["hello", "a<b", "c>d", "ok"])
    assert result == ["hello", "ok"]


def test_sanitize_keywords_drops_cr_lf():
    result = sanitize_keywords(["hello", "a\rb", "c\nd", "ok"])
    assert result == ["hello", "ok"]


def test_sanitize_keywords_strips_and_drops_empty():
    result = sanitize_keywords(["  hello  ", "  ", "", "ok"])
    assert result == ["hello", "ok"]


def test_sanitize_keywords_keeps_clean():
    terms = ["WhisperDeck", "gpt-transcribe", "OpenAI"]
    assert sanitize_keywords(terms) == terms


def test_sanitize_keywords_all_bad_returns_empty():
    assert sanitize_keywords(["<bad>", "a\rb"]) == []


# -- pricing -----------------------------------------------------------------

def test_pricing_openai_gpt_transcribe_rate():
    rate = get_stt_rate("openai", "gpt-transcribe")
    assert rate["rate_per_minute"] == 0.0045
    assert "0.0045" in rate["rate_source"]


def test_pricing_openrouter_gpt_transcribe_rate():
    rate = get_stt_rate("openrouter", "openai/gpt-transcribe")
    assert rate["rate_per_minute"] == 0.0045
    assert "0.0045" in rate["rate_source"]


# -- diarization empty segments no-op ---------------------------------------

@pytest.mark.asyncio
async def test_diarize_and_merge_empty_segments_noop():
    svc = DiarizationService()
    merged, count, method, err = await svc.diarize_and_merge(
        audio_path="dummy.mp3", num_speakers=2, segments=[]
    )
    assert merged == []
    assert count == 0
    assert method == "none"
    assert err is None


# -- openai list_models widens to transcribe --------------------------------

@pytest.mark.asyncio
async def test_openai_list_models_includes_transcribe():
    provider = OpenAIProvider({"api_key": "sk-test", "default_model": "whisper-1"})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "whisper-1"},
            {"id": "gpt-transcribe"},
            {"id": "gpt-4o-mini"},
            {"id": "gpt-transcribe-api-ev3"},
        ]
    }
    with patch("backends.openai.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client
        models = await provider.list_models()
    assert "whisper-1" in models
    assert "gpt-transcribe" in models
    assert "gpt-transcribe-api-ev3" in models
    assert "gpt-4o-mini" not in models


# -- openai transcribe: gpt-transcribe response shape ------------------------

@pytest.mark.asyncio
async def test_openai_gpt_transcribe_duration_and_language(tmp_path):
    """gpt-transcribe returns no segments; duration falls back to usage.seconds, language from languages array."""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    provider = OpenAIProvider({"api_key": "sk-test", "default_model": "gpt-transcribe"})

    # Mock httpx response for gpt-transcribe: {text, languages, usage} no segments
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "text": "hello world",
        "languages": [{"code": "en"}],
        "usage": {"type": "duration", "seconds": 60},
    }

    with patch("backends.openai.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        result = await provider.transcribe(str(audio), language="auto")

    assert result.full_text == "hello world"
    assert result.language == "en"
    assert result.duration_seconds == 60
    assert result.segments == []
    # Must not have sent verbose_json — should be json for transcribe family
    call_kwargs = mock_client.post.call_args
    sent_data = call_kwargs.kwargs.get("data") or call_kwargs[1].get("data")
    assert sent_data["response_format"] == "json"


@pytest.mark.asyncio
async def test_openai_whisper_still_uses_verbose_json(tmp_path):
    """Non-transcribe model keeps verbose_json and language singular."""
    audio = tmp_path / "test2.mp3"
    audio.write_bytes(b"fake")
    provider = OpenAIProvider({"api_key": "sk-test", "default_model": "whisper-1"})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "text": "hello",
        "language": "en",
        "segments": [{"start": 0, "end": 10, "text": "hello", "id": 0}],
    }

    with patch("backends.openai.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        result = await provider.transcribe(str(audio), language="en")
        sent_data = mock_client.post.call_args.kwargs.get("data") or mock_client.post.call_args[1].get("data")
        assert sent_data["response_format"] == "verbose_json"
        assert sent_data["language"] == "en"
        assert "languages[]" not in sent_data

    assert result.duration_seconds == 10
    assert result.language == "en"


@pytest.mark.asyncio
async def test_openai_gpt_transcribe_sends_keywords(tmp_path):
    audio = tmp_path / "test3.mp3"
    audio.write_bytes(b"fake")
    provider = OpenAIProvider({"api_key": "sk-test", "default_model": "gpt-transcribe"})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "hi", "languages": [{"code": "en"}], "usage": {"seconds": 5}}

    with patch("backends.openai.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        await provider.transcribe(str(audio), language="en", keywords=["WhisperDeck", "OpenAI"])

        sent_data = mock_client.post.call_args.kwargs.get("data") or mock_client.post.call_args[1].get("data")
        assert sent_data["keywords[]"] == ["WhisperDeck", "OpenAI"]
        assert sent_data["languages[]"] == ["en"]
        assert "language" not in sent_data


@pytest.mark.asyncio
async def test_openai_gpt_transcribe_sanitizes_keywords(tmp_path):
    audio = tmp_path / "test4.mp3"
    audio.write_bytes(b"fake")
    provider = OpenAIProvider({"api_key": "sk-test", "default_model": "gpt-transcribe"})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "hi", "languages": [{"code": "en"}], "usage": {"seconds": 1}}

    with patch("backends.openai.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        await provider.transcribe(str(audio), language="en", keywords=["ok", "a<b", "good"])

        sent_data = mock_client.post.call_args.kwargs.get("data") or mock_client.post.call_args[1].get("data")
        assert sent_data["keywords[]"] == ["ok", "good"]


# -- openrouter: prompt forwarding + gpt-transcribe handling ------------------

def test_openrouter_default_models_includes_gpt_transcribe():
    provider = OpenRouterProvider({"api_key": "sk-or-test"})
    assert "openai/gpt-transcribe" in provider._default_models()


@pytest.mark.asyncio
async def test_openrouter_forwards_prompt(tmp_path):
    audio = tmp_path / "test5.mp3"
    audio.write_bytes(b"fake")
    provider = OpenRouterProvider({"api_key": "sk-or-test", "default_model": "openai/whisper-1"})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "hi", "language": "en", "segments": []}

    with patch("backends.openrouter.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        await provider.transcribe(str(audio), language="en", prompt="hello prompt")

        sent_data = mock_client.post.call_args.kwargs.get("data") or mock_client.post.call_args[1].get("data")
        assert sent_data["prompt"] == "hello prompt"


@pytest.mark.asyncio
async def test_openrouter_gpt_transcribe_flat_text_duration(tmp_path):
    """OpenRouter flat-text branch for gpt-transcribe uses usage.seconds."""
    audio = tmp_path / "test6.mp3"
    audio.write_bytes(b"fake")
    provider = OpenRouterProvider({"api_key": "sk-or-test", "default_model": "openai/gpt-transcribe"})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # OpenRouter proxy: flat usage {seconds, cost}, no languages
    mock_resp.json.return_value = {"text": "hello", "usage": {"seconds": 42, "cost": 0.003}}

    with patch("backends.openrouter.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        result = await provider.transcribe(str(audio), language="en")

    assert result.duration_seconds == 42
    assert result.full_text == "hello"
    # OpenRouter strips languages — falls back to requested language
    assert result.language == "en"
