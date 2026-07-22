"""Diarization service — speaker segmentation and identification."""
import asyncio
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
            import warnings
            # pyannote.audio warns on import if torchcodec isn't installed —
            # harmless, we don't use torchcodec's in-memory decoding path,
            # but it's noisy on every startup since this check runs eagerly.
            with warnings.catch_warnings():
                # Narrowly scoped to just this one import — doesn't affect
                # warnings anywhere else in the app.
                warnings.filterwarnings("ignore", category=UserWarning)
                import pyannote.audio  # noqa
            return True
        except ImportError:
            return False

    async def diarize_and_merge(
        self,
        audio_path: str,
        num_speakers: Optional[int],
        segments: list[dict],
        hf_token: Optional[str] = None,
    ) -> tuple[list[dict], int, str]:
        """Best-available diarization (pyannote if installed, else the
        pause-gap heuristic) merged onto existing transcript segments.
        num_speakers=None lets pyannote auto-detect; the heuristic can't,
        so it defaults to 2. Returns (merged_segments, speaker_count, method).
        Raises on failure — callers decide whether that's fatal."""
        if self._check_pyannote():
            result = await self.diarize_pyannote(
                audio_path, num_speakers=num_speakers, hf_token=hf_token
            )
        else:
            result = await self.diarize_heuristic(
                audio_path, num_speakers=num_speakers or 2, segments=segments
            )
        merged = await self.combine_with_transcript(result, segments)
        return merged, result.speaker_count, result.method

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
            segments = self._pseudo_segments_from_silence(audio_path)
            if not segments:
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

    @staticmethod
    def _pseudo_segments_from_silence(
        audio_path: str,
        frame_ms: float = 30.0,
        silence_gap_s: float = 0.5,
        energy_ratio: float = 0.15,
    ) -> list[dict]:
        """Chunk raw audio into pseudo-segments via simple energy-based voice
        activity detection, for callers (the standalone /api/diarize
        endpoint) that have no transcript segments to hand the pause-gap
        heuristic above. Frames below `energy_ratio` of peak RMS are
        silence; a run of silence >= silence_gap_s splits a segment."""
        import numpy as np
        import soundfile as sf

        try:
            audio, sr = sf.read(audio_path, always_2d=False)
        except Exception:
            return []
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if len(audio) == 0:
            return []

        frame_len = max(int(sr * frame_ms / 1000), 1)
        n_frames = len(audio) // frame_len
        if n_frames == 0:
            return [{"start": 0.0, "end": len(audio) / sr, "text": ""}]

        frames = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
        energy = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
        threshold = max(float(energy.max()) * energy_ratio, 1e-9)
        frame_s = frame_len / sr
        voiced_times = [i * frame_s for i, e in enumerate(energy) if e > threshold]

        if not voiced_times:
            return []

        pseudo_segments = []
        seg_start = voiced_times[0]
        prev = voiced_times[0]
        for t in voiced_times[1:]:
            if t - prev >= silence_gap_s:
                pseudo_segments.append({"start": seg_start, "end": prev + frame_s, "text": ""})
                seg_start = t
            prev = t
        pseudo_segments.append({"start": seg_start, "end": prev + frame_s, "text": ""})

        return pseudo_segments

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

        def _run() -> list[DiarizationSegment]:
            import torch
            import soundfile as sf
            from pyannote.audio import Pipeline

            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=hf_token or os.environ.get("HUGGINGFACE_TOKEN", None),
            )

            # Load audio ourselves and hand pyannote a waveform tensor rather
            # than a file path — pyannote's built-in decoder requires torchcodec,
            # which needs FFmpeg's shared-library build; Windows installs
            # commonly have the static "full_build" instead, so the decoder
            # fails to load its native DLLs.
            data, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
            waveform = torch.from_numpy(data.T)  # (channel, time)

            output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, num_speakers=num_speakers)

            segments = []
            for turn, _, speaker in output.speaker_diarization.itertracks(yield_label=True):
                segments.append(DiarizationSegment(
                    start=turn.start,
                    end=turn.end,
                    speaker=speaker,
                ))
            return segments

        # Model load + inference are synchronous/blocking (no internal await) —
        # run off the event loop so one diarize call doesn't stall every other
        # request and both worker ticks for its duration.
        loop = asyncio.get_event_loop()
        result_segments = await loop.run_in_executor(None, _run)

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