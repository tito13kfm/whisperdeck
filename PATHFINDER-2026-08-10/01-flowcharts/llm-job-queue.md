# Feature: llm-job-queue

## Sources consulted
- `services/llm_jobs.py` full file (1-1081)
- `database/__init__.py:104-124` (LlmJob model)
- `app.py:54-59,150-164,1420-1472,2160-2192,2621-2870,2995-3051,3310-3371,3382-3401`
- `services/queue.py:60-121,330-360,445,554-556,680-709,785,788-844,882,892` (overlap note only)

## Correction to Phase 0 scope
`services/llm_client.py` is **not called from services/llm_jobs.py at all**. One layer down — invoked by correction.py, reformatting.py, transcription.py, classification.py, assistant.py, tagging.py, voice_notes.py. llm_jobs.py dispatches to those service modules, never to the client directly.

## Job-kind dispatch table (run_llm_job, llm_jobs.py:444-941)
| kind | pool | dispatch line | service called |
|---|---|---|---|
| correction | IO | 444 | services/correction.py correct_transcript (457) |
| summary | IO | 495 | services/transcription.py summarize (169) |
| format_markdown/email/coding_prompt | IO | 514 | services/reformatting.py format_as_* (523) |
| classify_intent | IO | 532 | services/reformatting.py classify_intent (535) |
| classify_pipeline | IO | 542 | services/classification.py classify_pipeline_kind (550) |
| tagging | IO | 597 | services/tagging.py generate_tags (604) |
| voice_note | IO | 624 | services/voice_notes.py run_voice_note_chain (639) |
| voice_dump | IO | 691 | services/voice_notes.py segment_voice_dump/_structure_from_text (700,711) |
| rediarize | CPU | 732 | diarization_service.diarize_and_merge (744, injected) |
| voice_match | CPU | 769 | services/voice_id.py identify_detailed (848), services/audio_prep.py extract_clips_concat (836) |
| assistant | IO | 938->954 | services/assistant.py interpret_request/execute_plan (975,995) |

IO_KINDS/CPU_KINDS partition (44-45): CPU pool = local compute (diarization clustering, voice embedding), cap 1 concurrent; IO pool = provider API calls, cap 2.

## State machine
States: pending -> running -> {completed, failed, cancelled}. All writes other than one documented exemption go through `_transition` (350-380), a single `UPDATE ... WHERE id=? AND status IN (expect)` — atomic compare-and-set (sqlite serializes writers on this statement).

- **PR #389** (per _transition's docstring, 358-364): two review rounds each found an independent read-then-write race on the same row — first _finish's terminal claim, then cancel_llm_job — fixed by collapsing every transition through this one primitive. `_finish` (383-411) claims a terminal state expect=ACTIVE_STATUSES; if a concurrent cancel already landed, _finish returns False, rolls back, refreshes to actual (cancelled) status — callers must not commit dependent writes first.
- **Documented exemption**: `reset_stuck_llm_jobs` (129-147) writes status directly, no CAS — safe because it runs during lifespan startup (app.py:157) before worker/endpoints exist to race it, "exempt by lifecycle, not by luck."
- **Enqueue can short-circuit to a terminal state never auto-retried**: `enqueue_llm_job` (109-126) with `error=` sets status="failed", attempts=0. Resurrection sweep (llm_worker_tick, 1012-1021) requires attempts>=1, so a job that failed pre-flight (e.g. missing API key) is reachable only via manual rerun, never auto-retry — serialize_llm_job's own comment (68-73) states this explicitly.
- **Cancel-vs-in-flight-work races**: several kinds check job.status=="cancelled" mid-work and return early WITHOUT calling _finish — tagging (612), voice_note (648), voice_dump (709, mid-loop), classify_pipeline (567), voice_match (832 pre-work, 883 post-loop). Terminal write was already made by cancel_llm_job's own CAS (331-338); branch just avoids writing dependent side effects after the fact.
- **Worker batching**: llm_worker_tick claims a batch then `await asyncio.gather(*(run_llm_job(...) for jid in job_ids))` (1070); llm_worker_loop awaits the whole tick before sleeping (1077, interval_seconds=3.0 default). IO cap of 2 is really "claim up to 2, wait for the slower of the two, then look again" — new job can't be picked up mid-batch even if a slot frees early.

