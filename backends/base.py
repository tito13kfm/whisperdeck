"""Base provider interface for all transcription backends."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class ProviderError(Exception):
    """Raised when a provider encounters an error."""
    pass


@dataclass
class Segment:
    """A single transcribed segment with optional speaker label."""
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    confidence: Optional[float] = None


@dataclass
class TranscriptionResult:
    """Full result from a transcription call."""
    segments: list[Segment] = field(default_factory=list)
    full_text: str = ""
    language: str = "en"
    duration_seconds: float = 0.0
    model: str = ""
    provider: str = ""
    processing_time: float = 0.0


class BaseProvider(ABC):
    """Abstract base class for transcription providers."""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = config.get("api_key", "")
        self.api_url = config.get("api_url", "")
        # `or` (not .get's default arg) so a saved-but-empty default_model in
        # the DB doesn't shadow this fallback with "".
        self.model = config.get("default_model") or "whisper-large-v3-turbo"

    @abstractmethod
    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        """Transcribe an audio file and return the result."""
        ...

    @abstractmethod
    async def check_health(self) -> dict:
        """Check if the provider is configured and reachable."""
        ...

    async def list_models(self) -> list[str]:
        """Return a list of available transcription model IDs for this provider.

        Default implementation returns the configured default model.
        Override to fetch live from the provider's API.
        """
        return [self.model]

    def _build_segments(self, raw_segments: list[dict]) -> list[Segment]:
        """Convert raw segment dicts from API responses into Segment objects."""
        return [
            Segment(
                start=s.get("start", 0),
                end=s.get("end", 0),
                text=s.get("text", "").strip(),
                speaker=s.get("speaker"),
                confidence=s.get("confidence"),
            )
            for s in raw_segments
        ]


__all__ = ["BaseProvider", "ProviderError", "Segment", "TranscriptionResult"]