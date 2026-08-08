# Self-Audit — Issue #169 (minimax-m3-r2)

Re-verify every concrete promise from `investigation.md` and the issue's
implicit acceptance criteria. Each `[x]` was confirmed by reading the
file/line and grepping for the change.

## Promises from investigation.md

### Backend — `services/llm_jobs.py`
- [x] `VALID_KINDS` includes `"voice_note"` — confirmed at line 23
- [x] `AUTO_RETRY_KINDS` includes `"voice_note"` — confirmed at line 35
- [x] `IO_KINDS` includes `"voice_note"` (and `CPU_KINDS` does not) — confirmed at line 42
- [x] `enqueue_auto_voice_note(db, transcript, user_settings)` helper defined at line 195
- [x] New `voice_note` dispatch branch in `run_llm_job` at line 349 with two awaits, progress updates, cancel-race check, and VoiceNote row write

### Backend — `services/voice_notes.py` (new file)
- [x] `classify_voice_note(...)` — JSON-mode LLM call at line 80-114
- [x] `structure_voice_note(...)` — per-type JSON-mode LLM call at line 117-148
- [x] `run_voice_note_chain(...)` — orchestrates the two calls at line 154-176

### Backend — `database/__init__.py`
- [x] New `VoiceNote` model with `id`, `user_id`, `transcript_id` (unique), `note_type`, `title`, `body`, `structured` (JSON), `model`, `provider`, `created_at` — confirmed at line 155
- [x] `voice_note` relationship on `Transcript` (uselist=False, cascade delete-orphan) — confirmed at line 64
- [x] `back_populates="transcript"` on the VoiceNote side — confirmed at line 179
- [x] Column comment on `Transcript.kind` updated to mention `voice_note` — confirmed at line 38

### Backend — `app.py`
- [x] `/api/transcribe` allowlist includes `voice_note` — confirmed at line 1206
- [x] PATCH allowlist includes `voice_note` — confirmed at line 1501
- [x] `_run_transcription_pipeline` line 954: `if kind in ("dictation", "voice_note"): diarize = False`
- [x] Post-pipeline enqueue (line 1171-1176): `kind != "voice_note"` branch skips correction+classify, `else` branch enqueues voice_note
- [x] `/format/{target}` (line 1962): rejects voice_note with per-kind message
- [x] `/rediarize` (line 2018-2019): rejects voice_note
- [x] `/voice-match` (line 2051-2052): rejects voice_note
- [x] `/summarize` (line 1919): rejects voice_note
- [x] New `GET /api/transcripts/{id}/voice-note` at line 2112
- [x] New `GET /api/voice-notes` (listing) at line 2132
- [x] New `DELETE /api/voice-notes/{id}` at line 2156
- [x] `_serialize_transcript` includes `voice_note_job` for `kind=="voice_note"` (line 376-381) and null for other kinds (line 374, 386)
- [x] `_SERIALIZED_JOB_KINDS` includes `"voice_note"` at line 270
- [x] `/api/transcripts/{id}/runs/{kind}` allowlist includes `"voice_note"` at line 2072
- [x] New `POST /api/transcripts/{id}/voice-note/rerun` at line 2167
- [x] `VoiceNote` count in `_build_status_payload` for the rail badge (line 495, 508)

### Backend — `services/transcription.py`
- [x] `summarize()` voice_note branch at line 187-217 (returns stub Summary, no LLM call)

### Backend — `services/queue.py`
- [x] Chunked-finalize path enqueues `voice_note` for `kind=="voice_note"` (line 565-573)

### Frontend — `static/rack.js`
- [x] `S.mode` 3-way cycle in `ctl-mode` click handler at line 1469
- [x] `modeLabel` / `singleSpeaker` 3-way logic in `syncTranscribe` at line 1654-1658
- [x] `vfd-mode` shows `MEETING` / `DICTATION` / `VOICE NOTE` at line 1657
- [x] `tog-mode.on` paddle reflects single-speaker state at line 1658
- [x] `ctl-diarize` locked when `singleSpeaker` (line 1659)
- [x] `detailTabsHtml()` pushes `'notes'` for `kind === 'voice_note'` at line 2528
- [x] Sticky `S.detailTab` guard for `'notes'` at line 2492
- [x] Detail header "Mode" button title updated at line 3507 ("Switch between meeting, dictation, and voice-note modes")
- [x] `data-dact="toggle-kind"` handler cycles 3-way (line 3620-3631) with try/catch on 400 (processing guard)
- [x] `voiceNoteHtml(t)` function at line 3280 — renders in-flight / failed / completed states with per-type structured fields
- [x] `loadVoiceNotes()` function at line 2074
- [x] `discardVoiceNote(id)` helper at line 2155
- [x] `'voicenotes'` added to `PAGES` array at line 404
- [x] `voicenotes` loader wired in `navigate()` at line 438
- [x] Rail badge `nav-badge-voicenotes` updated in both `refreshRailChrome` and the dashboard at line 417, 887
- [x] `KIND_LABELS.voice_note = 'VOICE NOTE'` for the Queue screen at line 2348
- [x] `voice_note` actions: `rerun-voice-note` and `delete-voice-note` wired in `detailAction` at line 3630, 3645

