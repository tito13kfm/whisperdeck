"""combine_with_transcript: per-speaker overlap totals and confidence."""
import pytest

from services.diarization import DiarizationResult, DiarizationSegment, DiarizationService


def _result(segs):
    return DiarizationResult(segments=segs, speaker_count=len({s.speaker for s in segs}), method="pyannote")


@pytest.mark.asyncio
async def test_uncontested_full_overlap_is_high_confidence():
    svc = DiarizationService()
    merged = await svc.combine_with_transcript(
        _result([DiarizationSegment(start=0.0, end=5.0, speaker="A")]),
        [{"start": 1.0, "end": 2.0, "text": "x"}],
    )
    assert merged[0]["speaker"] == "A"
    assert merged[0]["speaker_confidence"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_contested_split_overlap_is_low_confidence():
    svc = DiarizationService()
    merged = await svc.combine_with_transcript(
        _result([
            DiarizationSegment(start=0.0, end=1.1, speaker="A"),
            DiarizationSegment(start=1.1, end=2.0, speaker="B"),
        ]),
        [{"start": 0.0, "end": 2.0, "text": "x"}],
    )
    assert merged[0]["speaker"] == "A"  # 1.1s beats 0.9s
    assert merged[0]["speaker_confidence"] < 0.5


@pytest.mark.asyncio
async def test_adjacent_turns_of_same_speaker_are_not_competition():
    svc = DiarizationService()
    merged = await svc.combine_with_transcript(
        _result([
            DiarizationSegment(start=0.0, end=1.0, speaker="A"),
            DiarizationSegment(start=1.0, end=2.0, speaker="A"),
        ]),
        [{"start": 0.0, "end": 2.0, "text": "x"}],
    )
    assert merged[0]["speaker"] == "A"
    assert merged[0]["speaker_confidence"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_zero_overlap_keeps_prior_speaker_with_zero_confidence():
    svc = DiarizationService()
    merged = await svc.combine_with_transcript(
        _result([DiarizationSegment(start=10.0, end=11.0, speaker="A")]),
        [{"start": 0.0, "end": 2.0, "text": "x", "speaker": "KEEP_ME"}],
    )
    assert merged[0]["speaker"] == "KEEP_ME"
    assert merged[0]["speaker_confidence"] == 0.0
