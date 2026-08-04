"""The chunked-job path must honor the user's hallucination-filter dials.

Issue #317: services/queue.py called filter_hallucinations with rep_window=3,
logprob_cutoff=-2.0 and no_speech_cutoff=0.6 baked in, while app.py's inline
(non-chunked) path read all three from user settings. Exposing those dials in
the settings UI made the divergence user-visible: tuning them worked on files
small enough to transcribe in one pass and was silently ignored on any file
large enough to be chunked.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backends.base import Segment, TranscriptionResult
from database import Transcript, TranscriptionJob, User
from services.queue import _run_chunk_job
from services.settings import update_user_settings

# Deliberately none of the defaults (3 / -2.0 / 0.6), so an assertion on these
# values can only pass if the settings were actually read.
TUNED = {
    "cleanup_hallu_enabled": True,
    "cleanup_hallu_rep_window": 7,
    "cleanup_hallu_logprob_cutoff": -4.5,
    "cleanup_hallu_no_speech_cutoff": 0.25,
}


_seq = iter(range(1000))


def _seed(db, settings: dict):
    """A user with the given settings, one transcript, one pending chunk job.
    Usernames are unique per call so one test can seed more than once."""
    user = User(username=f"chunkuser{next(_seq)}", password_hash="x", password_salt="s")
    db.add(user)
    db.commit()
    if settings:
        update_user_settings(db, user.id, settings)
    t = Transcript(user_id=user.id, title="t", filename="long.wav", status="processing")
    db.add(t)
    db.commit()
    job = TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0.0,
                           end_time=300.0, audio_path="chunk_0.mp3", status="pending")
    db.add(job)
    db.commit()
    return user, t, job


def _stub_result():
    """Segments carrying confidence and no_speech_prob, the fields the filter
    reads — a filter call on a bare segment list would prove nothing."""
    return TranscriptionResult(
        segments=[Segment(start=0.0, end=2.0, text="hello hello hello",
                          confidence=-5.0, no_speech_prob=0.9)],
        full_text="hello hello hello",
        language="en",
        model="whisper-large-v3",
    )


def _run(db, job, provider):
    """Drive _run_chunk_job with a stubbed provider. 'groq' keeps the local
    provider semaphore out of the picture (it is not in LOCAL_PROVIDERS)."""
    with patch("services.queue.get_provider", return_value=provider):
        asyncio.run(_run_chunk_job(db, job, {"api_key": "k"}, "groq", "en",
                                   asyncio.Semaphore(1)))


def test_chunk_job_passes_user_hallucination_thresholds(db_session):
    _, _, job = _seed(db_session, TUNED)
    provider = MagicMock()
    provider.transcribe = AsyncMock(return_value=_stub_result())

    spy = MagicMock(side_effect=lambda segs, **kw: segs)
    with patch("services.audio_cleanup.filter_hallucinations", spy):
        _run(db_session, job, provider)

    assert spy.call_count == 1
    kwargs = spy.call_args.kwargs
    assert kwargs["rep_window"] == 7
    assert kwargs["logprob_cutoff"] == -4.5
    assert kwargs["no_speech_cutoff"] == 0.25
    assert job.status == "completed"


def test_chunk_job_falls_back_to_defaults_when_user_left_dials_alone(db_session):
    """Enabling the filter without touching the dials must still use the same
    defaults app.py's inline path uses, not zeros or None."""
    _, _, job = _seed(db_session, {"cleanup_hallu_enabled": True})
    provider = MagicMock()
    provider.transcribe = AsyncMock(return_value=_stub_result())

    spy = MagicMock(side_effect=lambda segs, **kw: segs)
    with patch("services.audio_cleanup.filter_hallucinations", spy):
        _run(db_session, job, provider)

    assert spy.call_count == 1
    kwargs = spy.call_args.kwargs
    assert kwargs["rep_window"] == 3
    assert kwargs["logprob_cutoff"] == -2.0
    assert kwargs["no_speech_cutoff"] == 0.6


def test_chunk_job_skips_filter_when_disabled(db_session):
    """The dials are only read to be used — with the filter off, the segments
    must pass through untouched."""
    _, _, job = _seed(db_session, {"cleanup_hallu_enabled": False, **{
        k: v for k, v in TUNED.items() if k != "cleanup_hallu_enabled"}})
    provider = MagicMock()
    provider.transcribe = AsyncMock(return_value=_stub_result())

    spy = MagicMock(side_effect=lambda segs, **kw: segs)
    with patch("services.audio_cleanup.filter_hallucinations", spy):
        _run(db_session, job, provider)

    spy.assert_not_called()
    assert job.result_json["full_text"] == "hello hello hello"


def test_filtered_segments_are_dropped_from_the_chunk_result(db_session):
    """End to end through the real filter, not a spy: a tuned cutoff the
    segment fails must actually remove it from job.result_json."""
    _, _, job = _seed(db_session, TUNED)
    provider = MagicMock()
    provider.transcribe = AsyncMock(return_value=_stub_result())

    _run(db_session, job, provider)

    # rep_window=7 exceeds the 3-token segment, so filter_hallucinations keeps
    # it (len(tokens) < rep_window short-circuits) — the segment survives.
    assert len(job.result_json["segments"]) == 1

    # Same segment, a window it does not escape: 3 repeated tokens at
    # rep_window=2 is a repeated bigram, and confidence -5.0 is below the
    # -4.5 cutoff, so it is dropped.
    _, _, job2 = _seed(db_session, {**TUNED, "cleanup_hallu_rep_window": 2})
    provider2 = MagicMock()
    provider2.transcribe = AsyncMock(return_value=_stub_result())
    _run(db_session, job2, provider2)
    assert job2.result_json["segments"] == []
