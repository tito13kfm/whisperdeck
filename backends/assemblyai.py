"""AssemblyAI provider — cloud STT via AssemblyAI's API.

AssemblyAI uses an async transcription model: upload audio, poll for
completion.  Model: universal-3-pro at $0.21/hr, with optional add-ons
for diarization, summarization, etc.
"""
import time
import asyncio
import httpx
from .base import BaseProvider, TranscriptionResult, ProviderError


class AssemblyAIProvider(BaseProvider):
    """Transcribe using AssemblyAI's hosted STT API."""

    API_BASE = "https://api.assemblyai.com/v2"

    def __init__(self, config: dict):
        super().__init__(config)
        self.model = config.get("default_model") or "universal-3-pro"

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        if not self.api_key:
            raise ProviderError(
                "AssemblyAI API key not configured. Set it in Settings > Providers."
            )

        language = kwargs.get("language", None)
        diarize = kwargs.get("diarize", False)
        start_time = time.time()

        headers = {"Authorization": self.api_key}

        async with httpx.AsyncClient(timeout=600) as client:
            # Step 1: upload the audio file
            upload_url = await self._upload_file(client, headers, audio_path)

            # Step 2: submit a transcript request
            transcript_id = await self._submit_transcript(
                client, headers, upload_url, language, diarize
            )

            # Step 3: poll for completion
            result = await self._poll_transcript(client, headers, transcript_id)

        duration_seconds = result.get("audio_duration", 0)
        raw_segments = [
            {
                "start": u.get("start", 0),
                "end": u.get("end", 0),
                "text": u.get("text", "").strip(),
                "speaker": u.get("speaker"),
                "confidence": u.get("confidence"),
            }
            for u in result.get("utterances", [])
            if u.get("text", "").strip()
        ]

        if not raw_segments:
            # Fallback: use the full text if no utterances
            return TranscriptionResult(
                full_text=result.get("text", ""),
                language=result.get("language_code") or language or "en",
                duration_seconds=duration_seconds,
                model=self.model,
                provider="assemblyai",
                processing_time=time.time() - start_time,
            )

        return TranscriptionResult(
            segments=self._build_segments(raw_segments),
            full_text=result.get("text", ""),
            language=result.get("language_code") or language or "en",
            duration_seconds=duration_seconds,
            model=self.model,
            provider="assemblyai",
            processing_time=time.time() - start_time,
        )

    async def _upload_file(
        self, client: httpx.AsyncClient, headers: dict, audio_path: str
    ) -> str:
        """Upload audio to AssemblyAI and return the hosted URL."""
        with open(audio_path, "rb") as f:
            resp = await client.post(
                f"{self.API_BASE}/upload",
                headers=headers,
                content=f,
            )
        if resp.status_code == 429:
            raise ProviderError(
                "AssemblyAI rate limit exceeded (too many concurrent uploads). "
                "The free tier allows up to 2 concurrent transcriptions. "
                "Try again later or upgrade your plan."
            )
        if resp.status_code != 200:
            raise ProviderError(
                f"AssemblyAI upload failed ({resp.status_code}): {resp.text}"
            )
        return resp.json()["upload_url"]

    async def _submit_transcript(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        audio_url: str,
        language: str | None,
        diarize: bool,
    ) -> str:
        """Submit a transcription request and return the transcript ID."""
        body = {
            "audio_url": audio_url,
            "speech_model": self.model,
        }
        if language and language != "auto":
            body["language_code"] = language
        if diarize:
            body["speaker_labels"] = True

        resp = await client.post(
            f"{self.API_BASE}/transcript",
            headers=headers,
            json=body,
        )
        if resp.status_code != 200:
            raise ProviderError(
                f"AssemblyAI transcript submission failed ({resp.status_code}): {resp.text}"
            )
        return resp.json()["id"]

    async def _poll_transcript(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        transcript_id: str,
    ) -> dict:
        """Poll for transcript completion with exponential backoff."""
        delays = [1, 2, 3, 5, 8, 13, 21, 34, 55]
        for delay in delays:
            resp = await client.get(
                f"{self.API_BASE}/transcript/{transcript_id}",
                headers=headers,
            )
            if resp.status_code != 200:
                raise ProviderError(
                    f"AssemblyAI transcript polling failed ({resp.status_code}): {resp.text}"
                )
            data = resp.json()
            status = data.get("status")
            if status == "completed":
                return data
            if status == "error":
                error_msg = data.get("error", "Unknown error")
                raise ProviderError(f"AssemblyAI transcription error: {error_msg}")
            await asyncio.sleep(delay)

        # Last attempt after all delays exhausted
        resp = await client.get(
            f"{self.API_BASE}/transcript/{transcript_id}",
            headers=headers,
        )
        if resp.status_code != 200:
            raise ProviderError(
                f"AssemblyAI transcript polling failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        if data.get("status") == "completed":
            return data
        if data.get("status") == "error":
            raise ProviderError(f"AssemblyAI transcription error: {data.get('error')}")
        raise ProviderError(
            f"AssemblyAI transcription timed out after ~140s polling. "
            f"Final status: {data.get('status')}"
        )

    async def list_models(self) -> list[str]:
        return ["universal-3-pro", "universal-2"]

    async def check_health(self) -> dict:
        if not self.api_key:
            return {"ok": False, "error": "No API key configured"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.API_BASE}/transcript",
                    headers={"Authorization": self.api_key},
                )
                return {
                    "ok": resp.status_code in (200, 404),
                    "error": None if resp.status_code in (200, 404) else resp.text,
                }
        except Exception as e:
            return {"ok": False, "error": str(e)}
