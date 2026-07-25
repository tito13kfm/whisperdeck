import io
from unittest.mock import AsyncMock, patch


async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello world")
    db.add(t)
    db.commit()
    return t


def _upload(client, provider="groq", auto_correct=None):
    data = {"provider": provider}
    if auto_correct is not None:
        data["auto_correct"] = "true" if auto_correct else "false"
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data=data,
        )


def test_auto_correct_enqueues_job_after_inline_transcription(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})

    response = _upload(client)
    assert response.status_code == 200
    transcript_id = response.json()["id"]

    # inline path queues a background LlmJob instead of blocking the response
    body = response.json()
    assert body["correction_job"] is not None
    assert body["correction_job"]["kind"] == "correction"
    assert body["correction_job"]["status"] == "pending"
    assert body["correction_job"]["transcript_id"] == transcript_id


def test_auto_correct_skipped_when_setting_disabled(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": False})

    response = _upload(client)

    assert response.status_code == 200
    assert response.json()["correction_job"] is None


def test_auto_correct_per_job_false_overrides_global_true(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": True})

    response = _upload(client, auto_correct=False)

    assert response.status_code == 200
    assert response.json()["correction_job"] is None


def test_auto_correct_field_omitted_falls_back_to_global_setting(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": False})

    # no auto_correct field sent at all — caller predates the per-job toggle
    response = _upload(client)

    assert response.status_code == 200
    assert response.json()["correction_job"] is None


def test_manual_correct_endpoint_enqueues_job(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": False})

    upload_response = _upload(client)
    transcript_id = upload_response.json()["id"]

    rerun_response = client.post(
        f"/api/transcripts/{transcript_id}/correct",
        data={"provider": "groq", "model": "llama-3.1-8b-instant"},
    )

    assert rerun_response.status_code == 200
    job = rerun_response.json()["job"]
    assert job["kind"] == "correction"
    assert job["status"] == "pending"
    assert job["model"] == "llama-3.1-8b-instant"

    # enqueueing again while a job is active returns the same job, not a dupe
    again = client.post(
        f"/api/transcripts/{transcript_id}/correct",
        data={"provider": "groq", "model": "llama-3.1-8b-instant"},
    )
    assert again.json()["job"]["id"] == job["id"]


def test_manual_correct_requires_completed_transcript(client):
    from database import Transcript

    response = client.post("/api/transcripts/99999/correct", data={"provider": "groq"})
    assert response.status_code == 404
