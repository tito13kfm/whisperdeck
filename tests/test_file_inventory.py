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


def test_delete_transcript_keeps_media_file_referenced_by_sibling_transcript(client, db_session, tmp_path):
    """Retranscribe carries audio_path/video_path forward verbatim, so two
    transcripts from the same user routinely end up pointing at the same
    physical file. Deleting one of them via DELETE /api/transcripts/{id}
    must not remove a file the sibling still depends on for playback."""
    from database import Transcript as _T
    shared_audio = tmp_path / "shared.mp3"
    shared_audio.write_bytes(b"shared")
    user = db_session.query(User).filter(User.username == "testuser").first()
    original = _T(user_id=user.id, title="original", filename="t.mp3", status="completed",
                  full_text="x", audio_path=str(shared_audio))
    retranscribed = _T(user_id=user.id, title="retranscribed", filename="t.mp3", status="completed",
                        full_text="y", audio_path=str(shared_audio))
    db_session.add_all([original, retranscribed])
    db_session.commit()
    retranscribed_id = retranscribed.id
    original_id = original.id

    r = client.delete(f"/api/transcripts/{retranscribed_id}")
    assert r.status_code == 200

    db_session.expire_all()
    assert db_session.query(_T).filter(_T.id == retranscribed_id).first() is None
    assert shared_audio.exists()
    survivor = db_session.query(_T).filter(_T.id == original_id).first()
    assert survivor is not None
    assert survivor.audio_path == str(shared_audio)


def test_delete_files_shared_same_user_path_skips_and_preserves_both_transcripts(client, db_session, tmp_path, monkeypatch):
    """Same setup as a retranscribe chain, but going through the manual
    Files-page delete endpoint instead of the transcript-delete button."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    shared_file = tmp_path / "shared.mp3"
    shared_file.write_bytes(b"shared")

    user = db_session.query(User).filter(User.username == "testuser").first()
    original = Transcript(user_id=user.id, title="original", filename="t.mp3", status="completed",
                          full_text="x", audio_path=str(shared_file))
    retranscribed = Transcript(user_id=user.id, title="retranscribed", filename="t.mp3", status="completed",
                               full_text="y", audio_path=str(shared_file))
    db_session.add_all([original, retranscribed])
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(shared_file)]})
    assert r.status_code == 200
    body = r.json()
    assert any(s["path"] == str(shared_file) and s["reason"] == "shared" for s in body["skipped"])
    assert str(shared_file) not in body["deleted"]
    assert shared_file.exists()

    db_session.expire_all()
    original2 = db_session.query(Transcript).filter(Transcript.id == original.id).first()
    retranscribed2 = db_session.query(Transcript).filter(Transcript.id == retranscribed.id).first()
    assert original2.audio_path == str(shared_file)
    assert retranscribed2.audio_path == str(shared_file)


def test_list_files_shows_one_linked_entry_per_transcript_for_shared_path(client, db_session, tmp_path, monkeypatch):
    """Two of the same user's own transcripts sharing a path (retranscribe
    chain) must both appear in the inventory as linked, so the Files page
    makes the sharing visible instead of hiding one dependent transcript."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    shared_file = tmp_path / "shared.mp3"
    shared_file.write_bytes(b"shared")

    user = db_session.query(User).filter(User.username == "testuser").first()
    original = Transcript(user_id=user.id, title="original", filename="t.mp3", status="completed",
                          full_text="x", audio_path=str(shared_file))
    retranscribed = Transcript(user_id=user.id, title="retranscribed", filename="t.mp3", status="completed",
                               full_text="y", audio_path=str(shared_file))
    db_session.add_all([original, retranscribed])
    db_session.commit()

    r = client.get("/api/files")
    assert r.status_code == 200
    body = r.json()
    matching = [f for f in body["linked"] if f["path"] == str(shared_file)]
    assert len(matching) == 2
    transcript_ids = {f["transcript_id"] for f in matching}
    assert transcript_ids == {original.id, retranscribed.id}


