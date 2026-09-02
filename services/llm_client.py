"""Shared low-level chat-completion client for OpenAI-compatible LLM
providers (Groq, OpenAI, OpenRouter, and any local server at a configured
api_url e.g. Ollama/Lemonade/LM Studio). Used by correction, summarization,
and transcript reformatting — each of those owns its own prompts and
response parsing, but shares this HTTP call shape, default-model/provider
resolution, and transcript-text prep so the three can't drift apart."""
import re

import httpx

API_BASES = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
# Providers whose chat endpoint is known to accept response_format json_object.
JSON_MODE_PROVIDERS = ("groq", "openai", "openrouter")

# Per-provider default model, shared by summarize() and every reformatting
# target — a future default-model bump only needs to happen here.
DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
    "openrouter": "deepseek/deepseek-v4-flash",
    "local": "llama3",
    "local_llm": "llama3",
}


def resolve_api_base(
    provider_name: str, provider_config: dict | None = None, feature_name: str = "This feature",
) -> str:
    if provider_name in ("local", "local_llm"):
        return (provider_config or {}).get("api_url") or "http://localhost:11434/v1"
    base = API_BASES.get(provider_name)
    if not base:
        # Never silently fall back to another provider's endpoint — that sends
        # the wrong key to the wrong host and reads as "invalid API key".
        raise RuntimeError(
            f"{feature_name} does not support provider '{provider_name}' — "
            f"use groq, openai, openrouter, local, or local_llm."
        )
    return base


def resolve_model(provider_name: str, model: str, feature_name: str) -> str:
    """Validate provider_name against DEFAULT_MODELS and fall back to its
    default when model is falsy. Raises RuntimeError for an unsupported
    provider — callers wrap this in their own exception type if needed
    (e.g. transcription.py/reformatting.py both re-raise as ProviderError)."""
    if provider_name not in DEFAULT_MODELS:
        raise RuntimeError(
            f"{feature_name} does not support provider '{provider_name}' — "
            f"use groq, openai, openrouter, local, or local_llm."
        )
    return model or DEFAULT_MODELS[provider_name]


def sanitize_tag_content(text: str, tag: str) -> str:
    """Escape closing XML tags inside user-controlled text so a delimiter
    wrapper built around it (`<tag>...</tag>` with a verbatim-data
    instruction) cannot be broken out of, including whitespace/case variants
    (e.g. ``</document >``, ``</Document>``) which are valid XML end tags.
    Shared by every prompt that wraps raw transcript/user text — see
    correction.py, reformatting.py, transcription.py, tagging.py,
    voice_notes.py, classification.py, and assistant.py's summarize step."""
    pattern = re.compile(r"</\s*" + re.escape(tag) + r"\s*>", re.IGNORECASE)
    return pattern.sub(lambda m: m.group(0).replace("</", "<\\/", 1), text)  # noqa: W605


def transcript_text_for_prompt(transcript, max_chars: int = 80000) -> str:
    """full_text, falling back to concatenated segment text, truncated to
    max_chars — shared by summarize() and every reformatting target so the
    truncation policy can't drift between them."""
    text = transcript.full_text
    if not text:
        text = " ".join(s.get("text", "") for s in (transcript.segments or []))
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text


async def chat_completion(
    prompt: str,
    api_key: str,
    provider_name: str,
    model: str,
    json_mode: bool,
    provider_config: dict | None = None,
    system: str = "You output only what is requested, no commentary.",
    temperature: float = 0.2,
    max_tokens: int = 16384,
    restrict_json_mode_to: tuple | None = None,
    raise_on_truncation: bool = False,
    feature_name: str = "This feature",
    http_error_label: str | None = None,
    truncation_message: str | None = None,
) -> str:
    """Raises RuntimeError on any failure — callers catch and set their own
    error field.

    feature_name: used only for the unsupported-provider message raised by
    resolve_api_base ("{feature_name} does not support provider 'x'").

    http_error_label: prefixes the plain HTTP-error message as
    "{label} API error (...)". Defaults to None, which renders as the
    generic "LLM API error (...)" text correction.py has always used —
    pass a label (e.g. "Summarization") for a feature-specific message.
    Kept independent of feature_name because the two call sites this was
    extracted from disagreed: correction's was generic, summarize's was
    already feature-prefixed — collapsing them into one param would have
    silently changed one or the other's user-facing text.

    restrict_json_mode_to: if given, response_format json_object is only
    sent when provider_name is in this tuple (some OpenAI-compatible local
    servers reject the field). If None, it's sent whenever json_mode=True
    regardless of provider — safe to send unconditionally per
    tests/test_summarize_local_provider.py: providers that ignore it pass
    through fine, and current local servers that support it (llama.cpp/LM
    Studio/Ollama) enforce valid JSON, which is exactly what local models
    with weaker instruction-following need most.

    raise_on_truncation: if True, a finish_reason=='length' response raises
    instead of silently returning truncated content. truncation_message
    overrides the default generic wording when set.
    """
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode and (restrict_json_mode_to is None or provider_name in restrict_json_mode_to):
        request_body["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{resolve_api_base(provider_name, provider_config, feature_name)}/chat/completions",
            headers=headers,
            json=request_body,
        )

    if response.status_code != 200:
        label = f"{http_error_label} API error" if http_error_label else "LLM API error"
        raise RuntimeError(f"{label} ({response.status_code}): {response.text}")

    choice = response.json()["choices"][0]
    msg = choice["message"]
    # Reasoning/MTP models (e.g. Qwen3.5) put their output in
    # reasoning_content instead of content. Fall back when content
    # is empty so correction/summary/reformatting work with those models too.
    content = msg.get("content") or msg.get("reasoning_content") or ""
    if raise_on_truncation and choice.get("finish_reason") == "length":
        raise RuntimeError(
            truncation_message
            or "Generation was cut off (model hit its token/context limit) — "
               "try shorter input or a model with a larger context window."
        )
    return content
