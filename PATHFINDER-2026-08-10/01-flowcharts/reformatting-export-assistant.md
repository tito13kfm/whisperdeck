# Feature: reformatting-export-assistant

## Sources consulted
- `app.py:2649-2751` (format_transcript, export_markdown routes), `3373-3421` (assistant_request, assistant_result routes)
- `services/reformatting.py` full file (1-161)
- `services/assistant.py` full file (1-180)
- `services/llm_client.py` full file (1-153)
- `services/llm_jobs.py:400-560` (job dispatch), `930-1007` (assistant dispatch, run_assistant_job)
- `services/search.py:1-146` (search_transcripts, FTS5 sanitization)
- `services/settings.py:52` (export_directory default)

## Concrete findings
**Format/export path**: `format_transcript` (app.py:2658) validates target against `_FORMAT_TARGET_KINDS` (2651-2655), checks ownership/kind/status (2673-2684), resolves provider key (2685-2688), enqueues job kind format_markdown|format_email|format_coding_prompt (2690). Worker (llm_jobs.py:514-531) dispatches to format_as_markdown/email/coding_prompt (reformatting.py:40/54/72), each calls shared `_generate` helper (17-37) -> `resolve_model` (llm_client.py:44) -> `chat_completion` (llm_client.py:69), single non-streaming HTTP POST. Truncation (finish_reason=='length') raises; HTTP non-200 raises; both caught at llm_jobs.py:530, job set failed.

`export_markdown` (app.py:2694) is the no-LLM sibling: reads export_directory from settings (2712-2716), existence+writability probe via temp file write/delete (2717-2726), calls `build_export_markdown` (reformatting.py:116, pure string assembly), builds filename from transcript title+date (2739-2742, sanitized only via fixed regex stripping `\/:*?"<>|`, title from transcript itself not LLM), writes with `open(...,"w")` (2745-2751).

**Assistant path**: `assistant_request` (app.py:3375) validates length (<=2000 chars), resolves provider key, enqueues job kind assistant, stashes raw user_request string in job.result_json (3398-3400) — interpretation/execution happens later in the worker, not inline. `run_assistant_job` (llm_jobs.py:954) calls `interpret_request` (assistant.py:54), sends user text + fixed system prompt to chat_completion in JSON mode, validates returned plan: must parse as JSON, must have "steps" key, every action must be in `_SUPPORTED_ACTIONS = ("search","summarize","save_markdown")` (78), ordering enforced (summarize/save_markdown must follow a search, 81-85). Violation returns {"error":...} -> failed job (llm_jobs.py:982-987), never proceeds to execute_plan.

If plan validates, `execute_plan` (assistant.py:89) walks plan["steps"], dispatching by action:
- search -> `search_transcripts(db,user_id,query)` (search.py:74), read-only, scoped by Transcript.user_id, FTS5 terms double-quoted/escaped (_quote_fts5_term, 20-23) — no raw string concat, safe.
- summarize -> builds context from prior search's matched segments, calls chat_completion again (131-135). No filesystem effect.
- save_markdown -> the only filesystem-writing action, gated by sanitization below.

## Security check: `_sanitize_filename` + `_resolve_export_path` (assistant.py:36-51)
```python
_FILENAME_SAFE_RE = re.compile(r"[^-_.a-zA-Z0-9]")
def _sanitize_filename(filename):
    safe = filename or "summary"
    safe = safe.replace("\\", "-").replace("/", "-").replace("..", "-")
    safe = _FILENAME_SAFE_RE.sub("-", safe)
    safe = safe.strip("-")
    return safe or "summary"

def _resolve_export_path(export_directory, filename):
    resolved = os.path.realpath(os.path.join(export_directory, filename))
    export_real = os.path.realpath(export_directory)
    if not resolved.startswith(export_real + os.sep) and resolved != export_real:
        raise ValueError("Filename escapes export directory")
    return resolved
```
**Verdict: sound, no traversal gap found.** Two independent layers, either alone would block traversal: (1) `_FILENAME_SAFE_RE.sub` is a whitelist ([-_.a-zA-Z0-9] allowed, everything else -> "-"), applied last — structurally excludes path separators regardless of earlier .replace() calls. (2) Even the earlier `.replace("..", "-")` handles all non-overlapping ".." occurrences in one pass, so bypass strings like "....//" don't survive (".... " -> "--", not a reformed ".."). `_resolve_export_path` uses `os.path.realpath` on both sides plus `os.sep`-bounded prefix check with explicit equals-case — correct pattern avoiding the well-known partial-prefix bug ("/home/user_export".startswith("/home/user_exp")-style false positives on sibling dirs). `export_directory` itself is never plan-controlled (comes only from get_user_settings, server-side per-user), so LLM-controlled surface reduces to exactly one string (filename), rendered separator-free before os.path.join. `.md` extension enforced by code (assistant.py:146-147), so plan can't target arbitrary extension either. No code-execution path in execute_plan — only scoped DB read, LLM call, single open(path,"w") text write; no subprocess/eval/exec/template rendering.
One structural note (not a vulnerability): prompt-injection in transcript content returned by search could only influence summarize's output text, not save_markdown's filename — filename is fixed in the plan before search ever runs.

