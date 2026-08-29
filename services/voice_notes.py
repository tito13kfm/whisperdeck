"""Voice-note LLM chain (issue #169).

A two-call chain that turns a single-speaker transcript into a structured
note: first classify what KIND of note the speaker is taking
(todo / idea / reminder / journal / general / bug), then run a per-kind
prompt that produces a structured payload (title, body, plus per-type
fields). Both calls run inside one LlmJob(kind="voice_note") — the
worker increments progress_done between them so the queue screen shows
real movement, and the call pair is the natural retry unit (a bad
classification call and a bad structure call are usually the same
provider hiccup; re-running the whole pair together is what the user
asked for when they click "Rerun").

The chain never raises mid-pair. The classifier falls back to
"general" on any error (mirroring services.reformatting.classify_intent
which falls back to "none"), and the structure call swallows parse
errors into a minimal `general`-shaped body. The LlmJob worker is the
caller and DOES raise on classification failure (a transcript with no
type and no body isn't useful, the job should mark failed)."""
import json

from backends import ProviderError
from services.llm_client import chat_completion, resolve_model, transcript_text_for_prompt

_transcript_text = transcript_text_for_prompt

NOTE_TYPES = ("todo", "idea", "reminder", "journal", "general", "bug")


# Per-type JSON schema, documented so the prompts below can target it
# directly and the frontend renderer can switch on `note_type` without
# a guessing game. Kept in one place so the LLM prompt and the renderer
# stay in lockstep.
def _structured_schema(note_type: str) -> str:
    if note_type == "todo":
        return (
            "{"
            '"items": [{"text": "the todo item", "priority": "high|medium|low", "due_date": "ISO 8601 date or null"}]'
            "}"
        )
    if note_type == "idea":
        return (
            "{"
            '"summary": "one-line distillation of the idea", '
            '"tags": ["topic1", "topic2"]'
            "}"
        )
    if note_type == "reminder":
        return (
            "{"
            '"trigger": "when to remind (e.g. \'tomorrow morning\', \'next monday\')", '
            '"subject": "what to remember"'
            "}"
        )
    if note_type == "journal":
        return (
            "{"
            '"mood": "one or two words (or null)", '
            '"themes": ["topic1", "topic2"]'
            "}"
        )
    # general
    return "{}"


def _structure_prompt(note_type: str) -> str:
    schema = _structured_schema(note_type)
    if note_type == "todo":
        specific = (
            "Extract the actionable items the speaker is committing to. "
            "For each item, infer a priority (high if time-sensitive or "
            "explicitly flagged; medium by default; low if the speaker "
            "hints at deferring). Set due_date only if the speaker named "
            "a specific date or relative time."
        )
    elif note_type == "idea":
        specific = (
            "Capture the idea as a single sharp summary line, plus 2-5 "
            "short topic tags that would help the user find this note "
            "later."
        )
    elif note_type == "reminder":
        specific = (
            "Capture when the speaker wants to be reminded and what "
            "they want to be reminded about. Express the trigger in the "
            "speaker's own words where possible (e.g. 'when I get to "
            "the office' is fine — we don't need an ISO date if none "
            "was given)."
        )
    elif note_type == "journal":
        specific = (
            "Capture a one-or-two-word mood if the speaker expressed "
            "one, and 2-4 short themes that summarize the entry. "
            "Don't infer mood when the speaker didn't say."
        )
    else:  # general
        specific = "No extra fields beyond title and body are needed."
    return (
        f"The following is a voice-note transcript that was classified as a "
        f'"{note_type}" note. Produce a structured write-up for the user. '
        f"{specific}\n\n"
        f"Respond in JSON with exactly these keys:\n"
        f'- "title": a short, scannable title (max 80 chars; no trailing punctuation)\n'
        f'- "body": a clean, tightened prose version of the note (preserve the speaker\'s voice, '
        f'but fix transcription artifacts and tighten rambling)\n'
        f'- "structured": {schema}\n\n'
        f"Return ONLY valid JSON, no markdown, no code fences.\n\n"
        f"TRANSCRIPT:\n{{text}}"
    )


async def _generate(
    prompt: str, api_key: str, provider_name: str, model: str,
    provider_config: dict | None, json_mode: bool = False,
) -> str:
    """Shared LLM call wrapper — same shape as services.reformatting._generate.
    A network/api error or model refusal raises and is caught by the caller
    (classify_voice_note falls back to 'general'; structure_voice_note
    falls back to a stub body)."""
    try:
        model = resolve_model(provider_name, model, feature_name="VoiceNote")
        return await chat_completion(
            prompt, api_key, provider_name, model, json_mode=json_mode,
            provider_config=provider_config,
            system="You output only valid JSON when asked, no commentary, no markdown code fences.",
            temperature=0.3,
            raise_on_truncation=True,
            feature_name="VoiceNote",
            http_error_label="VoiceNote",
            truncation_message=(
                "Voice-note generation was cut off (model hit its token/context "
                "limit) — try a shorter recording or a model with a larger "
                "context window."
            ),
        )
    except RuntimeError as e:
        raise ProviderError(str(e)) from e


