"""Direct unit tests of services.llm_jobs._finish, plus cross-connection
cancellation race regression tests for the fix landed alongside PR #389's
audit (see the docstring on _finish in services/llm_jobs.py).

_finish() used to be read-then-write: db.refresh(job), then branch on
job.status. That left a window a concurrent cancel could land in, between a
job branch's own post-loop status check and that branch's eventual commit of
its dependent writes. The fix makes _finish() claim the terminal state with a
conditional UPDATE ... WHERE status IN ACTIVE_STATUSES, so the claim and the
caller's still-pending dependent writes commit -- or roll back -- as one
transaction. There is no ordering left where half of it lands.
"""
import asyncio
import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import text

from database import LlmJob, RelabelHistory, Transcript, User, VoiceProfile, utcnow_naive as _real_utcnow_naive
from services.llm_jobs import (
    _finish, cancel_llm_job, enqueue_llm_job, get_active_job as _real_get_active_job,
    llm_worker_tick, run_llm_job,
)
from services.voice_id import voice_id_service


def _user(db_session, name="finisher"):
    user = User(username=name, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


# ── C: unit tests of _finish itself, no job branch involved ────────────────


def test_finish_claims_a_running_job_and_commits_a_pending_write(db_session):
    user = _user(db_session)
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.status = "running"
    db_session.commit()

    # A dependent write left pending by the caller -- the same shape as a job
    # branch setting job.result_json / a transcript field right before
    # calling _finish, with no db.commit() of its own in between (the fix
    # removed those commits so this write and the terminal-state claim below
    # land in one transaction).
    job.result_json = {"corrected_text": "hello"}
    t.corrected_text = "hello"

    claimed = _finish(db_session, job, "completed")

    assert claimed is True
    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert job.result_json == {"corrected_text": "hello"}
    assert t.corrected_text == "hello"


def test_finish_loses_the_race_to_an_already_cancelled_row_and_rolls_back(db_session):
    """The assertion that actually pins the new behaviour: a status that is
    ALREADY 'cancelled' in the database (not merely in the in-memory object)
    by the time _finish runs must roll back whatever the caller left
    pending, not commit it.

    The old read-then-write _finish committed the zeroed progress via its
    own db.commit(), but that commit had no way to also undo a caller's
    earlier, still-uncommitted writes -- job.result_json/transcript fields
    set before the read-then-write _finish ran had nowhere to go but
    forward into that same commit. That is the exact mechanism PR #389's
    audit found: a cancelled job carrying persisted results.
    """
    user = _user(db_session)
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.status = "cancelled"
    db_session.commit()

    # Dependent writes the caller left pending before calling _finish --
    # exactly what a cancelled correction/voice_match/rediarize branch must
    # NOT be allowed to persist.
    job.result_json = {"corrected_text": "leaked"}
    t.corrected_text = "leaked"

    claimed = _finish(db_session, job, "completed")

    assert claimed is False
    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "cancelled"
    assert job.progress_done == 0
    assert job.progress_total == 0
    assert job.result_json is None
    assert t.corrected_text is None


def test_finish_records_a_failure_with_its_error_string(db_session):
    user = _user(db_session)
    job = enqueue_llm_job(db_session, user.id, None, "correction", "groq", "m")
    job.status = "running"
    db_session.commit()

    claimed = _finish(db_session, job, "failed", "boom")

    assert claimed is True
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error == "boom"


# ── A + B: cancel committed from a genuinely separate DB connection ───────
#
# The race PR #389's audit found needs a cancel that commits from OUTSIDE the
# worker's own session. Committing it through the worker's own session (the
# way the existing cancel-mid-loop tests in test_voice_match_job.py do) would
# also commit the worker's own pending writes as part of the same commit,
# which destroys the very thing under test -- there would be no separate
# "the cancel landed but our writes didn't" outcome to observe.
#
# tests/conftest.py's db_session fixture is backed by a per-test FILE sqlite
# database (init_db(str(db_path)), not ":memory:"), so a second, genuinely
# independent connection to the same database exists: db_session.get_bind()
# returns the Engine, and a fresh engine.connect() is a separate connection
# from whatever the ORM Session is holding.


def _cancel_from_another_connection(engine, job_id):
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE llm_jobs SET status='cancelled', progress_done=0, progress_total=0 WHERE id=:i"
        ), {"i": job_id})
        conn.commit()


