import io
from unittest.mock import AsyncMock, patch


def test_transcribe_with_context_doc_extracts_hotwords(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})

    fake_extract = AsyncMock(return_value=["Acme Corp"])
    with patch("app.extract_hotwords_from_doc", fake_extract), \
         patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data={"provider": "groq", "context_doc": "Agenda: Acme Corp kickoff"},
        )

    assert response.status_code == 200
    fake_extract.assert_awaited_once()
    call_kwargs = fake_extract.await_args.kwargs
    assert call_kwargs.get("doc_text") == "Agenda: Acme Corp kickoff" or fake_extract.await_args.args[2] == "Agenda: Acme Corp kickoff"


def test_transcribe_without_context_doc_skips_extraction(client):
    fake_extract = AsyncMock()
    with patch("app.extract_hotwords_from_doc", fake_extract), \
         patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data={"provider": "groq"},
        )

    assert response.status_code == 200
    fake_extract.assert_not_awaited()


async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello world")
    db.add(t)
    db.commit()
    return t
