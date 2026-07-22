import io
import os
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
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed",
                   full_text="hello world", video_path=kwargs.get("video_path"))
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


def test_video_upload_persists_video_path(client, db_session):
    """audio_path/video_path are always absolute — UPLOAD_DIR is built from
    BASE_DIR = Path(__file__).parent.resolve() (app.py:49), and save_path =
    UPLOAD_DIR / safe_name (app.py:611) inherits that. video_path must be
    the raw upload's own path (with its original .mp4 extension), NOT the
    transcoded output — the mocked transcode returns the input path
    unchanged (`_transcode_mock`, line 5-6), so this also implicitly checks
    that has_video_stream's raw_path capture happens before transcode
    reassigns save_path, not after."""
    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.has_video_stream", return_value=True), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp4", io.BytesIO(b"fake mp4 bytes"), "video/mp4")},
            data={"provider": "moonshine"},
        )
    assert response.status_code == 200
    from database import Transcript
    saved = db_session.query(Transcript).order_by(Transcript.id.desc()).first()
    assert saved.video_path is not None
    assert os.path.isabs(saved.video_path)
    assert saved.video_path.endswith(".mp4")


def test_audio_only_upload_has_no_video_path(client, db_session):
    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.has_video_stream", return_value=False), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
            data={"provider": "moonshine"},
        )
    assert response.status_code == 200
    from database import Transcript
    saved = db_session.query(Transcript).order_by(Transcript.id.desc()).first()
    assert saved.video_path is None


def test_inline_upload_persists_requested_num_speakers(client, db_session):
    """Regression test: the inline (non-chunked) branch of
    _run_transcription_pipeline must persist the user's requested
    num_speakers onto the transcript row, mirroring what the chunked branch
    already does via create_transcript_stub (app.py ~line 785). Without the
    fix, num_speakers stayed None even when the form explicitly requested a
    count, so the re-diarize picker had nothing to prefill."""
    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
            data={"provider": "moonshine", "diarize": "true", "num_speakers": "2"},
        )
    assert response.status_code == 200
    from database import Transcript
    saved = db_session.query(Transcript).order_by(Transcript.id.desc()).first()
    assert saved.num_speakers == 2


def test_retranscribe_carries_forward_parent_video_path(client, db_session, tmp_path):
    """Parent's stored audio must actually exist on disk and transcode must
    be mocked — _run_transcription_pipeline probes duration and file size
    on the incoming path (app.py:461, app.py:494) before the has_video_stream
    branch even runs, so a missing file or a real ffmpeg call both blow up
    this test for reasons unrelated to what it's testing."""
    from database import Transcript
    parent_audio = tmp_path / "p_16k.mp3"
    parent_audio.write_bytes(b"fake mp3 bytes")
    parent = Transcript(user_id=1, title="p", filename="p.mp4", status="completed",
                        full_text="x", audio_path=str(parent_audio), video_path="/tmp/p.mp4")
    db_session.add(parent)
    db_session.commit()

    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.has_video_stream", return_value=False), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(f"/api/transcripts/{parent.id}/retranscribe",
                                data={"provider": "groq"})
    assert response.status_code == 200
    saved = db_session.query(Transcript).order_by(Transcript.id.desc()).first()
    assert saved.id != parent.id
    assert saved.video_path == "/tmp/p.mp4"