def _complete_from_another_connection(engine, job_id):
    """Same shape as _cancel_from_another_connection above, landing a
    genuine completion (non-null result_json, non-zero progress) instead of
    a cancel -- used by the cancel_llm_job race test below, where the
    competing writer is a finish, not a cancel."""
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE llm_jobs SET status='completed', result_json=:r, "
            "progress_done=5, progress_total=5 WHERE id=:i"
        ), {"i": job_id, "r": '{"corrected_text": "already done"}'})
        conn.commit()


class _NoCloseSession:
    """run_llm_job closes its session; tests share one -- swallow the close."""
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def _enrolled_profile(db_session, user, name="Alice"):
    profile = VoiceProfile(user_id=user.id, name=name, embedding=[0.1, 0.2, 0.3],
                            embedding_model=voice_id_service.backend_name, sample_count=1)
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


def _outcome(matches=None, **overrides):
    out = {"matches": matches or [], "probe_model": "test", "degraded": False,
           "compared": 1, "skipped_model_mismatch": 0, "warning": None}
    out.update(overrides)
    return out


def _relabel_rows(db_session, transcript):
    return (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == transcript.id).all())


def test_voice_match_cancel_committed_from_another_connection_wins_the_race(db_session, tmp_path):
    """The regression this pins: PR #389's audit found _finish() used to be
    read-then-write. That left a window between this branch's own post-loop
    cancel check (services/llm_jobs.py, `db.refresh(job); if job.status ==
    "cancelled": return`, right before the voice_match relabel writes) and
    the eventual commit of those writes: a cancel that committed from a
    genuinely separate connection in that window was neither seen by the
    check nor prevented by the commit, so the relabel writes landed anyway
    and the job still came out 'cancelled' -- a cancelled job carrying a
    relabelled transcript, a stored result_json, and a RelabelHistory undo
    row for a run the user had called off.

    Injection point: the branch's own body does
    `from services.relabel import record_relabel` INSIDE the `if changed:`
    block, so patching "services.relabel.record_relabel" intercepts that
    local import. record_relabel is the FIRST database touch after the
    branch's post-loop cancel check -- committing the outside cancel there,
    then calling through to the real record_relabel, lands the cancel with
    no lock contention, because nothing in this transaction has flushed yet.

    A later injection point was tried first and rejected: patching
    "services.relabel.count_distinct_speakers" (called a few lines later, at
    `transcript.speaker_count = count_distinct_speakers(new_segments)`)
    lands the outside cancel AFTER record_relabel's own db.add() + query has
    already autoflushed an INSERT into this transaction. That flush already
    holds sqlite's write lock, so the second connection's UPDATE blocks for
    PRAGMA busy_timeout (5000ms, database/__init__.py) and then raises:

        (sqlite3.OperationalError) database is locked
        [SQL: UPDATE llm_jobs SET status='cancelled', progress_done=0, progress_total=0 WHERE id=?]
        [parameters: (1,)]

    which run_llm_job's own try/except catches and turns into a 'failed' job
    with that message -- not the race this test is after (though it is
    itself evidence the fix's locking discipline works: a genuine concurrent
    writer is blocked, not silently interleaved). The injection point was
    moved earlier, to record_relabel, to land inside the actual window.
    """
    user = _user(db_session)
    _enrolled_profile(db_session, user)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    engine = db_session.get_bind()

    async def fake_extract(audio_path, clips, output_dir, batch_size=20):
        return [str(tmp_path / "clip.wav") for _ in clips]

    from services.relabel import record_relabel as _real_record_relabel

    def hook_record_relabel(*args, **kwargs):
        _cancel_from_another_connection(engine, job.id)
        return _real_record_relabel(*args, **kwargs)

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_segment_clips", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify_detailed",
               lambda db, user_id, audio_path, threshold=0.65, hf_token=None: _outcome(
                   [{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}])), \
         patch("services.relabel.record_relabel", hook_record_relabel):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "cancelled"
    assert job.result_json is None
    assert t.segments[0]["speaker"] == "SPEAKER_00"
    assert _relabel_rows(db_session, t) == []


