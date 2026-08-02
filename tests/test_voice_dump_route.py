"""Voice-dump routes (issue #285): /api/transcribe accepts kind=voice_dump,
/api/transcripts/{id}/voice-dump/rerun re-queues the chain,
/api/transcripts/{id}/voice-dump/save-draft patches the job's result_json,
/api/transcripts/{id}/voice-dump/finalize inserts VoiceDumpItem rows,
/api/transcripts/{id}/voice-dump-items lists items for one transcript,
/api/voice-dump-items lists items across all transcripts.
Mirrors test_voice_note_route.py for the existing voice_note flow."""
import io
import json
from unittest.mock import AsyncMock, patch

from database import Transcript, User, VoiceDumpItem, LlmJob


def _testuser(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _make_voice_dump_transcript(db_session, full_text="app has bug: login page crashes on empty email"):
    user = _testuser(db_session)
    t = Transcript(
        user_id=user.id, title="vd", filename="f.mp3", status="completed",
        full_text=full_text, segments=[], kind="voice_dump",
    )
    db_session.add(t)
    db_session.commit()
    return user, t


def _upload_voice_dump(client):
    async def _stub_transcribe(db, user_id, **kwargs):
        t = Transcript(
            user_id=user_id, title="t", filename="f.mp3", status="completed",
            full_text="hello world", kind="voice_dump",
        )
        db.add(t)
        db.commit()
        return t
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("m.mp3", io.BytesIO(b"x"), "audio/mpeg")},
            data={"provider": "groq", "kind": "voice_dump"},
        )


# ── upload kind handling ────────────────────────────────────────────────


def test_upload_persists_voice_dump_kind(client):
    r = _upload_voice_dump(client)
    assert r.status_code == 200
    assert r.json()["kind"] == "voice_dump"


def test_voice_dump_upload_enqueues_voice_dump_job(client):
    detail = _upload_voice_dump(client).json()
    runs = client.get(f"/api/transcripts/{detail['id']}/runs/voice_dump").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] in ("pending", "failed")


# ── voice_dump_job field on serialized transcript ───────────────────────


def test_serialize_voice_dump_transcript_has_voice_dump_job_field(client):
    detail = _upload_voice_dump(client).json()
    assert "voice_dump_job" in detail
    assert detail["voice_dump_job"] is not None
    assert detail["voice_dump_job"]["kind"] == "voice_dump"
    # The other kind-specific fields must still be null (uniform shape).
    assert detail["format_markdown_job"] is None
    assert detail["format_email_job"] is None
    assert detail["format_coding_prompt_job"] is None
    assert detail["classify_intent_job"] is None
    assert detail["classify_intent_hint"] is None
    assert detail["voice_note_job"] is None


# ── GET /api/transcripts/{id}/voice-dump-items ──────────────────────────


def test_get_voice_dump_items_empty(client, db_session):
    user, t = _make_voice_dump_transcript(db_session)
    r = client.get(f"/api/transcripts/{t.id}/voice-dump-items")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_get_voice_dump_items_returns_rows_after_finalize(client, db_session):
    user, t = _make_voice_dump_transcript(db_session)
    db_session.add(VoiceDumpItem(
        user_id=user.id, transcript_id=t.id, sequence_index=0,
        note_type="bug", title="Login crash", body="Empty email crashes login.",
        structured={}, model="llama3", provider="groq",
    ))
    db_session.add(VoiceDumpItem(
        user_id=user.id, transcript_id=t.id, sequence_index=1,
        note_type="idea", title="Dark mode", body="Add dark mode toggle.",
        structured={}, model="llama3", provider="groq",
    ))
    db_session.commit()

    r = client.get(f"/api/transcripts/{t.id}/voice-dump-items")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert items[0]["note_type"] == "bug"
    assert items[0]["sequence_index"] == 0
    assert items[1]["note_type"] == "idea"
    assert items[1]["sequence_index"] == 1


