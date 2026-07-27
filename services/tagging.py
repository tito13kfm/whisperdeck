"""LLM-driven topic tagging for transcripts (issue #171).

One LLM call per transcript; returns a short list of free-form tags
("Q3 budget", "vendor renewal", "hiring", ...). Tags are LLM-derived
metadata, not user-created glossary terms — they don't share storage or
settings with the hotwords service.

The call never raises. A failure path (API error, JSON parse error,
unsupported provider, model refusal, ...) returns an empty list, which
the LlmJob worker treats as a valid completed result (zero tags is a
legitimate outcome, even if unhelpful). This matches the never-raise
contract that classify_intent and run_voice_note_chain already follow
in services/voice_notes.py and services/reformatting.py — the LlmJob
worker is the single failure point that records status="failed", and
this service hands it a clean [] rather than an exception in the normal
case where the LLM just couldn't come up with tags.
"""
import json
import re

from services.llm_client import chat_completion, transcript_text_for_prompt


# Topic tagging is cheap — 20K chars is plenty to identify 1-5 topics
# even from a multi-hour meeting, and keeping the prompt small avoids
# the slower/expensive models of the per-provider catalogs.
_MAX_INPUT_CHARS = 20000
# Cap on returned tags, both as a prompt hint and as a hard post-parse
# limit. Five is enough to cover "budget, hiring, vendor, Q3" without
# letting the LLM pad with marginal topics.
_MAX_TAGS = 5
# Single-tag hard floor — "a" or "x" is too short to be useful.
_MIN_TAG_LEN = 2
# Hard ceiling per tag. Matches the DB column width (database/__init__.py
# TranscriptTag.tag is VARCHAR(64)) so a parsed tag can never be wider
# than the storage.
_MAX_TAG_LEN = 64

_PROMPT = """\
You read a transcript and return 1-{max_tags} short topic tags that describe \
what it is about. Tags are short phrases (1-3 words), lowercase, no punctuation \
beyond internal hyphens or ampersands. Examples of good tags: \
"q3 budget", "vendor renewal", "hiring", "product roadmap", "onboarding". \
Examples of bad tags: "meeting", "discussion", "general", "stuff".

Output a JSON object: {{"tags": ["tag one", "tag two", ...]}}
No prose, no markdown fence, just the JSON object.

Transcript:
{text}
""".strip()

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of a model response. The prompt asks
    for bare JSON, but models often wrap it in a ```json fence or prefix
    it with prose like "Sure, here you go:" — the worker just needs the
    object, so we strip the fence and slice to the outermost braces."""
    if not text:
        return None
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None


def _normalize(raw_tags) -> list[str]:
    """Lowercase, trim, dedupe, drop empties / overlong, cap to _MAX_TAGS.
    Accepts whatever the LLM returned: a list, a string, anything with
    .get(), or None. Returns a clean list ready to write to the DB."""
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if not isinstance(raw_tags, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw_tags:
        if not isinstance(entry, str):
            continue
        tag = entry.strip().lower()
        tag = re.sub(r"\s+", " ", tag)
        if not tag or len(tag) < _MIN_TAG_LEN or len(tag) > _MAX_TAG_LEN:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= _MAX_TAGS:
            break
    return out


async def generate_tags(
    transcript,
    api_key: str,
    provider_name: str,
    provider_config: dict | None,
    model: str,
) -> list[str]:
    """One LLM call → list of 0-5 normalized tags. Never raises.

    The text source prefers `corrected_text` (cleaner signal of what
    the meeting was about, since misheard words and typos have already
    been corrected) and falls back to `full_text` for transcripts that
    haven't been corrected. Segment text is the third fallback via
    `transcript_text_for_prompt`, which already concatenates segments
    when full_text is empty."""
    text = (transcript.corrected_text or "").strip() or (transcript.full_text or "").strip()
    if not text:
        # Concatenate segments as a last resort (the helper handles
        # full_text vs segments internally, but our preferred source
        # above may have been empty even with segments present).
        text = transcript_text_for_prompt(transcript, max_chars=_MAX_INPUT_CHARS)
    if not text:
        return []
    if len(text) > _MAX_INPUT_CHARS:
        text = text[:_MAX_INPUT_CHARS]

    prompt = _PROMPT.format(max_tags=_MAX_TAGS, text=text)
    try:
        raw = await chat_completion(
            prompt=prompt,
            api_key=api_key,
            provider_name=provider_name,
            provider_config=provider_config,
            model=model,
            json_mode=True,
            feature_name="Tag generation",
            http_error_label="Tag generation",
        )
    except RuntimeError:
        return []
    except Exception:
        return []

    obj = _extract_json_object(raw)
    if not obj:
        return []
    return _normalize(obj.get("tags"))
