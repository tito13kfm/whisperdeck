"""Issue #416, design decision 1: the diarization eligibility pre-pass.

Helper-level coverage for services.audio_prep.evaluate_diarization_eligibility
and the detect_silence_gaps/detect_silence_midpoints split it was built on
top of. Route-level regression tests for the two call sites (initial-flag
guard in _run_transcription_pipeline, rediarize's pre-pass gate) live in
tests/test_reformatting.py and tests/test_pending_classification_guards.py,
next to the existing tests for those guards.
"""
import asyncio

from unittest.mock import patch

import pytest

from services.audio_prep import (
    AudioPrepError,
    DiarizationEligibility,
    MAX_CONTINUOUS_SPEECH_SECONDS,
    MIN_DIARIZATION_DURATION_SECONDS,
    detect_silence_gaps,
    detect_silence_midpoints,
    evaluate_diarization_eligibility,
    evaluate_diarization_eligibility_async,
)


def _gap(start, end):
    return {"start": start, "end": end, "duration": end - start}


# ── Veto (a): duration below the minimum ────────────────────────────────


def test_short_clip_is_ineligible():
    with patch("services.audio_prep.get_audio_duration", return_value=10.0), \
         patch("services.audio_prep.detect_silence_gaps", return_value=[]):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is False
    assert result.reason != ""


# ── Veto (b): a continuous speech run over the cap ──────────────────────


def test_continuous_monologue_is_ineligible():
    """No silence gaps at all over a long recording -> the whole thing is
    one run, and that run exceeds the cap."""
    with patch("services.audio_prep.get_audio_duration", return_value=400.0), \
         patch("services.audio_prep.detect_silence_gaps", return_value=[]):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is False
    assert result.reason != ""


# ── Default eligible path ───────────────────────────────────────────────


def test_normal_recording_with_regular_pauses_is_eligible():
    """600s recording, a gap every 60s -> longest run ~60s, nowhere near
    the 300s cap, and comfortably over the 30s duration floor."""
    gaps = [_gap(60.0 * i, 60.0 * i + 1.0) for i in range(1, 10)]
    with patch("services.audio_prep.get_audio_duration", return_value=600.0), \
         patch("services.audio_prep.detect_silence_gaps", return_value=gaps):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is True
    assert result.reason == ""


# ── Head/tail runs must be counted, not just between-gap runs ──────────


def test_overlong_head_run_is_ineligible():
    """The only long run is BEFORE the first gap (0 -> 350). A naive
    implementation that only looks at gaps[i+1].start - gaps[i].end would
    miss this entirely and report eligible."""
    gaps = [_gap(350.0, 352.0), _gap(400.0, 402.0)]
    with patch("services.audio_prep.get_audio_duration", return_value=700.0), \
         patch("services.audio_prep.detect_silence_gaps", return_value=gaps):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is False
    assert result.reason != ""


def test_overlong_tail_run_is_ineligible():
    """The only long run is AFTER the last gap (152 -> 500 = 348s)."""
    gaps = [_gap(100.0, 102.0), _gap(150.0, 152.0)]
    with patch("services.audio_prep.get_audio_duration", return_value=500.0), \
         patch("services.audio_prep.detect_silence_gaps", return_value=gaps):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is False
    assert result.reason != ""


def test_short_head_and_tail_with_long_middle_gap_is_eligible():
    """Complement of the two tests above: head and tail are both short,
    only the well-covered middle is long, but it's broken up by gaps so no
    single run exceeds the cap. Sanity check that head/tail counting
    doesn't over-trigger on ordinary recordings."""
    gaps = [_gap(10.0, 12.0), _gap(310.0, 312.0), _gap(610.0, 612.0)]
    with patch("services.audio_prep.get_audio_duration", return_value=620.0), \
         patch("services.audio_prep.detect_silence_gaps", return_value=gaps):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is True
    assert result.reason == ""


# ── Boundary values: strict inequalities, not <=/>= ─────────────────────


def test_duration_exactly_at_minimum_is_eligible():
    """30.0s exactly must be eligible -- the veto is duration < minimum,
    not <=."""
    with patch("services.audio_prep.get_audio_duration", return_value=MIN_DIARIZATION_DURATION_SECONDS), \
         patch("services.audio_prep.detect_silence_gaps", return_value=[]):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is True
    assert result.reason == ""


def test_longest_run_exactly_at_cap_is_eligible():
    """A longest run of exactly 300.0s must be eligible -- the veto is
    run > cap, not >=. A single gap at [300, 302] on a 350s recording
    gives a head run of exactly 300.0 (the cap) and a tail run of 48.0
    (well under it), so 300.0 is the longest run in the whole recording."""
    duration = MAX_CONTINUOUS_SPEECH_SECONDS + 50.0  # 350.0
    gaps = [_gap(MAX_CONTINUOUS_SPEECH_SECONDS, MAX_CONTINUOUS_SPEECH_SECONDS + 2.0)]
    # head run = 300.0 - 0 = 300.0 (exactly the cap)
    # tail run = 350.0 - 302.0 = 48.0
    with patch("services.audio_prep.get_audio_duration", return_value=duration), \
         patch("services.audio_prep.detect_silence_gaps", return_value=gaps):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is True
    assert result.reason == ""


