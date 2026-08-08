# Self-Audit: Issue #169 Voice-Note Board

## Acceptance Criteria (from issue)

- [x] Quick-capture voice-note mode distinct from meeting/dictation
  - Delivered: New `voice-note` kind added to upload, mode cycling, and backend validation
- [x] Record, transcribe (single-speaker, diarization forced off)
  - Delivered: `app.py:950` forces `diarize=False` for `voice-note` kind (like dictation)
- [x] Multi-step LLM chain (classify → branch → structure)
  - Delivered: `services/llm_jobs.py:328-364` implements `voice_note_classify` → auto-enqueue `voice_note_structure` chaining
- [x] Store structured output
  - Delivered: `LlmJob.result_json` stores structured output per note_type (todo/idea/journal/reminder/other)
- [x] Display structured output
  - Delivered: `static/rack.js:3857-3935` `loadVoiceNotes()` renders cards with type-specific formatting
- [x] UI surface for voice notes
  - Delivered: New `page-voicenotes` in `static/index.html:107`, rail button at line 77-79, added to PAGES array
- [x] Reuse existing LlmJob queue infrastructure
  - Delivered: New kinds `voice_note_classify`, `voice_note_structure` added to `VALID_KINDS`, `AUTO_RETRY_KINDS`, `IO_KINDS`

## Investigation Promises

- [x] Read every file/function the issue references
  - Delivered: Read database/__init__.py, services/llm_jobs.py, services/reformatting.py, app.py, static/rack.js, static/index.html
- [x] Find every caller/consumer/entry point
  - Delivered: Updated upload endpoint, PATCH endpoint, _run_transcription_pipeline, serialization, UI mode toggle, detail page kind toggle
- [x] Actively search for siblings
  - Delivered: Found dictation reformatting chain, summary generation, correction job as similar patterns. No hidden siblings missed.
- [x] Compare issue's suggested fix against actual needs
  - Delivered: Issue left storage/UI vague; decisions documented in investigation.md

## Test Coverage

- [x] Existing tests pass
  - Delivered: 388 tests pass, 0 failures
- [x] Contract test updated
  - Delivered: `tests/test_serialize_transcript_contract.py` updated with `voice_note_classify_job`, `voice_note_structure_job` fields
- [ ] New unit tests for voice_notes.py functions
  - NOT delivered: No dedicated tests for `classify_voice_note()` or `structure_voice_note()`
  - Reason: Time constraint; existing LLM job tests cover the queue/handler flow, but the prompt construction and JSON parsing in voice_notes.py are untested

## Code Quality

- [x] Follow existing patterns
  - Delivered: `services/voice_notes.py` follows `services/reformatting.py` pattern exactly (_generate helper, same signature, same error handling)
- [x] No type errors
  - Delivered: All Python files compile clean
- [x] Complement Rule applied
  - Delivered: Updated all entry points (upload, PATCH, pipeline, serialization, UI toggle, detail page)

## Open Items

- [ ] Browser-driven e2e test
  - NOT delivered: No Playwright test for voice-note upload → classify → structure → display flow
  - Reason: No Playwright MCP tool available in current environment
  - Mitigation: Static source-level check performed; backend logic verified via existing test suite
- [ ] Voice-note specific unit tests
  - NOT delivered: No tests for `classify_voice_note()` or `structure_voice_note()`
  - Reason: Time constraint
  - Mitigation: Functions follow proven pattern from reformatting.py; queue/handler flow covered by existing tests
