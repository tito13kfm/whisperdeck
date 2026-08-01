"""Dispatch-block conversion (issue #268, design decision 11's app.py:1269-1277
row, mirrored in services/queue.py:565-585): correction now runs unconditional
of kind (gated only by the auto_correct setting), the voice-note chain is
gated on effective_kind() instead of raw kind, and auto_correct being off no
longer strands a kind='auto' transcript in classification_status='pending'
forever (issue #268 comment 2's gap) -- classify_pipeline gets triggered
immediately in that case since correction-completion (the usual trigger)
will never fire."""
import io
from unittest.mock import AsyncMock, patch

import pytest

from database import LlmJob, Transcript, TranscriptionJob, User
from services.diarization import DiarizationService
from services.queue import _finalize_if_done


def _testuser(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _upload(client, kind, auto_correct=None):
    async def _stub_transcribe(db, user_id, **kwargs):
        t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello world")
        db.add(t)
        db.commit()
        return t
    data = {"provider": "moonshine", "kind": kind}
    if auto_correct is not None:
        data["auto_correct"] = str(auto_correct).lower()
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("m.mp3", io.BytesIO(b"x"), "audio/mpeg")},
            data=data,
        )


# ── inline path (app.py) ─────────────────────────────────────────────────


def test_explicit_voice_note_upload_now_also_enqueues_correction(client, db_session):
    """Design decision 11: correction is no longer skipped for voice_note --
    it runs unconditionally (still gated by auto_correct), a deliberate
    behavior change from today's kind-exclusive dispatch."""
    resp = _upload(client, "voice_note")
    tid = resp.json()["id"]
    kinds = [k for (k,) in db_session.query(LlmJob.kind).filter(LlmJob.transcript_id == tid).all()]
    assert "correction" in kinds


def test_auto_kind_with_auto_correct_off_triggers_classify_pipeline_immediately(client, db_session):
    """auto_correct off means correction never runs, so the usual trigger
    (correction-job completion) never fires -- classify_pipeline must be
    triggered directly at dispatch time instead, or a kind='auto' upload
    would sit classification_status='pending' forever."""
    resp = _upload(client, "auto", auto_correct=False)
    tid = resp.json()["id"]
    kinds = [k for (k,) in db_session.query(LlmJob.kind).filter(LlmJob.transcript_id == tid).all()]
    assert "correction" not in kinds
    assert "classify_pipeline" in kinds


def test_explicit_kind_with_auto_correct_off_does_not_trigger_classify_pipeline(client, db_session):
    """An explicit kind is already an override (classification_status is
    never 'pending') -- the auto_correct-off fallback must no-op, matching
    enqueue_pipeline_classify's own status guard."""
    resp = _upload(client, "meeting", auto_correct=False)
    tid = resp.json()["id"]
    kinds = [k for (k,) in db_session.query(LlmJob.kind).filter(LlmJob.transcript_id == tid).all()]
    assert "classify_pipeline" not in kinds


def test_auto_kind_upload_does_not_immediately_enqueue_voice_note_chain(client, db_session):
    """A pending (unclassified) transcript must never be treated as
    voice_note -- effective_kind() returns None while pending."""
    resp = _upload(client, "auto")
    tid = resp.json()["id"]
    kinds = [k for (k,) in db_session.query(LlmJob.kind).filter(LlmJob.transcript_id == tid).all()]
    assert "voice_note" not in kinds


# ── chunked path parity (services/queue.py) ──────────────────────────────


def _chunked_transcript(db_session, kind="meeting", classification_status=None, auto_correct=True):
    user = User(username=f"chunked_{kind}_{auto_correct}", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    if auto_correct is not True:
        user.settings = {"auto_correct": auto_correct}
        db_session.commit()
    fields = dict(user_id=user.id, title="t", filename="f.mp3", kind=kind, status="processing")
    if classification_status is not None:
        fields["classification_status"] = classification_status
    t = Transcript(**fields)
    db_session.add(t)
    db_session.commit()
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="completed",
        result_json={
            "segments": [{"start": 0, "end": 5, "text": "hello", "speaker": None, "confidence": None}],
            "language": "en", "model": "whisper-large-v3",
        },
    ))
    db_session.commit()
    return t


@pytest.mark.asyncio
async def test_chunked_voice_note_finalize_also_enqueues_correction(db_session):
    t = _chunked_transcript(db_session, kind="voice_note")
    await _finalize_if_done(db_session, t.id, DiarizationService())
    kinds = [k for (k,) in db_session.query(LlmJob.kind).filter(LlmJob.transcript_id == t.id).all()]
    assert "correction" in kinds


@pytest.mark.asyncio
async def test_chunked_auto_kind_with_auto_correct_off_triggers_classify_pipeline(db_session):
    """Parity with the inline path -- both completion paths funnel through
    the same fallback so a chunked (long-recording) auto-kind upload with
    auto_correct off doesn't get stuck pending either."""
    t = _chunked_transcript(db_session, kind="meeting", classification_status="pending", auto_correct=False)
    await _finalize_if_done(db_session, t.id, DiarizationService())
    kinds = [k for (k,) in db_session.query(LlmJob.kind).filter(LlmJob.transcript_id == t.id).all()]
    assert "correction" not in kinds
    assert "classify_pipeline" in kinds
