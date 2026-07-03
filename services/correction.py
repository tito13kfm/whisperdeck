"""Post-hoc transcript correction using a user-maintained hotword glossary.

Two LLM-backed operations, both non-fatal (never raise): pulling candidate
vocabulary out of a pasted meeting-context doc into the glossary, and using
the full glossary to clean up a finished transcript's full_text. Whisper's
transcription-time `prompt` param is never touched by either — see
docs/superpowers/specs/2026-07-02-hotword-glossary-and-correction-pass-design.md
for why a same-audio pre-pass was rejected in favor of this approach.
"""
import json

import httpx

from services.hotwords import list_hotwords, add_hotword

_API_BASES = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}
_DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _api_base(provider_name: str, provider_config: dict | None = None) -> str:
    if provider_name == "local":
        return (provider_config or {}).get("api_url", "http://localhost:11434/v1")
    return _API_BASES.get(provider_name, _API_BASES["groq"])


async def _chat_completion(prompt: str, api_key: str, provider_name: str, model: str, json_mode: bool) -> str:
    """Raises on any failure — callers catch and set their own error field."""
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You output only what is requested, no commentary."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    if json_mode and provider_name in ("groq", "openai"):
        request_body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{_api_base(provider_name)}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_body,
        )

    if response.status_code != 200:
        raise RuntimeError(f"LLM API error ({response.status_code}): {response.text}")

    return response.json()["choices"][0]["message"]["content"]


async def correct_transcript(
    db, transcript, api_key: str, provider_name: str = "groq", model: str = _DEFAULT_MODEL,
) -> None:
    """Non-fatal: sets transcript.corrected_text + correction_model on
    success, or transcript.correction_error on failure. Never raises.
    full_text and segments are never modified."""
    glossary = [h.term for h in list_hotwords(db, transcript.user_id)]
    glossary_block = (
        f"Known names/jargon that may appear (spell these correctly if you "
        f"see a close phonetic match): {', '.join(glossary)}\n\n"
        if glossary else ""
    )
    prompt = (
        "Below is a raw speech-to-text transcript that may contain misheard "
        "words, awkward grammar, or missing punctuation. Rewrite it to fix "
        "likely transcription errors and improve readability, WITHOUT "
        "changing its meaning or adding any new content. Return only the "
        "corrected transcript text, nothing else.\n\n"
        f"{glossary_block}"
        f"TRANSCRIPT:\n{transcript.full_text}"
    )

    try:
        corrected = await _chat_completion(prompt, api_key, provider_name, model, json_mode=False)
        transcript.corrected_text = corrected.strip()
        transcript.correction_model = f"{provider_name}/{model}"
        transcript.correction_error = None
    except Exception as e:
        transcript.correction_error = str(e)

    db.commit()


async def extract_hotwords_from_doc(
    db, user_id: int, doc_text: str, api_key: str, provider_name: str = "groq", model: str = _DEFAULT_MODEL,
) -> list[str]:
    """Non-fatal: returns the list of newly-seen extracted terms (also
    persisted via add_hotword with source='extracted'), or [] on any
    failure. Never raises."""
    prompt = (
        "Extract a short list of proper nouns, names, and domain-specific "
        "jargon from the following document that might appear in a related "
        "meeting recording. Respond with JSON: {\"terms\": [\"...\", ...]}. "
        "Keep the list short (under 20 items) and skip common words.\n\n"
        f"DOCUMENT:\n{doc_text}"
    )

    try:
        content = await _chat_completion(prompt, api_key, provider_name, model, json_mode=True)
        terms = json.loads(content).get("terms", [])
    except Exception:
        return []

    for term in terms:
        add_hotword(db, user_id, term, source="extracted")
    return terms
