"""GET /api/files inventory and POST /api/files/delete cleanup — the file
never takes the transcript down with it, only the path/file it targets."""
import os

from database import Transcript, TranscriptionJob, User


def _other_user(db_session):
    u = User(username="otheruser2", password_hash="x", password_salt="y")
    db_session.add(u)
    db_session.commit()
    return u


def test_list_files_classifies_linked_and_orphaned(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    linked_file = tmp_path / "linked.mp3"
    linked_file.write_bytes(b"linked")
    orphan_file = tmp_path / "orphan.mp3"
    orphan_file.write_bytes(b"orphan")

    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed",
                   full_text="x", audio_path=str(linked_file))
    db_session.add(t)
    db_session.commit()

    r = client.get("/api/files")
    assert r.status_code == 200
    body = r.json()
    linked_paths = [f["path"] for f in body["linked"]]
    orphan_paths = [f["path"] for f in body["orphaned"]]
    assert str(linked_file) in linked_paths
    assert str(orphan_file) in orphan_paths
    assert str(linked_file) not in orphan_paths


def test_list_files_excludes_other_users_linked_file_from_both_lists(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    other = _other_user(db_session)
    other_file = tmp_path / "other.mp3"
    other_file.write_bytes(b"other")
    t = Transcript(user_id=other.id, title="t", filename="t.mp3", status="completed",
                   full_text="x", audio_path=str(other_file))
    db_session.add(t)
    db_session.commit()

    r = client.get("/api/files")
    body = r.json()
    all_paths = [f["path"] for f in body["linked"]] + [f["path"] for f in body["orphaned"]]
    assert str(other_file) not in all_paths


def test_list_files_excludes_in_flight_job_chunk_from_orphaned(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    chunk_file = tmp_path / "chunk_0.mp3"
    chunk_file.write_bytes(b"chunk")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="processing", full_text="")
    db_session.add(t)
    db_session.commit()
    job = TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=1.0,
                            audio_path=str(chunk_file), status="running")
    db_session.add(job)
    db_session.commit()

    r = client.get("/api/files")
    orphan_paths = [f["path"] for f in r.json()["orphaned"]]
    assert str(chunk_file) not in orphan_paths


def test_delete_rejects_path_outside_upload_dir(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"do not delete me")

    r = client.post("/api/files/delete", json={"paths": [str(outside)]})
    assert r.status_code == 400
    assert outside.exists()


def test_delete_linked_file_nulls_column_keeps_transcript(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    linked_file = tmp_path / "linked.mp3"
    linked_file.write_bytes(b"linked")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="keep me", filename="t.mp3", status="completed",
                   full_text="full transcript text", audio_path=str(linked_file))
    db_session.add(t)
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(linked_file)]})
    assert r.status_code == 200
    assert str(linked_file) in r.json()["deleted"]
    assert not linked_file.exists()

    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t2 is not None
    assert t2.title == "keep me"
    assert t2.full_text == "full transcript text"
    assert t2.audio_path is None


def test_delete_skips_other_users_linked_file(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    other = _other_user(db_session)
    other_file = tmp_path / "other.mp3"
    other_file.write_bytes(b"other")
    t = Transcript(user_id=other.id, title="t", filename="t.mp3", status="completed",
                   full_text="x", audio_path=str(other_file))
    db_session.add(t)
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(other_file)]})
    assert r.status_code == 200
    body = r.json()
    assert any(s["path"] == str(other_file) and s["reason"] == "not_found_or_forbidden" for s in body["skipped"])
    assert other_file.exists()


def test_delete_shared_path_skips_and_preserves_both_transcripts(client, db_session, tmp_path, monkeypatch):
    """Two transcripts (different users) point at the same audio_path — an
    organic collision (e.g. same-second uploads with a default filename).
    Deleting as one owner must not blow away the file out from under the
    other user's transcript: skip as ambiguous rather than deleting."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    other = _other_user(db_session)
    shared_file = tmp_path / "shared.mp3"
    shared_file.write_bytes(b"shared")

    user = db_session.query(User).filter(User.username == "testuser").first()
    mine = Transcript(user_id=user.id, title="mine", filename="t.mp3", status="completed",
                       full_text="x", audio_path=str(shared_file))
    theirs = Transcript(user_id=other.id, title="theirs", filename="t.mp3", status="completed",
                         full_text="y", audio_path=str(shared_file))
    db_session.add_all([mine, theirs])
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(shared_file)]})
    assert r.status_code == 200
    body = r.json()
    assert any(s["path"] == str(shared_file) and s["reason"] == "shared" for s in body["skipped"])
    assert str(shared_file) not in body["deleted"]
    assert shared_file.exists()

    db_session.expire_all()
    mine2 = db_session.query(Transcript).filter(Transcript.id == mine.id).first()
    theirs2 = db_session.query(Transcript).filter(Transcript.id == theirs.id).first()
    assert mine2.audio_path == str(shared_file)
    assert theirs2.audio_path == str(shared_file)


def test_delete_orphan_removes_outright(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    orphan_file = tmp_path / "orphan.mp3"
    orphan_file.write_bytes(b"orphan")

    r = client.post("/api/files/delete", json={"paths": [str(orphan_file)]})
    assert r.status_code == 200
    assert str(orphan_file) in r.json()["deleted"]
    assert not orphan_file.exists()


def test_delete_transcript_removes_its_media_files(client, db_session, tmp_path):
    from database import Transcript as _T
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"a")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = _T(user_id=user.id, title="t", filename="t.mp4", status="completed",
           full_text="x", audio_path=str(audio), video_path=str(video))
    db_session.add(t)
    db_session.commit()

    r = client.delete(f"/api/transcripts/{t.id}")
    assert r.status_code == 200
    assert not audio.exists()
    assert not video.exists()
