# Feature: correction-hotwords

## Sources consulted
- `app.py`: 945-964 (hotword CRUD), 1435-1460 (inline post-transcription auto-correct trigger), 1480-1530 (upload route + context_doc extraction), 2754-2778 (manual /correct route), 3220-3291 (context-attach route, correction-models route)
- `services/correction.py` full file (1-239)
- `services/hotwords.py` full file (1-44)
- `database/__init__.py` 33-66 (Transcript.corrected_text/correction_error/correction_model), 140-147 (HotwordEntry model)
- `services/llm_jobs.py` 100-194, 280-347, 414-494
- `services/llm_client.py` 69-128
- `services/queue.py` 675-699 (chunked-finalize auto-correct enqueue)

## Concrete findings
- **Duplication question answered**: `correct_transcript` has exactly ONE call site in the whole codebase: `services/llm_jobs.py:457`, inside `run_llm_job`'s `kind == "correction"` branch. Neither the manual route nor auto-correct paths call it directly — all three (manual route app.py:2754, inline-finalize app.py:1453, chunked-finalize queue.py:693) converge through `enqueue_llm_job`/`enqueue_auto_correction` into one `LlmJob` row. correction-hotwords is a pure client of llm-job-queue, not a parallel implementation.
- The two auto-correct call sites (app.py:1453, queue.py:693) are themselves near-duplicate (identical framing comments referencing "design decision 11") but within this feature's own boundary, not a competing job-queue implementation.
- Hotword-extraction is a separate inline (non-queued) side flow: `extract_hotwords_from_doc` called directly and awaited from two entry points — upload-time context_doc (app.py:1515-1528, swallows exceptions silently) and explicit context route (app.py:3236-3273, raises 502 on failure).
- LLM calls route through module-local `_chat_completion` wrapper (correction.py:32) → `services/llm_client.py:69 chat_completion`, feature_name="Correction".
- `_batch_lines` (correction.py:58) chunks transcript lines by `_CHUNK_CHAR_BUDGET=6000` chars with 4-line overlap; each line tagged `[Lnnnn]` by `_id_line` (correction.py:27) so LLM output can be validated/re-stitched (valid/misplaced/invented ID checks, correction.py:153-199).

## Mermaid flowchart

```mermaid
flowchart TD
    subgraph EP["Entry points"]
        A1["POST /api/transcripts/id/correct<br/>app.py:2754"]
        A2["POST /api/transcribe<br/>(context_doc param)<br/>app.py:1494"]
        A3["POST /api/transcripts/id/context<br/>app.py:3236"]
        A4["GET/POST/DELETE /api/hotwords<br/>app.py:945-963"]
        A5["Inline finalize:<br/>auto_correct branch<br/>app.py:1450-1453"]
        A6["Chunked finalize:<br/>auto_correct branch<br/>services/queue.py:690-693"]
    end

    A1 -->|"resolve_provider_key"| B1["enqueue_llm_job(kind='correction')<br/>call site app.py:2777<br/>(fn at llm_jobs.py:109)"]
    A5 --> B2["enqueue_auto_correction()<br/>services/llm_jobs.py:180"]
    A6 --> B2
    B2 -->|"resolve_provider_key;<br/>keyless-provider check"| B1

    B1 --> C1["LlmJob row created<br/>(status=pending, kind='correction')"]
    C1 --> C2["Background worker loop<br/>picks up job, sets 'running'"]
    C2 --> D1["run_llm_job()<br/>services/llm_jobs.py:414<br/>kind=='correction' branch: 444-494"]

    D1 --> E1["correct_transcript()<br/>services/correction.py:78"]
    E1 --> E2["list_hotwords(db, user_id)<br/>services/hotwords.py:9<br/>builds glossary_block"]
    E1 --> E3["_transcript_lines(transcript)<br/>services/correction.py:45<br/>segments -> 'Speaker: text' lines"]
    E3 --> E4["_id_line() tags each line [Lnnnn]<br/>services/correction.py:27"]
    E4 --> E5["_batch_lines(overlap=4)<br/>services/correction.py:58<br/>chunks by _CHUNK_CHAR_BUDGET=6000 chars"]
    E5 --> E6["per batch: cancel_cb() check<br/>services/correction.py:119"]
    E6 --> E7["_chat_completion()<br/>services/correction.py:32"]
    E7 --> E8["chat_completion()<br/>services/llm_client.py:69<br/>LLM API call, json_mode=True"]
    E8 --> E9["parse JSON records,<br/>validate IDs (valid/misplaced/invented)<br/>services/correction.py:153-199"]
    E9 -->|"more batches"| E6
    E9 -->|"done"| E10["stitch sorted records into<br/>transcript.corrected_text<br/>services/correction.py:201-205"]
    E10 --> F1["db.commit()<br/>writes Transcript.corrected_text,<br/>correction_model, correction_error<br/>database/__init__.py:64-66"]

    F1 --> D2{"result == 'ok'?"}
    D2 -->|"yes"| D3["_finish(job,'completed')<br/>job.result_json = corrected_text<br/>services/llm_jobs.py:462-464"]
    D2 -->|"no ('failed')"| D4["_finish(job,'failed', correction_error)<br/>services/llm_jobs.py:482"]
    D3 --> D5["enqueue_pipeline_classify()<br/>services/llm_jobs.py:294<br/>(no-op unless classification pending)"]
    D4 --> D5

    A2 -->|"context_doc present,<br/>key resolved"| G1["extract_hotwords_from_doc()<br/>(inline, awaited, non-queued)<br/>app.py:1522"]
    A3 -->|"key resolved or 502"| G1b["extract_hotwords_from_doc()<br/>app.py:3264"]
    G1 --> G2["services/correction.py:215"]
    G1b --> G2
    G2 --> G3["_chat_completion() -> chat_completion()<br/>services/llm_client.py:69"]
    G3 --> G4["parse {'terms':[...]}<br/>services/correction.py:234"]
    G4 --> G5["add_hotword(term, source='extracted')<br/>services/hotwords.py:13<br/>per term, dedup case-insensitive"]
    G5 --> G6["db.commit() per term<br/>writes HotwordEntry row<br/>database/__init__.py:140"]

    A4 -->|"GET"| H1["list_hotwords()<br/>services/hotwords.py:9"]
    A4 -->|"POST"| H2["add_hotword(source='manual')<br/>services/hotwords.py:13"]
    A4 -->|"DELETE"| H3["delete_hotword()<br/>services/hotwords.py:33"]
    H2 --> G6
    H3 --> H4["db.delete + commit<br/>HotwordEntry row removed"]
```

## External dependencies
- llm-job-queue feature: sole path to `correct_transcript` execution (via LlmJob row + worker loop).
- `services/llm_client.py chat_completion`: shared LLM call plumbing (also used by reformatting, tagging, voice_notes, assistant).
- classification-tagging feature: `enqueue_pipeline_classify` handoff after correction completes.
- `database/__init__.py` Transcript and HotwordEntry models.

## Confidence and gaps
High confidence — every node verified against source, not inferred; the single-call-site claim confirmed by whole-repo grep. Did not diagram the LLM worker loop's job-claiming/retry mechanics (belongs to llm-job-queue), classification pipeline internals (separate feature), or frontend call sites (out of scope). Omitted queue.py:690-699's "auto_correct disabled" fallback branch as minor.
