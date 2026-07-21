"""Dictation reformatting: services/reformatting.py's per-target LLM calls,
their LlmJob dispatch wiring, and the /api/transcripts/{id}/format/{target}
route — mirrors the shape of test_summarize_local_provider.py /
test_llm_jobs.py for the existing summarize/correction features."""
import asyncio
import io
import json
from unittest.mock import AsyncMock, patch

import pytest

from backends import ProviderError
from database import Transcript, User
from services.llm_jobs import enqueue_llm_job, run_llm_job
from services.reformatting import (
    format_as_markdown, format_as_email, format_as_coding_prompt, classify_intent,
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


def _make_user_and_dictation(db_session):
    user = User(username="dictator", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="f.mp3", status="completed",
        full_text="remind me to email dave about the budget numbers", segments=[],
        kind="dictation",
    )
    db_session.add(t)
    db_session.commit()
    return user, t


@pytest.mark.parametrize("fn", [format_as_markdown, format_as_email, format_as_coding_prompt])
def test_format_functions_return_generated_text(db_session, fn):
    user, t = _make_user_and_dictation(db_session)
    fake_post = AsyncMock(return_value=_chat_response("generated output"))
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(fn(
            t, api_key="", provider_name="local",
            provider_config={"api_url": "http://box:8080/v1"}, model="llama3",
        ))
    assert result == "generated output"


def test_format_function_raises_on_unsupported_provider(db_session):
    user, t = _make_user_and_dictation(db_session)
    with pytest.raises(ProviderError, match="does not support provider 'replicate'"):
        asyncio.run(format_as_markdown(t, api_key="k", provider_name="replicate"))


def test_format_function_raises_clear_error_when_cut_off(db_session):
    user, t = _make_user_and_dictation(db_session)
    fake_post = AsyncMock(return_value=_chat_response("truncated...", finish_reason="length"))
    with patch("httpx.AsyncClient.post", fake_post):
        with pytest.raises(ProviderError, match="cut off"):
            asyncio.run(format_as_email(t, api_key="", provider_name="local", model="llama3"))


def test_format_function_error_text_pinned(db_session):
    """Pins reformatting's own http_error_label/truncation_message wording
    (new copy, no prior text to preserve — but should stay stable now that
    it's decided) rather than genericizing to llm_client's shared defaults."""
    user, t = _make_user_and_dictation(db_session)

    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        with pytest.raises(ProviderError) as exc_info:
            asyncio.run(format_as_markdown(t, api_key="", provider_name="local", model="llama3"))
    assert str(exc_info.value).startswith("Reformatting API error (500):")

    fake_post_cut = AsyncMock(return_value=_chat_response("truncated...", finish_reason="length"))
    with patch("httpx.AsyncClient.post", fake_post_cut):
        with pytest.raises(ProviderError) as exc_info:
            asyncio.run(format_as_markdown(t, api_key="", provider_name="local", model="llama3"))
    assert str(exc_info.value) == (
        "Reformatting was cut off (model hit its token/context limit) — "
        "try a shorter recording or a model with a larger context window."
    )


def test_classify_intent_returns_label(db_session):
    user, t = _make_user_and_dictation(db_session)
    fake_post = AsyncMock(return_value=_chat_response('{"format": "email"}'))
    with patch("httpx.AsyncClient.post", fake_post):
        label = asyncio.run(classify_intent(t, api_key="", provider_name="local", model="llama3"))
    assert label == "email"


def test_classify_intent_falls_back_to_none_on_bad_json(db_session):
    user, t = _make_user_and_dictation(db_session)
    fake_post = AsyncMock(return_value=_chat_response("not json"))
    with patch("httpx.AsyncClient.post", fake_post):
        label = asyncio.run(classify_intent(t, api_key="", provider_name="local", model="llama3"))
    assert label == "none"


def test_classify_intent_falls_back_to_none_on_api_error(db_session):
    user, t = _make_user_and_dictation(db_session)
    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        label = asyncio.run(classify_intent(t, api_key="", provider_name="local", model="llama3"))
    assert label == "none"


class _NoCloseSession:
    """run_llm_job closes its session; tests share one — swallow the close."""
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


@pytest.mark.parametrize("kind,expected_key", [
    ("format_markdown", "text"),
    ("format_email", "text"),
    ("format_coding_prompt", "text"),
])
def test_run_llm_job_format_kinds_save_result_snapshot(db_session, kind, expected_key):
    user, t = _make_user_and_dictation(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, kind, "local_llm", "llama3")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_chat_response("generated output"))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.result_json == {expected_key: "generated output"}


def test_run_llm_job_classify_intent_saves_result_snapshot(db_session):
    user, t = _make_user_and_dictation(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "classify_intent", "local_llm", "llama3")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_chat_response('{"format": "coding_prompt"}'))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.result_json == {"format": "coding_prompt"}


# ── routes ────────────────────────────────────────────────────────────────

def _upload(client, kind="meeting"):
    async def _stub_transcribe(db, user_id, **kwargs):
        t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello")
        db.add(t)
        db.commit()
        return t
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("m.mp3", io.BytesIO(b"x"), "audio/mpeg")},
            data={"provider": "groq", "kind": kind},
        )


def test_upload_persists_dictation_kind(client):
    r = _upload(client, kind="dictation")
    assert r.json()["kind"] == "dictation"


def test_upload_rejects_unknown_kind(client):
    r = _upload(client, kind="bogus")
    assert r.status_code == 400


