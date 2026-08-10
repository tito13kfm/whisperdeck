# Feature: diarization

## Sources consulted
- `services/diarization.py` full file (1-465)
- `app.py:2577-2617` (POST /api/diarize), `2781-2820` (POST rediarize), `1111-1150,1380-1441` (_run_transcription_pipeline inline call), `150,160-161,594,975` (wiring), `1483,1547,1656,2242,2286` (pipeline callers)
- `services/llm_jobs.py:23,29,45,414,700-768,1009-1077` (rediarize job kind)
- `services/queue.py:592-644` (chunked-upload worker, second inline caller)
- `services/relabel.py:20,101` (signatures only)

## Concrete findings
- **Mode selection is auto-detect via `_check_pyannote()` (eager try/import probe), not a config flag.** Two independent selection points:
  1. `DiarizationService.diarize_and_merge` (diarization.py:51-84) — merge-aware path for all "attach to transcript" callers. Priority: stereo_audio_path given+exists+pyannote available -> `diarize_live_stereo` (382), any exception inside falls back to `diarize_pyannote` on mixed audio (70-74); else pyannote available -> `diarize_pyannote` (236); else -> `diarize_heuristic` (86), forcing num_speakers or 2. Result always passed through `combine_with_transcript` (279) for time-overlap merge.
  2. `POST /api/diarize` (app.py:2579-2616) — simpler explicit-param selector, does NOT go through diarize_and_merge: method=="pyannote" and check -> diarize_pyannote directly, else diarize_heuristic directly. No live-stereo, no transcript merge (standalone file, raw segments).
- **Four call sites feed diarize_and_merge** (Phase 0 only knew of two):
  - app.py:1422 inside `_run_transcription_pipeline` (1111), itself called from transcribe_audio (1530), bulk_transcribe (1656), retranscribe_transcript (2286) — inline, synchronous.
  - services/queue.py:630 inside chunked-upload background queue worker.
  - services/llm_jobs.py:744 inside run_llm_job's kind=="rediarize" branch (732-768), enqueued by POST /rediarize (app.py:2819).
- Side effects: audio reads via soundfile (diarize_pyannote:262, diarize_live_stereo:396); model load `Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=...)` inside `_run_pyannote_sync` (226), **reloaded every call, no caching** — network+disk load each invocation; blocking work offloaded via `loop.run_in_executor` (270, 434). DB writes happen only in callers, never in diarization.py itself.
- rediarize specifically calls `clear_relabel_history(db, transcript.id)` before overwriting segments (llm_jobs.py:758) since re-diarization invalidates prior manual relabels/voice-match results (index-based).
- Error/fallback: pyannote unavailable -> diarize_and_merge short-circuits to heuristic (never calls pyannote path); direct diarize_pyannote call with pyannote unavailable raises ImportError (247-251). diarize_live_stereo raises ValueError for <2 channels (398) or dual-mono identical channels (406-409), caught by diarize_and_merge's try/except, falls back to mixed-audio pyannote (not heuristic).

## Mermaid flowchart

