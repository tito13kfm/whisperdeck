"""Pipeline classification: decides a transcript's `kind` (meeting /
dictation / voice_note) from its full corrected text, as an async LlmJob.

Distinct from services/reformatting.py's classify_intent(), which only
suggests a dictation reformat target and is a UI hint that never raises.
This classifier drives real routing (design decision 8: a wrong-but-
confident-looking auto-kind is worse than staying in the safe fallback
state), so a malformed/empty response or provider error must raise — the
job runner lands it 'failed' and the normal AUTO_RETRY_KINDS sweep can
retry it, rather than silently guessing a default kind.

See docs/superpowers/specs/2026-08-01-studio-classification-design.md for
the full contract (decisions 2-4, 6, 11).
"""
import json

from services.llm_client import chat_completion, resolve_model

SCHEMA_VERSION = 1
CLASSIFICATION_KINDS = ("meeting", "dictation", "voice_note")


def _text_for_classification(transcript) -> str:
    """Corrected text is the intended signal (design decision 2). Falls back
    to full_text/segments when correction hasn't run (e.g. auto_correct off,
    or the correction pass itself failed) rather than raising outright —
    there's still a valid signal to classify."""
    text = (transcript.corrected_text or transcript.full_text or "").strip()
    if not text:
        text = " ".join(s.get("text", "") for s in (transcript.segments or [])).strip()
    return text


async def classify_pipeline_kind(
    transcript, api_key: str = "", provider_name: str = "local_llm",
    provider_config: dict | None = None, model: str = "",
) -> dict:
    """Returns {"kind": one of CLASSIFICATION_KINDS, "confidence": float in
    [0, 1]}. Raises ValueError on no usable text or a malformed/out-of-range
    response, RuntimeError on a provider/HTTP failure — both are real
    failures the caller should not swallow."""
    text = _text_for_classification(transcript)
    if not text:
        raise ValueError("transcript has no text to classify")

    resolved_model = resolve_model(provider_name, model, feature_name="Classification")
    prompt = f"""The following is a transcript of an audio recording. Decide which single category best describes it:
- "meeting": multiple participants having a conversation or discussion
- "dictation": one person speaking notes, ideas, or a message meant to be reformatted into another shape (email, markdown note, coding prompt)
- "voice_note": one person recording a short personal note, memo, or reminder to themselves

Respond with JSON: {{"kind": "meeting" | "dictation" | "voice_note", "confidence": <float between 0 and 1>}}

TRANSCRIPT:
{text}"""
    content = await chat_completion(
        prompt, api_key, provider_name, resolved_model, json_mode=True,
        provider_config=provider_config,
        system="You output only what is requested, no commentary.",
        temperature=0.0,
        raise_on_truncation=True,
        feature_name="Classification",
        http_error_label="Classification",
        truncation_message="Classification was cut off (model hit its token/context limit).",
    )
    parsed = json.loads(content)
    kind = parsed.get("kind")
    confidence = parsed.get("confidence")
    if kind not in CLASSIFICATION_KINDS:
        raise ValueError(f"classifier returned invalid kind: {kind!r}")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not (0.0 <= confidence <= 1.0):
        raise ValueError(f"classifier returned invalid confidence: {confidence!r}")
    return {"kind": kind, "confidence": float(confidence)}
