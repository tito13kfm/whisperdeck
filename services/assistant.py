"""Assistant service: LLM intent interpreter and action executor.

interpret_request() translates a natural-language request into a JSON action plan.
execute_plan() runs the plan step by step (search, summarize, save_markdown).
"""
import json
import os
import re

from services.llm_client import chat_completion, resolve_model
from services.search import search_transcripts

_SUPPORTED_ACTIONS = ("search", "summarize", "save_markdown")

_INTERPRETER_SYSTEM = """You are a meeting transcript assistant. You output ONLY valid JSON, no commentary.
Available actions:
- search: query all of the user's transcripts. params: {query: string}
- summarize: summarize the provided transcript excerpts. params: {focus: string (optional)}
- save_markdown: save the summary text as a .md file. params: {filename: string (optional)}

Rules:
1. Decompose multi-concept requests into separate search terms.
2. search must come before summarize and save_markdown.
3. Only output the JSON plan — no markdown, no code fences, no commentary.
4. If the request is ambiguous, include a clarification step with action "search" and the best interpretation.

Output format:
{"steps": [{"action": "search", "params": {"query": "Sandeep Claude"}}, {"action": "summarize", "params": {"focus": "what Sandeep said about Claude"}}, {"action": "save_markdown", "params": {"filename": "Sandeep-Claude-discussion.md"}}]}"""

_SUMMARIZE_SYSTEM = "Summarize the following transcript excerpts. Focus on what was said, by whom, and the key points. Be concise."

_FILENAME_SAFE_RE = re.compile(r"[^-_.a-zA-Z0-9]")
_MAX_FILENAME_CHARS = 128


def _sanitize_filename(filename: str) -> str:
    safe = filename or "summary"
    safe = safe.replace("\\", "-").replace("/", "-").replace("..", "-")
    safe = _FILENAME_SAFE_RE.sub("-", safe)
    safe = safe.strip("-")
    if len(safe) > _MAX_FILENAME_CHARS:
        safe = safe[:_MAX_FILENAME_CHARS]
    return safe or "summary"


def _resolve_export_path(export_directory: str, filename: str) -> str:
    resolved = os.path.realpath(os.path.join(export_directory, filename))
    export_real = os.path.realpath(export_directory)
    if not resolved.startswith(export_real + os.sep) and resolved != export_real:
        raise ValueError("Filename escapes export directory")
    return resolved


async def interpret_request(user_request: str, api_key: str, provider_name: str,
                            model: str, provider_config: dict | None = None) -> dict:
    resolved_model = resolve_model(provider_name, model, "Assistant")
    prompt = f"User request: {user_request}\n\nGenerate the JSON action plan."
    try:
        content = await chat_completion(
            prompt, api_key, provider_name, resolved_model, json_mode=True,
            provider_config=provider_config, system=_INTERPRETER_SYSTEM,
            temperature=0.2, feature_name="Assistant",
        )
    except RuntimeError as e:
        return {"error": str(e)}
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        content = content.rsplit("```", 1)[0]
        content = content.strip()
    try:
        plan = json.loads(content)
    except json.JSONDecodeError:
        return {"error": f"Model did not return valid JSON: {content[:200]!r}"}
    if not isinstance(plan, dict) or "steps" not in plan:
        return {"error": "Plan missing 'steps' key"}
    for step in plan["steps"]:
        if step.get("action") not in _SUPPORTED_ACTIONS:
            return {"error": f"Unsupported action: {step.get('action')}"}
    seen = set()
    for step in plan["steps"]:
        action = step.get("action")
        if action in ("summarize", "save_markdown") and "search" not in seen:
            return {"error": f"Action '{action}' must come after a search step"}
        seen.add(action)
    return plan


async def execute_plan(db, user_id: int, plan: dict, api_key: str, provider_name: str,
                       model: str, provider_config: dict | None = None,
                       export_directory: str = "", job=None) -> dict:
    summary_text = ""
    search_results = []

    for i, step in enumerate(plan.get("steps", [])):
        action = step.get("action")
        params = step.get("params", {})

        if action == "search":
            query = (params.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "Search step missing query",
                        "result": {"summary": "", "file_path": None, "preview": ""}}
            search_results = search_transcripts(db, user_id, query)
            if not search_results:
                return {"ok": True, "error": None,
                        "result": {"summary": "No matching transcripts found.",
                                   "file_path": None, "preview": ""}}

        elif action == "summarize":
            if not search_results:
                return {"ok": False, "error": "Summarize step called before search",
                        "result": {"summary": "", "file_path": None, "preview": ""}}
            focus = (params.get("focus") or "").strip()
            context_parts = []
            for t in search_results:
                for seg in (t.get("matching_segments") or []):
                    speaker = seg.get("speaker", "Unknown")
                    text = seg.get("text", "")
                    context_parts.append(f"[{t.get('title', 'Untitled')}] {speaker}: {text}")
            context = "\n".join(context_parts)
            if len(context) > 60000:
                context = context[:60000] + "..."
            prompt = f"""Transcript excerpts:

{context}

{"Focus: " + focus if focus else "Summarize the key points from these transcript excerpts."}"""
            try:
                resolved_model = resolve_model(provider_name, model, "Summarization")
                summary_text = await chat_completion(
                    prompt, api_key, provider_name, resolved_model, json_mode=False,
                    provider_config=provider_config, system=_SUMMARIZE_SYSTEM,
                    temperature=0.3, feature_name="Assistant Summarization",
                )
            except RuntimeError as e:
                return {"ok": False, "error": str(e),
                        "result": {"summary": "", "file_path": None, "preview": ""}}

        elif action == "save_markdown":
            if not summary_text:
                return {"ok": False, "error": "Save step called before summarize",
                        "result": {"summary": "", "file_path": None, "preview": ""}}
            filename = params.get("filename", "summary.md")
            safe_name = _sanitize_filename(filename)
            if not safe_name.endswith(".md"):
                safe_name += ".md"
            if not export_directory:
                return {"ok": True, "error": None,
                        "result": {"summary": summary_text, "file_path": None,
                                   "preview": summary_text[:500]}}
            try:
                os.makedirs(export_directory, exist_ok=True)
            except OSError as e:
                return {"ok": True, "error": f"Could not create export directory: {e}",
                        "result": {"summary": summary_text, "file_path": None,
                                   "preview": summary_text[:500]}}
            try:
                file_path = _resolve_export_path(export_directory, safe_name)
            except ValueError as e:
                return {"ok": False, "error": str(e),
                        "result": {"summary": summary_text, "file_path": None, "preview": ""}}
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(summary_text)
            except OSError as e:
                return {"ok": False, "error": f"Could not write file: {e}",
                        "result": {"summary": summary_text, "file_path": None, "preview": ""}}
            return {"ok": True, "error": None,
                    "result": {"summary": summary_text, "file_path": file_path,
                               "preview": summary_text[:500]}}

        if job:
            job.progress_done = 1 + i + 1
            db.commit()

    preview = summary_text[:500] if summary_text else ""
    return {"ok": True, "error": None,
            "result": {"summary": summary_text, "file_path": None, "preview": preview}}
