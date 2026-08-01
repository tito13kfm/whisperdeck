"""Post-hoc reprocessing: re-transcribe from stored audio, in-place
re-diarize as a background job, and paste-context-after-upload."""
import asyncio
import io
import os
from unittest.mock import AsyncMock, patch

from backends.base import Segment, TranscriptionResult
from database import LlmJob, Transcript, User
from services.llm_jobs import enqueue_llm_job, run_llm_job


class _FakeProvider:
    """Stands in for a real backend at the get_provider seam — the route,
    transcode decision, and TranscriptionService all run for real."""
    def __init__(self, text="hello world"):
        self._text = text

    async def transcribe(self, audio_path, **kwargs):
        return TranscriptionResult(
            segments=[Segment(start=0.0, end=1.0, text=self._text)],
            full_text=self._text,
            language="en",
            duration_seconds=1.0,
            model="fake-model",
        )


def _pipeline_patches(text="hello world"):
    return (
        patch("services.transcription.get_provider", lambda name, cfg: _FakeProvider(text)),
        patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)),
        patch("app.get_audio_duration", lambda path: 1.0),
    )


def _upload(client, text="hello world"):
    p1, p2, p3 = _pipeline_patches(text)
    with p1, p2, p3:
        return client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data={"provider": "groq"},
        )


