"""Relabel history: record helper, pruning, undo endpoint."""
import asyncio
from unittest.mock import patch

import pytest

from database import LlmJob, RelabelHistory, Transcript, TranscriptTag, User, VoiceProfile
from services.llm_jobs import enqueue_llm_job, run_llm_job
from services.voice_id import voice_id_service


def _test_user(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _transcript(db_session, **overrides):
    user = _test_user(db_session)
    fields = dict(
        user_id=user.id, title="mtg", filename="mtg.mp3", status="completed",
        full_text="hello there general",
        segments=[
            {"start": 0.0, "end": 2.0, "text": "hello there", "speaker": "SPEAKER_00"},
            {"start": 2.0, "end": 4.0, "text": "general kenobi", "speaker": "SPEAKER_01"},
            {"start": 4.0, "end": 6.0, "text": "you are bold", "speaker": "SPEAKER_00"},
        ],
    )
    fields.update(overrides)
    t = Transcript(**fields)
    db_session.add(t)
    db_session.commit()
    return t


def test_record_relabel_stores_inverse_and_prunes(client, db_session):
    # `client` is unused directly but its fixture setup registers "testuser"
    # via /api/register (see conftest.py) -- db_session alone never creates
    # any user, so _test_user() would return None without it.
    from services.relabel import record_relabel, MAX_HISTORY
    t = _transcript(db_session)
    for n in range(MAX_HISTORY + 5):
        record_relabel(db_session, t, "retag", [(0, f"OLD_{n}")], description=f"run {n}")
        db_session.commit()
    rows = (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == t.id)
            .order_by(RelabelHistory.id).all())
    assert len(rows) == MAX_HISTORY
    assert rows[-1].description == f"run {MAX_HISTORY + 4}"
    assert rows[-1].inverse["segments"] == [{"index": 0, "speaker": f"OLD_{MAX_HISTORY + 4}"}]


def test_rename_then_undo_restores_segments_and_corrected_text(client, db_session):
    t = _transcript(db_session, corrected_text="SPEAKER_00: hello there\n\nSPEAKER_01: general kenobi")
    r = client.post(f"/api/transcripts/{t.id}/speakers/rename",
                    json={"from": "SPEAKER_00", "to": "Alice"})
    assert r.status_code == 200

    r = client.post(f"/api/transcripts/{t.id}/relabel-undo")
    assert r.status_code == 200
    assert r.json()["undone"] == "rename"

    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert [s["speaker"] for s in t2.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert t2.corrected_text.startswith("SPEAKER_00: hello there")


def test_retag_then_undo(client, db_session):
    t = _transcript(db_session)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0, 2], "speaker": "Bob"})
    assert r.status_code == 200
    r = client.post(f"/api/transcripts/{t.id}/relabel-undo")
    assert r.status_code == 200
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert [s["speaker"] for s in t2.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


def test_undo_with_no_history_is_404(client, db_session):
    t = _transcript(db_session)
    assert client.post(f"/api/transcripts/{t.id}/relabel-undo").status_code == 404


def test_undo_skips_corrected_text_regenerated_after_rename(client, db_session):
    """Rename stores a corrected_text before-image; if a correction pass
    re-runs before the undo, the snapshot is stale — undo must revert the
    segment labels but leave the fresh corrected_text alone rather than
    clobbering it with the pre-rename document."""
    t = _transcript(db_session, corrected_text="SPEAKER_00: hello there\n\nSPEAKER_01: general kenobi")
    r = client.post(f"/api/transcripts/{t.id}/speakers/rename",
                    json={"from": "SPEAKER_00", "to": "Alice"})
    assert r.status_code == 200

    # A correction job re-runs in between and rewrites corrected_text.
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    fresh = "Alice: hello there, tidied up by a newer correction pass"
    t2.corrected_text = fresh
    db_session.commit()

    r = client.post(f"/api/transcripts/{t.id}/relabel-undo")
    assert r.status_code == 200
    db_session.expire_all()
    t3 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    # Labels revert, the newer correction output survives.
    assert [s["speaker"] for s in t3.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert t3.corrected_text == fresh


def test_delete_transcript_removes_relabel_history(client, db_session):
    """ORM-level cascade must clean up history rows: the FK's
    ondelete=CASCADE never fires (SQLite foreign_keys pragma is off), and
    orphaned rows are dangerous because SQLite reuses rowids — a future
    transcript could inherit a dead transcript's id and with it a foreign
    undo entry."""
    t = _transcript(db_session)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0], "speaker": "Bob"})
    assert r.status_code == 200
    tid = t.id
    assert (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == tid).count()) == 1

    assert client.delete(f"/api/transcripts/{tid}").status_code == 200
    db_session.expire_all()
    assert (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == tid).count()) == 0


