"""Transcription service — orchestrates provider calls and database persistence."""
import os
import datetime
import json
from typing import Optional

from database import Transcript, Summary, utcnow_naive
from backends import get_provider, ProviderError
from backends.base import BaseProvider, TranscriptionResult


class TranscriptionService:
    """Coordinates transcription jobs: file handling, provider calls, DB persistence."""

    def __init__(self, upload_dir: str = "data/uploads"):
        self.upload_dir = upload_dir
        os.makedirs(upload_dir, exist_ok=True)

    def create_transcript_stub(
        self,
        db,
        user_id: int,
        filename: str,
        provider_name: str,
        model: str,
        language: str,
        audio_path: str,
        diarize_requested: bool,
        title: Optional[str] = None,
        num_speakers: Optional[int] = None,
    ) -> Transcript:
        """Create a Transcript row in 'processing' status without calling a
        provider — used by the chunked upload path, which enqueues chunk
        jobs instead of transcribing inline. See services/queue.py for how
        those jobs eventually populate full_text/segments/status.

        num_speakers is persisted (not used here) because the chunked
        path's diarization runs later, from the worker's finalize step,
        which has no access to this request's form data otherwise."""
        transcript = Transcript(
            user_id=user_id,
            title=title or os.path.splitext(filename)[0],
            filename=filename,
            provider=provider_name,
            model=model or "",
            language=language,
            status="processing",
            audio_path=audio_path,
            diarize_requested=diarize_requested,
            num_speakers=num_speakers,
        )
        db.add(transcript)
        db.commit()
        return transcript

    async def transcribe(
        self,
        db,
        user_id: int,
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
            user_id=user_id,
            title=title or os.path.splitext(filename)[0],
            filename=filename,
            provider=provider_name,
            model=provider_config.get("default_model", ""),
            language=language,
            status="processing",
            audio_path=audio_path,
        )
        db.add(transcript)
        db.commit()

        try:
            # Commit above happens BEFORE this await, on purpose: it closes
            # out the transaction so no write lock is held while we wait on
            # the (multi-second) provider call. Do not move the commit to
            # after this await, or wrap the await inside an open
            # transaction — that would hold a lock across the wait and risk
            # "database is locked" errors under concurrent uploads.
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
            transcript.updated_at = utcnow_naive()

            transcript_dir = os.path.join(os.path.dirname(self.upload_dir), "transcripts")
            os.makedirs(transcript_dir, exist_ok=True)
            txt_path = os.path.join(
                transcript_dir, f"{transcript.id}_{os.path.splitext(filename)[0]}.txt"
            )
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(result.full_text)

            db.commit()
            return transcript

        except Exception as e:
            transcript.status = "failed"
            transcript.error = str(e)
            transcript.updated_at = utcnow_naive()
            db.commit()
            raise

    def get_transcript(self, db, user_id: int, transcript_id: int) -> Optional[Transcript]:
        return db.query(Transcript).filter(
            Transcript.id == transcript_id, Transcript.user_id == user_id
        ).first()

    def list_transcripts(self, db, user_id: int, limit: int = 50, offset: int = 0) -> list[Transcript]:
        return (
            db.query(Transcript)
            .filter(Transcript.user_id == user_id)
            .order_by(Transcript.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def delete_transcript(self, db, user_id: int, transcript_id: int) -> bool:
        t = self.get_transcript(db, user_id, transcript_id)
        if not t:
            return False
        db.delete(t)
        db.commit()
        return True

    async def summarize(
        self,
        db,
        user_id: int,
        transcript_id: int,
        api_key: str = "",
        provider_name: str = "groq",
        provider_config: Optional[dict] = None,
        model: str = "llama-3.3-70b-versatile",
    ) -> Summary:
        """Generate an LLM summary of a completed transcript."""
        transcript = self.get_transcript(db, user_id, transcript_id)
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
        elif provider_name == "openrouter":
            api_base = "https://openrouter.ai/api/v1"
            model = model or "deepseek/deepseek-v4-flash"
        elif provider_name in ("local", "local_llm"):
            api_base = (provider_config or {}).get("api_url") or "http://localhost:11434/v1"
            model = model or "llama3"
        elif provider_name == "groq":
            model = model or "llama-3.3-70b-versatile"
        else:
            raise ProviderError(
                f"Summarization does not support provider '{provider_name}' — "
                f"use groq, openai, openrouter, local, or local_llm."
            )

        request_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You output only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 8192,
        }
        # Forces the model to emit well-formed JSON instead of prose that
        # merely resembles JSON. Sent unconditionally, including to local
        # providers: OpenAI-compatible endpoints that don't support this
        # field just ignore it, while ones that do (current Ollama/LM
        # Studio/llama.cpp server) enforce valid JSON output, which is
        # exactly what local models — with weaker instruction-following —
        # need most.
        request_body["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{api_base}/chat/completions",
                headers=headers,
                json=request_body,
            )

        if resp.status_code != 200:
            raise ProviderError(f"Summarization API error ({resp.status_code}): {resp.text}")

        choice = resp.json()["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") == "length":
            raise ProviderError(
                "Summary generation was cut off (model hit its token/context "
                "limit) — try a shorter recording or a model with a larger "
                "context window."
            )
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        try:
            summary_data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ProviderError(f"Model did not return valid JSON ({e}): {content[:200]!r}")

        existing = db.query(Summary).filter(Summary.transcript_id == transcript_id).first()
        if existing:
            existing.short_summary = summary_data.get("short_summary", "")
            existing.key_points = summary_data.get("key_points", [])
            existing.action_items = summary_data.get("action_items", [])
            existing.decisions = summary_data.get("decisions", [])
            existing.model = model
            existing.provider = provider_name
            existing.created_at = utcnow_naive()
            summary = existing
        else:
            summary = Summary(
                transcript_id=transcript_id,
                short_summary=summary_data.get("short_summary", ""),
                key_points=summary_data.get("key_points", []),
                action_items=summary_data.get("action_items", []),
                decisions=summary_data.get("decisions", []),
                model=model,
                provider=provider_name,
            )
            db.add(summary)

        db.commit()
        return summary