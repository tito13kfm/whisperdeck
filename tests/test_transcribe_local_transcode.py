import io
from unittest.mock import AsyncMock, patch


def _transcode_mock():
    return AsyncMock(side_effect=lambda path, *a, **k: path)


def test_local_provider_webm_gets_transcoded(client):
    """Browser live capture uploads webm/opus, which libsndfile-backed local
    providers can't open — the ffmpeg pass must run for them too."""
    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("live_capture_2108.webm", io.BytesIO(b"fake webm bytes"), "audio/webm")},
            data={"provider": "moonshine"},
        )

    assert response.status_code == 200
    fake_transcode.assert_awaited_once()


def test_local_provider_wav_skips_transcode(client):
    """Formats local providers read natively keep the no-transcode fast path."""
    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
            data={"provider": "moonshine"},
        )

    assert response.status_code == 200
    fake_transcode.assert_not_awaited()


def test_hosted_provider_still_transcodes(client):
    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
            data={"provider": "groq"},
        )

    assert response.status_code == 200
    fake_transcode.assert_awaited_once()


async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello world")
    db.add(t)
    db.commit()
    return t


def test_transcript_stub_persists_video_path(db_session):
    from services.transcription import TranscriptionService
    svc = TranscriptionService()
    t = svc.create_transcript_stub(
        db_session, user_id=1, filename="f.mp4", provider_name="groq", model="",
        language="en", audio_path="/tmp/f_16k.mp3", diarize_requested=False,
        video_path="/tmp/f.mp4",
    )
    assert t.video_path == "/tmp/f.mp4"


def test_transcript_stub_video_path_defaults_none(db_session):
    from services.transcription import TranscriptionService
    svc = TranscriptionService()
    t = svc.create_transcript_stub(
        db_session, user_id=1, filename="f.mp3", provider_name="groq", model="",
        language="en", audio_path="/tmp/f_16k.mp3", diarize_requested=False,
    )
    assert t.video_path is None
