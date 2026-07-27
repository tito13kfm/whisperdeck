"""Voice-note routes (issue #169): /api/transcribe accepts kind=voice_note,
/api/transcripts/{id}/voice-note fetches the structured note, /api/voice-notes
lists the user's notes, DELETE removes a single row, the /format /rediarize
/voice-match routes reject voice_note (single-speaker, has its own
chain), and /voice-note/rerun re-queues the chain. Mirrors the shape
of test_reformatting.py for the existing dictation flow."""
import io
from unittest.mock import AsyncMock, patch

from database import Transcript, User, VoiceNote


# The testuser is the authenticated user the client fixture creates.
# Reuse it for "this user" — creating a separate User would 404/403 the
# route tests because the client only owns testuser's resources.
def _testuser(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _make_voice_note_transcript(db_session, full_text="remind me to email dave"):
    user = _testuser(db_session)
    t = Transcript(
        user_id=user.id, title="vn", filename="f.mp3", status="completed",
        full_text=full_text, segments=[], kind="voice_note",
    )
    db_session.add(t)
    db_session.commit()
    return user, t


def _upload_voice_note(client):
    async def _stub_transcribe(db, user_id, **kwargs):
        t = Transcript(
            user_id=user_id, title="t", filename="f.mp3", status="completed",
            full_text="hello", kind="voice_note",
        )
        db.add(t)
        db.commit()
        return t
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("m.mp3", io.BytesIO(b"x"), "audio/mpeg")},
            data={"provider": "groq", "kind": "voice_note"},
        )


# ── upload kind handling ────────────────────────────────────────────────


def test_upload_persists_voice_note_kind(client):
    r = _upload_voice_note(client)
    assert r.status_code == 200
    assert r.json()["kind"] == "voice_note"


def test_voice_note_upload_does_not_enqueue_classify_intent_job(client):
    """The voice-note chain replaces the dictation's correction+classify
    pair. A voice_note upload must NOT spawn a classify_intent job
    (that's a dictation-only UI hint)."""
    detail = _upload_voice_note(client).json()
    runs = client.get(f"/api/transcripts/{detail['id']}/runs/classify_intent").json()["runs"]
    assert runs == []


def test_voice_note_upload_enqueues_voice_note_job(client):
    detail = _upload_voice_note(client).json()
    runs = client.get(f"/api/transcripts/{detail['id']}/runs/voice_note").json()["runs"]
    # The /runs/{kind} payload doesn't include 'kind' (the URL param
    # IS the kind) — assert by id presence + a job row exists.
    assert len(runs) == 1
    # The voice_note job is enqueued; status may be "pending" if the
    # user's settings have a keyless format_provider (local_llm), or
    # "failed" if groq was picked and no key is saved. Both prove the
    # job landed in the queue.
    assert runs[0]["status"] in ("pending", "failed")


# ── voice_note_job field on serialized transcript ───────────────────────


def test_serialize_voice_note_transcript_has_voice_note_job_field(client):
    detail = _upload_voice_note(client).json()
    assert "voice_note_job" in detail
    assert detail["voice_note_job"] is not None
    assert detail["voice_note_job"]["kind"] == "voice_note"
    # The other dictation-only fields must still be null (uniform shape).
    assert detail["format_markdown_job"] is None
    assert detail["format_email_job"] is None
    assert detail["format_coding_prompt_job"] is None
    assert detail["classify_intent_job"] is None
    assert detail["classify_intent_hint"] is None


# ── /voice-note endpoint ─────────────────────────────────────────────────


def test_get_voice_note_returns_null_when_chain_not_run(client, db_session):
    user, t = _make_voice_note_transcript(db_session)
    r = client.get(f"/api/transcripts/{t.id}/voice-note")
    assert r.status_code == 200
    assert r.json()["voice_note"] is None


def test_get_voice_note_returns_row_after_chain_completes(client, db_session):
    user, t = _make_voice_note_transcript(db_session)
    db_session.add(VoiceNote(
        user_id=user.id, transcript_id=t.id, note_type="idea",
        title="Refactor", body="Extract helper.", structured={"summary": "Extract a helper.", "tags": ["dx"]},
        model="llama3", provider="groq",
    ))
    db_session.commit()

    r = client.get(f"/api/transcripts/{t.id}/voice-note")
    assert r.status_code == 200
    note = r.json()["voice_note"]
    assert note is not None
    assert note["note_type"] == "idea"
    assert note["title"] == "Refactor"
    assert note["structured"]["summary"] == "Extract a helper."