def _chunk_with_status(db_session, tmp_path, status, transcript_status="processing"):
    chunk_file = tmp_path / f"chunk_{status}.mp3"
    chunk_file.write_bytes(b"chunk")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status=transcript_status, full_text="")
    db_session.add(t)
    db_session.commit()
    job = TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=1.0,
                            audio_path=str(chunk_file), status=status)
    db_session.add(job)
    db_session.commit()
    return chunk_file, t


def test_list_files_excludes_failed_chunk_from_orphaned(client, db_session, tmp_path, monkeypatch):
    """Failed chunks are not dead — the queue worker auto-retries them after
    a backoff window, and 'Retry failed sections' resets them to pending.
    Their files must not be offered up as orphaned garbage."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    chunk_file, _ = _chunk_with_status(db_session, tmp_path, "failed", transcript_status="partial")

    r = client.get("/api/files")
    orphan_paths = [f["path"] for f in r.json()["orphaned"]]
    assert str(chunk_file) not in orphan_paths


def test_list_files_excludes_cancelled_chunk_from_orphaned(client, db_session, tmp_path, monkeypatch):
    """Cancelled chunks are resumable (resume_cancelled_chunks resets them
    to pending), so their files are still needed."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    chunk_file, _ = _chunk_with_status(db_session, tmp_path, "cancelled", transcript_status="cancelled")

    r = client.get("/api/files")
    orphan_paths = [f["path"] for f in r.json()["orphaned"]]
    assert str(chunk_file) not in orphan_paths


def test_delete_skips_failed_chunk_as_in_use(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    chunk_file, _ = _chunk_with_status(db_session, tmp_path, "failed", transcript_status="partial")

    r = client.post("/api/files/delete", json={"paths": [str(chunk_file)]})
    assert r.status_code == 200
    assert any(s["path"] == str(chunk_file) and s["reason"] == "in_use" for s in r.json()["skipped"])
    assert chunk_file.exists()


def test_delete_skips_cancelled_chunk_as_in_use(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    chunk_file, _ = _chunk_with_status(db_session, tmp_path, "cancelled", transcript_status="cancelled")

    r = client.post("/api/files/delete", json={"paths": [str(chunk_file)]})
    assert r.status_code == 200
    assert any(s["path"] == str(chunk_file) and s["reason"] == "in_use" for s in r.json()["skipped"])
    assert chunk_file.exists()


def test_delete_skips_processing_transcripts_source_audio(client, db_session, tmp_path, monkeypatch):
    """A processing transcript's audio_path is the pre-chunk source file —
    chunked-path diarization reads it at finalize (_finalize_if_done guards
    on transcript.audio_path being set, silently skipping diarization if
    it's gone). Deleting it mid-run silently loses speaker labels."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="processing",
                   full_text="", audio_path=str(source))
    db_session.add(t)
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(source)]})
    assert r.status_code == 200
    assert any(s["path"] == str(source) and s["reason"] == "in_use" for s in r.json()["skipped"])
    assert source.exists()
    db_session.expire_all()
    assert db_session.query(Transcript).filter(Transcript.id == t.id).first().audio_path == str(source)


def test_delete_skips_source_audio_of_transcript_with_retryable_chunks(client, db_session, tmp_path, monkeypatch):
    """A partial transcript with failed chunks can re-enter the pipeline via
    'Retry failed sections' — its source audio_path is needed again at
    finalize, even though the transcript's own status looks terminal."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="partial",
                   full_text="x", audio_path=str(source), diarize_requested=True)
    db_session.add(t)
    db_session.commit()
    chunk = tmp_path / "c0.mp3"
    chunk.write_bytes(b"c")
    job = TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=1.0,
                            audio_path=str(chunk), status="failed")
    db_session.add(job)
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(source)]})
    assert r.status_code == 200
    assert any(s["path"] == str(source) and s["reason"] == "in_use" for s in r.json()["skipped"])
    assert source.exists()


