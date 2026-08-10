# Feature Inventory — WhisperDeck

Source: Phase 0 discovery subagent, full repo walk (app.py route grep, services/, backends/, database/, static/rack.js definition grep, tests/ + docs/ enumeration). See subagent report archived in session transcript for exact sources-consulted list.

Excluded from flowcharting as non-product infra: **test infrastructure** (tests/, tests_js/, tests/e2e/) and **build/release tooling** (scripts/, tools/ghc). These get referenced as dependencies where relevant but don't get their own flowchart.

Job/queue client-side polling UI folded into **Web UI (Signal Rack)** below (same feature, client half).

## 1. auth-accounts
- Entry: `app.py:499` POST /api/register, `app.py:529` POST /api/login, `app.py:550` GET /api/me, `app.py:836` POST /api/reset-password
- Core: `services/auth.py` (hash_password:34, verify_password:40, create_user:82, authenticate_user:97, generate_reset_token:127, reset_password:158, set_admin_status:179), `services/security.py` (CSRF gen/validate:19/40, RateLimiter:50, API-key encrypt/decrypt:93/106), `database/__init__.py` User:17, `app.py:864-928` admin+device-token routes
- Purpose: registration/login/session/CSRF, password reset, admin promote/demote, device-token auth, rate limiting.

## 2. transcription-pipeline
- Entry: `app.py:1482` POST /api/transcribe, `backends/__init__.py:40` get_provider
- Core: `backends/base.py` (BaseProvider:35, Segment:13, TranscriptionResult:24), `backends/{moonshine,builtin,groq,openai,replicate,openrouter,local,assemblyai}.py`, `services/transcription.py` (TranscriptionService:13), `services/audio_prep.py` (transcode_for_upload:44, chunk_audio:308, extract_clips_concat:230, has_video_stream:134)
- Purpose: pluggable STT backends behind one interface; ffmpeg transcode/chunk feeding inline transcription or the chunk queue.

## 3. chunked-queue
- Entry: `services/queue.py:250` create_chunk_jobs, `services/queue.py:892` queue_worker_loop (started `app.py:160`)
- Core: `services/queue.py` (compute_audio_seconds_used:28, has_budget:84, merge_chunk_results:212, cancel_transcript_jobs:325, resume_cancelled_chunks:356, _run_chunk_job:454, _finalize_if_done:554, queue_worker_tick:788), `database/__init__.py` TranscriptionJob:85, `app.py:2204-2241` retry/cancel/resume/retranscribe routes
- Purpose: rate-limit-aware chunk dispatch/reassembly/cancel/resume/retry; provider-tier budget tracking.

