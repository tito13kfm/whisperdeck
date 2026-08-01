"""'auto' kind sentinel (issue #268, design decision 11): upload and
bulk-transcribe must accept 'auto' as a fourth valid kind value, meaning
"defer to the pipeline classifier" — the transcript is created with a
placeholder kind and classification_status='pending'. An explicit kind
(meeting/dictation/voice_note) is recorded as a manual override, same as
today's behavior, now made explicit via classification_status='override'
rather than relying on the column default."""
import io
import json
from unittest.mock import AsyncMock, patch

from database import Transcript


async def _stub_transcribe(db, user_id, **kwargs):
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello world")
    db.add(t)
    db.commit()
    return t


def _upload(client, db_session, kind):
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        resp = client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data={"provider": "moonshine", "kind": kind},
        )
    return resp


def test_transcribe_accepts_auto_kind_sentinel(client, db_session):
    resp = _upload(client, db_session, "auto")
    assert resp.status_code == 200
    t = db_session.query(Transcript).filter(Transcript.id == resp.json()["id"]).first()
    assert t.classification_status == "pending"


def test_transcribe_explicit_kind_records_override(client, db_session):
    resp = _upload(client, db_session, "dictation")
    assert resp.status_code == 200
    t = db_session.query(Transcript).filter(Transcript.id == resp.json()["id"]).first()
    assert t.kind == "dictation"
    assert t.classification_status == "override"


def test_transcribe_still_rejects_unknown_kind(client, db_session):
    resp = _upload(client, db_session, "podcast")
    assert resp.status_code == 400


def test_bulk_transcribe_accepts_auto_kind_global(client):
    settings = json.dumps({"provider": "moonshine", "kind": "auto"})
    files = [("files", ("a.mp3", io.BytesIO(b"fake audio"), "audio/mpeg"))]
    with patch("app._run_transcription_pipeline", new_callable=AsyncMock) as mock_pipeline:
        mock_pipeline.return_value = {"id": 1, "kind": "meeting", "batch_id": "B1"}
        resp = client.post("/api/bulk-transcribe", data={"settings": settings}, files=files)
    assert resp.status_code == 200
    assert mock_pipeline.call_args.kwargs["kind"] == "auto"


def test_bulk_transcribe_accepts_auto_kind_per_file_override(client):
    settings = json.dumps({"provider": "moonshine", "kind": "meeting"})
    file_settings = json.dumps([{"kind": "auto"}])
    files = [("files", ("a.mp3", io.BytesIO(b"fake audio"), "audio/mpeg"))]
    with patch("app._run_transcription_pipeline", new_callable=AsyncMock) as mock_pipeline:
        mock_pipeline.return_value = {"id": 1, "kind": "meeting", "batch_id": "B1"}
        resp = client.post(
            "/api/bulk-transcribe",
            data={"settings": settings, "file_settings": file_settings},
            files=files,
        )
    assert resp.status_code == 200
    assert mock_pipeline.call_args.kwargs["kind"] == "auto"
