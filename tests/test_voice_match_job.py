"""voice_match background job: relabels segments against the roster,
leaves low-confidence segments untouched, tolerates per-segment failures."""
import asyncio
import numpy as np
from unittest.mock import patch

from database import RelabelHistory, Transcript, User, VoiceProfile
from services.llm_jobs import cancel_llm_job, enqueue_llm_job, run_llm_job
from services.voice_id import voice_id_service, _MFCC_MODEL_ID


class _NoCloseSession:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def _user(db_session, name="matcher"):
    user = User(username=name, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


_CURRENT_BACKEND = object()


def _enrolled_profile(db_session, user, name="Alice", embedding_model=_CURRENT_BACKEND):
    """embedding_model defaults to the running backend's own model id.

    The job's pre-flight guard (issue #112) refuses to run when no enrolled
    profile was built by a model the current backend can produce, so a
    hardcoded literal here would fail every test that expects the job to run.
    Pass an explicit value to exercise mismatch/legacy-NULL cases.
    """
    if embedding_model is _CURRENT_BACKEND:
        embedding_model = voice_id_service.backend_name
    profile = VoiceProfile(
        user_id=user.id, name=name, embedding=[0.1, 0.2, 0.3],
        embedding_model=embedding_model, sample_count=1,
    )
    db_session.add(profile)
    db_session.commit()
    if embedding_model is None:
        # embedding_model carries a column default (database/__init__.py), so
        # the constructor arg above stored that default rather than NULL. An
        # UPDATE is the only way to reach the real pre-migration state.
        db_session.query(VoiceProfile).filter(VoiceProfile.id == profile.id).update(
            {VoiceProfile.embedding_model: None}, synchronize_session=False)
        db_session.commit()
        db_session.expire(profile)
        assert profile.embedding_model is None
    return profile


def _transcript_with_segments(db_session, user, tmp_path, segments):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                   full_text="x", segments=segments, audio_path=str(audio))
    db_session.add(t)
    db_session.commit()
    return t


def _outcome(matches=None, **overrides):
    out = {"matches": matches or [], "probe_model": "test", "degraded": False,
           "compared": 1, "skipped_model_mismatch": 0, "warning": None}
    out.update(overrides)
    return out


def test_voice_match_relabels_confident_segments_only(db_session, tmp_path):
    user = _user(db_session)
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
            return _outcome([{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 2}])
        return _outcome()
    fake_identify.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify_detailed", fake_identify):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "Alice"
    assert t.segments[1]["speaker"] == "SPEAKER_01"  # untouched, no confident match
    assert job.progress_done == 2
    assert job.progress_total == 2


def test_voice_match_runs_real_identify_through_executor(db_session, tmp_path, monkeypatch):
    """Only extraction and embedding extraction are stubbed — voice_id_service.identify()
    itself runs for real (its own db.query included), through the run_in_executor wrap
    added in services/llm_jobs.py, against the same file-backed sqlite db_session used
    everywhere else (check_same_thread=False, matching production)."""
    user = _user(db_session)
    _enrolled_profile(db_session, user)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    # Probe model must be the current backend's own id: identify() skips any
    # profile whose embedding_model differs from the probe's (voice_id.py).
    monkeypatch.setattr(voice_id_service, "_extract_embedding",
                        lambda path, hf_token=None: (np.array([0.1, 0.2, 0.3]), voice_id_service.backend_name))

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "Alice"


def test_voice_match_fails_fast_with_no_backend(db_session, tmp_path):
    user = _user(db_session)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.voice_id_service._backend", "none"):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert "backend" in job.error.lower()


def test_voice_match_fails_fast_with_empty_roster(db_session, tmp_path):
    """No VoiceProfile rows (or none with an embedding) for this user — the
    job should fail before extracting audio for a single segment."""
    user = _user(db_session)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("extract_clips_concat should not be called with an empty roster")

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fail_if_called):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error == "No enrolled voices with clips — add a clip to a roster profile first"


def test_voice_match_fails_fast_when_enrolled_voices_use_a_different_backend(db_session, tmp_path):
    """Profiles exist with embeddings, but every one was built by a model the
    running backend cannot produce, so identify() would skip all of them
    (services/voice_id.py mismatch check). The job must refuse up front instead
    of spawning one ffmpeg extraction per segment to match nothing (issue #112).

    _backend is patched rather than inherited so the mismatch is unambiguous
    regardless of which voice packages the test environment has installed.
    """
    user = _user(db_session)
    _enrolled_profile(db_session, user, embedding_model="speechbrain/spkrec-ecapa-voxceleb")
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("extract_clips_concat should not run when no profile matches the backend")

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.voice_id_service._backend", "librosa_mfcc"), \
         patch("services.llm_jobs.extract_clips_concat", fail_if_called):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "failed"
    # Both sides of the mismatch have to be named, otherwise the user has no
    # way to tell which backend to reinstall or which clips to re-add.
    assert "speechbrain/spkrec-ecapa-voxceleb" in job.error
    assert "MFCC fingerprint (librosa)" in job.error
    assert job.progress_done == 0
    assert t.segments[0]["speaker"] == "SPEAKER_00"  # transcript untouched