def test_format_route_enqueues_job(client):
    transcript_id = _upload(client, kind="dictation").json()["id"]
    r = client.post(
        f"/api/transcripts/{transcript_id}/format/markdown",
        data={"provider": "local_llm", "model": "llama3"},
    )
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["kind"] == "format_markdown"
    assert job["status"] == "pending"

    detail = client.get(f"/api/transcripts/{transcript_id}").json()
    assert detail["format_markdown_job"]["id"] == job["id"]


def test_format_route_rejects_unknown_target(client):
    transcript_id = _upload(client, kind="dictation").json()["id"]
    r = client.post(f"/api/transcripts/{transcript_id}/format/bogus", data={"provider": "local_llm"})
    assert r.status_code == 400


def test_runs_endpoint_accepts_new_format_kinds(client):
    # Dictation upload auto-enqueues a classify_intent job (see
    # enqueue_auto_classify), so that kind alone has one run already.
    transcript_id = _upload(client, kind="dictation").json()["id"]
    for kind in ("format_markdown", "format_email", "format_coding_prompt"):
        r = client.get(f"/api/transcripts/{transcript_id}/runs/{kind}")
        assert r.status_code == 200
        assert r.json()["runs"] == []

    r = client.get(f"/api/transcripts/{transcript_id}/runs/classify_intent")
    assert r.status_code == 200
    assert len(r.json()["runs"]) == 1


def test_dictation_upload_auto_enqueues_classify_intent_job(client):
    detail = _upload(client, kind="dictation").json()
    assert detail["classify_intent_hint"] is None  # job is pending, not completed yet

    runs = client.get(f"/api/transcripts/{detail['id']}/runs/classify_intent").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "pending"


def test_meeting_upload_does_not_enqueue_classify_intent_job(client):
    transcript_id = _upload(client, kind="meeting").json()["id"]
    runs = client.get(f"/api/transcripts/{transcript_id}/runs/classify_intent").json()["runs"]
    assert runs == []


def test_format_route_rejects_meeting_transcript(client):
    """A meeting transcript accepting a format job would burn an LLM call
    into a result no UI surface can show (only the Format tab renders it,
    and only for kind == 'dictation') — the route must reject it, matching
    the UI contract."""
    transcript_id = _upload(client, kind="meeting").json()["id"]
    r = client.post(f"/api/transcripts/{transcript_id}/format/markdown", data={"provider": "local_llm"})
    assert r.status_code == 400


def test_rediarize_rejects_dictation_transcript(client):
    """Re-diarizing a dictation transcript would persist diarize_requested
    True, which then silently turns diarization back on for a future
    re-transcribe (see _run_transcription_pipeline's kind==dictation
    override) while summarize keeps using the single-speaker prompt keyed
    off kind — a self-reinforcing inconsistency the route must block."""
    transcript_id = _upload(client, kind="dictation").json()["id"]
    r = client.post(f"/api/transcripts/{transcript_id}/rediarize")
    assert r.status_code == 400


def test_voicematch_rejects_dictation_transcript(client):
    transcript_id = _upload(client, kind="dictation").json()["id"]
    r = client.post(f"/api/transcripts/{transcript_id}/voice-match")
    assert r.status_code == 400


def _fake_chunks(n):
    return [
        {"index": i, "path": f"fake_chunk_{i}.mp3", "start_time": i * 300.0, "end_time": (i + 1) * 300.0}
        for i in range(n)
    ]


def test_transcribe_forces_diarize_false_server_side_for_dictation(client, db_session):
    """The client already forces diarize=false for dictation at submit
    time, but a direct API call could send diarize=true — the server must
    not trust it. Routed through the chunked path (get_audio_duration
    mocked long) since that's the branch that actually persists
    diarize_requested onto the Transcript row for a direct assertion."""
    with patch("app.get_audio_duration", return_value=1800.0), \
         patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.chunk_audio", AsyncMock(return_value=_fake_chunks(2))), \
         patch("os.path.getsize", return_value=1_000_000):
        r = client.post(
            "/api/transcribe",
            files={"file": ("dictation.wav", io.BytesIO(b"fake"), "audio/wav")},
            data={"provider": "moonshine", "kind": "dictation", "diarize": "true"},
        )
    assert r.status_code == 200
    transcript_id = r.json()["id"]
    t = db_session.query(Transcript).filter(Transcript.id == transcript_id).first()
    assert t.kind == "dictation"
    assert t.diarize_requested is False


def test_meeting_transcript_serializer_omits_dictation_job_fields(client):
    """Meeting transcripts can never have format_*/classify_intent jobs —
    the serializer should short-circuit to None for all of them instead of
    issuing the lookup queries (see _dictation_job_fields)."""
    transcript_id = _upload(client, kind="meeting").json()["id"]
    detail = client.get(f"/api/transcripts/{transcript_id}").json()
    assert detail["format_markdown_job"] is None
    assert detail["format_email_job"] is None
    assert detail["format_coding_prompt_job"] is None
    assert detail["classify_intent_job"] is None
    assert detail["classify_intent_hint"] is None


def test_dictation_transcript_exposes_classify_intent_job_while_pending(client):
    """Regression: the Suggested-badge poll needs a classify_intent_job
    field to detect the job is still in flight — classify_intent_hint alone
    stays null until completion, which previously meant the polling
    condition never started for the common upload-then-open-detail flow."""
    detail = _upload(client, kind="dictation").json()
    assert detail["classify_intent_job"] is not None
    assert detail["classify_intent_job"]["status"] == "pending"