# ── Fail-open: probe failures must never veto ───────────────────────────


def test_duration_probe_audio_prep_error_fails_open():
    with patch("services.audio_prep.get_audio_duration", side_effect=AudioPrepError("ffprobe failed")):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is True


def test_duration_probe_oserror_fails_open():
    """Missing ffmpeg/ffprobe binary case."""
    with patch("services.audio_prep.get_audio_duration", side_effect=OSError("ffprobe not found")):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is True


def test_silence_gaps_probe_audio_prep_error_fails_open():
    with patch("services.audio_prep.get_audio_duration", return_value=600.0), \
         patch("services.audio_prep.detect_silence_gaps", side_effect=AudioPrepError("ffmpeg failed")):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is True


class _FailedProbe:
    """subprocess.run result for an ffmpeg invocation that exited nonzero."""
    returncode = 1
    stdout = ""
    stderr = "Invalid data found when processing input\n"


def test_failed_silence_probe_fails_open_instead_of_vetoing():
    """Regression, PR #418 review: a silence probe that EXITS NONZERO produces
    no silence_end lines, which is byte-identical to a recording that genuinely
    has no pauses. Reading the empty result as "no pauses" vetoed any recording
    over the continuous-speech cap on the strength of a tool failure alone.

    The tests above this one all mock detect_silence_gaps itself, so they could
    never see this — the bug lived one layer below them, in how a nonzero exit
    was (not) distinguished from a clean scan. This one mocks subprocess.run.
    """
    with patch("services.audio_prep.get_audio_duration", return_value=600.0), \
         patch("services.audio_prep.subprocess.run", return_value=_FailedProbe()):
        result = evaluate_diarization_eligibility("fake.mp3")
    assert result.eligible is True
    assert result.reason == ""


def test_detect_silence_gaps_strict_raises_on_probe_failure():
    with patch("services.audio_prep.subprocess.run", return_value=_FailedProbe()):
        with pytest.raises(AudioPrepError):
            detect_silence_gaps("fake.mp3", strict=True)


def test_async_wrapper_runs_the_probe_off_the_event_loop():
    """PR #418 review, should-fix: both guard sites are async handlers, and the
    probe is two ffmpeg subprocesses (one decodes the whole file). Running that
    inline stalls every other request on the loop.

    The route-level tests can't see this — they patch the wrapper itself with an
    AsyncMock, so its body never runs. Assert the hop directly instead: without
    asyncio.to_thread the coroutine still awaits fine and every other test stays
    green, which is exactly why this needs its own check.
    """
    real_to_thread = asyncio.to_thread
    threaded = []

    async def spy(fn, *args, **kwargs):
        threaded.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    with patch("services.audio_prep.get_audio_duration", return_value=600.0), \
         patch("services.audio_prep.detect_silence_gaps",
               return_value=[_gap(60.0 * i, 60.0 * i + 1.0) for i in range(1, 10)]), \
         patch("services.audio_prep.asyncio.to_thread", spy):
        result = asyncio.run(evaluate_diarization_eligibility_async("fake.mp3"))

    assert threaded == [evaluate_diarization_eligibility]
    assert result.eligible is True


def test_detect_silence_gaps_lenient_returns_empty_on_probe_failure():
    """chunk_audio() has always relied on the lenient default: an empty list
    makes it fall back to fixed-interval cuts. Raising there instead would turn
    graceful degradation into a failed transcription, so the default must stay
    lenient even though the eligibility caller needs the opposite.
    """
    with patch("services.audio_prep.subprocess.run", return_value=_FailedProbe()):
        assert detect_silence_gaps("fake.mp3") == []
        assert detect_silence_midpoints("fake.mp3") == []


# ── detect_silence_midpoints must still behave identically post-refactor ──


def test_detect_silence_midpoints_matches_old_behavior():
    """detect_silence_midpoints is now a thin wrapper over
    detect_silence_gaps. Given the same gaps the old single-pass
    implementation would have found, it must return exactly the same
    list[float] of midpoints, in the same order."""
    gaps = [
        {"start": 10.0, "end": 12.0, "duration": 2.0},
        {"start": 100.0, "end": 100.6, "duration": 0.6},
        {"start": 250.0, "end": 253.0, "duration": 3.0},
    ]
    with patch("services.audio_prep.detect_silence_gaps", return_value=gaps):
        midpoints = detect_silence_midpoints("fake.mp3")
    assert midpoints == [11.0, 100.3, 251.5]
    assert all(isinstance(m, float) for m in midpoints)