def test_voice_match_proceeds_when_enrolled_voice_matches_current_backend(db_session, tmp_path):
    """Mutation-check partner of the mismatch test above: if
    compatible_embedding_models() returned an empty set, the guard would reject
    a profile the current backend can actually use and this job would fail.
    """
    user = _user(db_session)
    _enrolled_profile(db_session, user, embedding_model="MFCC fingerprint (librosa)")
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.voice_id_service._backend", "librosa_mfcc"), \
         patch("services.llm_jobs.extract_clips_concat", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify_detailed",
               lambda db, user_id, audio_path, threshold=0.65, hf_token=None: _outcome([{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}])):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "Alice"


def test_voice_match_proceeds_for_legacy_profile_with_no_embedding_model(db_session, tmp_path):
    """A NULL embedding_model is a pre-migration row, and identify() treats it
    as compatible with any probe (services/voice_id.py). The pre-flight guard
    must mirror that, or the fix would hard-fail jobs that would have matched.
    """
    user = _user(db_session)
    _enrolled_profile(db_session, user, embedding_model=None)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.voice_id_service._backend", "librosa_mfcc"), \
         patch("services.llm_jobs.extract_clips_concat", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify_detailed",
               lambda db, user_id, audio_path, threshold=0.65, hf_token=None: _outcome([{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}])):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "Alice"


def _relabel_rows(db_session, transcript):
    return (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == transcript.id).all())


def test_voice_match_cancel_mid_loop_stops_and_leaves_transcript_unchanged(db_session, tmp_path):
    """A cancel doesn't signal the worker, it just flips the row, so the loop
    has to notice. Before issue #330 this branch had no cancellation check at
    all: it ran every remaining segment, then committed the relabel history and
    the speaker overwrite anyway, and only then did _finish see 'cancelled' and
    leave the status alone. The user got a job the Queue called cancelled on a
    transcript whose labels had all been rewritten.
    """
    user = _user(db_session)
    _enrolled_profile(db_session, user)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
        {"start": 2.0, "end": 3.0, "text": "again", "speaker": "SPEAKER_02"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    # Cancel lands while the first segment is being extracted, the same way a
    # user clicking cancel in the Queue would land it: straight onto the row.
    async def cancel_then_extract(audio_path, clips, output_dir):
        cancel_then_extract.calls += 1
        if cancel_then_extract.calls == 1:
            cancel_llm_job(db_session, user.id, job.id)
        return str(tmp_path / "clip.wav")
    cancel_then_extract.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", cancel_then_extract), \
         patch("services.llm_jobs.voice_id_service.identify_detailed",
               lambda db, user_id, audio_path, threshold=0.65, hf_token=None: _outcome([{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}])):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "cancelled"
    # Stopped early instead of grinding through the remaining segments.
    assert cancel_then_extract.calls == 1
    # The dependent writes must not have landed.
    assert [s["speaker"] for s in t.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    assert _relabel_rows(db_session, t) == []


def test_voice_match_cancel_after_a_committed_segment_still_skips_the_writes(db_session, tmp_path):
    """Stronger ordering than the test above, where the cancel lands during the
    FIRST extraction and so fires before the loop body has committed anything.

    Here the cancel lands during the second extraction, after segment 0 already
    ran `job.progress_done = i + 1; db.commit()`. That commit flushes the whole
    session, `transcript` included, so this pins down that the guard's return
    leaves no partially-durable relabel behind: the matched speaker for segment
    0 lives only in the local `new_segments` list until the post-loop write that
    the guard skips.
    """
    user = _user(db_session)
    _enrolled_profile(db_session, user)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
        {"start": 2.0, "end": 3.0, "text": "again", "speaker": "SPEAKER_02"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def cancel_on_second(audio_path, clips, output_dir):
        cancel_on_second.calls += 1
        if cancel_on_second.calls == 2:
            cancel_llm_job(db_session, user.id, job.id)
        return str(tmp_path / "clip.wav")
    cancel_on_second.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", cancel_on_second), \
         patch("services.llm_jobs.voice_id_service.identify_detailed",
               lambda db, user_id, audio_path, threshold=0.65, hf_token=None: _outcome([{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}])):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "cancelled"
    # Segment 0 matched and segment 1 matched, but the third was never started.
    assert cancel_on_second.calls == 2
    # Nothing durable, even though segment 0's progress commit already flushed.
    assert [s["speaker"] for s in t.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    assert _relabel_rows(db_session, t) == []


def test_voice_match_cancel_during_final_segment_still_skips_the_writes(db_session, tmp_path):
    """The loop-top check isn't enough on its own: a cancel landing during the
    LAST iteration has no further iteration to catch it, so the guard before the
    dependent writes is what keeps this case from overwriting the transcript.
    """
    user = _user(db_session)
    _enrolled_profile(db_session, user)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    def cancel_then_match(db, user_id, audio_path, threshold=0.65, hf_token=None):
        cancel_llm_job(db_session, user.id, job.id)
        return _outcome([{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}])

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify_detailed", cancel_then_match):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "cancelled"
    assert t.segments[0]["speaker"] == "SPEAKER_00"
    assert _relabel_rows(db_session, t) == []


def test_voice_match_fails_when_audio_missing(db_session):
    user = _user(db_session)
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                   full_text="x", segments=[{"start": 0, "end": 1, "text": "hi", "speaker": "S"}],
                   audio_path="nope/missing.mp3")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert "No stored audio" in job.error


def test_voice_match_skips_segment_on_extraction_failure_without_failing_job(db_session, tmp_path):
    user = _user(db_session)
    _enrolled_profile(db_session, user)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def flaky_extract(audio_path, clips, output_dir):
        flaky_extract.calls += 1
        if flaky_extract.calls == 1:
            raise ValueError("boom")
        return str(tmp_path / "clip.wav")
    flaky_extract.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", flaky_extract), \
         patch("services.llm_jobs.voice_id_service.identify_detailed",
               lambda db, user_id, audio_path, threshold=0.65, hf_token=None: _outcome([{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}])):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "SPEAKER_00"  # extraction failed, left alone
    assert t.segments[1]["speaker"] == "Alice"
    assert "1 segment" in job.error  # skip count surfaced even though status is completed