def test_get_voice_note_404_for_other_users_transcript(client, db_session):
    # Create a separate user and transcript; the client (testuser) doesn't own it.
    other = User(username="other_vn", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(
        user_id=other.id, title="vnot", filename="f.mp3", status="completed",
        full_text="x", segments=[], kind="voice_note",
    )
    db_session.add(t)
    db_session.commit()
    r = client.get(f"/api/transcripts/{t.id}/voice-note")
    assert r.status_code == 404


# ── /voice-notes list endpoint ───────────────────────────────────────────


def test_list_voice_notes_empty(client):
    r = client.get("/api/voice-notes")
    assert r.status_code == 200
    assert r.json()["voice_notes"] == []


def test_list_voice_notes_returns_users_own_notes(client, db_session):
    user, t = _make_voice_note_transcript(db_session)
    db_session.add(VoiceNote(
        user_id=user.id, transcript_id=t.id, note_type="todo",
        title="Things", body="Do these.", structured={"items": []},
        model="llama3", provider="groq",
    ))
    db_session.commit()

    r = client.get("/api/voice-notes")
    assert r.status_code == 200
    notes = r.json()["voice_notes"]
    assert len(notes) == 1
    n = notes[0]
    assert n["note_type"] == "todo"
    assert n["transcript_title"] == "vn"
    assert n["transcript_status"] == "completed"


# ── DELETE /voice-notes/{id} ────────────────────────────────────────────


def test_delete_voice_note_removes_row_only(client, db_session):
    user, t = _make_voice_note_transcript(db_session)
    note = VoiceNote(
        user_id=user.id, transcript_id=t.id, note_type="general",
        title="X", body="x", structured={}, model="m", provider="p",
    )
    db_session.add(note)
    db_session.commit()

    r = client.delete(f"/api/voice-notes/{note.id}")
    assert r.status_code == 200
    assert r.json()["deleted"] == note.id

    assert db_session.query(VoiceNote).filter(VoiceNote.id == note.id).first() is None
    assert db_session.query(Transcript).filter(Transcript.id == t.id).first() is not None


def test_delete_voice_note_404_for_other_users_note(client, db_session):
    other = User(username="other_vn2", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(
        user_id=other.id, title="vnot", filename="f.mp3", status="completed",
        full_text="x", segments=[], kind="voice_note",
    )
    db_session.add(t)
    db_session.commit()
    note = VoiceNote(
        user_id=other.id, transcript_id=t.id, note_type="general",
        title="X", body="x", structured={}, model="m", provider="p",
    )
    db_session.add(note)
    db_session.commit()

    r = client.delete(f"/api/voice-notes/{note.id}")
    assert r.status_code == 404


# ── rejection routes ────────────────────────────────────────────────────


def test_format_route_rejects_voice_note(client):
    tid = _upload_voice_note(client).json()["id"]
    r = client.post(
        f"/api/transcripts/{tid}/format/markdown",
        data={"provider": "local_llm", "model": "llama3"},
    )
    assert r.status_code == 400
    assert "voice note" in r.json()["detail"].lower()


def test_rediarize_route_rejects_voice_note(client):
    tid = _upload_voice_note(client).json()["id"]
    r = client.post(f"/api/transcripts/{tid}/rediarize")
    assert r.status_code == 400
    assert "single-speaker" in r.json()["detail"].lower()


def test_voice_match_route_rejects_voice_note(client):
    tid = _upload_voice_note(client).json()["id"]
    r = client.post(f"/api/transcripts/{tid}/voice-match")
    assert r.status_code == 400
    assert "single-speaker" in r.json()["detail"].lower()


def test_summarize_route_rejects_voice_note(client):
    tid = _upload_voice_note(client).json()["id"]
    r = client.post(f"/api/transcripts/{tid}/summarize", data={"provider": "groq"})
    assert r.status_code == 400
    assert "voice note" in r.json()["detail"].lower()


# ── rerun endpoint ──────────────────────────────────────────────────────


def test_voice_note_rerun_route_enqueues_new_job(client, db_session):
    # Save a key so the rerun's key-check passes and the job actually
    # enqueues (rather than 400'ing on "no key saved").
    from database import ProviderConfig
    user = _testuser(db_session)
    db_session.add(ProviderConfig(
        user_id=user.id, name="groq", api_key="sk-test-fake", is_active=True,
    ))
    db_session.commit()

    tid = _upload_voice_note(client).json()["id"]
    r = client.post(
        f"/api/transcripts/{tid}/voice-note/rerun",
        data={"provider": "groq", "model": "llama-3.3-70b-versatile"},
    )
    assert r.status_code == 200
    assert r.json()["job"]["kind"] == "voice_note"
    assert r.json()["job"]["status"] == "pending"


def test_voice_note_rerun_rejects_no_key(client):
    tid = _upload_voice_note(client).json()["id"]
    r = client.post(
        f"/api/transcripts/{tid}/voice-note/rerun",
        data={"provider": "groq"},
    )
    assert r.status_code == 400
    assert "no groq api key" in r.json()["detail"].lower()


def test_voice_note_rerun_rejects_non_voice_note(client, db_session):
    user = _testuser(db_session)
    meeting = Transcript(
        user_id=user.id, title="m", filename="m.mp3", status="completed",
        full_text="x", segments=[], kind="meeting",
    )
    db_session.add(meeting)
    db_session.commit()
    r = client.post(
        f"/api/transcripts/{meeting.id}/voice-note/rerun",
        data={"provider": "groq"},
    )
    assert r.status_code == 400


def test_voice_note_rerun_404_for_other_users_transcript(client, db_session):
    other = User(username="other_vn3", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(
        user_id=other.id, title="vnot", filename="f.mp3", status="completed",
        full_text="x", segments=[], kind="voice_note",
    )
    db_session.add(t)
    db_session.commit()
    r = client.post(
        f"/api/transcripts/{t.id}/voice-note/rerun",
        data={"provider": "groq"},
    )
    assert r.status_code == 404


# ── runs/{kind} endpoint includes voice_note ─────────────────────────────


def test_runs_endpoint_accepts_voice_note_kind(client):
    tid = _upload_voice_note(client).json()["id"]
    r = client.get(f"/api/transcripts/{tid}/runs/voice_note")
    assert r.status_code == 200
    runs = r.json()["runs"]
    # Upload auto-enqueued one voice_note job.
    assert len(runs) == 1
    # /runs/{kind} payload doesn't echo kind (URL param is the kind).
    assert "provider" in runs[0]