def test_rediarize_cancel_committed_from_another_connection_wins_the_race(db_session, tmp_path):
    """Same shape as the voice_match race test above, for a kind whose
    dependent writes are all in-session (clear_relabel_history's DELETE,
    transcript.segments/speaker_count/diarization_method, job.result_json)
    rather than voice_match's out-of-line relabel call -- demonstrating the
    _finish() fix closes the window framework-wide, not just for one kind's
    shape of dependent write.

    Injection point: the rediarize branch does
    `from services.relabel import clear_relabel_history, count_distinct_speakers`
    INSIDE the branch body, right after its own post-loop cancel check
    (`db.refresh(job); if job.status == "cancelled": return`) and before any
    of its own writes. clear_relabel_history is the first DB touch after
    that check, so patching "services.relabel.clear_relabel_history" to
    commit the outside cancel before calling through lands it in the same
    kind of window as the voice_match test above, with no lock contention.
    """
    user = _user(db_session)
    original_segments = [{"start": 0, "end": 1, "speaker": "Speaker A", "text": "hi"}]
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                    full_text="x", segments=list(original_segments))
    db_session.add(t)
    db_session.commit()
    audio_path = tmp_path / "a.mp3"
    audio_path.write_bytes(b"x")
    t.audio_path = str(audio_path)
    db_session.commit()

    job = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    job.status = "running"
    db_session.commit()

    engine = db_session.get_bind()

    new_segments = [{"start": 0, "end": 1, "speaker": "Speaker B", "text": "hi"}]

    class _FakeDiarizationService:
        async def diarize_and_merge(self, *args, **kwargs):
            return new_segments, 1, "pyannote", None

    from services.relabel import clear_relabel_history as _real_clear

    def hook_clear(db, transcript_id):
        _cancel_from_another_connection(engine, job.id)
        return _real_clear(db, transcript_id)

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.relabel.clear_relabel_history", hook_clear):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None,
                                 diarization_service=_FakeDiarizationService()))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "cancelled"
    assert job.result_json is None
    assert t.segments == original_segments


# ── D: the other three status writers _transition now guards ──────────────
#
# Two rounds of audit on PR #389 each found a read-then-write race on
# LlmJob.status: round 1 in _finish (pinned by the tests above), round 2 in
# cancel_llm_job. Rather than patch cancel_llm_job alone, every status
# transition moved onto _transition (services/llm_jobs.py), and a sweep
# found two more instances the audit hadn't reached: the retry sweep and the
# claim loop inside llm_worker_tick. These three tests pin those three
# sites the same way the tests above pin _finish -- a competing transition
# committed from a genuinely separate connection, landed inside the window
# between the read and the write.


def test_cancel_loses_the_race_to_a_completion_from_another_connection(db_session):
    """The round-2 audit finding: cancel_llm_job's own courtesy check
    (`if job.status not in ACTIVE_STATUSES`) reads the status, but the write
    that follows -- the _transition call -- is several lines later. A job
    that completes in that window used to have its 'completed' status and
    results silently overwritten by a stale cancel, which is worse than a
    failed cancel: the results stayed on disk while the row claimed the run
    had been called off.

    Injection point: `_transition` builds its `values` dict with
    `utcnow_naive()` before issuing its `UPDATE ... WHERE status IN (...)`,
    and cancel_llm_job reaches `_transition` through the module namespace --
    so patching `services.llm_jobs.utcnow_naive` intercepts that call. The
    courtesy check above never calls utcnow_naive, so this lands the
    competing commit after the check has already passed and immediately
    before the CAS write actually executes -- squarely inside the window.
    Confirmed by the assertions below: if the commit had landed outside the
    window, the CAS would already have claimed 'cancelled' and the row
    would come back cancelled with progress zeroed, not completed with
    results intact.
    """
    user = _user(db_session, "canceler")
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.status = "running"
    job.progress_done = 3
    job.progress_total = 5
    db_session.commit()

    engine = db_session.get_bind()
    fired = {"done": False}

    def hook_utcnow_naive():
        if not fired["done"]:
            fired["done"] = True
            _complete_from_another_connection(engine, job.id)
        return _real_utcnow_naive()

    with patch("services.llm_jobs.utcnow_naive", side_effect=hook_utcnow_naive):
        with pytest.raises(ValueError) as exc_info:
            cancel_llm_job(db_session, user.id, job.id)

    assert "completed" in str(exc_info.value)
    db_session.refresh(job)
    assert job.status == "completed"
    assert job.result_json == {"corrected_text": "already done"}
    assert job.progress_total == 5


