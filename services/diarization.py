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


class MissingTokenError(ValueError):
    """pyannote requested but no HuggingFace token configured (issue #119)."""


HF_TOKEN_HELP = (
    "HuggingFace token required for pyannote speaker diarization. "
    "Set it in Settings → Service Panel or the HUGGINGFACE_TOKEN "
    "environment variable."
)


def degraded_error_text(fallback_error: str) -> str:
    """One shared wording for the three call sites that persist a
    pyannote-failed-but-heuristic-rescued warning (issue #121)."""
    return (f"Diarization degraded: pyannote failed ({fallback_error}); "
            f"used pause-gap heuristic")


def resolve_hf_token(hf_token: Optional[str]) -> str:
    """Explicit token, else the HUGGINGFACE_TOKEN env var, else a clear
    MissingTokenError instead of pyannote's cryptic gated-model 401.
    Treats None, "" and whitespace alike — the settings default is ""
    (services/settings.py), so an unset token arrives as an empty string,
    never None. Also used by services/voice_id.py for its pyannote
    embedding backend (same gated-model auth)."""
    token = (hf_token or "").strip() or os.environ.get("HUGGINGFACE_TOKEN", "").strip()
    if not token:
        raise MissingTokenError(HF_TOKEN_HELP)
    return token


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
        stereo_audio_path: Optional[str] = None,
    ) -> tuple[list[dict], int, str, Optional[str]]:
        """Best-available diarization merged onto existing transcript
        segments: channel-aware live-stereo when a stereo copy exists and
        pyannote is installed, else pyannote on the mixed audio, else the
        pause-gap heuristic (which can't auto-detect, so it defaults to 2).

        Returns (merged_segments, speaker_count, method, fallback_error).
        fallback_error is non-None when pyannote was attempted and failed
        but the heuristic rescued the run (issue #121) — callers surface it
        so the degradation is never silent; `method` then reads
        "heuristic (pyannote failed)" to distinguish rescue-heuristic from
        heuristic-by-configuration in the DB and the UI sub-label.
        Still raises if the heuristic itself fails — the issue-#120 hard
        failure surfacing in callers stays live for that case."""
        fallback_error: Optional[str] = None
        result = None
        if stereo_audio_path and os.path.exists(stereo_audio_path) and self._check_pyannote():
            try:
                result = await self.diarize_live_stereo(
                    stereo_audio_path, num_speakers=num_speakers, hf_token=hf_token
                )
            except Exception as e:
                print(f"[diarization] live-stereo path failed ({e}); falling back to mixed audio")
                try:
                    result = await self.diarize_pyannote(
                        audio_path, num_speakers=num_speakers, hf_token=hf_token
                    )
                except Exception as e2:
                    fallback_error = str(e2)
        elif self._check_pyannote():
            try:
                result = await self.diarize_pyannote(
                    audio_path, num_speakers=num_speakers, hf_token=hf_token
                )
            except Exception as e:
                fallback_error = str(e)
        if result is None:
            # pyannote not installed, or every pyannote tier failed.
            if fallback_error:
                print(f"[diarization] pyannote failed ({fallback_error}); "
                      f"falling back to pause-gap heuristic")
            result = await self.diarize_heuristic(
                audio_path, num_speakers=num_speakers or 2, segments=segments
            )
        # 27 chars — fits the diarization_method String(32) column.
        method = "heuristic (pyannote failed)" if fallback_error else result.method
        merged = await self.combine_with_transcript(result, segments)
        return merged, result.speaker_count, method, fallback_error

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

        # If the gap alternation assigned fewer labels than requested,
        # reassign duplicate segments to unused labels, preserving at
        # least one segment per existing label.
        used = set(s.speaker for s in speakers)
        if len(used) < len(speaker_labels):
            # Count how many segments each label has so we only reassign
            # labels that appear more than once.
            counts = {}
            for s in speakers:
                counts[s.speaker] = counts.get(s.speaker, 0) + 1
            unused = [l for l in speaker_labels if l not in used]
            ui = 0
            for s in speakers:
                if ui >= len(unused):
                    break
                if counts.get(s.speaker, 0) > 1:
                    counts[s.speaker] -= 1
                    s.speaker = unused[ui]
                    ui += 1

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

    def _run_pyannote_sync(
        self,
        waveform,
        sample_rate: int,
        num_speakers: Optional[int],
        hf_token: Optional[str],
    ) -> list[DiarizationSegment]:
        """Blocking pyannote inference on a (channel, time) audio buffer —
        either a torch tensor or a plain numpy array. Callers wrap this in
        run_in_executor; imports stay inside so machines without torch can
        still import this module. A plain numpy array (e.g. from
        diarize_live_stereo, which never needs torch on the async side) is
        converted to a tensor here, keeping the torch dependency confined to
        this one method."""
        # Token guard BEFORE the torch import: the clear error must win
        # even on machines where torch itself would fail to import, and
        # tests can exercise it on torch-less venvs (issue #119).
        token = resolve_hf_token(hf_token)

        import torch
        from pyannote.audio import Pipeline

        if not isinstance(waveform, torch.Tensor):
            waveform = torch.from_numpy(waveform)

        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=token,
        )
        output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, num_speakers=num_speakers)
        return [
            DiarizationSegment(start=turn.start, end=turn.end, speaker=speaker)
            for turn, _, speaker in output.speaker_diarization.itertracks(yield_label=True)
        ]

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
        # Validate the token before the executor hop: _run imports torch
        # before it reaches _run_pyannote_sync's own guard, and the clear
        # missing-token error must win over a torch ImportError (issue #119).
        resolve_hf_token(hf_token)

        def _run() -> list[DiarizationSegment]:
            import torch
            import soundfile as sf

            # Load audio ourselves and hand pyannote a waveform tensor rather
            # than a file path — pyannote's built-in decoder requires torchcodec,
            # which needs FFmpeg's shared-library build; Windows installs
            # commonly have the static "full_build" instead, so the decoder
            # fails to load its native DLLs.
            data, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
            waveform = torch.from_numpy(data.T)  # (channel, time)
            return self._run_pyannote_sync(waveform, sample_rate, num_speakers, hf_token)

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
            duration = max(seg_end - seg_start, 1e-6)

            # Total overlap per SPEAKER, not per turn: one speaker usually
            # owns several adjacent diarization turns, and treating those as
            # competing would mark nearly every line uncertain.
            per_speaker: dict[str, float] = {}
            for dseg in diarization.segments:
                overlap = max(0.0, min(seg_end, dseg.end) - max(seg_start, dseg.start))
                if overlap > 0:
                    per_speaker[dseg.speaker] = per_speaker.get(dseg.speaker, 0.0) + overlap

            if per_speaker:
                # Heuristic-diarized transcripts always land here at 1.0: the
                # heuristic's own turns are byte-identical to these transcript
                # segments, so there is never a rival speaker to contest against.
                # The signal is only informative for pyannote / live_stereo.
                ranked = sorted(per_speaker.items(), key=lambda kv: kv[1], reverse=True)
                best_speaker, best_total = ranked[0]
                second_total = ranked[1][1] if len(ranked) > 1 else 0.0
                coverage = min(best_total / duration, 1.0)
                margin = (best_total - second_total) / best_total  # 1.0 when uncontested
                confidence = round(coverage * margin, 3)
            else:
                best_speaker, confidence = None, 0.0

            merged.append({
                **seg,
                "speaker": best_speaker or seg.get("speaker", "Unknown"),
                "speaker_confidence": confidence,
            })

        return merged

    @staticmethod
    def _active_intervals(
        channel,
        sample_rate: int,
        frame_ms: int = 30,
        threshold_ratio: float = 4.0,
        min_speech_s: float = 0.25,
        max_gap_s: float = 0.6,
    ) -> list[tuple[float, float]]:
        """Energy VAD over one channel: frames whose RMS exceeds
        threshold_ratio times the noise floor (10th-percentile frame RMS)
        count as speech; speech runs closer than max_gap_s merge; runs
        shorter than min_speech_s drop. Returns [(start_s, end_s), ...].

        Uses the 10th percentile rather than a higher one so the floor
        estimate stays anchored to true background noise even on clips
        that are mostly speech (e.g. a near-continuous single-speaker
        turn) rather than drifting up into the signal itself.

        Known blind spot: this is a purely relative-floor VAD, so a channel
        with zero internal silence (fully continuous energy, no pauses at
        all) has no low-RMS frames to anchor the floor to and will not be
        detected as speech. Real microphone audio always has some pause
        structure, so this hasn't been worth fixing with an absolute floor
        (which would need calibrating against real room-tone levels, not
        the digital-zero silence typical of unit-test fixtures)."""
        import numpy as np

        frame = int(sample_rate * frame_ms / 1000)
        n = len(channel) // frame
        if n == 0:
            return []
        rms = np.sqrt((channel[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
        floor = float(np.percentile(rms, 10)) + 1e-8
        speech = rms > threshold_ratio * floor

        intervals: list[tuple[float, float]] = []
        start = None
        for i, flag in enumerate(speech):
            t = i * frame / sample_rate
            if flag and start is None:
                start = t
            elif not flag and start is not None:
                intervals.append((start, t))
                start = None
        if start is not None:
            intervals.append((start, n * frame / sample_rate))

        merged: list[tuple[float, float]] = []
        for s, e in intervals:
            if merged and s - merged[-1][1] <= max_gap_s:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
        return [(s, e) for s, e in merged if e - s >= min_speech_s]

    async def diarize_live_stereo(
        self,
        stereo_path: str,
        num_speakers: Optional[int] = None,
        hf_token: Optional[str] = None,
    ) -> DiarizationResult:
        """Channel-aware diarization for live captures (mic on channel 0,
        system audio on channel 1, see static/rack.js live capture). The mic
        channel needs no clustering: any speech there is the local user.
        Remote speakers exist only on the system channel, so pyannote runs
        on that channel alone with one fewer expected speaker."""
        import numpy as np
        import soundfile as sf

        data, sample_rate = sf.read(stereo_path, dtype="float32", always_2d=True)
        if data.shape[1] < 2:
            raise ValueError(f"{stereo_path} has fewer than 2 channels — cannot channel-split")
        mic, system = data[:, 0], data[:, 1]
        # Dual-mono defense: a mono source upmixed somewhere along the chain
        # yields two identical channels. The bleed filter's mic-dominance
        # test can never pass on those (rms_mic == rms_sys), so we'd silently
        # emit zero "You" segments while still shrinking pyannote's expected
        # count. Raising instead lets diarize_and_merge fall back to the
        # ordinary mixed-audio path.
        if np.array_equal(mic, system):
            raise ValueError(
                f"{stereo_path} channels are identical (dual-mono) — no channel separation to exploit"
            )

        mic_intervals = self._drop_bleed(
            self._active_intervals(mic, sample_rate), mic, system, sample_rate
        )
        segments = [
            DiarizationSegment(start=s, end=e, speaker="You") for s, e in mic_intervals
        ]

        remote_count = (num_speakers - 1) if num_speakers else None
        system_intervals = self._active_intervals(system, sample_rate)
        # The remote channel runs through pyannote only when there is work
        # for it (remote speakers expected, system channel not silent). In
        # production the pyannote_available leg never gates anything —
        # diarize_and_merge only routes here when pyannote is installed, and
        # falls through to the heuristic otherwise (a deliberate, test-pinned
        # choice) — it is defense-in-depth for direct callers, degrading to
        # mic-only "You" segments rather than crashing in _run_pyannote_sync.
        if remote_count != 0 and system_intervals and self.pyannote_available:
            # Hand _run_pyannote_sync a plain numpy array rather than a torch
            # tensor — it converts internally, so this async method (and its
            # tests, which monkeypatch _run_pyannote_sync entirely) never
            # need torch importable.
            waveform = np.ascontiguousarray(system[np.newaxis, :])
            loop = asyncio.get_event_loop()
            remote = await loop.run_in_executor(
                None, self._run_pyannote_sync, waveform, sample_rate, remote_count, hf_token
            )
            segments.extend(remote)

        segments.sort(key=lambda s: s.start)
        speaker_set = set(s.speaker for s in segments)
        return DiarizationResult(
            segments=segments, speaker_count=len(speaker_set), method="live_stereo"
        )

    @staticmethod
    def _drop_bleed(
        intervals: list[tuple[float, float]],
        mic,
        system,
        sample_rate: int,
        dominance: float = 1.2,
    ) -> list[tuple[float, float]]:
        """Remote voices leak into the mic through speakers. A genuine local
        utterance is louder on the mic channel than on the system channel
        over the same span; bleed is the reverse. Keep mic-dominant spans."""
        import numpy as np

        kept = []
        for s, e in intervals:
            a, b = int(s * sample_rate), int(e * sample_rate)
            rms_mic = float(np.sqrt((mic[a:b] ** 2).mean() + 1e-12))
            rms_sys = float(np.sqrt((system[a:b] ** 2).mean() + 1e-12))
            if rms_mic > rms_sys * dominance:
                kept.append((s, e))
        return kept