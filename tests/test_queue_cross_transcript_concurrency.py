"""Cross-transcript concurrency in the queue worker tick: hosted-provider
transcripts should dispatch concurrently instead of one fully finishing
(dispatch + finalize) before the next even starts, and one transcript's
finalize failure must not cancel a sibling transcript's in-flight chunk
job. The local-provider global-cap regression test,
test_local_provider_cap_of_one_holds_globally_across_transcripts, is added
in the next task — it needs this task's gather restructuring in place
first to be meaningful."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from database import Transcript, TranscriptionJob, User


class _NoClose:
    """Wraps a db session so queue_worker_tick's `db.close()` in its
    `finally` block doesn't tear down the test's shared session."""
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def _make_transcript_with_job(db_session, username, provider, chunk_seconds=60.0):
    user = User(username=username, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="processing", provider=provider, model="base")
    db_session.add(t)
    db_session.commit()
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=chunk_seconds,
        audio_path="c0.mp3", status="pending",
    ))
    db_session.commit()
    return t


def test_two_hosted_transcripts_dispatch_concurrently(db_session):
    """Two different transcripts on a hosted provider (groq) must both be
    able to have their chunk in flight at the same time — proving
    queue_worker_tick no longer fully awaits one transcript's batch before
    even looking at the next."""
    from services.queue import queue_worker_tick

    t1 = _make_transcript_with_job(db_session, "hosted1", "groq")
    t2 = _make_transcript_with_job(db_session, "hosted2", "groq")

    entered = []
    both_entered = asyncio.Event()

    class _FakeProvider:
        async def transcribe(self, *a, **k):
            entered.append(1)
            if len(entered) >= 2:
                both_entered.set()
            # Each call waits for the OTHER to have entered too, with NO
            # timeout of its own — only satisfiable if both transcripts'
            # chunk jobs are genuinely in flight at the same time. If run
            # sequentially, this hangs forever (t2 never gets a turn while
            # t1 is still awaiting here) — the outer asyncio.wait_for below
            # is what turns that hang into a clean test failure instead of
            # an actually-hung test process. (A timeout on this inner wait
            # would be silently swallowed by _run_chunk_job's own
            # `except (ProviderError, Exception)`, masking the hang as a
            # "failed" job instead of surfacing it — deliberately avoided.)
            await both_entered.wait()
            return SimpleNamespace(segments=[], full_text="", language="en", model="whisper-large-v3-flash")

    with patch("services.queue.get_provider", return_value=_FakeProvider()), \
         patch("services.queue._finalize_if_done", AsyncMock()):
        try:
            asyncio.run(asyncio.wait_for(
                queue_worker_tick(lambda: _NoClose(db_session), diarization_service=None),
                timeout=5.0,
            ))
        except asyncio.TimeoutError:
            pytest.fail(
                "queue_worker_tick timed out — transcripts are still dispatched "
                "sequentially, so the second transcript's chunk never started "
                "while the first was in flight"
            )

    assert len(entered) == 2
    job1 = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t1.id).first()
    job2 = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t2.id).first()
    assert job1.status == "completed"
    assert job2.status == "completed"


def test_one_transcript_finalize_exception_does_not_cancel_sibling_dispatch(db_session):
    """A raising _finalize_if_done for one transcript must not cancel a
    sibling transcript's in-flight chunk job. Plain asyncio.gather (without
    return_exceptions=True) cancels every other pending task the moment one
    raises — cancelling a job parked at `await provider.transcribe(...)`
    would leave it stuck at status="running" forever, since CancelledError
    isn't caught by _run_chunk_job's `except (ProviderError, Exception)`.

    `broken`'s chunk is given a duration that blows its rate-limit budget,
    so has_budget() rejects it and _process_transcript_jobs calls
    _finalize_if_done immediately (no dispatch, no sleep) — while
    `healthy`'s chunk is still mid-flight in its own 0.05s fake transcribe
    call. This makes the exception land deterministically while a sibling
    is still in flight, rather than relying on scheduling luck.
    """
    from services.queue import queue_worker_tick

    broken = _make_transcript_with_job(db_session, "broken", "groq", chunk_seconds=100_000.0)
    healthy = _make_transcript_with_job(db_session, "healthy", "groq")

    class _FakeProvider:
        async def transcribe(self, *a, **k):
            await asyncio.sleep(0.05)
            return SimpleNamespace(segments=[], full_text="", language="en", model="whisper-large-v3-flash")

    async def _boom(db, transcript_id, diarization_service):
        if transcript_id == broken.id:
            raise RuntimeError("simulated finalize failure")

    with patch("services.queue.get_provider", return_value=_FakeProvider()), \
         patch("services.queue._finalize_if_done", AsyncMock(side_effect=_boom)):
        asyncio.run(asyncio.wait_for(
            queue_worker_tick(lambda: _NoClose(db_session), diarization_service=None),
            timeout=5.0,
        ))

    healthy_job = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == healthy.id).first()
    assert healthy_job.status == "completed"  # not stuck at "running" from a cancelled sibling task
