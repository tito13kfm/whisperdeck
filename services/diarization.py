"""Diarization service — speaker segmentation and identification."""
import os
import json
import tempfile
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class DiarizationSegment:
    start: float
    end: float
    speaker: str
    text: Optional[str] = None


@dataclass
class DiarizationResult:
    segments: list[DiarizationSegment] = field(default_factory=list)
    speaker_count: int = 0
    method: str = "none"


class DiarizationService:
    """Speaker diarization using clustering heuristics or pyannote (optional).

    Two modes:
      1. Heuristic — assigns speakers based on audio pause-gap clustering (no ML dep)
      2. ML-based — uses pyannote.audio if installed (SPEAKER_01, SPEAKER_02, ...)
    """

    def __init__(self):
        self.pyannote_available = self._check_pyannote()

    def _check_pyannote(self) -> bool:
        try:
            import pyannote.audio  # noqa
            return True
        except ImportError:
            return False

    async def diarize_heuristic(
        self,
        audio_path: str,
        num_speakers: int = 2,
        segments: Optional[list[dict]] = None,
    ) -> DiarizationResult:
        """Simple heuristic diarization: assign speaker labels based on segment
        timing patterns. Uses pause-gap clustering — segments close in time
        are likely the same speaker; long gaps suggest speaker changes.

        This is a best-effort approach when pyannote is not installed.
        For real accuracy, install pyannote.audio.
        """
        if not segments:
            # Try to get duration from the file
            try:
                import soundfile as sf
                info = sf.info(audio_path)
                total_duration = info.duration
            except Exception:
                total_duration = 0

            # Create pseudo-segments from silence detection
            return DiarizationResult(
                segments=[], speaker_count=0, method="heuristic"
            )

        # Sort segments by start time
        sorted_segs = sorted([dict(s) for s in segments], key=lambda s: s.get("start", 0))

        # Simple alternating speaker assignment based on gaps > 1.5s
        # A more sophisticated approach would use actual voice embeddings
        speakers = []
        last_end = 0
        speaker_idx = 0
        speaker_labels = [f"Speaker {i+1}" for i in range(max(num_speakers, 2))]

        for seg in sorted_segs:
            gap = seg.get("start", 0) - last_end
            if gap > 1.5 and last_end > 0:
                speaker_idx = (speaker_idx + 1) % len(speaker_labels)

            speakers.append(DiarizationSegment(
                start=seg.get("start", 0),
                end=seg.get("end", 0),
                speaker=speaker_labels[speaker_idx],
                text=seg.get("text", ""),
            ))
            last_end = seg.get("end", 0)

        speaker_set = set(s.speaker for s in speakers)
        return DiarizationResult(
            segments=speakers,
            speaker_count=len(speaker_set),
            method="heuristic",
        )

    async def diarize_pyannote(
        self,
        audio_path: str,
        num_speakers: Optional[int] = None,
        hf_token: Optional[str] = None,
    ) -> DiarizationResult:
        """Diarize using pyannote.audio (requires torch + pyannote.audio installed).

        hf_token is the caller's HuggingFace read token (pyannote's models
        are gated). Falls back to the HUGGINGFACE_TOKEN env var if not
        passed, for backwards compatibility with the old setup method."""
        if not self.pyannote_available:
            raise ImportError(
                "pyannote.audio is not installed. "
                "Install it with: pip install pyannote.audio torch"
            )

        from pyannote.audio import Pipeline
        from pyannote.core import Segment

        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token or os.environ.get("HUGGINGFACE_TOKEN", None),
        )

        if num_speakers:
            pipeline.instantiate({"clustering": {"threshold": 0.7}})

        diarization = pipeline(audio_path, num_speakers=num_speakers)

        result_segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            result_segments.append(DiarizationSegment(
                start=turn.start,
                end=turn.end,
                speaker=speaker,
            ))

        speaker_set = set(s.speaker for s in result_segments)
        return DiarizationResult(
            segments=result_segments,
            speaker_count=len(speaker_set),
            method="pyannote",
        )

    async def combine_with_transcript(
        self,
        diarization: DiarizationResult,
        transcript_segments: list[dict],
    ) -> list[dict]:
        """Merge diarization speaker labels with transcript segments using
        overlapping time windows."""
        if not diarization.segments:
            return transcript_segments

        merged = []
        for seg in transcript_segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)

            # Find which diarization segment overlaps most with this transcript segment
            best_speaker = None
            best_overlap = 0

            for dseg in diarization.segments:
                overlap_start = max(seg_start, dseg.start)
                overlap_end = min(seg_end, dseg.end)
                overlap = max(0, overlap_end - overlap_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = dseg.speaker

            merged.append({
                **seg,
                "speaker": best_speaker or seg.get("speaker", "Unknown"),
            })

        return merged