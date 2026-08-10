# Feature: transcription-pipeline

## Sources consulted
- `app.py:1111-1480` (`_run_transcription_pipeline`), `1482-1543` (POST /api/transcribe route), `32-69` (imports), `149-150` (service instantiation)
- `backends/__init__.py` (full — get_provider, PROVIDER_REGISTRY, LOCAL_PROVIDERS)
- `backends/base.py` (full — BaseProvider, Segment, TranscriptionResult)
- `services/transcription.py` (full — TranscriptionService.create_transcript_stub, .transcribe)
- `services/audio_prep.py` (full — transcode_for_upload, transcode_stereo_for_diarization, get_audio_duration, has_video_stream, extract_clips_concat, chunk_audio, is_silent_audio, detect_silence_midpoints)
- `backends/moonshine.py:1-30`
- Grep-confirmed `async def transcribe` line numbers across all 8 backend files

## Concrete findings
- `app.py:1482 transcribe_audio()` → save upload (1511) → optional non-fatal hotword extraction from context_doc (1515-1528) → `_run_transcription_pipeline()` (1111, called 1530).
- Pipeline: video detect (audio_prep.py:134) → duration probe (audio_prep.py:123) → transcode decision → `transcode_for_upload` ffmpeg (audio_prep.py:44, HTTP 500 on AudioPrepError) → non-fatal `cleanup_audio` (audio_cleanup.py:41) → optional stereo FLAC copy for diarization (audio_prep.py:89, non-fatal) → provider config load + API-key decrypt (DB read, 1263-1275) → re-probe duration (1283).
- Branch at app.py:1295-1298 `hosted_chunked or local_chunked`:
  - **Chunked**: `chunk_audio` silence-aware ffmpeg split (audio_prep.py:308) → empty-chunks special case (all-silent) creates completed stub with empty segments, early return → else `create_transcript_stub` (status=processing, DB write) → **HANDOFF** `create_chunk_jobs` (app.py:1376 → services/queue.py:250, chunk-queue feature boundary) → return.
  - **Inline**: `TranscriptionService.transcribe` (transcription.py:61) → `get_provider()` factory → create Transcript row (status=processing) → `provider.transcribe(...)` dispatches to one of: groq/openai/replicate/openrouter/assemblyai/local (all httpx external) or builtin/moonshine (on-device, no network) → persist full_text/segments + write .txt file → commit; exception path sets status=failed, re-raises.
- Both branches converge: merge fields/commit (1395-1404) → optional `filter_hallucinations` (audio_cleanup.py:124) → optional `DiarizationService.diarize_and_merge` (diarization.py:51), failure is non-fatal (status=partial) → **HANDOFF** enqueue correction/classify/tagging LlmJobs (1450-1470, llm-job-queue feature boundary) → return serialized transcript.
- Top-level `except Exception` (1474-1479): discards orphaned stereo FLAC, HTTP 500.
- `/api/bulk-transcribe` reuses this same `_run_transcription_pipeline` per file — thin fan-out wrapper, not a divergent pipeline (relevant to batch-bulk-files feature).

## Mermaid flowchart