```mermaid
flowchart TD
    EP1["POST /api/transcribe, /bulk-transcribe,<br/>/retranscribe<br/>app.py:1530,1656,2286"]
    EP2["POST /api/diarize<br/>app.py:2579"]
    EP3["POST /transcripts/{id}/rediarize<br/>app.py:2781"]

    EP1 --> PIPE["_run_transcription_pipeline<br/>app.py:1111"]
    PIPE --> GATE1{"diarize flag true<br/>and segments non-empty?<br/>app.py:1420"}
    GATE1 -->|"yes"| DAM

    EP1B["Chunked large-file upload<br/>(async queue worker)"] --> QW["queue_worker_loop<br/>services/queue.py:892"]
    QW --> DAM

    EP3 --> ENQ["t.diarize_requested=True; db.commit<br/>enqueue_llm_job kind=rediarize<br/>app.py:2816-2819"]
    ENQ --> WORKER["llm_worker_loop -> run_llm_job<br/>services/llm_jobs.py:414,732"]
    WORKER --> DAM

    DAM["DiarizationService.diarize_and_merge<br/>services/diarization.py:51"]
    DAM --> D1{"stereo_audio_path exists<br/>and pyannote_available?<br/>diarization.py:65"}

    D1 -->|"yes"| LS["diarize_live_stereo<br/>diarization.py:382"]
    LS --> LS1["_active_intervals mic channel (VAD)<br/>diarization.py:411,327"]
    LS1 --> LS2["_drop_bleed: keep mic-dominant spans<br/>diarization.py:446"]
    LS --> LS3{"remote_count!=0 and system<br/>has speech and pyannote_available?<br/>diarization.py:427"}
    LS3 -->|"yes"| LS4["_run_pyannote_sync on system channel<br/>via run_in_executor<br/>diarization.py:206,270 (thread)"]
    LS3 -->|"no"| LS5["mic-only 'You' segments"]
    LS -->|"success"| MERGE
    LS -->|"raises (bad channels/<br/>dual-mono/inference error)"| PYFB["fallback: diarize_pyannote on mixed audio<br/>diarization.py:70-74"]
    PYFB --> MERGE

    D1 -->|"no, pyannote_available"| PY["diarize_pyannote<br/>diarization.py:236"]
    PY --> PY1["soundfile.read audio file<br/>diarization.py:262"]
    PY1 --> PY2["run_in_executor -> _run_pyannote_sync<br/>diarization.py:206,270 (thread)"]
    PY2 --> PY3["Pipeline.from_pretrained<br/>'pyannote/speaker-diarization-3.1'<br/>(model load, HF token)<br/>diarization.py:226"]
    PY3 --> PY4["pipeline inference; itertracks<br/>diarization.py:230-233"]
    PY4 --> MERGE
    PY -->|"pyannote_available False"| PYERR["raise ImportError<br/>diarization.py:247-251"]

    D1 -->|"no pyannote"| HE["diarize_heuristic<br/>diarization.py:86"]
    HE --> HE1{"segments param given?<br/>diarization.py:99"}
    HE1 -->|"no"| HE2["_pseudo_segments_from_silence<br/>energy-based VAD<br/>diarization.py:156"]
    HE1 -->|"yes"| HE3
    HE2 --> HE3["sort by start; gap>1.5s<br/>alternates speaker label<br/>diarization.py:106-127"]
    HE3 --> HE4["reassign duplicate labels<br/>to unused speaker slots<br/>diarization.py:129-147"]
    HE4 --> MERGE

    MERGE["combine_with_transcript:<br/>overlap-by-speaker, confidence score<br/>diarization.py:279"]
    MERGE --> RESULT["return merged, speaker_count, method<br/>diarization.py:83-84"]

    RESULT --> SAVE1["transcript.segments=merged; commit<br/>app.py:1429-1432"]
    RESULT --> SAVE2["segments=merged; committed later in<br/>chunk-finalize flow<br/>services/queue.py:635"]
    RESULT --> SAVE3["clear_relabel_history (invalidate<br/>prior manual/voice-match relabels);<br/>transcript.segments=merged; commit<br/>services/llm_jobs.py:758-766"]

    GATE1 -->|"no"| SKIP["diarization skipped entirely"]
    PIPE -->|"exception"| ERR1["transcript.error set;<br/>status->'partial' if completed<br/>app.py:1433-1440"]
    QW -->|"exception"| ERR2["diarization_error set;<br/>status->'partial'<br/>services/queue.py:636-641"]
    WORKER -->|"exception"| ERR3["_finish(job,'failed', str(e))<br/>services/llm_jobs.py:767-768"]

    EP2 --> M2{"method=='pyannote' and<br/>_check_pyannote()?<br/>app.py:2597"}
    M2 -->|"yes"| PY2D["diarize_pyannote (direct, no merge)<br/>diarization.py:236 via app.py:2599"]
    M2 -->|"no"| HE2D["diarize_heuristic (direct, no merge)<br/>diarization.py:86 via app.py:2603"]
    PY2D --> RESP2["return raw segments JSON<br/>(no transcript, no DB write)<br/>app.py:2607-2614"]
    HE2D --> RESP2
    EP2 -->|"exception"| ERR4["HTTPException 500<br/>app.py:2615-2616"]
```

## External dependencies
- app.py routes: /api/transcribe, /bulk-transcribe, /retranscribe all via shared `_run_transcription_pipeline` calling diarize_and_merge inline.
- POST /api/diarize: standalone, calls diarize_pyannote/diarize_heuristic directly, no transcript context.
- POST rediarize: enqueues LlmJob(kind="rediarize"), picked up by llm_worker_loop.
- services/queue.py:892 queue_worker_loop: fourth caller of diarize_and_merge (chunked large-file background transcription), found via diarization_service parameter threading, not in Phase 0's original scope note.
- After rediarize completes, clear_relabel_history invalidates prior relabels; separate voice_match job kind (llm_jobs.py:769, entry POST voice-match app.py:2823) runs independently afterward — this is the voice-id-match feature boundary, not traced further.

## Confidence and gaps
High confidence on diarization.py internals (full file read) and mode-selection/fallback chain. Two call sites beyond Phase 0's scope note discovered and included (queue.py chunked-upload worker; bulk_transcribe/retranscribe as additional _run_transcription_pipeline callers) — flagging for Phase 0 inventory correction. Did not open services/voice_id.py, services/relabel.py beyond signatures, or static/rack.js. Did not verify enqueue_llm_job internals beyond call site/effect.
