"""Cost computation functions for STT transcription and LLM jobs.

Aggregates pricing from services/pricing.py and services/model_catalog.py
to produce structured cost breakdowns per transcript or per user+provider.
"""
import asyncio
from datetime import datetime

from database import Transcript, LlmJob
from services.pricing import get_stt_rate, get_provider_stt_rate
from services.model_catalog import _price_note, _openrouter_live_models


def transcript_cost(db, transcript: Transcript) -> dict:
    """Return a structured cost breakdown for one transcript.

    Returns:
        {
            "stt": {"cost": float, "rate_per_minute": float, "rate_source": str,
                     "duration_seconds": float},
            "correction": {"cost": float, "rate_per_minute": float, "rate_source": str},
            "summary": {"cost": float, "rate_per_minute": float, "rate_source": str},
            "total": float,
        }
    """
    duration = float(transcript.duration_seconds or 0.0)
    stt_rate = get_stt_rate(transcript.provider, transcript.model)
    stt_cost = (duration / 60.0) * stt_rate["rate_per_minute"]

    correction = _llm_job_cost(db, transcript.id, "correction")
    summary = _llm_job_cost(db, transcript.id, "summary")

    total = stt_cost + correction["cost"] + summary["cost"]

    return {
        "stt": {
            "cost": round(stt_cost, 6),
            "rate_per_minute": stt_rate["rate_per_minute"],
            "rate_source": stt_rate["rate_source"],
            "duration_seconds": duration,
        },
        "correction": correction,
        "summary": summary,
        "total": round(total, 6),
    }


def _llm_job_cost(db, transcript_id: int, kind: str) -> dict:
    """Compute cost for the latest completed LlmJob of `kind` for this
    transcript. LLM jobs are token-based; we can't compute exact cost from
    the job row alone, so cost is always 0.0 with a descriptive rate_source.
    """
    job = (
        db.query(LlmJob)
        .filter(
            LlmJob.transcript_id == transcript_id,
            LlmJob.kind == kind,
            LlmJob.status == "completed",
        )
        .order_by(LlmJob.id.desc())
        .first()
    )

    if job is None:
        return {"cost": 0.0, "rate_per_minute": 0.0, "rate_source": "no completed job"}

    provider = job.provider or ""

    if provider == "openrouter":
        rate_source = _resolve_openrouter_rate(job.model or "")
        return {"cost": 0.0, "rate_per_minute": 0.0, "rate_source": rate_source}

    if provider in ("groq", "openai"):
        return {"cost": 0.0, "rate_per_minute": 0.0, "rate_source": "cost unknown, token-based"}

    if provider in ("local", "local_llm"):
        return {"cost": 0.0, "rate_per_minute": 0.0, "rate_source": "Local LLM (free)"}

    return {"cost": 0.0, "rate_per_minute": 0.0, "rate_source": "unknown"}


def _resolve_openrouter_rate(model_id: str) -> str:
    """Look up the pricing display string for an OpenRouter model id.
    Falls back to a descriptive string on network failure, or when called
    from inside a running event loop (this function is sync; a request
    handler is already in a loop, so a live catalog fetch is skipped there
    rather than attempted with asyncio.run(), which would raise).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return "OpenRouter (rate lookup skipped — called from an async context)"
    try:
        live = asyncio.run(_openrouter_live_models())
    except Exception:
        return "OpenRouter (unknown rate — network error)"
    if live is None:
        return "OpenRouter (unknown rate — network error)"
    info = live.get(model_id)
    if info is None:
        return f"OpenRouter ({model_id} — not in catalog)"
    note = _price_note(info)
    if not note:
        return f"OpenRouter ({model_id})"
    return f"OpenRouter {note}"


def provider_cost(db, user_id: int, provider: str, since: datetime) -> dict:
    """Aggregate STT cost for all completed/partial transcripts from this
    user+provider since `since`.

    Returns:
        {
            "total_seconds": float,
            "total_cost": float,
            "rate_per_minute": float,
            "rate_source": str,
        }
    """
    from sqlalchemy import func

    result = (
        db.query(func.sum(Transcript.duration_seconds))
        .filter(
            Transcript.user_id == user_id,
            Transcript.provider == provider,
            Transcript.status.in_(["completed", "partial"]),
            Transcript.created_at >= since,
        )
        .scalar()
    )
    total_seconds = float(result or 0.0)

    rate = get_provider_stt_rate(provider)
    total_cost = (total_seconds / 60.0) * rate["rate_per_minute"]

    return {
        "total_seconds": total_seconds,
        "total_cost": round(total_cost, 6),
        "rate_per_minute": rate["rate_per_minute"],
        "rate_source": rate["rate_source"],
    }


def estimate_cost(provider: str, model: str, duration_seconds: float) -> dict:
    """One-off cost estimate for a single transcription.

    Returns:
        {"cost": float, "rate_per_minute": float, "rate_source": str}
    """
    rate = get_stt_rate(provider, model)
    cost = (duration_seconds / 60.0) * rate["rate_per_minute"]

    return {
        "cost": round(cost, 6),
        "rate_per_minute": rate["rate_per_minute"],
        "rate_source": rate["rate_source"],
    }
