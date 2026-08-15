"""Dictation reformatting: services/reformatting.py's per-target LLM calls,
their LlmJob dispatch wiring, and the /api/transcripts/{id}/format/{target}
route — mirrors the shape of test_summarize_local_provider.py /
test_llm_jobs.py for the existing summarize/correction features."""
import asyncio
import io
import json
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from backends import ProviderError
from database import Transcript, User
from services.audio_prep import DiarizationEligibility
from services.llm_jobs import enqueue_llm_job, run_llm_job
from services.reformatting import (
    format_as_markdown, format_as_email, format_as_coding_prompt, classify_intent,
    build_export_markdown,
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


def test_enqueue_auto_classify_no_ops_while_pending_even_if_kind_is_dictation(db_session):
    """effective_kind(), not raw kind, gates enqueue_auto_classify (design
    decision 11) -- a placeholder/stale kind value on a still-pending
    transcript must never trigger the dictation reformat hint."""
    from database import User
    from services.llm_jobs import enqueue_auto_classify
    user = User(username="pending_classify", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="t.mp3", status="completed",
        full_text="x", kind="dictation", classification_status="pending",
    )
    db_session.add(t)
    db_session.commit()
    assert enqueue_auto_classify(db_session, t, {}) is None


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


@pytest.mark.parametrize("kind", ["dictation", "voice_note", "voice_dump"])
def test_transcribe_forces_diarize_false_even_when_prepass_reports_eligible(client, db_session, kind):
    """Issue #416: the audio-feature pre-pass sits AFTER the kind veto in
    _run_transcription_pipeline and can only narrow eligibility further,
    never widen it back open. Patch the pre-pass to explicitly report the
    audio as eligible, so a pass here proves the kind veto -- not a lucky
    pre-pass rejection -- is what keeps these single-speaker kinds off."""
    with patch("app.get_audio_duration", return_value=1800.0), \
         patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.chunk_audio", AsyncMock(return_value=_fake_chunks(2))), \
         patch("app.evaluate_diarization_eligibility_async", new_callable=AsyncMock, return_value=DiarizationEligibility(True)), \
         patch("os.path.getsize", return_value=1_000_000):
        r = client.post(
            "/api/transcribe",
            files={"file": (f"{kind}.wav", io.BytesIO(b"fake"), "audio/wav")},
            data={"provider": "moonshine", "kind": kind, "diarize": "true"},
        )
    assert r.status_code == 200
    transcript_id = r.json()["id"]
    t = db_session.query(Transcript).filter(Transcript.id == transcript_id).first()
    assert t.kind == kind
    assert t.diarize_requested is False


def test_transcribe_prepass_forces_diarize_false_for_ineligible_meeting(client, db_session):
    """Issue #416: a meeting upload with diarize=true must end up with
    diarization off when the audio-feature pre-pass rejects it."""
    with patch("app.get_audio_duration", return_value=1800.0), \
         patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.chunk_audio", AsyncMock(return_value=_fake_chunks(2))), \
         patch("app.evaluate_diarization_eligibility_async", new_callable=AsyncMock,
               return_value=DiarizationEligibility(False, "too long a monologue")), \
         patch("os.path.getsize", return_value=1_000_000):
        r = client.post(
            "/api/transcribe",
            files={"file": ("meeting.wav", io.BytesIO(b"fake"), "audio/wav")},
            data={"provider": "moonshine", "kind": "meeting", "diarize": "true"},
        )
    assert r.status_code == 200
    transcript_id = r.json()["id"]
    t = db_session.query(Transcript).filter(Transcript.id == transcript_id).first()
    assert t.kind == "meeting"
    assert t.diarize_requested is False


