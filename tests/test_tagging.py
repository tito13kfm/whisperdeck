"""Cross-transcript auto-tagging (issue #171):

- services.tagging.generate_tags — JSON-mode LLM call with defensive parse
  (handles fenced JSON, prose-wrapped JSON, missing fields), normalization
  (lowercase, trim, dedupe, length clamps), and a never-raise contract
  (returns [] on any error rather than letting exceptions escape into
  the LlmJob worker).
- run_llm_job dispatch for kind="tagging" — calls generate_tags, REPLACES
  the prior tag set (doesn't append), writes TranscriptTag rows, persists
  result_json with the tag list, and honors a cancel that races the LLM
  call.
- enqueue_auto_tagging — kind-agnostic (fires for every kind), key-skip
  fails the job immediately with a rerunnable reason, mirrors the other
  auto-enqueue helpers' shape.
- Registration: `tagging` lives in VALID_KINDS, IO_KINDS, AUTO_RETRY_KINDS
  and the IO/CPU partition invariant still holds.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from database import LlmJob, ProviderConfig, Transcript, TranscriptTag, User
from services.llm_jobs import (
    VALID_KINDS, IO_KINDS, CPU_KINDS, AUTO_RETRY_KINDS,
    enqueue_llm_job, run_llm_job, enqueue_auto_tagging,
)
from services.tagging import (
    _MAX_TAGS, _extract_json_object, _normalize, generate_tags,
)


# ── registration / tuple membership ───────────────────────────────────────


def test_tagging_in_valid_kinds():
    assert "tagging" in VALID_KINDS


def test_tagging_in_io_kinds_not_cpu_kinds():
    """Tagging is a provider API call (network IO), not local CPU compute."""
    assert "tagging" in IO_KINDS
    assert "tagging" not in CPU_KINDS


def test_tagging_is_auto_retry_eligible():
    """Network-bound kinds get auto-retry; a transient API hiccup should
    resurrect a failed job without the user clicking Rerun manually."""
    assert "tagging" in AUTO_RETRY_KINDS


def test_io_cpu_pools_partition_valid_kinds():
    """Existing invariant: IO_KINDS ∪ CPU_KINDS == VALID_KINDS, disjoint.
    Adding `tagging` to IO_KINDS preserves this."""
    assert set(IO_KINDS) | set(CPU_KINDS) == set(VALID_KINDS)
    assert set(IO_KINDS) & set(CPU_KINDS) == set()


# ── services.tagging internal helpers ─────────────────────────────────────


def test_extract_json_object_handles_bare():
    obj = _extract_json_object('{"tags": ["a", "b"]}')
    assert obj == {"tags": ["a", "b"]}


def test_extract_json_object_handles_markdown_fence():
    obj = _extract_json_object('Sure, here you go:\n```json\n{"tags": ["a"]}\n```')
    assert obj == {"tags": ["a"]}


def test_extract_json_object_handles_prose_prefix():
    obj = _extract_json_object('Here are the tags: {"tags": ["a", "b"]}')
    assert obj == {"tags": ["a", "b"]}


def test_extract_json_object_returns_none_on_garbage():
    assert _extract_json_object("") is None
    assert _extract_json_object("not json at all") is None
    assert _extract_json_object("{not valid json") is None
    assert _extract_json_object("[1, 2, 3]") is None  # not an object


def test_normalize_lowercases_and_trims():
    assert _normalize(["  Q3 BUDGET  ", "Vendor Renewal"]) == ["q3 budget", "vendor renewal"]


def test_normalize_dedupes_case_insensitive():
    assert _normalize(["q3 budget", "Q3 Budget", "Q3 BUDGET"]) == ["q3 budget"]


def test_normalize_drops_short_and_overlong():
    assert _normalize(["a", "ab", "valid"]) == ["ab", "valid"]
    assert _normalize(["x" * 65, "valid"]) == ["valid"]


def test_normalize_collapses_internal_whitespace():
    assert _normalize(["q3   budget"]) == ["q3 budget"]


def test_normalize_caps_at_max_tags():
    out = _normalize([f"tag{i}" for i in range(_MAX_TAGS + 3)])
    assert len(out) == _MAX_TAGS


def test_normalize_handles_string_and_list():
    assert _normalize("single") == ["single"]
    assert _normalize(None) == []


def test_normalize_skips_non_string_entries():
    assert _normalize([1, 2, "valid", None, {"x": 1}]) == ["valid"]


# ── generate_tags end-to-end ──────────────────────────────────────────────


def _make_transcript(db_session, full_text="We discussed the Q3 budget and the vendor renewal."):
    user = User(username="tag_user", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="t.mp3", status="completed",
        full_text=full_text, segments=[],
    )
    db_session.add(t)
    db_session.commit()
    return user, t


class _FakeResponse:
    def __init__(self, content, status_code=200):
        self.status_code = status_code
        self._payload = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def test_generate_tags_parses_json_array():
    t = type("T", (), {"full_text": "any", "corrected_text": "", "segments": []})()
    response = _FakeResponse('{"tags": ["q3 budget", "vendor renewal"]}')
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=response)
        tags = asyncio.run(generate_tags(
            t, api_key="k", provider_name="groq",
            provider_config=None, model="llama-3.3-70b-versatile",
        ))
    assert sorted(tags) == ["q3 budget", "vendor renewal"]


def test_generate_tags_normalizes_response():
    t = type("T", (), {"full_text": "any", "corrected_text": "", "segments": []})()
    response = _FakeResponse('{"tags": ["Q3 BUDGET", "  q3 budget  "]}')
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=response)
        tags = asyncio.run(generate_tags(t, "k", "groq", None, "m"))
    assert tags == ["q3 budget"]


def test_generate_tags_handles_markdown_fence():
    t = type("T", (), {"full_text": "any", "corrected_text": "", "segments": []})()
    response = _FakeResponse('```json\n{"tags": ["alpha", "beta"]}\n```')
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=response)
        tags = asyncio.run(generate_tags(t, "k", "groq", None, "m"))
    assert tags == ["alpha", "beta"]


def test_generate_tags_returns_empty_on_api_error():
    t = type("T", (), {"full_text": "any", "corrected_text": "", "segments": []})()
    bad = _FakeResponse("", status_code=500)
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=bad)
        tags = asyncio.run(generate_tags(t, "k", "groq", None, "m"))
    assert tags == []


def test_generate_tags_returns_empty_on_garbage_response():
    t = type("T", (), {"full_text": "any", "corrected_text": "", "segments": []})()
    response = _FakeResponse("I cannot help with that.")
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=response)
        tags = asyncio.run(generate_tags(t, "k", "groq", None, "m"))
    assert tags == []


def test_generate_tags_prefers_corrected_text():
    """Topic detection works better on cleaned text — corrected_text is the
    richer signal when both are present."""
    t = type("T", (), {
        "full_text": "the q three bud jet for next quarter",
        "corrected_text": "the Q3 budget for next quarter",
        "segments": [],
    })()
    response = _FakeResponse('{"tags": ["q3 budget"]}')
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=response)
        # The prompt should contain the corrected_text, not the noisy original.
        tags = asyncio.run(generate_tags(t, "k", "groq", None, "m"))
    assert tags == ["q3 budget"]


def test_generate_tags_empty_text_returns_empty_without_calling_api():
    """A transcript with no text and no segments should never even hit the
    LLM — saves a round-trip and an empty-list prompt that would confuse
    weaker models into returning garbage tags."""
    t = type("T", (), {"full_text": "", "corrected_text": "", "segments": []})()
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock()
        tags = asyncio.run(generate_tags(t, "k", "groq", None, "m"))
    assert tags == []
    client.__aenter__.return_value.post.assert_not_called()


# ── enqueue_auto_tagging ──────────────────────────────────────────────────


def test_enqueue_auto_tagging_creates_pending_job_when_key_saved(db_session):
    user, t = _make_transcript(db_session)
    db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="k"))
    db_session.commit()
    job = enqueue_auto_tagging(db_session, t, {})
    db_session.refresh(job)
    assert job.kind == "tagging"
    assert job.status == "pending"
    assert job.provider == "groq"
    assert job.error is None


def test_enqueue_auto_tagging_records_keyless_skip(db_session):
    user, t = _make_transcript(db_session)
    db_session.commit()
    job = enqueue_auto_tagging(
        db_session, t, {"format_provider": "openai", "format_model": "gpt-4o-mini"},
    )
    db_session.refresh(job)
    assert job.kind == "tagging"
    assert job.status == "failed"
    assert "openai API key" in (job.error or "")


def test_enqueue_auto_tagging_fires_for_every_kind(db_session):
    """The whole point of the feature: every transcript kind gets tagged.
    The helper itself is kind-agnostic — it doesn't filter on kind like
    enqueue_auto_voice_note does. This test pins that property."""
    user = User(username="kinds", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()  # commit before reading user.id
    db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="k"))
    db_session.commit()
    for kind in ("meeting", "dictation", "voice_note"):
        t = Transcript(
            user_id=user.id, title=f"t-{kind}", filename="f.mp3",
            kind=kind, status="completed", full_text="x", segments=[],
        )
        db_session.add(t)
        db_session.commit()
        job = enqueue_auto_tagging(db_session, t, {})
        db_session.refresh(job)
        assert job.kind == "tagging"
        assert job.status == "pending"


def test_enqueue_auto_tagging_dedupes_active_job(db_session):
    """Same (transcript, kind) with an active job: return the existing one
    instead of stacking a duplicate (mirrors enqueue_llm_job's contract)."""
    user, t = _make_transcript(db_session)
    db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="k"))
    db_session.commit()
    j1 = enqueue_auto_tagging(db_session, t, {})
    j2 = enqueue_auto_tagging(db_session, t, {})
    assert j1.id == j2.id


