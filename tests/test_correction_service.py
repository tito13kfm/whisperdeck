import json
from unittest.mock import AsyncMock, patch

from database import Transcript, User
from services.correction import correct_transcript, extract_hotwords_from_doc
from services.hotwords import list_hotwords


def _make_user_and_transcript(db_session, full_text="we discussed the api rate limiting"):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="completed", full_text=full_text)
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


def _chat_completion_response(content: str):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def test_correct_transcript_sets_corrected_text_on_success(db_session):
    user, transcript = _make_user_and_transcript(db_session)

    fake_post = AsyncMock(return_value=_chat_completion_response("We discussed the API rate limiting."))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    db_session.refresh(transcript)
    assert transcript.corrected_text == "We discussed the API rate limiting."
    assert transcript.correction_model == "groq/llama-3.3-70b-versatile"
    assert transcript.correction_error is None


def test_correct_transcript_sets_error_on_failure_without_raising(db_session):
    user, transcript = _make_user_and_transcript(db_session)

    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    db_session.refresh(transcript)
    assert transcript.corrected_text is None
    assert "500" in transcript.correction_error
    # Pins the pre-extraction wording exactly — services/llm_client.py's
    # chat_completion() defaults http_error_label to None so correction
    # keeps its original generic text instead of picking up a feature
    # prefix meant for summarize/reformatting (see llm_client.py docstring).
    assert transcript.correction_error == "LLM API error (500): " + json.dumps({"error": "boom"})


def test_correct_transcript_includes_glossary_in_prompt(db_session):
    from services.hotwords import add_hotword

    user, transcript = _make_user_and_transcript(db_session)
    add_hotword(db_session, user.id, "Groqonomicon")

    fake_post = AsyncMock(return_value=_chat_completion_response("corrected"))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    sent_json = fake_post.call_args.kwargs["json"]
    prompt_text = json.dumps(sent_json)
    assert "Groqonomicon" in prompt_text


def test_extract_hotwords_from_doc_persists_terms(db_session):
    user = User(username="bob", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    fake_post = AsyncMock(
        return_value=_chat_completion_response(json.dumps({"terms": ["Acme Corp", "Q3 rollout"]}))
    )
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        terms = asyncio.run(
            extract_hotwords_from_doc(db_session, user.id, "Agenda: Acme Corp Q3 rollout status", api_key="fake-key")
        )

    assert set(terms) == {"Acme Corp", "Q3 rollout"}
    stored = {h.term for h in list_hotwords(db_session, user.id)}
    assert stored == {"Acme Corp", "Q3 rollout"}
    assert all(h.source == "extracted" for h in list_hotwords(db_session, user.id))


def test_extract_hotwords_from_doc_returns_empty_on_failure(db_session):
    user = User(username="carol", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        terms = asyncio.run(
            extract_hotwords_from_doc(db_session, user.id, "some doc text", api_key="fake-key")
        )

    assert terms == []
    assert list_hotwords(db_session, user.id) == []
