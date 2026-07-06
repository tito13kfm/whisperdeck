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
    "openrouter": "https://openrouter.ai/api/v1",
}
_DEFAULT_MODEL = "llama-3.3-70b-versatile"
# Providers whose chat endpoint accepts response_format json_object
_JSON_MODE_PROVIDERS = ("groq", "openai", "openrouter")
# Rough per-call input budget for the correction pass; keeps each request's
# output comfortably inside max_tokens instead of silently truncating long
# transcripts at 8192 output tokens.
_CHUNK_CHAR_BUDGET = 6000
_CONTEXT_TAIL_LINES = 2


def _api_base(provider_name: str, provider_config: dict | None = None) -> str:
    if provider_name in ("local", "local_llm"):
        return (provider_config or {}).get("api_url") or "http://localhost:11434/v1"
    base = _API_BASES.get(provider_name)
    if not base:
        # Never silently fall back to another provider's endpoint — that sends
        # the wrong key to the wrong host and reads as "invalid API key".
        raise RuntimeError(
            f"Correction does not support provider '{provider_name}' — "
            f"use groq, openai, openrouter, local, or local_llm."
        )
    return base


async def _chat_completion(
    prompt: str, api_key: str, provider_name: str, model: str, json_mode: bool,
    provider_config: dict | None = None,
) -> str:
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
    if json_mode and provider_name in _JSON_MODE_PROVIDERS:
        request_body["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{_api_base(provider_name, provider_config)}/chat/completions",
            headers=headers,
            json=request_body,
        )

    if response.status_code != 200:
        raise RuntimeError(f"LLM API error ({response.status_code}): {response.text}")

    return response.json()["choices"][0]["message"]["content"]


def _transcript_lines(transcript) -> list[str]:
    """One line per segment, 'Speaker Name: text' when a speaker is known.
    Falls back to raw full_text for transcripts without segments."""
    lines = []
    for seg in transcript.segments or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = (seg.get("speaker") or "").strip()
        lines.append(f"{speaker}: {text}" if speaker else text)
    return lines if lines else [(transcript.full_text or "").strip()]


def _batch_lines(lines: list[str], budget: int = _CHUNK_CHAR_BUDGET) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) > budget:
            batches.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line) + 2
    if current:
        batches.append(current)
    return batches


async def correct_transcript(
    db, transcript, api_key: str, provider_name: str = "groq", model: str = _DEFAULT_MODEL,
    provider_config: dict | None = None,
    progress_cb=None, cancel_cb=None,
) -> str:
    """Non-fatal: sets transcript.corrected_text + correction_model on
    success, or transcript.correction_error on failure. Never raises.
    full_text and segments are never modified.

    The transcript is handed to the LLM as speaker-labeled lines
    ('Speaker Name: text'), batched to stay inside output-token limits;
    the model is instructed to preserve labels and line structure so the
    corrected text renders with the same speakers.

    progress_cb(done, total) fires after each batch; cancel_cb() is checked
    before each batch — returning True stops cleanly without touching the
    transcript. Returns 'ok' | 'failed' | 'cancelled' (job runners use it;
    fire-and-forget callers may ignore it)."""
    glossary = [h.term for h in list_hotwords(db, transcript.user_id)]
    glossary_block = (
        f"Known names/jargon that may appear (spell these correctly if you "
        f"see a close phonetic match): {', '.join(glossary)}\n\n"
        if glossary else ""
    )
    batches = _batch_lines(_transcript_lines(transcript))
    corrected_parts: list[str] = []

    try:
        for i, batch in enumerate(batches):
            if cancel_cb and cancel_cb():
                return "cancelled"
            context_block = ""
            if corrected_parts:
                tail = "\n".join(corrected_parts[-1].splitlines()[-_CONTEXT_TAIL_LINES:])
                context_block = (
                    "For context, the previous part of the transcript ended "
                    f"with these already-corrected lines (do NOT repeat them):\n{tail}\n\n"
                )
            part_note = f" (part {i + 1} of {len(batches)})" if len(batches) > 1 else ""
            prompt = (
                f"Below is a raw speech-to-text transcript{part_note}. Each "
                "line has the form 'Speaker Name: text' (some lines may have "
                "no speaker prefix). It may contain misheard words, awkward "
                "grammar, or missing punctuation. Rewrite it to fix likely "
                "transcription errors and improve readability, WITHOUT "
                "changing its meaning or adding any new content. Keep exactly "
                "one output line per input line, in the same order, and "
                "reproduce each 'Speaker Name:' prefix exactly as given — "
                "never rename, merge, or drop speakers. Separate lines with "
                "blank lines. Return only the corrected transcript lines, "
                "nothing else.\n\n"
                f"{glossary_block}"
                f"{context_block}"
                f"TRANSCRIPT:\n" + "\n\n".join(batch)
            )
            part = await _chat_completion(
                prompt, api_key, provider_name, model, json_mode=False,
                provider_config=provider_config,
            )
            corrected_parts.append(part.strip())
            if progress_cb:
                progress_cb(i + 1, len(batches))

        transcript.corrected_text = "\n\n".join(corrected_parts)
        transcript.correction_model = f"{provider_name}/{model}"
        transcript.correction_error = None
        result = "ok"
    except Exception as e:
        transcript.correction_error = str(e)
        result = "failed"

    db.commit()
    return result


async def run_auto_correction(db, transcript, user_settings: dict) -> None:
    """Auto-correct entry point shared by the inline and chunked-finalize
    paths. Provider/model come from user settings; the key from the central
    ProviderConfig pool. Never raises. When the chosen provider needs a key
    and none is saved, the skip is recorded on the transcript so the
    corrected tab can explain itself instead of staying silently empty."""
    from services.settings import resolve_provider_key

    provider = user_settings.get("correction_provider", "groq")
    model = user_settings.get("correction_model", _DEFAULT_MODEL)
    from services.settings import KEYLESS_PROVIDERS

    api_key, provider_config = resolve_provider_key(db, transcript.user_id, provider)
    if provider not in KEYLESS_PROVIDERS and not api_key:
        transcript.correction_error = (
            f"auto-correct skipped: no {provider} API key saved (see service panel)"
        )
        db.commit()
        return
    try:
        await correct_transcript(
            db, transcript, api_key=api_key, provider_name=provider, model=model,
            provider_config=provider_config,
        )
    except Exception as e:  # correct_transcript never raises; belt and braces
        print(f"[correction] non-fatal auto-correct failure for transcript {transcript.id}: {e}")


async def extract_hotwords_from_doc(
    db, user_id: int, doc_text: str, api_key: str, provider_name: str = "groq", model: str = _DEFAULT_MODEL,
    provider_config: dict | None = None,
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
        content = await _chat_completion(
            prompt, api_key, provider_name, model, json_mode=True,
            provider_config=provider_config,
        )
        terms = json.loads(content).get("terms", [])
    except Exception:
        return []

    for term in terms:
        add_hotword(db, user_id, term, source="extracted")
    return terms
