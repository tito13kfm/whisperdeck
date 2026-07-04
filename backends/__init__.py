"""Provider abstraction layer for WhisperDeck.

Supports configurable backends:
  - Built-in (Whisper Tiny) — local, no API key needed
  - Groq (whisper-large-v3-turbo)
  - OpenAI (whisper-1)
  - Replicate (whisper-large-v3-turbo)
  - OpenRouter (unified API)
  - Local / Custom (Whisper.cpp / Ollama / OpenAI-compatible)
"""

from .base import BaseProvider, ProviderError
from .groq import GroqProvider
from .openai import OpenAIProvider
from .replicate import ReplicateProvider
from .local import LocalProvider
from .openrouter import OpenRouterProvider
from .builtin import BuiltinProvider
from .moonshine import MoonshineProvider

PROVIDER_REGISTRY = {
    "builtin": BuiltinProvider,
    "moonshine": MoonshineProvider,
    "groq": GroqProvider,
    "openai": OpenAIProvider,
    "replicate": ReplicateProvider,
    "local": LocalProvider,
    "openrouter": OpenRouterProvider,
}


# Providers that run on-device: no API key, no upload limits, no rate budget.
LOCAL_PROVIDERS = ("builtin", "moonshine")


def get_provider(name: str, config: dict) -> BaseProvider:
    """Factory: get a provider instance by name with the given config."""
    cls = PROVIDER_REGISTRY.get(name)
    if not cls:
        raise ProviderError(f"Unknown provider: {name}. Available: {list(PROVIDER_REGISTRY.keys())}")
    return cls(config)


def list_providers() -> list[dict]:
    """Return metadata about all available providers."""
    return [
        {
            "id": "builtin",
            "name": "Built-in (Whisper Tiny)",
            "description": "Local · no API key · great for quick dictation",
            "default_model": "tiny",
            "needs_key": False,
            "key_prefix": "",
            "zero_setup": True,
        },
        {
            "id": "moonshine",
            "name": "Moonshine",
            "description": "Local · no API key · lightweight on-device ASR",
            "default_model": "base",
            "needs_key": False,
            "key_prefix": "",
            "zero_setup": True,
        },
        {
            "id": "groq",
            "name": "Groq",
            "description": "whisper-large-v3-flash · fast, strong accuracy on noisy/accented audio",
            "default_model": "whisper-large-v3-flash",
            "needs_key": True,
            "key_prefix": "gsk_",
        },
        {
            "id": "openai",
            "name": "OpenAI",
            "description": "whisper-1 · high accuracy, $0.006/min",
            "default_model": "whisper-1",
            "needs_key": True,
            "key_prefix": "sk-",
        },
        {
            "id": "replicate",
            "name": "Replicate",
            "description": "whisper-large-v3-turbo · pay-per-run",
            "default_model": "whisper-large-v3-turbo",
            "needs_key": True,
            "key_prefix": "r8_",
        },
        {
            "id": "openrouter",
            "name": "OpenRouter",
            "description": "Unified API — OpenAI/Groq/Deepgram Whisper models",
            "default_model": "openai/whisper-1",
            "needs_key": True,
            "key_prefix": "sk-or-",
        },
        {
            "id": "local",
            "name": "Local / Custom",
            "description": "Whisper.cpp, Ollama, or OpenAI-compatible endpoint",
            "default_model": "whisper-large-v3-turbo",
            "needs_key": False,
            "key_prefix": "",
        },
    ]


__all__ = [
    "BaseProvider", "ProviderError",
    "GroqProvider", "OpenAIProvider", "ReplicateProvider", "LocalProvider", "OpenRouterProvider",
    "BuiltinProvider", "MoonshineProvider",
    "get_provider", "list_providers", "PROVIDER_REGISTRY",
]