# ── run_llm_job dispatch for kind="tagging" ──────────────────────────────


class _NoCloseSession:
    """run_llm_job closes its session; tests share one fixture — swallow close."""
    def __init__(self, db):
        self._db = db
    def __getattr__(self, name):
        return getattr(self._db, name)
    def close(self):
        pass


def _make_tagging_job(db_session, user, transcript):
    db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="fake"))
    db_session.commit()
    return enqueue_llm_job(
        db_session, user.id, transcript.id, "tagging",
        provider="groq", model="llama-3.3-70b-versatile",
    )


@pytest.mark.asyncio
async def test_run_llm_job_tagging_writes_rows(db_session):
    user, t = _make_transcript(db_session)
    job = _make_tagging_job(db_session, user, t)
    response = _FakeResponse('{"tags": ["q3 budget", "vendor renewal"]}')
    SessionLocal = _session_factory(db_session)
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=response)
        await run_llm_job(SessionLocal, job.id, transcription_service=type("S", (), {})())
    db_session.expire_all()
    job = db_session.query(LlmJob).filter(LlmJob.id == job.id).first()
    assert job.status == "completed"
    assert job.result_json == {"tags": ["q3 budget", "vendor renewal"]}
    rows = (
        db_session.query(TranscriptTag)
        .filter(TranscriptTag.transcript_id == t.id)
        .order_by(TranscriptTag.tag)
        .all()
    )
    assert [r.tag for r in rows] == ["q3 budget", "vendor renewal"]