def _test_user(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


# ── Task 1: inline path persists audio_path ────────────────────────────────

def test_inline_transcribe_persists_audio_path(client, db_session):
    client.put("/api/settings", json={"auto_correct": False})
    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["has_audio"] is True
    t = db_session.query(Transcript).filter(Transcript.id == body["id"]).first()
    assert t.audio_path and os.path.exists(t.audio_path)


# ── Task 3: retranscribe ───────────────────────────────────────────────────

def test_retranscribe_creates_new_row_and_keeps_original(client, db_session):
    client.put("/api/settings", json={"auto_correct": False})
    original = _upload(client, text="first pass").json()

    p1, p2, p3 = _pipeline_patches(text="second pass")
    with p1, p2, p3:
        r = client.post(
            f"/api/transcripts/{original['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        )
    assert r.status_code == 200
    fresh = r.json()
    assert fresh["id"] != original["id"]
    assert fresh["full_text"] == "second pass"

    db_session.expire_all()
    old = db_session.query(Transcript).filter(Transcript.id == original["id"]).first()
    assert old.full_text == "first pass"


def test_retranscribe_child_classification_status_not_forced_by_268(client, db_session):
    """Issue #268 introduces the 'auto' kind sentinel for fresh uploads, but
    retranscribe (source_transcript_id set) never passes kind='auto' — its
    child must land at the plain column default ('override'), not have
    #268's upload-path logic silently stamp a value that would block #271's
    real carry-forward/reclassify decision (design decision 9) later.
    Regression guard for a real review finding, not yet observably different
    from #268's actual (guarded) behavior -- exists to catch a future
    accidental removal of the source_transcript_id guard."""
    client.put("/api/settings", json={"auto_correct": False})
    original = _upload(client, text="first pass").json()
    parent = db_session.query(Transcript).filter(Transcript.id == original["id"]).first()
    parent.classification_status = "success"
    db_session.commit()

    p1, p2, p3 = _pipeline_patches(text="second pass")
    with p1, p2, p3:
        r = client.post(
            f"/api/transcripts/{original['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        )
    assert r.status_code == 200
    child = db_session.query(Transcript).filter(Transcript.id == r.json()["id"]).first()
    assert child.classification_status == "override"


def test_retranscribe_chain_sets_source_transcript_id_to_root(client, db_session):
    client.put("/api/settings", json={"auto_correct": False})
    original = _upload(client, text="first pass").json()

    p1, p2, p3 = _pipeline_patches(text="second pass")
    with p1, p2, p3:
        first_rerun = client.post(
            f"/api/transcripts/{original['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        ).json()

    p1, p2, p3 = _pipeline_patches(text="third pass")
    with p1, p2, p3:
        second_rerun = client.post(
            f"/api/transcripts/{first_rerun['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        ).json()

    db_session.expire_all()
    first = db_session.query(Transcript).filter(Transcript.id == first_rerun["id"]).first()
    second = db_session.query(Transcript).filter(Transcript.id == second_rerun["id"]).first()
    assert first.source_transcript_id == original["id"]
    # A rerun of a rerun still points at the original root, not its immediate parent.
    assert second.source_transcript_id == original["id"]


def test_retranscribe_links_root_even_if_pipeline_fails_after_commit(client, db_session):
    """source_transcript_id is set on the same commit that creates the row
    (inside _run_transcription_pipeline), not patched on afterward — so a
    failure later in the same request (e.g. auto-correct enqueue) still
    leaves the new row correctly linked to its root, even though the
    request itself surfaces as a 500."""
    client.put("/api/settings", json={"auto_correct": True})
    original = _upload(client, text="first pass").json()

    p1, p2, p3 = _pipeline_patches(text="second pass")
    with p1, p2, p3, patch("app.enqueue_auto_correction", side_effect=RuntimeError("boom")):
        r = client.post(
            f"/api/transcripts/{original['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        )
    assert r.status_code == 500

    db_session.expire_all()
    linked = [t for t in db_session.query(Transcript).all() if t.id != original["id"] and t.full_text == "second pass"]
    assert len(linked) == 1
    assert linked[0].source_transcript_id == original["id"]


def test_versions_endpoint_returns_root_and_all_reruns(client, db_session):
    client.put("/api/settings", json={"auto_correct": False})
    original = _upload(client, text="first pass").json()

    p1, p2, p3 = _pipeline_patches(text="second pass")
    with p1, p2, p3:
        rerun = client.post(
            f"/api/transcripts/{original['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        ).json()

    versions = client.get(f"/api/transcripts/{original['id']}/versions").json()["versions"]
    # Oldest first -- the opposite convention from Phase 2's /runs/{kind},
    # which is newest-first. A set comparison wouldn't catch a regression
    # to .desc() here, so assert order explicitly.
    assert [v["id"] for v in versions] == [original["id"], rerun["id"]]

    # Querying from the rerun side must resolve to the same group, same order.
    versions_from_rerun = client.get(f"/api/transcripts/{rerun['id']}/versions").json()["versions"]
    assert [v["id"] for v in versions_from_rerun] == [original["id"], rerun["id"]]


def test_versions_endpoint_exposes_status_for_in_progress_reruns(client, db_session):
    """The frontend's compare-versions modal must be able to tell a
    still-processing/failed rerun apart from a completed one (otherwise it
    would diff against empty full_text and show a misleading full
    delete/insert). Status has to be in the response for that guard to work."""
    client.put("/api/settings", json={"auto_correct": False})
    original = _upload(client, text="first pass").json()

    pending = Transcript(
        user_id=db_session.query(Transcript).filter(Transcript.id == original["id"]).first().user_id,
        title="rerun", filename="rerun.mp3", status="processing", full_text="",
        source_transcript_id=original["id"],
    )
    db_session.add(pending)
    db_session.commit()

    versions = client.get(f"/api/transcripts/{original['id']}/versions").json()["versions"]
    by_id = {v["id"]: v for v in versions}
    assert by_id[original["id"]]["status"] == "completed"
    assert by_id[pending.id]["status"] == "processing"


def test_versions_endpoint_returns_only_self_for_standalone_transcript(client, db_session):
    client.put("/api/settings", json={"auto_correct": False})
    original = _upload(client, text="only pass").json()

    versions = client.get(f"/api/transcripts/{original['id']}/versions").json()["versions"]
    assert [v["id"] for v in versions] == [original["id"]]


def test_versions_endpoint_404s_for_missing_transcript(client, db_session):
    r = client.get("/api/transcripts/999999/versions")
    assert r.status_code == 404


def test_retranscribe_400_without_stored_audio(client, db_session):
    user = _test_user(db_session)
    t = Transcript(user_id=user.id, title="old", filename="old.mp3",
                   status="completed", full_text="x", audio_path=None)
    gone = Transcript(user_id=user.id, title="gone", filename="gone.mp3",
                      status="completed", full_text="x",
                      audio_path="data/uploads/does-not-exist-anymore.mp3")
    db_session.add_all([t, gone])
    db_session.commit()

    for tid in (t.id, gone.id):
        r = client.post(f"/api/transcripts/{tid}/retranscribe", data={"provider": "groq"})
        assert r.status_code == 400
        assert "No stored audio" in r.json()["detail"]


# ── Task 4: rediarize ──────────────────────────────────────────────────────

def _transcript_with_audio(db_session, tmp_path, **overrides):
    user = _test_user(db_session)
    audio = tmp_path / "stored.mp3"
    audio.write_bytes(b"fake audio")
    fields = dict(
        user_id=user.id, title="d", filename="d.mp3", status="completed",
        full_text="a b", segments=[{"start": 0, "end": 1, "text": "a b", "speaker": None}],
        audio_path=str(audio),
    )
    fields.update(overrides)
    t = Transcript(**fields)
    db_session.add(t)
    db_session.commit()
    return t


def test_rediarize_route_enqueues_job_and_persists_count(client, db_session, tmp_path):
    t = _transcript_with_audio(db_session, tmp_path)
    r = client.post(f"/api/transcripts/{t.id}/rediarize", data={"num_speakers": "3"})
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["kind"] == "rediarize"
    assert job["status"] == "pending"
    db_session.refresh(t)
    assert t.num_speakers == 3
    assert t.diarize_requested is True


def test_rediarize_route_400_without_stored_audio(client, db_session):
    user = _test_user(db_session)
    t = Transcript(user_id=user.id, title="n", filename="n.mp3",
                   status="completed", full_text="x", audio_path=None)
    db_session.add(t)
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/rediarize")
    assert r.status_code == 400
    assert "No stored audio" in r.json()["detail"]


class _NoCloseSession:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def test_run_llm_job_rediarize_merges_in_place_without_key(db_session, tmp_path):
    # No ProviderConfig rows at all — proves the API-key gate is skipped.
    user = User(username="diarist", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    t = Transcript(
        user_id=user.id, title="d", filename="d.mp3", status="completed",
        full_text="a b", segments=[{"start": 0, "end": 1, "text": "a b", "speaker": None}],
        audio_path=str(audio), num_speakers=2,
    )
    db_session.add(t)
    db_session.commit()

    job = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    job.status = "running"
    db_session.commit()

    merged = [{"start": 0, "end": 1, "text": "a b", "speaker": "SPEAKER_01"}]
    fake_diar = AsyncMock()
    fake_diar.diarize_and_merge = AsyncMock(return_value=(merged, 2, "pyannote"))

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None,
                            diarization_service=fake_diar))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments == merged
    assert t.speaker_count == 2
    kwargs = fake_diar.diarize_and_merge.await_args.kwargs
    assert kwargs.get("num_speakers") == 2


def test_run_llm_job_rediarize_fails_when_audio_missing(db_session):
    user = User(username="diarist2", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                   full_text="x", audio_path="nope/missing.mp3")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    job.status = "running"
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None,
                            diarization_service=AsyncMock()))
    db_session.refresh(job)
    assert job.status == "failed"
    assert "No stored audio" in job.error


# ── Task 5: post-hoc context doc ───────────────────────────────────────────

def test_context_route_extracts_terms(client, db_session):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    user = _test_user(db_session)
    t = Transcript(user_id=user.id, title="c", filename="c.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()

    fake_extract = AsyncMock(return_value=["Acme Corp", "Roadmap"])
    with patch("app.extract_hotwords_from_doc", fake_extract):
        r = client.post(f"/api/transcripts/{t.id}/context",
                        data={"context_doc": "Agenda: Acme Corp roadmap review"})
    assert r.status_code == 200
    assert r.json()["terms"] == ["Acme Corp", "Roadmap"]
    fake_extract.assert_awaited_once()


def test_context_route_400_without_provider_key(client, db_session):
    # correction_provider defaults to local_llm (keyless) — force a
    # key-requiring provider to exercise the missing-key error path.
    client.put("/api/settings", json={"correction_provider": "groq"})
    user = _test_user(db_session)
    t = Transcript(user_id=user.id, title="c", filename="c.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/context", data={"context_doc": "Agenda"})
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]


def test_context_route_400_when_empty(client, db_session):
    user = _test_user(db_session)
    t = Transcript(user_id=user.id, title="c", filename="c.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/context", data={"context_doc": "   "})
    assert r.status_code == 400