def test_delete_completed_transcripts_media_is_allowed(client, db_session, tmp_path, monkeypatch):
    """Counterpart to the in_use guards: a completed transcript with only
    completed jobs has no revival path that needs the file — deleting is the
    user's informed choice, and has_audio-gated features degrade cleanly."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    source = tmp_path / "done.mp3"
    source.write_bytes(b"done")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed",
                   full_text="x", audio_path=str(source))
    db_session.add(t)
    db_session.commit()
    job = TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=1.0,
                            audio_path=str(tmp_path / "c_done.mp3"), status="completed")
    db_session.add(job)
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(source)]})
    assert r.status_code == 200
    assert str(source) in r.json()["deleted"]
    assert not source.exists()
    db_session.expire_all()
    assert db_session.query(Transcript).filter(Transcript.id == t.id).first().audio_path is None


def test_delete_matches_db_reference_by_realpath_not_string(client, db_session, tmp_path, monkeypatch):
    """The inventory classifies linked files by realpath, so delete must
    too — a textual variant of the same path stored in the DB (here a
    redundant '.' segment) must still count as a reference, or the file
    gets removed as an 'orphan' out from under the transcript."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    f = tmp_path / "variant.mp3"
    f.write_bytes(b"variant")
    variant = os.path.join(str(tmp_path), ".", "variant.mp3")
    assert variant != str(f) and os.path.realpath(variant) == os.path.realpath(str(f))
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed",
                   full_text="x", audio_path=variant)
    db_session.add(t)
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(f)]})
    assert r.status_code == 200
    assert str(f) in r.json()["deleted"]
    db_session.expire_all()
    assert db_session.query(Transcript).filter(Transcript.id == t.id).first().audio_path is None


def test_delete_transcript_keeps_file_referenced_by_sibling_under_textual_path_variant(client, db_session, tmp_path):
    """delete_transcript's still-referenced check must also compare
    realpaths — a sibling holding a textual variant of the same path is
    still a live reference."""
    from database import Transcript as _T
    shared = tmp_path / "shared2.mp3"
    shared.write_bytes(b"shared2")
    variant = os.path.join(str(tmp_path), ".", "shared2.mp3")
    user = db_session.query(User).filter(User.username == "testuser").first()
    a = _T(user_id=user.id, title="a", filename="t.mp3", status="completed",
           full_text="x", audio_path=str(shared))
    b = _T(user_id=user.id, title="b", filename="t.mp3", status="completed",
           full_text="y", audio_path=variant)
    db_session.add_all([a, b])
    db_session.commit()

    r = client.delete(f"/api/transcripts/{a.id}")
    assert r.status_code == 200
    assert shared.exists()


def test_delete_rejects_upload_dir_itself(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    r = client.post("/api/files/delete", json={"paths": [str(tmp_path)]})
    assert r.status_code == 400


def test_list_files_survives_file_vanishing_between_listdir_and_stat(client, db_session, tmp_path, monkeypatch):
    """A file deleted by another actor between os.listdir and the stat
    calls must be skipped, not 500 the whole inventory."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    ghost = tmp_path / "ghost.mp3"
    ghost.write_bytes(b"ghost")
    real_getsize = os.path.getsize

    def flaky_getsize(p):
        if os.path.basename(str(p)) == "ghost.mp3":
            raise OSError("vanished")
        return real_getsize(p)

    monkeypatch.setattr(app_module.os.path, "getsize", flaky_getsize)
    r = client.get("/api/files")
    assert r.status_code == 200
    all_paths = [f["path"] for f in r.json()["linked"]] + [f["path"] for f in r.json()["orphaned"]]
    assert str(ghost) not in all_paths


def test_list_files_modified_at_is_naive_utc_isoformat(client, db_session, tmp_path, monkeypatch):
    """modified_at must match the app-wide naive-UTC isoformat convention
    (created_at etc.) — the frontend's timeAgo() appends 'Z' itself, so a
    '+00:00' offset suffix would render as 'Invalid Date' / 'NaN ago'."""
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    (tmp_path / "o.mp3").write_bytes(b"o")
    r = client.get("/api/files")
    stamp = r.json()["orphaned"][0]["modified_at"]
    assert "+" not in stamp and not stamp.endswith("Z")
