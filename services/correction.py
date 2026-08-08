"""Post-hoc transcript correction using a user-maintained hotword glossary.

Two LLM-backed operations, both non-fatal (never raise): pulling candidate
vocabulary out of a pasted meeting-context doc into the glossary, and using
the full glossary to clean up a finished transcript's full_text. Whisper's
transcription-time `prompt` param is never touched by either — see
docs/superpowers/specs/2026-07-02-hotword-glossary-and-correction-pass-design.md
for why a same-audio pre-pass was rejected in favor of this approach.
"""
import json

from services.hotwords import list_hotwords, add_hotword
from services.llm_client import chat_completion, JSON_MODE_PROVIDERS

_DEFAULT_MODEL = "llama-3.3-70b-versatile"
# Rough per-call input budget for the correction pass; keeps each request's
# output comfortably inside max_tokens instead of silently truncating long
# transcripts at 8192 output tokens.
_CHUNK_CHAR_BUDGET = 6000
# Number of raw input lines shared between consecutive batches so the LLM
# sees the same text on both sides of the boundary and corrects consistently.
# Post-processing strips the overlap from the start of batch N+1's output.
_BATCH_OVERLAP_LINES = 4


async def _chat_completion(
    prompt: str, api_key: str, provider_name: str, model: str, json_mode: bool,
    provider_config: dict | None = None,
) -> str:
    """Raises on any failure — callers catch and set their own error field."""
    return await chat_completion(
        prompt, api_key, provider_name, model, json_mode,
        provider_config=provider_config,
        restrict_json_mode_to=JSON_MODE_PROVIDERS,
        feature_name="Correction",
    )


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


def _batch_lines(lines: list[str], budget: int = _CHUNK_CHAR_BUDGET, overlap: int = 0) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) > budget:
            batches.append(current)
            if overlap:
                overlap_lines = current[-overlap:]
                current = list(overlap_lines)
                size = sum(len(l) + 2 for l in overlap_lines)
            else:
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
    batches overlap by _BATCH_OVERLAP_LINES raw lines so the LLM sees
    consistent context on both sides of each boundary. The overlap is
    stripped from batch N+1's output before stitching to avoid duplicates.

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
    batches = _batch_lines(_transcript_lines(transcript), overlap=_BATCH_OVERLAP_LINES)
    corrected_parts: list[str] = []

    try:
        for i, batch in enumerate(batches):
            if cancel_cb and cancel_cb():
                return "cancelled"
            part_note = f" (part {i + 1} of {len(batches)})" if len(batches) > 1 else ""
            overlap_note = ""
            if i > 0:
                overlap_note = (
                    f" The first {_BATCH_OVERLAP_LINES} "
                    "lines overlap with the previous batch so you see both "
                    "sides of the boundary — correct them consistently.\n\n"
                )
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
                f"{overlap_note}"
                f"TRANSCRIPT:\n" + "\n\n".join(batch)
            )
            part = await _chat_completion(
                prompt, api_key, provider_name, model, json_mode=False,
                provider_config=provider_config,
            )
            corrected_text = part.strip()
            if i > 0:
                content_lines = corrected_text.split("\n\n")
                keep_from = min(_BATCH_OVERLAP_LINES, len(content_lines))
                corrected_text = "\n\n".join(content_lines[keep_from:])
            corrected_parts.append(corrected_text)
            if progress_cb:
                progress_cb(i + 1, len(batches))

        transcript.corrected_text = "\n\n".join(p for p in corrected_parts if p)
        transcript.correction_model = f"{provider_name}/{model}"
        transcript.correction_error = None
        result = "ok"
    except Exception as e:
        transcript.correction_error = str(e)
        result = "failed"

    db.commit()
    return result


async def extract_hotwords_from_doc(
    db, user_id: int, doc_text: str, api_key: str, provider_name: str = "groq", model: str = _DEFAULT_MODEL,
    provider_config: dict | None = None,
) -> list[str]:
    """Non-fatal: returns the list of newly-seen extracted terms (also
    persisted via add_hotword with source='extracted'), or raises on
    LLM failure. Callers should catch and handle appropriately."""
    prompt = (
        "Extract a short list of proper nouns, names, and domain-specific "
        "jargon from the following document that might appear in a related "
        "meeting recording. Respond with JSON: {\"terms\": [\"...\", ...]}. "
        "Keep the list short (under 20 items) and skip common words.\n\n"
        f"DOCUMENT:\n{doc_text}"
    )

    content = await _chat_completion(
        prompt, api_key, provider_name, model, json_mode=True,
        provider_config=provider_config,
    )
    terms = json.loads(content).get("terms", [])

    for term in terms:
        add_hotword(db, user_id, term, source="extracted")
    return terms