def test_transcribe_prepass_keeps_diarize_true_for_eligible_meeting(client, db_session):
    """Complement of the test above: when the pre-pass reports the audio
    eligible, a meeting upload with diarize=true keeps diarization on --
    proves the pre-pass isn't just an unconditional veto."""
    with patch("app.get_audio_duration", return_value=1800.0), \
         patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.chunk_audio", AsyncMock(return_value=_fake_chunks(2))), \
         patch("app.evaluate_diarization_eligibility_async", new_callable=AsyncMock, return_value=DiarizationEligibility(True)), \
         patch("os.path.getsize", return_value=1_000_000):
        r = client.post(
            "/api/transcribe",
            files={"file": ("meeting.wav", io.BytesIO(b"fake"), "audio/wav")},
            data={"provider": "moonshine", "kind": "meeting", "diarize": "true"},
        )
    assert r.status_code == 200
    transcript_id = r.json()["id"]
    t = db_session.query(Transcript).filter(Transcript.id == transcript_id).first()
    assert t.kind == "meeting"
    assert t.diarize_requested is True


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


# ── build_export_markdown (issue #172) ────────────────────────────────────


def _make_segment(speaker, text):
    return {"start": 0.0, "end": 1.0, "speaker": speaker, "text": text}


def _make_export_transcript(db_session, title="Test Meeting", segments=None, full_text="", kind="meeting"):
    user = User(username="exporter", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title=title, filename="f.mp3", status="completed",
        full_text=full_text, segments=segments or [], kind=kind,
    )
    db_session.add(t)
    db_session.commit()
    return t


def _make_summary_dict():
    return {
        "short_summary": "A productive meeting about the Q3 roadmap.",
        "key_points": ["Launch date moved to September", "Budget approved for 3 hires"],
        "action_items": ["Alice: draft the launch timeline", "Bob: post the job listings"],
        "decisions": ["Go with vendor B for infrastructure", "Delay the mobile app to Q4"],
    }


class TestBuildExportMarkdown:
    def test_full_transcript_with_summary(self, db_session):
        t = _make_export_transcript(
            db_session,
            title="Q3 Roadmap",
            segments=[_make_segment("Alice", "We need to ship by September."),
                      _make_segment("Bob", "Budget approved for three new hires.")],
        )
        md = build_export_markdown(t, _make_summary_dict())
        assert md.startswith("# Q3 Roadmap")
        assert "## Transcript" in md
        assert "**Alice:** We need to ship by September." in md
        assert "**Bob:** Budget approved for three new hires." in md
        assert "## Summary" in md
        assert "Q3 roadmap" in md
        assert "## Key Points" in md
        assert "- Launch date moved to September" in md
        assert "- Budget approved for 3 hires" in md
        assert "## Action Items" in md
        assert "- [ ] Alice: draft the launch timeline" in md
        assert "- [ ] Bob: post the job listings" in md
        assert "## Decisions" in md
        assert "- Go with vendor B for infrastructure" in md

    def test_no_summary(self, db_session):
        t = _make_export_transcript(
            db_session,
            title="Standup",
            segments=[_make_segment("Alice", "Quick sync today.")],
        )
        md = build_export_markdown(t, None)
        assert "# Standup" in md
        assert "## Transcript" in md
        assert "**Alice:** Quick sync today." in md
        assert "## Summary" not in md
        assert "## Key Points" not in md
        assert "## Action Items" not in md
        assert "## Decisions" not in md

    def test_empty_summary_fields(self, db_session):
        t = _make_export_transcript(
            db_session,
            title="Empty Summary",
            segments=[_make_segment("Alice", "hi")],
        )
        empty_summary = {
            "short_summary": "",
            "key_points": [],
            "action_items": [],
            "decisions": [],
        }
        md = build_export_markdown(t, empty_summary)
        assert "## Summary" not in md
        assert "## Key Points" not in md
        assert "## Action Items" not in md
        assert "## Decisions" not in md

    def test_fallback_to_full_text(self, db_session):
        t = _make_export_transcript(
            db_session,
            title="No Segments",
            segments=[],
            full_text="This is the full text fallback content.",
        )
        md = build_export_markdown(t, None)
        assert "## Transcript" in md
        assert "This is the full text fallback content." in md

    def test_title_sanitization(self, db_session):
        t = _make_export_transcript(db_session, title="  ## Already a heading")
        md = build_export_markdown(t, None)
        assert md.startswith("# Already a heading")
        assert "## Already a heading" not in md

    def test_special_chars_pass_through(self, db_session):
        t = _make_export_transcript(
            db_session,
            title="Edge <Case>",
            segments=[_make_segment("Alice", "<script>alert(1)</script> & emoji 🦊")],
        )
        md = build_export_markdown(t, None)
        assert "<script>alert(1)</script> & emoji 🦊" in md


