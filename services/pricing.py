"""STT pricing catalog for WhisperDeck transcription providers.

Locked rate table — update these manually when provider pricing changes.
Follows the dict-keyed-by-provider pattern from model_catalog.py and the
flat dict from queue.py's PROVIDER_LIMITS.
"""

from backends import LOCAL_PROVIDERS

# Provider names that always run locally (on-device, no API cost).
# Mirrors backends/__init__.py line 37: ("builtin", "moonshine").
LOCAL_STT_PROVIDERS: set[str] = set(LOCAL_PROVIDERS)

# (provider, model) -> {"rate_per_minute": USD, "rate_source": display string}
STT_RATES: dict[tuple[str, str], dict] = {
    ("groq", "whisper-large-v3-flash"):     {"rate_per_minute": 0.004,  "rate_source": "Groq ($0.004/min)"},
    ("groq", "whisper-large-v3-turbo"):     {"rate_per_minute": 0.006,  "rate_source": "Groq ($0.006/min)"},
    ("openai", "whisper-1"):                {"rate_per_minute": 0.006,  "rate_source": "OpenAI ($0.006/min)"},
    ("assemblyai", "universal-3-pro"):      {"rate_per_minute": 0.0035, "rate_source": "AssemblyAI ($0.0035/min)"},
    ("openrouter", "deepgram/nova-3"):      {"rate_per_minute": 0.0043, "rate_source": "Deepgram via OpenRouter ($0.0043/min)"},
}


def get_stt_rate(provider: str, model: str) -> dict:
    """Return {"rate_per_minute": float, "rate_source": str}.

    Local providers (builtin, moonshine) return 0.0 with rate_source
    "Local (free)". Unknown pairs return 0.0 with "unknown — assuming
    free". Never raises.
    """
    if provider in LOCAL_STT_PROVIDERS:
        return {"rate_per_minute": 0.0, "rate_source": "Local (free)"}

    key = (provider, model)
    if key in STT_RATES:
        return dict(STT_RATES[key])

    return {"rate_per_minute": 0.0, "rate_source": "unknown — assuming free"}


def get_provider_stt_rate(provider: str) -> dict:
    """Return the STT rate for a provider by finding the first model match
    in STT_RATES, or the free/unknown sentinel if none found. Useful when
    aggregating across multiple transcripts where the specific model isn't
    available.
    """
    if provider in LOCAL_STT_PROVIDERS:
        return {"rate_per_minute": 0.0, "rate_source": "Local (free)"}

    for (prov, model), rate_info in STT_RATES.items():
        if prov == provider:
            return dict(rate_info)

    return {"rate_per_minute": 0.0, "rate_source": "unknown — assuming free"}