def test_voice_match_route_enqueues_job(client, db_session, tmp_path):
    from database import User as _User
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _transcript_with_segments(db_session, user, tmp_path,
                                   [{"start": 0, "end": 1, "text": "hi", "speaker": "S"}])
    r = client.post(f"/api/transcripts/{t.id}/voice-match")
    assert r.status_code == 200
    assert r.json()["job"]["kind"] == "voice_match"
    assert r.json()["job"]["status"] == "pending"


def test_voice_match_route_400_without_stored_audio(client, db_session):
    from database import User as _User, Transcript as _Transcript
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _Transcript(user_id=user.id, title="n", filename="n.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/voice-match")
    assert r.status_code == 400


def test_transcript_serialization_includes_voice_match_job(client, db_session, tmp_path):
    from database import User as _User
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _transcript_with_segments(db_session, user, tmp_path,
                                   [{"start": 0, "end": 1, "text": "hi", "speaker": "S"}])
    client.post(f"/api/transcripts/{t.id}/voice-match")
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["voice_match_job"]["kind"] == "voice_match"


def test_voice_match_passes_hf_token_from_user_settings(db_session, tmp_path):
    """The background job has no route to fetch settings for it — it must
    thread the user's hf_token into identify() itself, or a fresh process
    with a settings-only token silently probes with MFCC and matches nothing."""
    user = _user(db_session)
    user.settings = {"hf_token": "job-token-7"}
    db_session.commit()
    _enrolled_profile(db_session, user)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    captured = {}

    def fake_identify(db, user_id, audio_path, threshold=0.65, hf_token=None):
        captured["hf_token"] = hf_token
        return _outcome()

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify_detailed", fake_identify):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    assert captured["hf_token"] == "job-token-7"


def test_voice_match_reports_a_degraded_probe_in_job_error(db_session, tmp_path, monkeypatch):
    """Issue #109. The probe embedding is identical to the enrolled one, so the
    only reason nothing is relabeled is that the probe silently fell back to
    MFCC while the roster is on another model. That used to finish as a clean
    'completed' with error None, indistinguishable from an honest no-match."""
    user = _user(db_session)
    _enrolled_profile(db_session, user, embedding_model="speechbrain/spkrec-ecapa-voxceleb")
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    monkeypatch.setattr(voice_id_service, "_backend", "speechbrain")
    monkeypatch.setattr(voice_id_service, "_extract_embedding",
                        lambda path, hf_token=None: (np.array([0.1, 0.2, 0.3]), _MFCC_MODEL_ID))

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "SPEAKER_00"  # nothing relabeled
    assert job.error is not None                    # and it no longer looks clean
    assert "MFCC" in job.error
    assert "could not be compared" in job.error


def test_voice_match_stays_error_free_when_the_probe_model_matches(db_session, tmp_path, monkeypatch):
    """Guards the degradation reporting against firing on every run: same setup
    as above, only the probe's model_id agrees with the roster."""
    user = _user(db_session)
    _enrolled_profile(db_session, user, embedding_model="speechbrain/spkrec-ecapa-voxceleb")
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    monkeypatch.setattr(voice_id_service, "_backend", "speechbrain")
    monkeypatch.setattr(voice_id_service, "_extract_embedding",
                        lambda path, hf_token=None: (np.array([0.1, 0.2, 0.3]),
                                                     "speechbrain/spkrec-ecapa-voxceleb"))

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "Alice"
    assert job.error is None