## 4. llm-job-queue
- Entry: `services/llm_jobs.py:1073` llm_worker_loop (started `app.py:161`), `services/llm_jobs.py:109` enqueue_llm_job
- Core: `services/llm_jobs.py` (enqueue_auto_correction:180, enqueue_auto_classify:197, enqueue_auto_voice_note:221, enqueue_auto_voice_dump:248, enqueue_auto_tagging:275, enqueue_pipeline_classify:294, _transition:350, run_llm_job:414, run_assistant_job:954), `database/__init__.py` LlmJob:104, `app.py:2621-2823` summarize/format/correct/rediarize/voice-match routes, `app.py:3322-3366` jobs list/cancel/rerun/dismiss/clear
- Purpose: generic background-job runner for every non-transcription async op; atomic status transitions (PR #389).
- **Watch for Phase 2**: this is a second, parallel queue implementation to #3 — prime duplication-hunt target.

## 5. correction-hotwords
- Entry: `app.py:2754` POST .../correct, `app.py:945-959` hotwords CRUD
- Core: `services/correction.py` (correct_transcript:78, extract_hotwords_from_doc:215, _batch_lines:58), `services/hotwords.py` (list_hotwords:9, add_hotword:13), `database/__init__.py` HotwordEntry:140, `app.py:3236` context/doc attach route
- Purpose: per-user glossary feeding LLM correction pass; context-document term extraction.

## 6. diarization
- Entry: `app.py:2781` POST .../rediarize, `app.py:2579` POST /api/diarize
- Core: `services/diarization.py` (DiarizationService:25, DiarizationSegment:11, DiarizationResult:19 — heuristic + pyannote modes)
- Purpose: speaker segmentation, heuristic pause-gap fallback vs pyannote.audio ML mode.

## 7. voice-id-match
- Entry: `app.py:3444` POST /api/voices/enroll, `app.py:3489` POST /api/voices/identify, `app.py:2823` POST .../voice-match
- Core: `services/voice_id.py` (VoiceIdentificationService:44, backend auto-detect speechbrain→pyannote→librosa MFCC ~31-41), `database/__init__.py` VoiceProfile:243, VoiceClip:258, `services/relabel.py` (record_relabel:46, count_distinct_speakers:20, latest_relabel:89)
- Purpose: enroll speaker voice clips, identify/relabel speakers by embedding similarity.
- **Flag**: per-speaker match similarity (PR #311) reportedly computed but discarded before relabel UI uses it — verify in flowchart trace.

## 8. run-history-versions
- Entry: `app.py:2852` GET .../runs/{kind}, `app.py:3203` GET .../versions, `app.py:2455` POST .../relabel-undo
- Core: `services/relabel.py` (record_relabel:46, latest_relabel:89, clear_relabel_history:101), `database/__init__.py` RelabelHistory:126, `database/__init__.py:406` backfill_llm_job_result_snapshots
- Purpose: per-transcript audit trail of correction/summary/rediarize/voice-match runs, word-level diffing, version chains.

## 9. search
- Entry: `app.py:1852` GET /api/search
- Core: `services/search.py` (search_transcripts:74, search_transcripts_snippets:146, _sanitize_fts5_query:26), `database/__init__.py:492` populate_fts, `database/__init__.py:558` cleanup_fts_orphans
- Purpose: SQLite FTS5 full-text search with snippet extraction.

## 10. classification-tagging
- Entry: `services/classification.py:46` classify_pipeline_kind, `services/tagging.py:102` generate_tags
- Core: `services/classification.py` (effective_kind:23), `database/__init__.py` TranscriptTag:220, `database/__init__.py:448` classification_columns_were_absent, `database/__init__.py:463` backfill_legacy_classification
- Purpose: auto-classify transcript "kind", auto-tag content; legacy migration path.

## 11. voice-notes-dump
- Entry: `app.py:2921` GET .../voice-note, `app.py:3080` POST .../voice-dump/finalize, `app.py:2945` GET /api/voice-notes
- Core: `services/voice_notes.py` (classify_voice_note:140, structure_voice_note:217, segment_voice_dump:229, run_voice_note_chain:285), `database/__init__.py` VoiceNote:166, VoiceDumpItem:193, `static/dump_review.js`
- Purpose: single-speaker quick-capture notes structured via LLM chain (classify → structure → segment).

## 12. reformatting-export-assistant
- Entry: `app.py:2658` POST .../format/{target}, `app.py:2694` POST .../export-markdown, `app.py:3375` POST /api/assistant
- Core: `services/reformatting.py` (format_as_markdown:40, format_as_email:54, format_as_coding_prompt:72, classify_intent:88, build_export_markdown:116), `services/assistant.py` (interpret_request:54, execute_plan:89, _resolve_export_path/_sanitize_filename:36-46), `services/llm_client.py` (chat_completion:69, resolve_model:44)
- Purpose: transform transcript to other formats; NL "assistant" that interprets/executes requests over app data.
- **Flag**: assistant.py has path-sanitization helpers — security-relevant, worth closer look given LLM-driven execute_plan.

## 13. providers-settings-cost
- Entry: `app.py:982-1059` providers list/get/put/models, `app.py:3699` GET /api/costs, `app.py:3755` POST /api/costs/estimate
- Core: `services/settings.py` (resolve_provider_key:90, get_user_settings:117, update_user_settings:126), `services/model_catalog.py` (get_correction_models:128, _openrouter_live_models:55), `services/pricing.py` (get_stt_rate:24), `services/cost.py` (transcript_cost:14, provider_cost:110, estimate_cost:147), `database/__init__.py` ProviderConfig:273
- Purpose: per-user encrypted API-key storage, curated LLM model lists w/ live pricing, cost accounting/estimation.

## 14. batch-bulk-files
- Entry: `app.py:1546` POST /api/bulk-transcribe, `app.py:1695` GET /api/batches, `app.py:2033` GET /api/files
- Core: `static/batch_aggregate.js`, `app.py:1546-2153` (routes — no dedicated service module found, gap to confirm)
- Purpose: multi-file upload/batch transcription tracking, file-management view. Not in README's API table.

## 15. web-ui-signal-rack
- Entry: `static/index.html`, `app.py:3613` GET /, `static/rack.js:425` PAGES array
- Core: `static/rack.js` (6574 lines: state S:6, theme system 67-136, dashboard instruments 1296-1465, transcribe deck/MFD 1699-2034, bulk 2810-3099, detail/diff/export 5009-5767+, assistant 481-725, job poll: startBackgroundJobPoll:932, jobStatusView:3380, jobActions:3395, updateQueueBadge:3610, scheduleDetailPoll:3793, _jobFingerprint:3784), `static/rack.css` (874 lines), `static/sw.js` (service worker intercepting /api/*)
- Purpose: single hand-rolled vanilla-JS SPA ("Signal Rack" hardware tape-deck aesthetic), 13 pages, no framework; includes client-side job/queue polling.

## 16. storage-db-layer
- Entry: `database/__init__.py:643` init_db
- Core: `database/__init__.py` (13 ORM models lines 17-286, migrate_schema:288, ensure_columns:337, populate_fts:492)
- Purpose: SQLAlchemy models + hand-rolled forward migrations at startup (no Alembic), FTS5 index population/cleanup.

## Known gaps (carried from Phase 0)
- Route *bodies* in app.py mostly unread — service-call wiring inferred from imports/naming, not verified line-by-line.
- rack.js definition grep capped at 150 matches; ~lines 5767-6574 unseen. Flowchart subagent for web-ui-signal-rack should re-grep past that cutoff.
- batch-bulk-files may have inline logic in app.py 1546-2153 rather than a service module — flowchart subagent should confirm and report actual structure.
- docs/superpowers/{plans,specs} and docs/plans/01-13 not read — provenance only, not required for current-state flowcharts.
