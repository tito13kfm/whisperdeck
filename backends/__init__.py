"""Provider abstraction layer for WhisperDeck.

Supports configurable backends:
  - Built-in (Whisper Tiny) — local, no API key needed
  - Moonshine — local, SOTA on-device ASR (beats Whisper Large v3)
  - Groq (whisper-large-v3-flash)
  - OpenAI (whisper-1)
  - Replicate (whisper-large-v3-turbo)
  - OpenRouter (unified API, including Deepgram Nova-3)
  - AssemblyAI (universal-3-pro)
  - Local / Custom (Whisper.cpp / Ollama / OpenAI-compatible)
"""

from .base import BaseProvider, ProviderError
from .groq import GroqProvider
from .openai import OpenAIProvider
from .replicate import ReplicateProvider
from .local import LocalProvider
from .openrouter import OpenRouterProvider
from .assemblyai import AssemblyAIProvider
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
    "assemblyai": AssemblyAIProvider,
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
            "description": "Local · no API key · SOTA on-device ASR (6.65% WER, beats Whisper Large v3)",
            "default_model": "medium-streaming",
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
            "description": "Unified API — OpenAI/Groq/Deepgram Whisper + Nova-3 models",
            "default_model": "openai/whisper-1",
            "needs_key": True,
            "key_prefix": "sk-or-",
        },
        {
            "id": "assemblyai",
            "name": "AssemblyAI",
            "description": "universal-3-pro · $0.21/hr · async polling · 45+ languages",
            "default_model": "universal-3-pro",
            "needs_key": True,
            "key_prefix": "",
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
    "AssemblyAIProvider",
    "BuiltinProvider", "MoonshineProvider",
    "get_provider", "list_providers", "PROVIDER_REGISTRY",
]