### Frontend — `static/index.html`
- [x] Rail button `<button class="rail-btn" data-nav="voicenotes">` at line 71
- [x] Page div `<div class="page" id="page-voicenotes">` at line 107

### Frontend — `static/rack.css`
- [x] `.voice-note-grid` (auto-fill responsive) at line 753
- [x] `.voice-note-card` hover lift at line 758-763

## Issue's implicit acceptance criteria (from body)

- [x] "quick-capture voice-note mode, distinct from the existing meeting/dictation transcript flow" — new `kind="voice_note"` is a third value in the upload allowlist
- [x] "record, transcribe (single-speaker, diarization forced off)" — server-side force at app.py:954 (`if kind in ("dictation", "voice_note"): diarize = False`)
- [x] "run the result through a chain of LLM calls that figures out what kind of note it is" — `classify_voice_note` → `structure_voice_note` chain in `services/voice_notes.py`, auto-triggered after transcribe (app.py:1176 inline + services/queue.py:573 chunked)
- [x] "produces a structured write-up for the user, not just plain reformatted text" — type-specific JSON output in `VoiceNote.structured`, per-type rendering in `voiceNoteHtml`
- [x] "a place for these notes to live" — `VoiceNote` table (database/__init__.py:155) with `transcript_id` UNIQUE constraint (one per transcript, in-place update)
- [x] "somewhere to store and display the structured output" — `VoiceNote.content_json` / `structured` JSON column, Voice Notes board page (static/rack.js:2074), Notes tab on detail (static/rack.js:2528)
- [x] "a UI surface for it" — dedicated Voice notes page with rail button (static/index.html:71), card grid with type badges, open-to-detail navigation
- [x] "Reuse the existing LlmJob queue infrastructure" — new `kind="voice_note"` in `VALID_KINDS`/`IO_KINDS`/`AUTO_RETRY_KINDS` (services/llm_jobs.py:23,35,42), dispatched in existing `run_llm_job` (line 349), auto-retried by existing worker loop
- [x] "These recordings are also naturally clean single-speaker audio, which could be useful later as enrollment material" — explicitly deferred (issue says "worth keeping in mind, not required for this issue"). The voice roster flow is untouched.

## Test coverage

- [x] `tests/test_voice_note_chain.py` — 22 tests covering classify/structure/chain happy + fallback paths, IO/CPU partition, AUTO_RETRY_KINDS, run_llm_job dispatch with row write + in-place update + API error fallback
- [x] `tests/test_voice_note_route.py` — 17 tests covering upload with voice_note, /voice-note endpoint (null + completed), /voice-notes listing, DELETE, 404 across users, /format /rediarize /voice-match /summarize rejection, /voice-note/rerun, /runs/{kind} allowlist
- [x] `tests/test_serialize_transcript_contract.py` — updated for `voice_note_job` key (was missing; new key tested across all three kinds via `test_all_kinds_have_same_job_field_names`)
- [x] `tests/test_transcript_kind_patch.py` — new `test_patch_kind_accepts_voice_note` covers the PATCH allowlist
- [x] `tests/test_bootstrap.py` — updated for the new `voice_notes` key in the status payload
- [x] Full suite: **432 passed, 1 skipped, 0 failed** in 41.55s

## What was NOT delivered (intentional, documented)

- Voice-roster enrollment integration — the issue explicitly says "worth keeping in mind, not required for this issue."
- Dedicated quick-capture button on the voice-notes board — the user records via the existing Transcribe page with the mode toggle set to VOICE NOTE; the board page points there in the empty state.
- e2e regression test — no Playwright MCP browser tool was available in this session. Static source-level check (432 tests) instead, per the test-tier fallback in AGENTS.md.
- `voice_note` and `format_*/classify_intent` are mutually exclusive on the detail page (a transcript is one kind), but a user wanting to also extract a markdown summary from a voice note's body has no route for that — out of scope for this issue.