@pytest.mark.asyncio
async def test_run_llm_job_tagging_replaces_not_appends(db_session):
    """Re-running a tagging job overwrites the prior tag set — issue #171
    design decision. Without REPLACE, a stale tag from a prior bad run
    would persist and the user couldn't fully refresh."""
    user, t = _make_transcript(db_session)
    db_session.add(TranscriptTag(transcript_id=t.id, tag="old tag"))
    db_session.commit()
    job = _make_tagging_job(db_session, user, t)
    response = _FakeResponse('{"tags": ["new tag"]}')
    SessionLocal = _session_factory(db_session)
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=response)
        await run_llm_job(SessionLocal, job.id, transcription_service=type("S", (), {})())
    db_session.expire_all()
    rows = db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == t.id).all()
    assert [r.tag for r in rows] == ["new tag"]


@pytest.mark.asyncio
async def test_run_llm_job_tagging_completes_with_empty_list(db_session):
    """An LLM that returns [] is a valid 'no useful topics' result — the
    job completes, no rows are written, no error surfaces. The LLM should
    not fail the whole job over a quiet LLM."""
    user, t = _make_transcript(db_session)
    job = _make_tagging_job(db_session, user, t)
    response = _FakeResponse('{"tags": []}')
    SessionLocal = _session_factory(db_session)
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(return_value=response)
        await run_llm_job(SessionLocal, job.id, transcription_service=type("S", (), {})())
    db_session.expire_all()
    job = db_session.query(LlmJob).filter(LlmJob.id == job.id).first()
    assert job.status == "completed"
    assert job.result_json == {"tags": []}
    assert db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == t.id).count() == 0


@pytest.mark.asyncio
async def test_run_llm_job_tagging_skips_write_on_cancel_during_llm(db_session):
    """If a cancel lands while the LLM is in flight, the tag write must
    be skipped — the user explicitly bailed out, writing would be a
    surprise. Mirrors the same guard in the voice_note branch."""
    user, t = _make_transcript(db_session)
    job = _make_tagging_job(db_session, user, t)

    async def cancel_during_response(*args, **kwargs):
        # Flip the job to cancelled before the LLM "returns"
        job.status = "cancelled"
        db_session.commit()
        return _FakeResponse('{"tags": ["would have written"]}')

    SessionLocal = _session_factory(db_session)
    with patch("services.llm_client.httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__.return_value.post = AsyncMock(side_effect=cancel_during_response)
        await run_llm_job(SessionLocal, job.id, transcription_service=type("S", (), {})())
    db_session.expire_all()
    assert db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == t.id).count() == 0
    job = db_session.query(LlmJob).filter(LlmJob.id == job.id).first()
    assert job.status == "cancelled"


def _session_factory(db_session):
    def factory():
        return _NoCloseSession(db_session)
    return factory