async def classify_voice_note(
    transcript, api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> str:
    """Classify the transcript into one of NOTE_TYPES. Never raises —
    a bad LLM response (non-JSON, network error, out-of-vocab label) falls
    back to "general" so the structure call can always proceed with a
    safe type. Mirrors services.reformatting.classify_intent's
    never-raise guarantee."""
    text = _transcript_text(transcript)
    prompt = (
        "The following is a raw speech-to-text transcript of someone "
        "capturing a quick personal voice note. Decide which single kind "
        "of note this MOST naturally is:\n"
        '- "todo": a list of things to do, a task, a plan of action\n'
        '- "idea": a concept, an observation, something to think about, a note to self\n'
        '- "reminder": something the speaker wants to be reminded of later\n'
        '- "journal": a personal reflection, a moment being recorded, what happened today\n'
        '- "bug": a defect, error, or crash report\n'
        '- "general": none of the above fit well\n\n'
        'Respond with JSON: {"type": "todo" | "idea" | "reminder" | "journal" | "bug" | "general"}\n\n'
        f"TRANSCRIPT:\n{text}"
    )
    try:
        content = await _generate(prompt, api_key, provider_name, model, provider_config, json_mode=True)
        label = json.loads(content).get("type", "general")
    except Exception:
        return "general"
    return label if label in NOTE_TYPES else "general"


async def _structure_from_text(
    text: str, note_type: str,
    api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
    include_clarifying: bool = False,
) -> dict:
    """Run the per-type structure prompt against an already-extracted text
    string. Same contract as structure_voice_note but takes text directly
    so the voice-dump path can call it per-span without a transcript object.

    When include_clarifying is True, the prompt also requests a
    'clarifying_questions' key (array of short follow-up questions) and
    the result dict includes it."""
    if note_type not in NOTE_TYPES:
        note_type = "general"
    prompt = _structure_prompt(note_type).replace("{text}", text)
    if include_clarifying:
        prompt += (
            '\nAlso include a "clarifying_questions" key: an array of 0-3 '
            "short follow-up questions to ask the user if their note is "
            "unclear or needs more context to structure well.\n"
        )
    try:
        content = await _generate(prompt, api_key, provider_name, model, provider_config, json_mode=True)
        data = json.loads(content)
    except Exception:
        fallback = {
            "type": note_type,
            "title": (text.strip().splitlines()[0] if text.strip() else "Voice note")[:80],
            "body": text,
            "structured": {},
        }
        if include_clarifying:
            fallback["clarifying_questions"] = []
        return fallback
    result = {
        "type": note_type,
        "title": (data.get("title") or "").strip()[:255],
        "body": (data.get("body") or "").strip(),
        "structured": data.get("structured") if isinstance(data.get("structured"), dict) else {},
    }
    if include_clarifying:
        cq = data.get("clarifying_questions")
        result["clarifying_questions"] = cq if isinstance(cq, list) else []
    return result


async def structure_voice_note(
    transcript, note_type: str,
    api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> dict:
    """Thin wrapper that extracts text from the transcript object and
    delegates to _structure_from_text. Preserves the original signature
    and behavior for existing callers."""
    text = _transcript_text(transcript)
    return await _structure_from_text(text, note_type, api_key, provider_name, provider_config, model)


async def segment_voice_dump(
    transcript,
    api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> list[dict]:
    """Split a long multi-topic voice-dump transcript into ordered spans.

    One LLM call classifies the raw transcript into individual items each
    with an exact text span and a tentative type. Returns
    [{span_text, tentative_type}] — spans + labels only, no full bodies
    (avoids context truncation when the transcript has many items).

    Falls back to a single "general"-typed item wrapping the full
    transcript on any parse error or empty result, so the caller's loop
    always has something to iterate over."""
    text = _transcript_text(transcript)
    if not text.strip():
        return [{"span_text": "", "tentative_type": "general"}]
    prompt = (
        "The following is a raw speech-to-text transcript of a continuous "
        "voice capture session where the speaker dictated multiple separate "
        "items one after another (bugs, ideas, todos, reminders, journal "
        "entries, or general notes).\n\n"
        "Split this transcript into individual items. For each item, include "
        "the exact span of text belonging to that item and classify its type "
        "into one of:\n"
        '- "todo": a list of things to do, a task, a plan of action\n'
        '- "idea": a concept, an observation, something to think about\n'
        '- "reminder": something the speaker wants to be reminded of later\n'
        '- "journal": a personal reflection, a moment being recorded\n'
        '- "bug": a defect, error, or crash report\n'
        '- "general": none of the above fit well\n\n'
        "Respond with a JSON array: "
        '[{"span_text": "the exact transcript text for this item", '
        '"tentative_type": "todo"}, ...]\n\n'
        f"TRANSCRIPT:\n{text}"
    )
    try:
        content = await _generate(
            prompt, api_key, provider_name, model,
            provider_config, json_mode=True,
        )
        items = json.loads(content)
        if isinstance(items, list) and len(items) > 0:
            valid = [
                {"span_text": (item.get("span_text") or "").strip(),
                 "tentative_type": item.get("tentative_type", "general")}
                for item in items
                if isinstance(item, dict) and (item.get("span_text") or "").strip()
            ]
            if valid:
                return valid
    except Exception:
        pass
    return [{"span_text": text.strip(), "tentative_type": "general"}]


async def run_voice_note_chain(
    transcript, api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> dict:
    """Run the two-call chain. Returns the same shape structure_voice_note
    returns ({type, title, body, structured}). The classification call
    itself is best-effort (returns "general" on failure), so this entry
    point only raises if both calls fail in a way that the structure
    fallback can't paper over — which is currently never (structure
    always returns something). The LlmJob worker is therefore responsible
    for catching any unexpected exception and marking the job failed
    with a clear message rather than letting it bubble into the worker
    loop's generic handler."""
    note_type = await classify_voice_note(
        transcript, api_key=api_key, provider_name=provider_name,
        provider_config=provider_config, model=model,
    )
    return await structure_voice_note(
        transcript, note_type, api_key=api_key, provider_name=provider_name,
        provider_config=provider_config, model=model,
    )
