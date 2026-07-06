"""HTTP layer for the Queue screen's dismiss/clear-finished routes: hides
terminal entries (either id shape) without touching underlying data."""
from database import LlmJob, Transcript, TranscriptionJob, User


def _testuser(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def test_dismiss_llm_job_entry_removes_it_from_the_list(client, db_session):
    user = _testuser(db_session)
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="summary", status="completed")
    db_session.add(job)
    db_session.commit()

    r = client.post(f"/api/jobs/{job.id}/dismiss")
    assert r.status_code == 200

    ids = [e["id"] for e in client.get("/api/jobs").json()["jobs"]]
    assert job.id not in ids


def test_dismiss_transcription_entry_removes_it_from_the_list(client, db_session):
    user = _testuser(db_session)
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=1.0,
        audio_path="c.mp3", status="completed",
    ))
    db_session.commit()

    r = client.post(f"/api/jobs/transcription-{t.id}/dismiss")
    assert r.status_code == 200

    ids = [e["id"] for e in client.get("/api/jobs").json()["jobs"]]
    assert f"transcription-{t.id}" not in ids


def test_dismiss_running_job_returns_400(client, db_session):
    user = _testuser(db_session)
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="summary", status="running")
    db_session.add(job)
    db_session.commit()

    r = client.post(f"/api/jobs/{job.id}/dismiss")
    assert r.status_code == 400


def test_clear_finished_removes_terminal_but_leaves_active(client, db_session):
    user = _testuser(db_session)
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    done_job = LlmJob(user_id=user.id, transcript_id=t.id, kind="summary", status="completed")
    active_job = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction", status="running")
    db_session.add_all([done_job, active_job])
    db_session.commit()

    r = client.post("/api/jobs/clear")
    assert r.status_code == 200
    assert r.json()["cleared"] >= 1

    ids = [e["id"] for e in client.get("/api/jobs").json()["jobs"]]
    assert done_job.id not in ids
    assert active_job.id in ids
