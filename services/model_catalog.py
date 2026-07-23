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
    "local": [],
    "local_llm": [],
}

# Small models worth recommending for local inference. These are annotations,
# not selectable entries: a model only appears in the picker when the live
# endpoint actually serves it (offering an unserved id would 404 at chat time).
_LOCAL_LLM_RECOMMENDATIONS = [
    {"id": "llama3.2", "label": "Llama 3.2 3B — recommended for most hardware"},
    {"id": "llama3.1", "label": "Llama 3.1 8B — stronger, needs ~8 GB VRAM"},
    {"id": "phi3", "label": "Phi-3 Mini 3.8B — lightweight, good on CPU"},
    {"id": "qwen2.5", "label": "Qwen 2.5 7B — strong instruction following"},
    {"id": "mistral", "label": "Mistral 7B — solid general purpose"},
]

# Must match resolve_api_base's local fallback in services/llm_client.py so
# the picker lists models from the same endpoint chat requests will hit.
_LOCAL_LLM_DEFAULT_API_URL = "http://localhost:11434/v1"
_LOCAL_LLM_CACHE_TTL_SECONDS = 60
_local_llm_cache: dict = {"at": 0.0, "base": None, "models": None}

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


def _recommendation_label(live_id: str) -> str | None:
    """Starred label when a live model id matches a recommendation, either
    exactly or as an Ollama-style tag ('llama3.2:latest'). None otherwise."""
    for rec in _LOCAL_LLM_RECOMMENDATIONS:
        if live_id == rec["id"] or live_id.startswith(rec["id"] + ":"):
            return f"{rec['label']} ★"
    return None


async def _local_llm_models(api_url: str | None, api_key: str | None = None) -> list[dict]:
    """Live models from the local OpenAI-compatible endpoint (defaulting to
    the same endpoint the chat path uses), with recommended small models
    starred and listed first. Returns [] when the endpoint is unreachable,
    which tells the UI to fall back to free-text model entry. Cached briefly:
    the settings panel fires three picker fills per open."""
    base = (api_url or _LOCAL_LLM_DEFAULT_API_URL).rstrip("/")
    now = time.monotonic()
    if (
        _local_llm_cache["models"] is not None
        and _local_llm_cache["base"] == base
        and now - _local_llm_cache["at"] < _LOCAL_LLM_CACHE_TTL_SECONDS
    ):
        return _local_llm_cache["models"]
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base}/models", headers=headers)
        resp.raise_for_status()
        live = [m["id"] for m in resp.json().get("data", []) if m.get("id")]
    except Exception:
        return []
    starred, rest = [], []
    for m_id in live:
        label = _recommendation_label(m_id)
        (starred if label else rest).append({"id": m_id, "label": label or m_id})
    models = starred + rest
    _local_llm_cache.update(at=now, base=base, models=models)
    return models


async def get_correction_models(
    provider: str,
    local_llm_api_url: str | None = None,
    local_llm_api_key: str | None = None,
) -> list[dict]:
    """Curated list for the provider as [{id, label}]. For OpenRouter the
    labels gain live pricing and ids missing from the live catalog are
    dropped (they would 404 at request time anyway).

    For local_llm, fetches live models from the configured endpoint's /models
    route (recommended small models starred). Returns [] when the endpoint is
    unreachable so the UI can fall back to free-text entry."""
    if provider == "local_llm":
        return await _local_llm_models(local_llm_api_url, local_llm_api_key)
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