def test_get_voice_dump_items_404_for_other_users_transcript(client, db_session):
    other = User(username="other_vd", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(
        user_id=other.id, title="vdot", filename="f.mp3", status="completed",
        full_text="x", segments=[], kind="voice_dump",
    )
    db_session.add(t)
    db_session.commit()
    r = client.get(f"/api/transcripts/{t.id}/voice-dump-items")
    assert r.status_code == 404


# ── GET /api/voice-dump-items (board list) ──────────────────────────────


def test_list_voice_dump_items_empty(client):
    r = client.get("/api/voice-dump-items")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_list_voice_dump_items_returns_users_own_items(client, db_session):
    user, t = _make_voice_dump_transcript(db_session)
    db_session.add(VoiceDumpItem(
        user_id=user.id, transcript_id=t.id, sequence_index=0,
        note_type="todo", title="Things", body="Do these.",
        structured={"items": []}, model="llama3", provider="groq",
    ))
    db_session.commit()

    r = client.get("/api/voice-dump-items")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    n = items[0]
    assert n["note_type"] == "todo"
    assert n["transcript_title"] == "vd"
    assert n["transcript_status"] == "completed"


# ── POST /api/transcripts/{id}/voice-dump/rerun ───────────────────────────


def test_voice_dump_rerun_route_enqueues_new_job(client, db_session):
    from database import ProviderConfig
    user = _testuser(db_session)
    db_session.add(ProviderConfig(
        user_id=user.id, name="groq", api_key="sk-test-fake", is_active=True,
    ))
    db_session.commit()

    tid = _upload_voice_dump(client).json()["id"]
    r = client.post(
        f"/api/transcripts/{tid}/voice-dump/rerun",
        data={"provider": "groq", "model": "llama-3.3-70b-versatile"},
    )
    assert r.status_code == 200
    assert r.json()["job"]["kind"] == "voice_dump"
    assert r.json()["job"]["status"] == "pending"


def test_voice_dump_rerun_rejects_no_key(client):
    tid = _upload_voice_dump(client).json()["id"]
    r = client.post(
        f"/api/transcripts/{tid}/voice-dump/rerun",
        data={"provider": "groq"},
    )
    assert r.status_code == 400
    assert "no groq api key" in r.json()["detail"].lower()


def test_voice_dump_rerun_rejects_non_voice_dump(client, db_session):
    user = _testuser(db_session)
    meeting = Transcript(
        user_id=user.id, title="m", filename="m.mp3", status="completed",
        full_text="x", segments=[], kind="meeting",
    )
    db_session.add(meeting)
    db_session.commit()
    r = client.post(
        f"/api/transcripts/{meeting.id}/voice-dump/rerun",
        data={"provider": "groq"},
    )
    assert r.status_code == 400


def test_voice_dump_rerun_404_for_other_users_transcript(client, db_session):
    other = User(username="other_vd2", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(
        user_id=other.id, title="vdot", filename="f.mp3", status="completed",
        full_text="x", segments=[], kind="voice_dump",
    )
    db_session.add(t)
    db_session.commit()
    r = client.post(
        f"/api/transcripts/{t.id}/voice-dump/rerun",
        data={"provider": "groq"},
    )
    assert r.status_code == 404


# ── POST /api/transcripts/{id}/voice-dump/save-draft ─────────────────────


def test_save_draft_updates_job_result_json(client, db_session):
    user, t = _make_voice_dump_transcript(db_session)
    job = LlmJob(
        user_id=user.id, transcript_id=t.id, kind="voice_dump",
        provider="groq", model="llama3", status="pending",
        result_json={"items": [{"index": 0, "type": "bug", "title": "Old"}]},
    )
    db_session.add(job)
    db_session.commit()

    new_items = [
        {"index": 0, "type": "bug", "title": "Edited bug", "body": "Fixed it"},
        {"index": 1, "type": "idea", "title": "New idea"},
    ]
    r = client.post(
        f"/api/transcripts/{t.id}/voice-dump/save-draft",
        json=new_items,
    )
    assert r.status_code == 200
    assert r.json()["items"] == new_items

    # Verify the job was updated in DB
    db_session.refresh(job)
    assert job.result_json["items"] == new_items


def test_save_draft_404_no_job(client, db_session):
    user, t = _make_voice_dump_transcript(db_session)
    r = client.post(
        f"/api/transcripts/{t.id}/voice-dump/save-draft",
        json=[],
    )
    assert r.status_code == 404


def test_save_draft_404_other_users_transcript(client, db_session):
    other = User(username="other_vd3", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(
        user_id=other.id, title="vdot", filename="f.mp3", status="completed",
        full_text="x", segments=[], kind="voice_dump",
    )
    db_session.add(t)
    db_session.commit()
    r = client.post(
        f"/api/transcripts/{t.id}/voice-dump/save-draft",
        json=[],
    )
    assert r.status_code == 404


# ── POST /api/transcripts/{id}/voice-dump/finalize ───────────────────────


def test_finalize_inserts_rows_and_filters_discarded(client, db_session):
    user, t = _make_voice_dump_transcript(db_session)
    job = LlmJob(
        user_id=user.id, transcript_id=t.id, kind="voice_dump",
        provider="groq", model="llama3", status="completed",
        result_json={"items": []},
    )
    db_session.add(job)
    db_session.commit()
    items = [
        {"index": 0, "type": "bug", "title": "Bug one", "body": "desc", "structured": {}},
        {"index": 1, "type": "idea", "title": "Idea one", "body": "desc", "structured": {}, "discarded": True},
        {"index": 2, "type": "todo", "title": "Todo one", "body": "desc", "structured": {}},
    ]
    r = client.post(
        f"/api/transcripts/{t.id}/voice-dump/finalize",
        json=items,
    )
    assert r.status_code == 200
    result = r.json()["items"]
    assert len(result) == 2  # discarded item filtered out
    assert result[0]["note_type"] == "bug"
    assert result[1]["note_type"] == "todo"
    assert all(it["id"] is not None for it in result)
    assert all(it["source_job_id"] == job.id for it in result)

    # Verify DB rows
    rows = db_session.query(VoiceDumpItem).filter(
        VoiceDumpItem.transcript_id == t.id
    ).order_by(VoiceDumpItem.sequence_index).all()
    assert len(rows) == 2
    assert rows[0].note_type == "bug"
    assert rows[0].sequence_index == 0
    assert rows[0].source_job_id == job.id
    assert rows[1].note_type == "todo"
    assert rows[1].sequence_index == 1  # original index preserved
    assert rows[1].source_job_id == job.id


def test_finalize_all_discarded_returns_empty(client, db_session):
    user, t = _make_voice_dump_transcript(db_session)
    items = [
        {"index": 0, "type": "bug", "title": "Bug one", "discarded": True},
        {"index": 1, "type": "idea", "title": "Idea one", "discarded": True},
    ]
    r = client.post(
        f"/api/transcripts/{t.id}/voice-dump/finalize",
        json=items,
    )
    assert r.status_code == 200
    assert r.json()["items"] == []

    rows = db_session.query(VoiceDumpItem).filter(
        VoiceDumpItem.transcript_id == t.id
    ).all()
    assert rows == []


def test_finalize_404_other_users_transcript(client, db_session):
    other = User(username="other_vd4", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(
        user_id=other.id, title="vdot", filename="f.mp3", status="completed",
        full_text="x", segments=[], kind="voice_dump",
    )
    db_session.add(t)
    db_session.commit()
    r = client.post(
        f"/api/transcripts/{t.id}/voice-dump/finalize",
        json=[],
    )
    assert r.status_code == 404


# ── rejection routes (voice_dump is single-speaker like voice_note) ──────


def test_format_route_rejects_voice_dump(client):
    tid = _upload_voice_dump(client).json()["id"]
    r = client.post(
        f"/api/transcripts/{tid}/format/markdown",
        data={"provider": "local_llm", "model": "llama3"},
    )
    assert r.status_code == 400


def test_rediarize_route_rejects_voice_dump(client):
    tid = _upload_voice_dump(client).json()["id"]
    r = client.post(f"/api/transcripts/{tid}/rediarize")
    assert r.status_code == 400


def test_summarize_route_rejects_voice_dump(client):
    tid = _upload_voice_dump(client).json()["id"]
    r = client.post(f"/api/transcripts/{tid}/summarize", data={"provider": "groq"})
    assert r.status_code == 400


# ── runs/{kind} endpoint includes voice_dump ─────────────────────────────


def test_runs_endpoint_accepts_voice_dump_kind(client):
    tid = _upload_voice_dump(client).json()["id"]
    r = client.get(f"/api/transcripts/{tid}/runs/voice_dump")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) == 1
    assert "provider" in runs[0]