## Side effects
- File writes: export_markdown route (app.py:2745-2751, path from transcript title, sanitized against Windows-reserved chars, not LLM-controlled); execute_plan's save_markdown step (sandboxed write inside export_directory, assistant.py:163-165).
- LLM calls: one per format_as_*/classify_intent call, one for interpret_request (plan generation), one for summarize inside execute_plan — all through chat_completion (llm_client.py:69).
- DB writes: job.progress_done/result_json/status commits throughout llm_jobs.py.

## Error/fallback branches
- classify_intent never raises, falls back to "none" (reformatting.py:108-113) — a UI hint, not reachable via the three traced entry points (sibling auto-classifier).
- format_as_* failures (bad key, HTTP error, truncation) -> job failed (llm_jobs.py:530-531).
- export_markdown: missing/non-existent/non-writable export dir -> HTTP 400/500 before any LLM/plan logic.
- interpret_request: LLM RuntimeError, invalid JSON, missing steps, unsupported action, bad step ordering -> {"error":...}, never reaches execute_plan.
- execute_plan: empty search query, no results, summarize-before-search, save-before-summarize, os.makedirs failure, _resolve_export_path ValueError, file-write OSError all short-circuit with distinct {"ok":bool,"error":...} shapes.

## Mermaid flowchart