# ── export-markdown route (issue #172) ───────────────────────────────────


class TestExportMarkdownRoute:
    def test_export_success(self, client):
        tmpdir = tempfile.mkdtemp()
        try:
            r = client.put("/api/settings", json={"export_directory": tmpdir})
            assert r.status_code == 200
            transcript_id = _upload(client, kind="meeting").json()["id"]
            r = client.post(f"/api/transcripts/{transcript_id}/export-markdown")
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert body["path"].startswith(tmpdir)
            assert os.path.isfile(body["path"])
            with open(body["path"], "r", encoding="utf-8") as fp:
                content = fp.read()
            assert content.startswith("# ")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_export_no_directory_configured(self, client):
        client.put("/api/settings", json={"export_directory": ""})
        transcript_id = _upload(client, kind="meeting").json()["id"]
        r = client.post(f"/api/transcripts/{transcript_id}/export-markdown")
        assert r.status_code == 400
        assert "not configured" in r.json()["detail"].lower()

    def test_export_nonexistent_directory(self, client):
        client.put("/api/settings", json={"export_directory": "/definitely/does/not/exist/12345"})
        transcript_id = _upload(client, kind="meeting").json()["id"]
        r = client.post(f"/api/transcripts/{transcript_id}/export-markdown")
        assert r.status_code == 500
        assert "does not exist" in r.json()["detail"].lower()

    def test_export_transcript_not_found(self, client):
        tmpdir = tempfile.mkdtemp()
        try:
            client.put("/api/settings", json={"export_directory": tmpdir})
            r = client.post("/api/transcripts/99999/export-markdown")
            assert r.status_code == 404
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_export_transcript_not_completed(self, client, db_session):
        tmpdir = tempfile.mkdtemp()
        try:
            client.put("/api/settings", json={"export_directory": tmpdir})
            testuser = db_session.query(User).filter(User.username == "testuser").first()
            t = Transcript(
                user_id=testuser.id, title="Pending", filename="f.mp3",
                status="processing", full_text="", segments=[], kind="meeting",
            )
            db_session.add(t)
            db_session.commit()
            r = client.post(f"/api/transcripts/{t.id}/export-markdown")
            assert r.status_code == 400
            assert "not ready" in r.json()["detail"].lower()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_export_csrf_required(self, client, db_session):
        tmpdir = tempfile.mkdtemp()
        try:
            client.put("/api/settings", json={"export_directory": tmpdir})
            transcript_id = _upload(client, kind="meeting").json()["id"]
            old_token = client.headers["X-CSRF-Token"]
            client.headers["X-CSRF-Token"] = "definitely-not-a-real-token"
            try:
                r = client.post(f"/api/transcripts/{transcript_id}/export-markdown")
                assert r.status_code == 403
            finally:
                client.headers["X-CSRF-Token"] = old_token
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── settings round-trip for export_directory (issue #172) ─────────────────


class TestExportDirectorySettings:
    def test_roundtrip(self, client):
        r = client.put("/api/settings", json={"export_directory": "/home/user/vault"})
        assert r.status_code == 200
        assert r.json()["export_directory"] == "/home/user/vault"
        r = client.get("/api/settings")
        assert r.json()["export_directory"] == "/home/user/vault"
        r = client.put("/api/settings", json={"export_directory": ""})
        assert r.status_code == 200
        r = client.get("/api/settings")
        assert r.json()["export_directory"] == ""

    def test_default_is_empty(self, client):
        r = client.get("/api/settings")
        assert r.json()["export_directory"] == ""

    def test_bootstrap_includes_settings(self, client):
        client.put("/api/settings", json={"export_directory": "/tmp/x"})
        r = client.get("/api/bootstrap")
        assert r.status_code == 200
        body = r.json()
        assert body["settings"] is not None
        assert body["settings"]["export_directory"] == "/tmp/x"
