"""Voice-note LLM chain (issue #169): classify_voice_note,
structure_voice_note, run_voice_note_chain, and the run_llm_job dispatch
for kind="voice_note" — the two-call chain writes a VoiceNote row AND
the job's result_json, mirroring how summary does it. Mirrors the
shape of test_reformatting.py for the existing reformat chain."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from database import Transcript, User, VoiceNote
from services.llm_jobs import (
    VALID_KINDS, IO_KINDS, CPU_KINDS, AUTO_RETRY_KINDS, enqueue_llm_job, run_llm_job,
    enqueue_auto_voice_note,
)
from services.voice_notes import (
    NOTE_TYPES, classify_voice_note, structure_voice_note, run_voice_note_chain,
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


def _make_user_and_voice_note(db_session, full_text="remind me to email dave about the budget numbers"):
    user = User(username="vn_user", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="vn", filename="f.mp3", status="completed",
        full_text=full_text, segments=[], kind="voice_note",
    )
    db_session.add(t)
    db_session.commit()
    return user, t


def test_valid_kinds_includes_voice_note():
    assert "voice_note" in VALID_KINDS


def test_voice_note_is_in_io_pool_not_cpu():
    assert "voice_note" in IO_KINDS
    assert "voice_note" not in CPU_KINDS


def test_voice_note_is_auto_retry_eligible():
    assert "voice_note" in AUTO_RETRY_KINDS


def test_note_types_includes_all_five_classes():
    assert set(NOTE_TYPES) == {"todo", "idea", "reminder", "journal", "general", "bug"}


# ── classify_voice_note ───────────────────────────────────────────────────


@pytest.mark.parametrize("label", ["todo", "idea", "reminder", "journal", "general"])
def test_classify_voice_note_returns_each_known_label(db_session, label):
    user, t = _make_user_and_voice_note(db_session)
    fake_post = AsyncMock(return_value=_chat_response(json.dumps({"type": label})))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(classify_voice_note(
            t, api_key="", provider_name="local",
            provider_config={"api_url": "http://box:8080/v1"}, model="llama3",
        ))
    assert result == label


def test_classify_voice_note_falls_back_to_general_on_bad_json(db_session):
    user, t = _make_user_and_voice_note(db_session)
    fake_post = AsyncMock(return_value=_chat_response("not json at all"))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(classify_voice_note(
            t, api_key="", provider_name="local", model="llama3",
        ))
    assert result == "general"


def test_classify_voice_note_falls_back_to_general_on_unknown_label(db_session):
    user, t = _make_user_and_voice_note(db_session)
    fake_post = AsyncMock(return_value=_chat_response(json.dumps({"type": "podcast"})))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(classify_voice_note(
            t, api_key="", provider_name="local", model="llama3",
        ))
    assert result == "general"


def test_classify_voice_note_falls_back_to_general_on_api_error(db_session):
    user, t = _make_user_and_voice_note(db_session)
    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(classify_voice_note(
            t, api_key="", provider_name="local", model="llama3",
        ))
    assert result == "general"


# ── structure_voice_note ─────────────────────────────────────────────────


def test_structure_voice_note_returns_full_payload(db_session):
    user, t = _make_user_and_voice_note(db_session)
    payload = json.dumps({
        "title": "Email Dave re: budget",
        "body": "Send Dave the latest numbers by Friday.",
        "structured": {"trigger": "Friday morning", "subject": "Send Dave the budget numbers"},
    })
    fake_post = AsyncMock(return_value=_chat_response(payload))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(structure_voice_note(
            t, "reminder", api_key="", provider_name="local", model="llama3",
        ))
    assert result["type"] == "reminder"
    assert result["title"] == "Email Dave re: budget"
    assert result["body"] == "Send Dave the latest numbers by Friday."
    assert result["structured"]["trigger"] == "Friday morning"


def test_structure_voice_note_falls_back_to_raw_transcript_on_parse_error(db_session):
    """A bad LLM parse must NOT strand the user with a VoiceNote row
    that has only a type and no body — the fallback is the raw
    transcript so the user has SOMETHING usable."""
    user, t = _make_user_and_voice_note(db_session, full_text="first line of note\nrest of note")
    fake_post = AsyncMock(return_value=_chat_response("not json"))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(structure_voice_note(
            t, "idea", api_key="", provider_name="local", model="llama3",
        ))
    assert result["type"] == "idea"
    assert "first line of note" in result["title"]
    assert result["body"] == t.full_text


def test_structure_voice_note_coerces_unknown_type_to_general(db_session):
    user, t = _make_user_and_voice_note(db_session)
    payload = json.dumps({"title": "T", "body": "B", "structured": {}})
    fake_post = AsyncMock(return_value=_chat_response(payload))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(structure_voice_note(
            t, "totally-invalid", api_key="", provider_name="local", model="llama3",
        ))
    assert result["type"] == "general"


# ── run_voice_note_chain ─────────────────────────────────────────────────


def test_run_voice_note_chain_calls_classify_then_structure(db_session):
    user, t = _make_user_and_voice_note(db_session)
    classify_payload = json.dumps({"type": "todo"})
    structure_payload = json.dumps({
        "title": "Three things",
        "body": "Do these.",
        "structured": {"items": [{"text": "First", "priority": "high", "due_date": None}]},
    })
    # Two separate responses, one per LLM call in the chain.
    fake_post = AsyncMock(side_effect=[
        _chat_response(classify_payload),
        _chat_response(structure_payload),
    ])
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(run_voice_note_chain(
            t, api_key="", provider_name="local", model="llama3",
        ))
    assert result["type"] == "todo"
    assert result["title"] == "Three things"
    assert fake_post.await_count == 2


# ── enqueue_auto_voice_note ──────────────────────────────────────────────


def test_enqueue_auto_voice_note_kind_gating(db_session):
    """Non-voice-note transcripts must not get a voice_note job
    enqueued — the pipeline only calls this helper for kind=='voice_note',
    but it's a no-op guard against a future caller that forgets."""
    user = User(username="kind_user", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    meeting = Transcript(
        user_id=user.id, title="m", filename="m.mp3", status="completed",
        full_text="x", segments=[], kind="meeting",
    )
    db_session.add(meeting)
    db_session.commit()
    assert enqueue_auto_voice_note(db_session, meeting, {}) is None


def test_enqueue_auto_voice_note_no_ops_while_pending_even_if_kind_is_voice_note(db_session):
    """effective_kind(), not raw kind, gates enqueue_auto_voice_note (design
    decision 11) -- a placeholder/stale kind value on a still-pending
    transcript must never trigger the voice-note chain."""
    user = User(username="pending_vn", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="t.mp3", status="completed",
        full_text="x", segments=[], kind="voice_note", classification_status="pending",
    )
    db_session.add(t)
    db_session.commit()
    assert enqueue_auto_voice_note(db_session, t, {}) is None


def test_enqueue_auto_voice_note_creates_prefailed_job_without_key(db_session):
    """No API key for groq saved → the job is pre-failed with the skip
    reason (the same shape enqueue_auto_correction / enqueue_auto_classify
    use). This makes the skip visible in the Queue screen rather than
    silently dropping the chain."""
    user, t = _make_user_and_voice_note(db_session)
    job = enqueue_auto_voice_note(db_session, t, {"format_provider": "groq", "format_model": "llama-3.3"})
    assert job is not None
    assert job.kind == "voice_note"
    assert job.status == "failed"
    assert "no groq API key" in (job.error or "")


def test_enqueue_auto_voice_note_creates_pending_job_with_key(db_session):
    """With a key saved, the job lands in pending and the worker picks
    it up — the happy path that drives the real voice-note flow."""
    from database import ProviderConfig
    db_session.add(ProviderConfig(
        user_id=1, name="groq", api_key="sk-test-fake", is_active=True,
    ))
    user, t = _make_user_and_voice_note(db_session)
    job = enqueue_auto_voice_note(db_session, t, {"format_provider": "groq", "format_model": "llama-3.3"})
    assert job is not None
    assert job.kind == "voice_note"
    assert job.status == "pending"
    assert job.provider == "groq"


# ── run_llm_job dispatch for kind=voice_note ──────────────────────────────


class _NoCloseSession:
    """run_llm_job closes its session; tests share one — swallow the close."""
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def test_run_llm_job_voice_note_writes_voice_note_row_and_result(db_session):
    """End-to-end through the worker dispatch: enqueue, run, verify
    both the VoiceNote row and the job's result_json got the same
    payload (mirrors how summary's backfill + result_json work)."""
    user, t = _make_user_and_voice_note(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_note", "local_llm", "llama3")
    job.status = "running"
    job.progress_total = 2
    db_session.commit()

    classify_payload = json.dumps({"type": "idea"})
    structure_payload = json.dumps({
        "title": "Refactor idea",
        "body": "Extract a helper.",
        "structured": {"summary": "Extract a small helper.", "tags": ["refactor", "dx"]},
    })
    fake_post = AsyncMock(side_effect=[
        _chat_response(classify_payload),
        _chat_response(structure_payload),
    ])
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.progress_done == 2
    assert job.result_json["type"] == "idea"
    assert job.result_json["title"] == "Refactor idea"
    assert job.result_json["structured"]["summary"] == "Extract a small helper."

    note = db_session.query(VoiceNote).filter(VoiceNote.transcript_id == t.id).first()
    assert note is not None
    assert note.note_type == "idea"
    assert note.title == "Refactor idea"
    assert note.body == "Extract a helper."
    assert note.structured == {"summary": "Extract a small helper.", "tags": ["refactor", "dx"]}
    assert note.model == "llama3"
    assert note.provider == "local_llm"


def test_run_llm_job_voice_note_overwrites_existing_row(db_session):
    """Re-running the chain on the same transcript must update the
    existing VoiceNote row in place, not create a duplicate. Mirrors
    how Summary's first-or-update logic works."""
    user, t = _make_user_and_voice_note(db_session)
    existing = VoiceNote(
        user_id=user.id, transcript_id=t.id, note_type="general",
        title="old", body="old body", structured={"x": 1},
        model="old", provider="old",
    )
    db_session.add(existing)
    db_session.commit()

    job = enqueue_llm_job(db_session, user.id, t.id, "voice_note", "local_llm", "llama3")
    job.status = "running"
    job.progress_total = 2
    db_session.commit()

    fake_post = AsyncMock(side_effect=[
        _chat_response(json.dumps({"type": "todo"})),
        _chat_response(json.dumps({"title": "new", "body": "new body", "structured": {"items": []}})),
    ])
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    notes = db_session.query(VoiceNote).filter(VoiceNote.transcript_id == t.id).all()
    assert len(notes) == 1
    assert notes[0].note_type == "todo"
    assert notes[0].title == "new"
    assert notes[0].body == "new body"
    assert notes[0].model == "llama3"


def test_run_llm_job_voice_note_survives_chain_api_error_with_fallback(db_session):
    """The chain's never-raise contract: even when both LLM calls hit
    API errors, the chain still returns a fallback body. The job
    completes (with the fallback) rather than failing — a transient
    network error on the FIRST try shouldn't lose the user's note."""
    user, t = _make_user_and_voice_note(db_session, full_text="call dave tomorrow morning about the budget")
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_note", "local_llm", "llama3")
    job.status = "running"
    job.progress_total = 2
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    note = db_session.query(VoiceNote).filter(VoiceNote.transcript_id == t.id).first()
    assert note is not None
    # Fallback: the raw transcript becomes the body.
    assert "call dave tomorrow" in note.body
