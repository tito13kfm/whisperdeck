import io
from unittest.mock import AsyncMock, patch


async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello world")
    db.add(t)
    db.commit()
    return t


def _upload(client, provider="groq"):
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data={"provider": provider},
        )


def test_auto_correct_runs_after_inline_transcription(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    fake_correct = AsyncMock()

    async def _fake_correct(db, transcript, **kwargs):
        transcript.corrected_text = "Hello world."
        transcript.correction_model = "groq/llama-3.3-70b-versatile"
        db.commit()

    with patch("app.correct_transcript", side_effect=_fake_correct):
        response = _upload(client)

    assert response.status_code == 200
    body = response.json()
    assert body["corrected_text"] == "Hello world."
    assert body["correction_model"] == "groq/llama-3.3-70b-versatile"


def test_auto_correct_skipped_when_setting_disabled(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": False})
    fake_correct = AsyncMock()

    with patch("app.correct_transcript", fake_correct):
        response = _upload(client)

    assert response.status_code == 200
    fake_correct.assert_not_awaited()


def test_manual_correct_endpoint_reruns_with_different_model(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})

    async def _fake_correct(db, transcript, api_key, provider_name="groq", model="llama-3.3-70b-versatile"):
        transcript.corrected_text = f"corrected by {model}"
        transcript.correction_model = f"{provider_name}/{model}"
        db.commit()

    with patch("app.correct_transcript", side_effect=_fake_correct):
        upload_response = _upload(client)
        transcript_id = upload_response.json()["id"]

        rerun_response = client.post(
            f"/api/transcripts/{transcript_id}/correct",
            data={"provider": "groq", "model": "llama-3.1-8b-instant"},
        )

    assert rerun_response.status_code == 200
    body = rerun_response.json()
    assert body["corrected_text"] == "corrected by llama-3.1-8b-instant"
    assert body["correction_model"] == "groq/llama-3.1-8b-instant"


def test_manual_correct_requires_completed_transcript(client):
    from database import Transcript

    response = client.post("/api/transcripts/99999/correct", data={"provider": "groq"})
    assert response.status_code == 404
