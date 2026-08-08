# Self-Audit — Issue #169 (deepseek-pro-r2)

## Promises from investigation.md

[x] Single-speaker capture (diarization off) — confirmed at app.py:941 (`if kind in ("dictation", "voicenote"): diarize = False`)
[x] LLM chain classifies note type (todo, idea, reminder, journal) — confirmed at services/llm_jobs.py:443-467 (classify_voicenote dispatch, auto-enqueues structure_voicenote on non-"none")
[x] LLM produces structured output per note type — confirmed at services/voicenotes.py:82-89 (per-type prompts via _todo_prompt, _idea_prompt, _reminder_prompt, _journal_prompt)
[x] UI surface for browsing voice notes — confirmed at static/rack.js:4180-4248 (loadVoiceNotes function), static/index.html:80 (rail button), static/index.html:111 (page div)
[x] Reuses LlmJob queue infrastructure (not new async mechanism) — confirmed at services/llm_jobs.py:23,35,42 (new kinds in existing VALID_KINDS/IO_KINDS/AUTO_RETRY_KINDS), services/llm_jobs.py:443-497 (dispatch in existing run_llm_job)
[x] VoiceNote model with proper relationships — confirmed at database/__init__.py:114-135 (VoiceNote class with user_id FK, transcript_id FK with ondelete SET NULL)
[x] Transcribe endpoint accepts kind=voicenote — confirmed at app.py:1181 (kind validation updated)
[x] Audio chain trigger after transcribe — confirmed at app.py:1174 (inline path), services/queue.py:572-573 (chunked path)
[x] Full test suite passes — confirmed: 388 passed, 0 failed, 35.01s
[x] Frontend kind gates updated — confirmed: 5 places in rack.js (syncTranscribe, startJob, renderDetail, renderDetailBody, detailAction toggle-kind)
[x] No Format tab for voicenote — confirmed: detailTabsHtml only shows format for dictation, unchanged
[x] Contract test updated — confirmed at tests/test_serialize_transcript_contract.py (new fields in EXPECTED_KEYS and null checks)

## Issue's implicit acceptance criteria (from body)

[x] "quick-capture voice-note mode, distinct from the existing meeting/dictation transcript flow" — new kind="voicenote" in transcribe mode selector, distinct from meeting/dictation
[x] "record, transcribe (single-speaker, diarization forced off)" — diarization server-side forced off for voicenote, same as dictation
[x] "run the result through a chain of LLM calls that figures out what kind of note it is" — classify_voicenote → structure_voicenote chain, auto-triggered after transcribe
[x] "produces a structured write-up for the user, not just plain reformatted text" — type-specific JSON output stored in content_json, rendered in card UI
[x] "a place for these notes to live" — VoiceNote model with own table
[x] "somewhere to store and display the structured output" — VoiceNote.content_json (JSON column), Voice Notes page with card grid
[x] "a UI surface for it" — dedicated Voice notes page with rail button, card grid, expand/detail view, delete
[x] "Reuse the existing LlmJob queue infrastructure" — new kinds added to existing VALID_KINDS/IO_KINDS/AUTO_RETRY_KINDS, dispatched in existing run_llm_job, auto-retried by existing worker loop

## Not delivered (intentional)

[ ] "audio could be useful later as enrollment material for voice roster" — issue says "worth keeping in mind, not required for this issue." Deferred.
[ ] Dedicated quick-capture button on voice-notes page — user must use Transcribe page with kind="Voice note" for now. Voice-notes page has link text guiding there.
[ ] e2e regression test (e2e-regression-http) — no Playwright MCP browser tool available. Static check + full 388-test suite passes instead.