```mermaid
flowchart TD
    subgraph FMT["Format / Export entry points"]
        A1["format_transcript route<br/>app.py:2658"]
        A2{"target in _FORMAT_TARGET_KINDS?<br/>app.py:2670-2672"}
        A3["ownership / kind / status checks<br/>app.py:2673-2684"]
        A4["resolve_provider_key + key check<br/>app.py:2685-2688"]
        A5["enqueue_llm_job kind=format_*<br/>app.py:2690"]
        A6["export_markdown route<br/>app.py:2694"]
        A7["load export_directory setting<br/>app.py:2712-2716"]
        A8["dir exists + writability probe<br/>app.py:2717-2726"]
        A9["build_export_markdown<br/>services/reformatting.py:116"]
        A10["filename = title+date (regex-cleaned)<br/>app.py:2739-2742"]
        A11["write .md file<br/>app.py:2745-2751"]
        AERR["400/500 HTTPException<br/>app.py:2672,2677,2682,2684,2716,2718,2726,2749"]
    end

    subgraph WORKER["Job worker: format_* kinds"]
        B1["run_llm_job dispatch<br/>services/llm_jobs.py:514"]
        B2["format_as_markdown/email/coding_prompt<br/>services/reformatting.py:40/54/72"]
        B3["resolve_model<br/>services/llm_client.py:44"]
        B4["chat_completion POST .../chat/completions<br/>services/llm_client.py:69-134"]
        B5{"HTTP 200 and not truncated?<br/>services/llm_client.py:136-151"}
        B6["job completed, result_json.text<br/>services/llm_jobs.py:527-529"]
        B7["job failed, error msg<br/>services/llm_jobs.py:530-531"]
    end

    subgraph ASSIST["Assistant entry"]
        C1["assistant_request route<br/>app.py:3375"]
        C2{"1-2000 chars?<br/>app.py:3383-3387"}
        C3["resolve_provider_key + key check<br/>app.py:3390-3396"]
        C4["enqueue_llm_job kind=assistant<br/>stash user_request in result_json<br/>app.py:3398-3400"]
        C5["assistant_result poll route<br/>app.py:3404"]
        CERR["400 HTTPException<br/>app.py:3385,3387,3396"]
    end

    subgraph AWORKER["Job worker: assistant"]
        D1["run_assistant_job<br/>services/llm_jobs.py:954"]
        D2["interpret_request<br/>services/assistant.py:54"]
        D3["chat_completion json_mode=true (plan)<br/>services/llm_client.py:69"]
        D4{"valid JSON, has steps,<br/>actions supported, ordering OK?<br/>services/assistant.py:71-86"}
        D5["job failed: interpretation error<br/>services/llm_jobs.py:980-987"]
        D6["execute_plan<br/>services/assistant.py:89"]
    end

    subgraph EXEC["execute_plan step loop (assistant.py:95-176)"]
        E0["for i, step in plan.steps<br/>services/assistant.py:95"]
        E1{"action == search?<br/>assistant.py:99"}
        E1B{"query non-empty?<br/>assistant.py:100-103"}
        E2["search_transcripts<br/>scoped by user_id, FTS5-escaped<br/>services/search.py:74"]
        E3{"action == summarize?<br/>assistant.py:110"}
        E3B{"search already ran?<br/>assistant.py:111-113"}
        E4["chat_completion over search context<br/>assistant.py:129-135"]
        E5{"action == save_markdown?<br/>assistant.py:140"}
        E5B{"summary already produced?<br/>assistant.py:141-143"}
        E6["_sanitize_filename: strip \\ / ..,<br/>whitelist [-_.a-zA-Z0-9]<br/>services/assistant.py:36-43"]
        E7{{"SECURITY GUARD<br/>_resolve_export_path:<br/>os.path.realpath + os.sep-bounded<br/>prefix check<br/>services/assistant.py:46-51"}}
        E8["open(file_path,'w') write summary_text<br/>services/assistant.py:163-165"]
        E9["raise ValueError: escapes export dir<br/>services/assistant.py:50"]
        DONE["job completed, result_json<br/>services/llm_jobs.py:1004-1006"]
        FAIL["job failed, error surfaced<br/>services/llm_jobs.py:1006 / assistant.py various"]
    end

    A1 --> A2 -- no --> AERR
    A2 -- yes --> A3 --> A4 --> A5
    A6 --> A7 --> A8 --> A9 --> A10 --> A11
    A3 -. fails .-> AERR
    A4 -. fails .-> AERR
    A8 -. fails .-> AERR
    A11 -. fails .-> AERR
    A5 -. enqueues .-> B1
    B1 --> B2 --> B3 --> B4 --> B5
    B5 -- yes --> B6
    B5 -- no --> B7

    C1 --> C2 -- no --> CERR
    C2 -- yes --> C3 -- fails --> CERR
    C3 -- ok --> C4
    C4 -. enqueues .-> D1
    D1 --> D2 --> D3 --> D4
    D4 -- invalid --> D5 --> FAIL
    D4 -- valid --> D6 --> E0

    E0 --> E1
    E1 -- yes --> E1B
    E1B -- empty --> FAIL
    E1B -- ok --> E2 --> E0
    E1 -- no --> E3
    E3 -- yes --> E3B
    E3B -- no --> FAIL
    E3B -- yes --> E4 --> E0
    E3 -- no --> E5
    E5 -- yes --> E5B
    E5B -- no --> FAIL
    E5B -- yes --> E6 --> E7
    E7 -- inside export dir --> E8 --> DONE
    E7 -- escapes --> E9 --> FAIL
    E5 -- no --> E0

    C5 -.polls status/result.-> DONE
    C5 -.polls status/result.-> FAIL
```

## External dependencies
- httpx.AsyncClient — outbound LLM provider HTTP calls (llm_client.py:7,129)
- LLM provider HTTP APIs: Groq, OpenAI, OpenRouter, local OpenAI-compatible server (llm_client.py:9-25)
- SQLite FTS5 virtual table transcripts_fts via SQLAlchemy text() (search.py:11,13,50-64,98-104)
- Filesystem via os/open() (app.py:2717-2751, assistant.py:153-165)
- re/json stdlib for filename sanitization and plan parsing

## Confidence and gaps
High confidence — full text of every core file read, sanitization logic hand-traced against known bypass patterns rather than assumed safe. Did not read enqueue_llm_job's implementation (just call sites) or frontend code calling these endpoints (backend-only scope). Did not verify job.result_json's user_request field protection between enqueue and worker pickup (assumed same-process DB round-trip). classify_intent noted as adjacent, not force-fit into the two traced paths since not reachable from the three named entry points.
