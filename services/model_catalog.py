"""Curated, cost-aware model shortlists for LLM-backed features (correction,
summarize), optionally annotated/validated against live provider catalogs.

The seeds below are deliberately small: cheap, strong text models that fit
"clean up a transcript" — not a mirror of each provider's full zoo. Edit the
seeds freely; OpenRouter entries are validated against its public /models
endpoint at read time, so stale ids drop out on their own.
"""
import time

import httpx

# Per-provider curated seeds: (model id, short human label)
CORRECTION_MODELS: dict[str, list[dict]] = {
    "groq": [
        {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B — strong default"},
        {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B — fastest/cheapest"},
    ],
    "openai": [
        {"id": "gpt-4o-mini", "label": "GPT-4o mini — cheap + reliable"},
        {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini — stronger, still cheap"},
    ],
    "openrouter": [
        {"id": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash — cheap, strong"},
        {"id": "deepseek/deepseek-chat", "label": "DeepSeek Chat"},
        {"id": "meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B"},
        {"id": "openai/gpt-4o-mini", "label": "GPT-4o mini"},
    ],
    # local/local_llm: whatever the endpoint serves — the UI offers free text instead.
    "local": [],
    "local_llm": [],
}

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_TTL_SECONDS = 3600
_openrouter_cache: dict = {"at": 0.0, "models": None}


async def _openrouter_live_models() -> dict[str, dict] | None:
    """id -> model info from OpenRouter's public catalog, cached ~1h.
    Returns None when the network fetch fails (callers keep the curated
    list unannotated — never fatal)."""
    now = time.monotonic()
    if _openrouter_cache["models"] is not None and now - _openrouter_cache["at"] < _CACHE_TTL_SECONDS:
        return _openrouter_cache["models"]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(_OPENROUTER_MODELS_URL)
        response.raise_for_status()
        models = {m["id"]: m for m in response.json().get("data", []) if m.get("id")}
    except Exception:
        return None
    _openrouter_cache.update(at=now, models=models)
    return models


def _price_note(model_info: dict) -> str:
    """'$0.14/M in · $0.28/M out' from OpenRouter's per-token pricing."""
    pricing = model_info.get("pricing") or {}
    try:
        prompt = float(pricing.get("prompt", 0)) * 1_000_000
        completion = float(pricing.get("completion", 0)) * 1_000_000
    except (TypeError, ValueError):
        return ""
    if prompt <= 0 and completion <= 0:
        return "free"
    return f"${prompt:.2f}/M in · ${completion:.2f}/M out"


async def get_correction_models(provider: str) -> list[dict]:
    """Curated list for the provider as [{id, label}]. For OpenRouter the
    labels gain live pricing and ids missing from the live catalog are
    dropped (they would 404 at request time anyway)."""
    curated = [dict(m) for m in CORRECTION_MODELS.get(provider, [])]
    if provider != "openrouter":
        return curated

    live = await _openrouter_live_models()
    if live is None:
        return curated

    validated = []
    for m in curated:
        info = live.get(m["id"])
        if not info:
            continue
        note = _price_note(info)
        if note:
            m["label"] = f"{m['label']} ({note})"
        validated.append(m)
    return validated
