"""_contributing_rows (services/queue.py, issue #406 dedup): the two-query
row fetch shared by compute_audio_seconds_used and _oldest_contributing_timestamp.
Pins the exact filter semantics both callers depend on being identical:
provider, user, the trailing window cutoff, and the strictly-complementary
status sets between the transcript-side and job-side queries (a completed/
partial transcript counts on the transcript side; anything still in-flight
counts via its running/completed TranscriptionJob rows instead, never both).

Mutation check: fails if the function returns ([], []) or returns every row
unfiltered (would pick up the excluded old/wrong-provider/wrong-status rows
built into this test on purpose).
"""
import datetime

from database import Transcript, TranscriptionJob, User, utcnow_naive
from services.queue import _contributing_rows


def _make_user(db_session, name="qrow-user"):
    user = User(username=name, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def test_contributing_rows_filters_by_provider_status_and_window(db_session):
    user = _make_user(db_session)
    now = utcnow_naive()

    # Counts: completed, right provider, inside the window.
    t_in = Transcript(
        user_id=user.id, title="in", filename="in.mp3", provider="groq",
        status="completed", duration_seconds=42.0, updated_at=now,
    )
    # Excluded: right provider/status but outside the trailing window.
    t_old = Transcript(
        user_id=user.id, title="old", filename="old.mp3", provider="groq",
        status="completed", duration_seconds=999.0,
        updated_at=now - datetime.timedelta(seconds=10_000),
    )
    # Excluded: wrong provider.
    t_wrong_provider = Transcript(
        user_id=user.id, title="wp", filename="wp.mp3", provider="openai",
        status="completed", duration_seconds=999.0, updated_at=now,
    )
    # Still in-flight (not completed/partial) — its job row should count
    # instead, on the job side, not here.
    t_processing = Transcript(
        user_id=user.id, title="proc", filename="proc.mp3", provider="groq",
        status="processing", updated_at=now,
    )
    db_session.add_all([t_in, t_old, t_wrong_provider, t_processing])
    db_session.commit()

    # Counts: attached to the in-flight transcript, running, inside window.
    j_in = TranscriptionJob(
        transcript_id=t_processing.id, chunk_index=0,
        start_time=0.0, end_time=30.0, audio_path="x",
        status="running", updated_at=now,
    )
    # Excluded: job status not in the running/completed set.
    j_wrong_status = TranscriptionJob(
        transcript_id=t_processing.id, chunk_index=1,
        start_time=0.0, end_time=99.0, audio_path="x",
        status="failed", updated_at=now,
    )
    # Excluded: its parent transcript already counts on the transcript side
    # (status=completed) — the two queries' status filters are strict
    # complements specifically to prevent this row from double-counting.
    j_double_count = TranscriptionJob(
        transcript_id=t_in.id, chunk_index=0,
        start_time=0.0, end_time=99.0, audio_path="x",
        status="completed", updated_at=now,
    )
    db_session.add_all([j_in, j_wrong_status, j_double_count])
    db_session.commit()

    transcript_rows, job_rows = _contributing_rows(db_session, user.id, "groq", window_seconds=3600)

    assert {t.id for t in transcript_rows} == {t_in.id}
    assert {j.id for j in job_rows} == {j_in.id}
