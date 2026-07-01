"""Transcription service — orchestrates provider calls and database persistence."""
import os
import datetime
import json
from typing import Optional

from database import Transcript, Summary
from backends import get_provider, ProviderError
from backends.base import BaseProvider, TranscriptionResult


class TranscriptionService:
    """Coordinates transcription jobs: file handling, provider calls, DB persistence."""

    def __init__(self, db_session, upload_dir: str = "data/uploads"):
        self.db = db_session
        self.upload_dir = upload_dir
        os.makedirs(upload_dir, exist_ok=True)

    async def transcribe(
        self,
        audio_path: str,
        provider_name: str = "groq",
        provider_config: Optional[dict] = None,
        title: Optional[str] = None,
        language: str = "en",
        model: Optional[str] = None,
        temperature: float = 0.0,
        **kwargs,
    ) -> Transcript:
        """Transcribe an audio file and persist the result."""
        provider_config = provider_config or {}
        if model:
            provider_config["default_model"] = model

        provider = get_provider(provider_name, provider_config)

        filename = os.path.basename(audio_path)
        transcript = Transcript(
            title=title or os.path.splitext(filename)[0],
            filename=filename,
            provider=provider_name,
            model=provider_config.get("default_model", ""),
            language=language,
            status="processing",
        )
        self.db.add(transcript)
        self.db.commit()

        try:
            result = await provider.transcribe(
                audio_path, language=language, temperature=temperature, **kwargs
            )

            transcript.status = "completed"
            transcript.full_text = result.full_text
            if result.language:
                transcript.language = result.language
            if result.model:
                transcript.model = result.model
            transcript.segments = [
                {
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "speaker": s.speaker,
                    "confidence": s.confidence,
                }
                for s in result.segments
            ]
            transcript.duration_seconds = result.duration_seconds
            transcript.updated_at = datetime.datetime.utcnow()

            transcript_dir = os.path.join(os.path.dirname(self.upload_dir), "transcripts")
            os.makedirs(transcript_dir, exist_ok=True)
            txt_path = os.path.join(
                transcript_dir, f"{transcript.id}_{os.path.splitext(filename)[0]}.txt"
            )
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(result.full_text)

            self.db.commit()
            return transcript

        except Exception as e:
            transcript.status = "failed"
            transcript.error = str(e)
            transcript.updated_at = datetime.datetime.utcnow()
            self.db.commit()
            raise

    def get_transcript(self, transcript_id: int) -> Optional[Transcript]:
        return self.db.query(Transcript).filter(Transcript.id == transcript_id).first()

    def list_transcripts(self, limit: int = 50, offset: int = 0) -> list[Transcript]:
        return (
            self.db.query(Transcript)
            .order_by(Transcript.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def delete_transcript(self, transcript_id: int) -> bool:
        t = self.get_transcript(transcript_id)
        if not t:
            return False
        self.db.delete(t)
        self.db.commit()
        return True

    async def summarize(
        self,
        transcript_id: int,
        api_key: str = "",
        provider_name: str = "groq",
        provider_config: Optional[dict] = None,
        model: str = "llama-3.3-70b-versatile",
    ) -> Summary:
        """Generate an LLM summary of a completed transcript."""
        transcript = self.get_transcript(transcript_id)
        if not transcript:
            raise ValueError(f"Transcript {transcript_id} not found")
        if transcript.status != "completed":
            raise ValueError(f"Transcript {transcript_id} is not completed")

        text = transcript.full_text
        if not text:
            text = " ".join(s.get("text", "") for s in (transcript.segments or []))

        max_chars = 80000
        if len(text) > max_chars:
            text = text[:max_chars] + "..."

        prompt = f"""You are an expert meeting summarizer. Analyze the following transcript and produce a structured summary.

TRANSCRIPT:
{text}

Respond in JSON format with exactly these keys:
- "short_summary": A 2-3 sentence overview of what was discussed
- "key_points": An array of 3-8 key discussion points, each as a concise string
- "action_items": An array of specific action items with responsible person if mentioned, each as a string
- "decisions": An array of decisions made during the meeting, each as a string

Return ONLY valid JSON, no markdown, no code fences."""

        import httpx

        api_base = "https://api.groq.com/openai/v1"
        if provider_name == "openai":
            api_base = "https://api.openai.com/v1"
            model = model or "gpt-4o-mini"
        elif provider_name == "local":
            api_base = (provider_config or {}).get("api_url", "http://localhost:11434/v1")
            model = model or "llama3"
        elif provider_name == "groq":
            model = model or "llama-3.3-70b-versatile"

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You output only valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
            )

        if resp.status_code != 200:
            raise ProviderError(f"Summarization API error ({resp.status_code}): {resp.text}")

        content = resp.json()["choices"][0]["message"]["content"]
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        try:
            summary_data = json.loads(content)
        except json.JSONDecodeError:
            summary_data = {
                "short_summary": content[:500],
                "key_points": [],
                "action_items": [],
                "decisions": [],
            }

        existing = self.db.query(Summary).filter(Summary.transcript_id == transcript_id).first()
        if existing:
            existing.short_summary = summary_data.get("short_summary", "")
            existing.key_points = summary_data.get("key_points", [])
            existing.action_items = summary_data.get("action_items", [])
            existing.decisions = summary_data.get("decisions", [])
            existing.model = model
            existing.created_at = datetime.datetime.utcnow()
            summary = existing
        else:
            summary = Summary(
                transcript_id=transcript_id,
                short_summary=summary_data.get("short_summary", ""),
                key_points=summary_data.get("key_points", []),
                action_items=summary_data.get("action_items", []),
                decisions=summary_data.get("decisions", []),
                model=model,
            )
            self.db.add(summary)

        self.db.commit()
        return summary