def test_claim_loop_loses_the_race_to_a_cancel(db_session):
    """A pending job cancelled between the claim loop's pending query and
    its CAS write must not be claimed and run -- 'pending dies instantly'
    (cancel_llm_job's own comment) has to hold even when the worker's claim
    query already saw it as pending a moment earlier.

    Injection point: same trick as the cancel-vs-completion test above.
    _transition's `values` dict build calls `utcnow_naive()` right before
    the claim loop's CAS UPDATE executes -- patching
    `services.llm_jobs.utcnow_naive` lands the competing cancel there,
    after the pending query already built `claimed` and before the write.
    This also holds for the *old*, pre-fix claim loop (the one the mutation
    check below restores): its per-job body ends with
    `job.updated_at = utcnow_naive()`, so the same hook lands inside that
    version's window too, immediately before its trailing `db.commit()`
    flushes the stale claim over the top of the outside cancel.
    """
    user = _user(db_session, "claimer")
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    assert job.status == "pending"

    engine = db_session.get_bind()
    fired = {"done": False}

    def hook_utcnow_naive():
        if not fired["done"]:
            fired["done"] = True
            _cancel_from_another_connection(engine, job.id)
        return _real_utcnow_naive()

    run_calls = []

    async def fake_run_llm_job(SessionLocal, jid, *args, **kwargs):
        run_calls.append(jid)

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.utcnow_naive", side_effect=hook_utcnow_naive), \
         patch("services.llm_jobs.run_llm_job", fake_run_llm_job):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "cancelled"
    assert job.attempts == 0  # the lost CAS must not have incremented it
    assert job.id not in run_calls  # never dispatched to run_llm_job


def test_retry_sweep_loses_the_race_to_a_cancel(db_session):
    """A failed, retry-eligible job cancelled between the resurrection
    sweep's query and its CAS write must not come back as 'pending' -- a
    cancel is supposed to be the last word on a job, not something a stale
    retry can steamroll on the next tick.

    This job is built to genuinely qualify for the sweep: attempts=1 (>=1,
    and < MAX_ATTEMPTS), kind='correction' (in AUTO_RETRY_KINDS),
    dismissed=False (the default), and updated_at pushed back 1000s, well
    past the 10s backoff _retry_eligible computes for attempts=1. The
    positive control that this exact shape resurrects when nothing races it
    already exists --
    tests/test_llm_jobs.py::test_worker_tick_resurrects_failed_job_past_backoff_window
    uses the identical construction (attempts=1, kind='correction', backoff
    elapsed) and drives it all the way to 'completed' with a mocked provider
    call. Not duplicated here, to keep this test about the race rather than
    re-proving the eligibility rules.

    Injection point: the sweep loop calls `get_active_job(...)` immediately
    before its `_transition(...)` write (guarding against a manual rerun
    already covering this transcript+kind) -- and that call is untouched by
    the mutation check below, which only replaces the `_transition(...)`
    line itself with a raw `job.status = "pending"; job.error = None`. So
    patching `services.llm_jobs.get_active_job` lands the competing cancel
    inside the window for *both* the fixed and the pre-fix code, unlike
    hooking utcnow_naive here: the pre-fix line for this call site (per the
    mutation spec) never calls utcnow_naive, so that hook would silently
    fail to fire under the mutation and the test would go red for the wrong
    reason. get_active_job's call site is shared by both versions, so the
    race lands identically either way.
    """
    user = _user(db_session, "sweeper")
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.status = "failed"
    job.attempts = 1
    job.error = "transient network blip"
    job.updated_at = _real_utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    engine = db_session.get_bind()
    fired = {"done": False}

    def hook_get_active_job(db, transcript_id, kind):
        if not fired["done"] and transcript_id == t.id and kind == "correction":
            fired["done"] = True
            _cancel_from_another_connection(engine, job.id)
        return _real_get_active_job(db, transcript_id, kind)

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.get_active_job", hook_get_active_job):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "cancelled"
    assert job.error == "transient network blip"  # not cleared by the lost retry