```mermaid
flowchart TD
    A["POST /api/transcribe<br/>app.py:1482"] --> B["Save upload to disk<br/>app.py:1511-1513"]
    B --> C{"context_doc provided?<br/>app.py:1515"}
    C -->|yes| D["extract_hotwords_from_doc (non-fatal)<br/>app.py:1522"]
    C -->|no| E
    D --> E["_run_transcription_pipeline()<br/>app.py:1111 / called app.py:1530"]

    E --> F["has_video_stream probe<br/>services/audio_prep.py:134, app.py:1201"]
    F --> G["get_audio_duration raw probe<br/>services/audio_prep.py:123, app.py:1205"]
    G --> H{"needs_transcode?<br/>app.py:1208-1215"}
    H -->|yes| I["transcode_for_upload (ffmpeg subprocess)<br/>services/audio_prep.py:44, app.py:1218"]
    I -->|AudioPrepError| I_ERR["HTTP 500<br/>app.py:1221-1222"]
    H -->|no| J
    I --> J["cleanup_audio loudnorm/denoise/highpass (non-fatal)<br/>services/audio_cleanup.py:41, app.py:1229"]
    J --> K{"capture_source == live_stereo?<br/>app.py:1235"}
    K -->|yes| L["transcode_stereo_for_diarization (ffmpeg, FLAC, non-fatal)<br/>services/audio_prep.py:89, app.py:1237"]
    K -->|no| M
    L --> M["Load provider config + decrypt API key (DB read)<br/>app.py:1263-1275"]
    M --> N["Re-probe duration post-transcode<br/>app.py:1283"]
    N --> O{"hosted_chunked or local_chunked?<br/>app.py:1295-1298"}

    O -->|chunked| P["chunk_audio: silence-aware ffmpeg split xN<br/>services/audio_prep.py:308, app.py:1306"]
    P -->|AudioPrepError| P_ERR["HTTP 500, discard stereo copy<br/>app.py:1307-1312"]
    P --> Q{"chunks empty (all-silent)?<br/>app.py:1314"}
    Q -->|yes| R["create_transcript_stub, status=completed, empty segments (DB write)<br/>services/transcription.py:20, app.py:1315-1343"]
    R --> RET1["Return serialized transcript<br/>app.py:1343"]
    Q -->|no| S["create_transcript_stub, status=processing (DB write)<br/>services/transcription.py:20, app.py:1346-1371"]
    S --> T["create_chunk_jobs - HANDOFF<br/>app.py:1376 -> services/queue.py:250<br/>(boundary: chunk-queue feature)"]
    T --> RET2["Return serialized transcript, status=processing<br/>app.py:1377"]

    O -->|inline| U["TranscriptionService.transcribe()<br/>services/transcription.py:61, app.py:1380"]
    U --> V["get_provider() factory<br/>backends/__init__.py:40"]
    V --> W["Create Transcript row, status=processing (DB write)<br/>services/transcription.py:83-95"]
    W --> X["provider.transcribe(audio_path, ...)<br/>services/transcription.py:104"]
    X --> X1["GroqProvider (httpx)<br/>backends/groq.py:20"]
    X --> X2["OpenAIProvider (httpx)<br/>backends/openai.py:16"]
    X --> X3["ReplicateProvider (httpx)<br/>backends/replicate.py:28"]
    X --> X4["OpenRouterProvider (httpx)<br/>backends/openrouter.py:23"]
    X --> X5["AssemblyAIProvider (httpx, async polling)<br/>backends/assemblyai.py:22"]
    X --> X6["LocalProvider (httpx, local endpoint)<br/>backends/local.py:21"]
    X --> X7["BuiltinProvider (faster-whisper, on-device)<br/>backends/builtin.py:105"]
    X --> X8["MoonshineProvider (moonshine-voice, on-device)<br/>backends/moonshine.py:89"]
    X1 & X2 & X3 & X4 & X5 & X6 & X7 & X8 --> Y["Persist full_text/segments, write .txt file (DB write + file I/O)<br/>services/transcription.py:108-134"]
    Y -->|exception| Y_ERR["status=failed, db.commit, re-raise<br/>services/transcription.py:139-144"]
    Y --> Z["Merge fields onto transcript row, db.commit<br/>app.py:1395-1404"]

    Z --> AA{"cleanup_hallu_enabled?<br/>app.py:1410"}
    AA -->|yes| AB["filter_hallucinations<br/>services/audio_cleanup.py:124, app.py:1411-1417"]
    AA -->|no| AC
    AB --> AC{"diarize requested & segments present?<br/>app.py:1420"}
    AC -->|yes| AD["DiarizationService.diarize_and_merge<br/>services/diarization.py:51, app.py:1422"]
    AD -->|success| AE["db.commit segments+speaker_count<br/>app.py:1432"]
    AD -->|failure, non-fatal| AD_ERR["status=partial, error set, db.commit<br/>app.py:1433-1439"]
    AC -->|no| AF
    AE --> AF
    AD_ERR --> AF["Enqueue LLM jobs (correction/classify/tagging) - HANDOFF<br/>app.py:1450-1470<br/>(boundary: llm-job-queue feature)"]
    AF --> AG["Return serialized transcript<br/>app.py:1472"]

    U -.->|any exception in outer try| GLOBAL_ERR["discard stereo copy, HTTP 500<br/>app.py:1474-1479"]
```

## External dependencies
- **chunk-queue handoff**: app.py:1376 -> services/queue.py:250 `create_chunk_jobs`. Downstream chunk processing is a separate feature.
- **llm-job-queue handoff**: app.py:1450-1470 enqueues enqueue_auto_correction/enqueue_pipeline_classify/enqueue_auto_classify/enqueue_auto_voice_note/enqueue_auto_voice_dump/enqueue_auto_tagging in services/llm_jobs.py.
- ffmpeg subprocess via `services/audio_prep.py` (`_ffmpeg_bin()`/`_ffprobe_bin()` at 25-34).
- External HTTP APIs: Groq, OpenAI, Replicate, OpenRouter, AssemblyAI, user-configured Local endpoint (httpx.AsyncClient).
- On-device ML: builtin.py (faster-whisper), moonshine.py (moonshine-voice), no network.
- DB writes: ProviderConfig read, Transcript row create/update both branches.
- File I/O: uploaded file, transcoded mp3/FLAC/chunk files under UPLOAD_DIR, plaintext .txt under transcripts/.

## Confidence and gaps
High confidence, all cited lines read directly. Not traced: services/queue.py internals beyond create_chunk_jobs signature (separate feature), services/llm_jobs.py internals for the enqueue calls (separate feature). diarization.py and audio_cleanup.py aren't in the original core-files list but are inline dependencies, included with verified line numbers. /api/bulk-transcribe intentionally left out of the diagram (thin per-file wrapper around this same pipeline, not divergent) — noted for cross-reference with batch-bulk-files feature.
