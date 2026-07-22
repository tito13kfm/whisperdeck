"""Reformats a dictation transcript into other useful shapes — a clean
Markdown note, an email draft, or a well-formed prompt for a coding agent
(e.g. Claude Code) — plus a lightweight classifier that guesses which one
fits best. Mirrors services/transcription.py's summarize(): one LLM call
per target, plain text out (classify_intent returns a short label instead).
"""
import json

from backends import ProviderError
from services.llm_client import chat_completion, resolve_model, transcript_text_for_prompt

_transcript_text = transcript_text_for_prompt
INTENT_LABELS = ("markdown", "email", "coding_prompt", "none")


async def _generate(
    prompt: str, api_key: str, provider_name: str, model: str,
    provider_config: dict | None, json_mode: bool = False,
) -> str:
    try:
        model = resolve_model(provider_name, model, feature_name="Reformatting")
        return await chat_completion(
            prompt, api_key, provider_name, model, json_mode=json_mode,
            provider_config=provider_config,
            system="You output only what is requested, no commentary, no markdown code fences unless asked for them.",
            temperature=0.3,
            raise_on_truncation=True,
            feature_name="Reformatting",
            http_error_label="Reformatting",
            truncation_message=(
                "Reformatting was cut off (model hit its token/context limit) — "
                "try a shorter recording or a model with a larger context window."
            ),
        )
    except RuntimeError as e:
        raise ProviderError(str(e)) from e


async def format_as_markdown(
    transcript, api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> str:
    """Return a clean, structured Markdown note from a rambling dictation."""
    text = _transcript_text(transcript)
    prompt = f"""The following is a raw speech-to-text transcript of someone thinking out loud or dictating notes. Turn it into a clean, well-organized Markdown note: give it a short title (# heading), group related thoughts under sensible subheadings or bullet lists, and tighten the language without changing its meaning or adding information that wasn't said. Return only the Markdown, no commentary before or after it.

TRANSCRIPT:
{text}"""
    content = await _generate(prompt, api_key, provider_name, model, provider_config)
    return content.strip()


async def format_as_email(
    transcript, api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> str:
    """Return a subject + body email draft from a rambling dictation."""
    text = _transcript_text(transcript)
    prompt = f"""The following is a raw speech-to-text transcript of someone dictating what they want to say in an email. Turn it into a polished email draft: infer a concise subject line and write a clear, appropriately professional body, without inventing facts, names, or details that weren't in the transcript. Format the output exactly as:

Subject: <subject line>

<body>

TRANSCRIPT:
{text}"""
    content = await _generate(prompt, api_key, provider_name, model, provider_config)
    return content.strip()


async def format_as_coding_prompt(
    transcript, api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> str:
    """Return a well-formed prompt suitable for pasting into an AI coding
    assistant (e.g. Claude Code), rewritten from a rambling description of
    a coding task, bug, or feature."""
    text = _transcript_text(transcript)
    prompt = f"""The following is a raw speech-to-text transcript of someone describing a coding task, bug, or feature they want help with. Rewrite it as a clear, well-structured prompt suitable for pasting directly into an AI coding assistant: state the goal, any constraints or context mentioned, and what a successful outcome looks like. Do not invent requirements that weren't mentioned, and do not write any actual code — only the prompt text a person would send. Return only the prompt, no commentary before or after it.

TRANSCRIPT:
{text}"""
    content = await _generate(prompt, api_key, provider_name, model, provider_config)
    return content.strip()


async def classify_intent(
    transcript, api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> str:
    """Guess which reformat target best fits this dictation. Returns one of
    INTENT_LABELS; never raises — falls back to 'none' on any failure (bad
    provider, API error, unparseable response) so a classification hiccup
    never blocks the underlying transcript. Purely a UI hint — all format
    actions stay available regardless of the result."""
    text = _transcript_text(transcript)
    prompt = f"""The following is a raw speech-to-text transcript of someone dictating out loud. Decide which single output format would be MOST useful to turn this into:
- "markdown": a general note, idea dump, or list of thoughts
- "email": something clearly meant to be sent to someone as an email
- "coding_prompt": a description of a coding task, bug, or feature request meant for an AI coding assistant
- "none": none of the above fit well

Respond with JSON: {{"format": "markdown" | "email" | "coding_prompt" | "none"}}

TRANSCRIPT:
{text}"""
    try:
        content = await _generate(prompt, api_key, provider_name, model, provider_config, json_mode=True)
        label = json.loads(content).get("format", "none")
    except Exception:
        return "none"
    return label if label in INTENT_LABELS else "none"