## Side effects beyond the LlmJob row
- Transcript.correction_error (192), Transcript.classification_status/confidence/provenance/kind (561-580), Transcript.segments/speaker_count/diarization_method (rediarize 760-763, voice_match 890-895), VoiceNote upsert (656-680), TranscriptTag delete-then-reinsert (616-620), RelabelHistory (clear_relabel_history 759 rediarize, record_relabel 887 voice_match). Voice-dump VoiceDumpItem rows explicitly NOT written here (693-695 comment) — separate finalization endpoint (#285).

## Cross-queue overlap with services/queue.py (chunked-transcription queue)
- **Shared retry policy, diverged write discipline**: llm_jobs.py:16 imports MAX_ATTEMPTS/_retry_eligible from services/queue.py. But resurrection write differs: llm_jobs.py:1022-1036 uses `_transition(db,job.id,"pending",expect=("failed",),...)` (CAS), while services/queue.py:818-822 does plain read-then-write `job.status="pending"` on TranscriptionJob — the exact race shape PR #389 fixed here was never ported to the sibling queue.
- Parallel startup reconciliation: reset_stuck_transcription_jobs/reset_stuck_llm_jobs, both called back-to-back at app.py:156-157.
- Two independent loops, different cadence: queue_worker_loop (queue.py:892, 5.0s) vs llm_worker_loop (llm_jobs.py:1073, 3.0s).
- Duplicated enqueue call site: auto-correction/classify/voice-note/voice-dump/tagging enqueue sequence appears twice — inline finalize (app.py:1450-1470) and chunked finalize (queue.py:688-709) — a comment at app.py:1469 explicitly flags "keep this site in lockstep."

## Observation (not fixed, investigation only): cross-user dedupe bug on assistant jobs
`get_active_job` (88-97) filters only on transcript_id+kind+status, no user_id. Harmless for transcript-scoped kinds (a transcript belongs to one user). But the assistant route enqueues with transcript_id=None (app.py:3398), and enqueue_llm_job (109-126) calls get_active_job(db, transcript_id, kind) with no user_id argument at all. So if any user has a pending/running assistant job, a DIFFERENT user's POST to /api/assistant will match that row and be handed back user A's in-flight job instead of creating their own. Confirmed by reading both call sites; not verified against a running system. Worth a bug report.

## Mermaid flowchart

```mermaid
flowchart TD
    subgraph ENTRY["Entry points"]
        inline["Inline finalize enqueue sequence<br/>app.py:1450-1470"]
        chunked["Chunked finalize enqueue sequence<br/>services/queue.py:688-709"]
        manual["Manual routes: summarize/format/correct/<br/>rediarize/voice-match/voice-note-rerun/<br/>voice-dump-rerun/assistant<br/>app.py:2645,2690,2777,2819,2848,3021,3049,3398"]
        rerunRoute["POST /api/jobs/{id}/rerun<br/>app.py:3340-3348"]
    end

    enqAuto["enqueue_auto_correction / _classify / _voice_note /<br/>_voice_dump / _tagging / enqueue_pipeline_classify<br/>services/llm_jobs.py:180-313"]
    enqDirect["enqueue_llm_job<br/>services/llm_jobs.py:109-126"]
    rerunFn["rerun_llm_job<br/>services/llm_jobs.py:341-347"]
    dedupe{"get_active_job:<br/>existing pending/running row<br/>for transcript_id+kind?<br/>services/llm_jobs.py:88-97"}
    preErr{"error= passed<br/>(e.g. no API key)?<br/>services/llm_jobs.py:119-123"}

    inline --> enqAuto
    chunked --> enqAuto
    manual --> enqDirect
    rerunRoute --> rerunFn
    rerunFn --> enqDirect
    enqAuto --> enqDirect
    enqDirect --> dedupe
    dedupe -->|"yes: return existing"| existingJob["LlmJob row (unchanged)<br/>services/llm_jobs.py:118"]
    dedupe -->|"no: insert row"| preErr
    preErr -->|"yes"| failedNoRetry["status=failed, attempts=0<br/>services/llm_jobs.py:122<br/>(never auto-retried, only rerunnable)"]
    preErr -->|"no"| pendingRow["status=pending<br/>services/llm_jobs.py:119-124"]

    lifespan["app.py lifespan startup<br/>app.py:153-161"] --> resetStuck["reset_stuck_llm_jobs<br/>services/llm_jobs.py:129-147<br/>(direct write, no CAS - pre-worker exemption)"]
    lifespan --> loopStart["asyncio.create_task(llm_worker_loop)<br/>app.py:161"]

    loopStart --> loop["llm_worker_loop (interval 3.0s)<br/>services/llm_jobs.py:1073-1080"]
    loop --> tick["llm_worker_tick<br/>services/llm_jobs.py:1009-1066"]

    tick --> sweep["Resurrect eligible failed jobs<br/>(_retry_eligible from services/queue.py)<br/>services/llm_jobs.py:1012-1036"]
    sweep --> sweepCas["_transition(pending, expect=(failed,))<br/>services/llm_jobs.py:1036"]
    sweepCas --> claimQuery["Query pending jobs per pool,<br/>respecting IO cap=2 / CPU cap=1<br/>services/llm_jobs.py:1040-1051"]
    claimQuery --> claimCas["_transition(running, expect=(pending,),<br/>attempts+=1) per row<br/>services/llm_jobs.py:1059-1063"]
    claimCas --> gather["await asyncio.gather over claimed jobs<br/>services/llm_jobs.py:1070<br/>(tick blocks until slowest job finishes)"]
    gather --> loop

    gather --> runJob["run_llm_job (own DB session)<br/>services/llm_jobs.py:414-951"]
    runJob --> keyCheck{"CPU_KINDS?<br/>services/llm_jobs.py:438"}
    keyCheck -->|"IO kind: resolve provider key"| keyGate{"key missing &<br/>not keyless provider?<br/>services/llm_jobs.py:440"}
    keyGate -->|"yes"| finFailKey["_finish(failed, 'no API key')<br/>services/llm_jobs.py:441"]
    keyGate -->|"no"| dispatch
    keyCheck -->|"CPU kind: skip key resolution"| dispatch

    dispatch{"dispatch by job.kind<br/>services/llm_jobs.py:444-941"}
    dispatch -->|"correction"| correction["correct_transcript<br/>services/correction.py (call at llm_jobs.py:457)"]
    dispatch -->|"summary"| summary["transcription_service.summarize<br/>services/transcription.py:169 (call at llm_jobs.py:499)"]
    dispatch -->|"format_*/classify_intent"| reformat["format_as_*/classify_intent<br/>services/reformatting.py (calls at llm_jobs.py:523,535)"]
    dispatch -->|"classify_pipeline"| classify["classify_pipeline_kind<br/>services/classification.py (call at llm_jobs.py:550)"]
    dispatch -->|"tagging"| tagging["generate_tags<br/>services/tagging.py (call at llm_jobs.py:604)"]
    dispatch -->|"voice_note"| voicenote["run_voice_note_chain<br/>services/voice_notes.py (call at llm_jobs.py:639)"]
    dispatch -->|"voice_dump"| voicedump["segment_voice_dump / _structure_from_text<br/>services/voice_notes.py (calls at llm_jobs.py:700,711)"]
    dispatch -->|"rediarize"| rediarize["diarization_service.diarize_and_merge<br/>(injected dep, call at llm_jobs.py:744)"]
    dispatch -->|"voice_match"| voicematch["voice_id_service.identify_detailed +<br/>extract_clips_concat<br/>services/voice_id.py, services/audio_prep.py<br/>(calls at llm_jobs.py:848,836)"]
    dispatch -->|"assistant"| assistant["run_assistant_job<br/>services/llm_jobs.py:954-1006"]

    assistant --> interpret["interpret_request / execute_plan<br/>services/assistant.py (calls at llm_jobs.py:975,995)"]

    correction --> finCorr["_finish(completed/failed)<br/>services/llm_jobs.py:464,482<br/>+ enqueue_pipeline_classify trigger<br/>services/llm_jobs.py:480,493"]
    summary --> finSumm["_finish(completed/failed)<br/>services/llm_jobs.py:511,513"]
    reformat --> finReformat["_finish(completed/failed)<br/>services/llm_jobs.py:529,531,541"]
    classify --> finClassify["_finish(completed) or early return on cancel<br/>services/llm_jobs.py:564,567-568,596"]
    tagging --> finTag["_finish(completed) or early return on cancel<br/>services/llm_jobs.py:612-613,623"]
    voicenote --> finVN["_finish(completed/failed) or early return on cancel<br/>services/llm_jobs.py:648-649,688,690"]
    voicedump --> finVD["_finish(completed/failed) or early return on cancel<br/>services/llm_jobs.py:709-710,729,731"]
    rediarize --> finRD["_finish(completed/failed) or early return on cancel<br/>services/llm_jobs.py:752-753,766,768"]
    voicematch --> finVM["_finish(completed, notes) or early return on cancel<br/>services/llm_jobs.py:832-833,883-884,937"]
    interpret --> finAsst["_finish(completed/failed)<br/>services/llm_jobs.py:980,983,986,1001,1006"]

    finCorr --> transition["_transition CAS primitive<br/>UPDATE ... WHERE id=? AND status IN (expect)<br/>services/llm_jobs.py:350-380"]
    finSumm --> transition
    finReformat --> transition
    finClassify --> transition
    finTag --> transition
    finVN --> transition
    finVD --> transition
    finRD --> transition
    finVM --> transition
    finAsst --> transition

    transition --> terminal["Terminal state:<br/>completed / failed / cancelled<br/>services/llm_jobs.py:20"]

    cancelRoute["POST /api/jobs/{id}/cancel<br/>app.py:3329-3337"] --> cancelFn["cancel_llm_job<br/>services/llm_jobs.py:316-338"]
    cancelFn -->|"_transition(cancelled, expect=ACTIVE_STATUSES)"| transition

    terminal -->|"failed + kind in AUTO_RETRY_KINDS<br/>+ attempts>=1"| sweep
    terminal -->|"rerun (failed/cancelled only)"| rerunFn
    terminal -->|"dismiss<br/>app.py:3351-3359 / llm_jobs.py:150-161"| dismissed["dismissed=True (row untouched)"]
    terminal -->|"clear all terminal, undismissed<br/>app.py:3366-3370 / llm_jobs.py:164-177"] --> dismissed
```

## External dependency map (job-kind -> service module)
correction -> services/correction.py; summary -> services/transcription.py; format_*/classify_intent -> services/reformatting.py; classify_pipeline -> services/classification.py (+services/settings.py threshold); tagging -> services/tagging.py (writes TranscriptTag); voice_note/voice_dump -> services/voice_notes.py (voice_note writes VoiceNote); rediarize -> diarization_service (injected) + services/relabel.py; voice_match -> services/voice_id.py, services/audio_prep.py, services/relabel.py; assistant -> services/assistant.py. All IO kinds -> services/settings.py for key resolution. All kinds -> database.LlmJob, shared MAX_ATTEMPTS/_retry_eligible from services/queue.py. None call services/llm_client.py directly.

## Confidence and gaps
All prompt-given line numbers verified against source. Individual job-type dispatch bodies (correction/reformatting/voice_notes/tagging/voice_id internals) deliberately not traced past call sites, per scope. services/queue.py read only at cited lines for the overlap note. Frontend Queue-screen behavior not examined. The "Unknown job kind" branch (941) is defensively unreachable given VALID_KINDS validation at enqueue (114-115). Cross-user assistant-job dedupe observation is static-analysis only, not live-reproduced.