def test_delete_transcript_removes_llm_jobs_and_tags(client, db_session):
    """Issue #300: llm_jobs and transcript_tags were relying on the FK's
    ondelete=CASCADE alone, which never fires because the foreign_keys pragma
    is off. Same hazard as relabel_history above: orphaned rows plus SQLite
    rowid reuse means the next transcript created can inherit a dead
    transcript's jobs and tags."""
    t = _transcript(db_session)
    tid = t.id
    db_session.add(LlmJob(transcript_id=tid, kind="correction", status="completed",
                          user_id=_test_user(db_session).id))
    db_session.add(TranscriptTag(transcript_id=tid, tag="billing"))
    db_session.add(TranscriptTag(transcript_id=tid, tag="migration"))
    db_session.commit()
    assert db_session.query(LlmJob).filter(LlmJob.transcript_id == tid).count() == 1
    assert db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == tid).count() == 2

    assert client.delete(f"/api/transcripts/{tid}").status_code == 200
    db_session.expire_all()
    assert db_session.query(LlmJob).filter(LlmJob.transcript_id == tid).count() == 0
    assert db_session.query(TranscriptTag).filter(TranscriptTag.transcript_id == tid).count() == 0


def test_transcriptless_llm_job_survives_unrelated_transcript_delete(client, db_session):
    """Guards the choice of cascade="all, delete" over "all, delete-orphan"
    for llm_jobs. transcript_id is nullable because assistant jobs have no
    transcript (#175); delete-orphan would refuse to flush those at all, and
    a plain delete must not sweep them up when some other transcript goes."""
    user = _test_user(db_session)
    standalone = LlmJob(transcript_id=None, kind="assistant", status="completed",
                        user_id=user.id)
    db_session.add(standalone)
    db_session.commit()  # would raise under delete-orphan
    standalone_id = standalone.id

    t = _transcript(db_session)
    db_session.delete(t)
    db_session.commit()

    assert db_session.query(LlmJob).filter(LlmJob.id == standalone_id).count() == 1


def test_two_undos_walk_back_two_actions(client, db_session):
    t = _transcript(db_session)
    client.post(f"/api/transcripts/{t.id}/segments/retag", json={"indices": [0], "speaker": "A"})
    client.post(f"/api/transcripts/{t.id}/segments/retag", json={"indices": [0], "speaker": "B"})
    client.post(f"/api/transcripts/{t.id}/relabel-undo")
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t2.segments[0]["speaker"] == "A"
    client.post(f"/api/transcripts/{t.id}/relabel-undo")
    db_session.expire_all()
    t3 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t3.segments[0]["speaker"] == "SPEAKER_00"


class _NoCloseSession:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def _voice_match_user(db_session, name="matcher"):
    user = User(username=name, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def _enrolled_profile(db_session, user, name="Alice"):
    profile = VoiceProfile(
        user_id=user.id, name=name, embedding=[0.1, 0.2, 0.3],
        embedding_model="test", sample_count=1,
    )
    db_session.add(profile)
    db_session.commit()
    return profile


def _transcript_with_segments(db_session, user, tmp_path, segments):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                   full_text="x", segments=segments, audio_path=str(audio))
    db_session.add(t)
    db_session.commit()
    return t


def test_voice_match_records_relabel_history(db_session, tmp_path):
    user = _voice_match_user(db_session)
    _enrolled_profile(db_session, user)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    def fake_identify(db, user_id, audio_path, threshold=0.65, hf_token=None):
        # first call (segment 0) matches confidently, second doesn't
        fake_identify.calls += 1
        if fake_identify.calls == 1:
            return [{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 2}]
        return []
    fake_identify.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify", fake_identify):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    rows = db_session.query(RelabelHistory).filter(RelabelHistory.kind == "voice_match").all()
    assert len(rows) == 1
    assert rows[0].inverse["segments"][0]["speaker"] == "SPEAKER_00"


class _FakeDiarizationService:
    async def diarize_and_merge(self, audio_path, num_speakers, segments,
                                hf_token=None, stereo_audio_path=None):
        merged = [{"start": 0.0, "end": 2.0, "text": "regenerated", "speaker": "SPEAKER_00"}]
        return merged, 1, "heuristic"


def test_rediarize_clears_relabel_history(client, db_session, tmp_path):
    """Rediarize regenerates the segmentation wholesale, so index-based
    inverse patches recorded against the old segments must be invalidated —
    otherwise undo stamps stale labels onto unrelated new lines."""
    user = _test_user(db_session)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    t = _transcript(db_session, audio_path=str(audio))

    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0], "speaker": "Bob"})
    assert r.status_code == 200
    assert (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == t.id).count()) == 1

    job = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    job.status = "running"
    db_session.commit()
    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None,
                            diarization_service=_FakeDiarizationService()))

    db_session.expire_all()
    assert (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == t.id).count()) == 0
    # And the undo endpoint agrees there is nothing left to undo.
    assert client.post(f"/api/transcripts/{t.id}/relabel-undo").status_code == 404
