"""Voice-dump LLM chain (issue #284): _structure_from_text, segment_voice_dump,
and the run_llm_job dispatch for kind="voice_dump"."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from database import Transcript, User, VoiceNote
from services.llm_jobs import (
    VALID_KINDS, IO_KINDS, CPU_KINDS, AUTO_RETRY_KINDS, enqueue_llm_job, run_llm_job,
)
from services.voice_notes import (
    _structure_from_text, segment_voice_dump,
)


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _chat_response(content, finish_reason="stop"):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]})


class _FakeTranscript:
    """A transcript-shaped object for unit tests that don't need the DB.
    segment_voice_dump calls _transcript_text(transcript) which reads
    transcript.full_text."""
    def __init__(self, full_text):
        self.full_text = full_text
        self.segments = []


# ── Kind pool tests ────────────────────────────────────────────────────────


def test_voice_dump_is_in_valid_kinds():
    assert "voice_dump" in VALID_KINDS


def test_voice_dump_is_in_io_pool_not_cpu():
    assert "voice_dump" in IO_KINDS
    assert "voice_dump" not in CPU_KINDS


def test_voice_dump_is_auto_retry():
    assert "voice_dump" in AUTO_RETRY_KINDS


# ── _structure_from_text ───────────────────────────────────────────────────


def test_structure_from_text_returns_structured_payload():
    payload = json.dumps({
        "title": "Email Dave re: budget",
        "body": "Send Dave the latest numbers by Friday.",
        "structured": {"trigger": "Friday", "subject": "Budget numbers"},
    })
    fake_post = AsyncMock(return_value=_chat_response(payload))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(_structure_from_text(
            "remind me to email dave", "reminder",
            api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert result["type"] == "reminder"
    assert result["title"] == "Email Dave re: budget"
    assert result["body"] == "Send Dave the latest numbers by Friday."
    assert result["structured"]["trigger"] == "Friday"


def test_structure_from_text_falls_back_on_bad_json():
    """A bad LLM parse must return the original text as the body, not lose
    the user's content. Title is the first line, structured is {}."""
    fake_post = AsyncMock(return_value=_chat_response("{not valid"))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(_structure_from_text(
            "first line of note\nrest of note", "idea",
            api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert result["type"] == "idea"
    assert "first line of note" in result["title"]
    assert result["body"] == "first line of note\nrest of note"
    assert result["structured"] == {}


def test_structure_from_text_falls_back_on_network_error():
    """A network error (Exception from httpx) must fall back, not raise."""
    fake_post = AsyncMock(side_effect=Exception("Connection refused"))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(_structure_from_text(
            "some text here", "journal",
            api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert result["type"] == "journal"
    assert result["title"] == "some text here"
    assert result["body"] == "some text here"
    assert result["structured"] == {}


def test_structure_from_text_clamps_invalid_note_type_to_general():
    """An unknown note_type must be forced to 'general' before the prompt
    is built, so the LLM gets a safe fallback type."""
    payload = json.dumps({"title": "T", "body": "B", "structured": {}})
    fake_post = AsyncMock(return_value=_chat_response(payload))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(_structure_from_text(
            "text", "bogus",
            api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert result["type"] == "general"


def test_structure_from_text_include_clarifying_parses_questions():
    """include_clarifying=True — LLM returns clarifying_questions array,
    result includes it."""
    payload = json.dumps({
        "title": "Task",
        "body": "Get groceries.",
        "structured": {"priority": "medium"},
        "clarifying_questions": ["What store?", "By when?"],
    })
    fake_post = AsyncMock(return_value=_chat_response(payload))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(_structure_from_text(
            "get groceries", "todo",
            api_key="", provider_name="local",
            provider_config=None, model="test",
            include_clarifying=True,
        ))
    assert result["type"] == "todo"
    assert result["title"] == "Task"
    assert result["clarifying_questions"] == ["What store?", "By when?"]


def test_structure_from_text_include_clarifying_handles_missing_key():
    """include_clarifying=True — LLM returns JSON without
    clarifying_questions key, result has empty list."""
    payload = json.dumps({
        "title": "Task",
        "body": "Get groceries.",
        "structured": {},
    })
    fake_post = AsyncMock(return_value=_chat_response(payload))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(_structure_from_text(
            "get groceries", "todo",
            api_key="", provider_name="local",
            provider_config=None, model="test",
            include_clarifying=True,
        ))
    assert result["clarifying_questions"] == []


def test_structure_from_text_include_clarifying_falls_back_to_empty():
    """include_clarifying=True — LLM raises Exception, fallback dict
    includes clarifying_questions: []."""
    fake_post = AsyncMock(side_effect=Exception("LLM timeout"))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(_structure_from_text(
            "get groceries", "todo",
            api_key="", provider_name="local",
            provider_config=None, model="test",
            include_clarifying=True,
        ))
    assert result["type"] == "todo"
    assert result["title"] == "get groceries"
    assert result["body"] == "get groceries"
    assert result["structured"] == {}
    assert result["clarifying_questions"] == []


# ── segment_voice_dump ─────────────────────────────────────────────────────


def test_segment_voice_dump_parses_valid_array():
    transcript = _FakeTranscript("Item one. Item two.")
    segments = json.dumps([
        {"span_text": "Item one.", "tentative_type": "todo"},
        {"span_text": "Item two.", "tentative_type": "idea"},
    ])
    fake_post = AsyncMock(return_value=_chat_response(segments))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(segment_voice_dump(
            transcript, api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert len(result) == 2
    assert result[0]["span_text"] == "Item one."
    assert result[0]["tentative_type"] == "todo"
    assert result[1]["span_text"] == "Item two."
    assert result[1]["tentative_type"] == "idea"


def test_segment_voice_dump_filters_out_items_with_empty_span_text():
    transcript = _FakeTranscript("First. Second.")
    segments = json.dumps([
        {"span_text": "First.", "tentative_type": "todo"},
        {"span_text": "", "tentative_type": "idea"},
        {"span_text": "Second.", "tentative_type": "journal"},
    ])
    fake_post = AsyncMock(return_value=_chat_response(segments))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(segment_voice_dump(
            transcript, api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert len(result) == 2
    assert result[0]["span_text"] == "First."
    assert result[1]["span_text"] == "Second."


def test_segment_voice_dump_falls_back_on_non_list_response():
    """When the LLM returns a JSON object instead of an array, the fallback
    must return a single-item list wrapping the full transcript."""
    transcript = _FakeTranscript("Full transcript text here.")
    fake_post = AsyncMock(return_value=_chat_response(json.dumps({"not": "a list"})))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(segment_voice_dump(
            transcript, api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert len(result) == 1
    assert result[0]["span_text"] == "Full transcript text here."
    assert result[0]["tentative_type"] == "general"


def test_segment_voice_dump_falls_back_on_empty_array():
    """An empty JSON array must also trigger the single-item fallback."""
    transcript = _FakeTranscript("Full transcript text.")
    fake_post = AsyncMock(return_value=_chat_response(json.dumps([])))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(segment_voice_dump(
            transcript, api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert len(result) == 1
    assert result[0]["span_text"] == "Full transcript text."
    assert result[0]["tentative_type"] == "general"


def test_segment_voice_dump_falls_back_on_parse_error():
    """A malformed LLM response that can't be parsed as JSON must fall
    back, not raise. The entire transcript becomes one 'general' item."""
    transcript = _FakeTranscript("Fallback text.")
    fake_post = AsyncMock(return_value=_chat_response("not json at all {{{"))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(segment_voice_dump(
            transcript, api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    assert len(result) == 1
    assert result[0]["span_text"] == "Fallback text."
    assert result[0]["tentative_type"] == "general"


def test_segment_voice_dump_handles_empty_transcript():
    """An empty full_text must short-circuit — no LLM call, single-item
    fallback with empty span_text."""
    transcript = _FakeTranscript("")
    with patch("httpx.AsyncClient.post") as fake_post:
        result = asyncio.run(segment_voice_dump(
            transcript, api_key="", provider_name="local",
            provider_config=None, model="test",
        ))
    fake_post.assert_not_called()
    assert result == [{"span_text": "", "tentative_type": "general"}]


# ── voice_dump dispatch inside run_llm_job ──────────────────────────────────


class _NoCloseSession:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def test_voice_dump_job_writes_items_to_result_json(db_session):
    """End-to-end through the worker dispatch: enqueue, run, verify
    job.result_json has items array with correct shape (index, type,
    title, body, structured, clarifying_questions)."""
    user = User(username="vd_user", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="vd", filename="f.mp3", status="completed",
        full_text="First note. Second note.", segments=[], kind="voice_dump",
    )
    db_session.add(t)
    db_session.commit()

    job = enqueue_llm_job(db_session, user.id, t.id, "voice_dump", "local_llm", "llama3")
    job.status = "running"
    job.progress_total = 1
    db_session.commit()

    segments_payload = json.dumps([
        {"span_text": "First note.", "tentative_type": "todo"},
        {"span_text": "Second note.", "tentative_type": "idea"},
    ])
    struct0_payload = json.dumps({
        "title": "First item",
        "body": "Body of first.",
        "structured": {"priority": "high"},
        "clarifying_questions": ["What is the deadline?"],
    })
    struct1_payload = json.dumps({
        "title": "Second item",
        "body": "Body of second.",
        "structured": {"summary": "An idea"},
        "clarifying_questions": [],
    })
    fake_post = AsyncMock(side_effect=[
        _chat_response(segments_payload),
        _chat_response(struct0_payload),
        _chat_response(struct1_payload),
    ])
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    items = job.result_json["items"]
    assert len(items) == 2

    assert items[0]["index"] == 0
    assert items[0]["type"] == "todo"
    assert items[0]["title"] == "First item"
    assert items[0]["body"] == "Body of first."
    assert items[0]["structured"] == {"priority": "high"}
    assert items[0]["clarifying_questions"] == ["What is the deadline?"]

    assert items[1]["index"] == 1
    assert items[1]["type"] == "idea"
    assert items[1]["title"] == "Second item"
    assert items[1]["body"] == "Body of second."
    assert items[1]["structured"] == {"summary": "An idea"}
    assert items[1]["clarifying_questions"] == []


def test_voice_dump_cancel_during_final_span_leaves_no_items(db_session):
    user = User(username="vd_cancel_user", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="vd", filename="f.mp3", status="completed",
        full_text="Only note.", segments=[], kind="voice_dump",
    )
    db_session.add(t)
    db_session.commit()

    job = enqueue_llm_job(db_session, user.id, t.id, "voice_dump", "local_llm", "llama3")
    job.status = "running"
    db_session.commit()
    job_id = job.id

    segments_payload = json.dumps([{"span_text": "Only note.", "tentative_type": "todo"}])
    struct_payload = json.dumps({"title": "One", "body": "Body", "structured": {}, "clarifying_questions": []})

    calls = {"n": 0}

    async def fake_post(self, url=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _chat_response(segments_payload)
        # Before returning the struct response, flip the job to cancelled in DB
        j = db_session.query(type(job)).filter(type(job).id == job_id).first()
        j.status = "cancelled"
        db_session.commit()
        return _chat_response(struct_payload)

    factory = lambda: _NoCloseSession(db_session)
    # Pin the post-final-await guard (lines 786-788): if that guard is removed,
    # _finish would still see the cancellation and roll back, so we assert the
    # early return path was taken instead.
    from services import llm_jobs as _llm
    orig_finish = _llm._finish
    finish_calls: list[str] = []
    def spy_finish(db_arg, job_arg, status, error=None):
        finish_calls.append(status)
        return orig_finish(db_arg, job_arg, status, error=error)
    with patch("httpx.AsyncClient.post", fake_post), patch.object(_llm, "_finish", side_effect=spy_finish):
        asyncio.run(run_llm_job(factory, job_id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "cancelled"
    assert job.result_json is None or job.result_json.get("items") is None
    assert finish_calls == [], "post-final-await guard should have returned before _finish"
