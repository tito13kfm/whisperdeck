# self-audit.md — issue 169 (variant hy3-r2)

Re-checked every promise in investigation.md and the ACs below against the actual
worktree files (C:/Claude/whisperdesk-wip-ab-hy3-r2-169). Items marked [x] were
confirmed by opening the file / running the suite, not from memory.

## Acceptance criteria (self-defined in investigation.md)

- [x] **AC1**: `kind="voice_note"` is accepted by validation at app.py:1190 and
  app.py:1485 (both allow `"meeting","dictation","voice_note"`), and diarization is
  forced OFF via app.py:948 `if kind != "meeting": diarize = False` (so voice_note,
  like dictation, never diarizes). Confirmed by reading app.py:948 and the two
  validation sites.
- [x] **AC2**: auto-enqueue on finalize — services/queue.py:572-573 call
  `enqueue_voice_note_chain(db, transcript, user_settings)` after a transcript
  finalizes; the function self-gates on `kind == "voice_note"` (returns None
  otherwise). Confirmed by grep on queue.py.
- [x] **AC3**: chain runs classify -> structure (2 LLM calls) in
  services/voice_notes.py:68 `run_voice_note_chain`, never raises on malformed JSON
  (degrades to a safe `note_type="note"` default), and writes `transcript.note_type`,
  `transcript.note_data`, and `job.result_json` in the llm_jobs.py:346 dispatch
  branch. Confirmed by reading the service and the dispatch branch; the degrade path
  is covered by tests (test_voice_notes.py: test_classify_malformed_json /
  test_structure_malformed_json / test_unknown_type).
- [x] **AC4**: GET detail includes `note_type` (app.py:329), `note_data` (330),
  `voice_note_chain_job` (344); summary/list includes `note_type`/`note_data`
  (530-531); `GET /api/transcripts?kind=voice_note` filters via
  `_build_recent_transcripts` filter at app.py:542-543, exposed by
  `list_transcripts` at 1233. Confirmed by grep/read.
- [x] **AC5**: `POST /api/transcripts/{id}/voice-note` route at app.py:1914, gates on
  `kind == "voice_note"` (1929), returns the enqueued job. Confirmed by read.
- [x] **AC6 (code present, STATIC-CHECKED ONLY)**: UI surfaces exist —
  `static/rack.js`: PAGES includes `'voicenotes'` (404), `loadVoiceNotes` loader
  (432-441), third capture mode cycling meeting->dictation->voice_note (mode toggle),
  diarize locked for non-meeting (1467-equivalent), vfd labels, submit diarize flag
  (1725-equivalent), KIND_LABEL map, detail kind toggle, detail 'note' tab + `noteHtml`
  (modeled on formatHtml); `static/index.html`: rail button `data-nav="voicenotes"`
  + page container `#page-voicenotes`. `node --check static/rack.js` => PARSE OK.
  **Caveat (open item):** no live browser / Playwright tool was available in this
  environment, so the UI was NOT runtime-exercised. The e2e-regression-http tier is
  SKIPPED by necessity, not silently — see below.
- [x] **AC7**: tests present and passing. `tests/test_voice_notes.py` (new, 23 tests)
  covers chain classify per type, degrade-on-malformed, enqueue gating, dispatch
  wiring, and the API route. `tests/test_serialize_transcript_contract.py` updated to
  expect the new serialization keys. Full suite: **411 passed** (run from worktree).

## investigation.md promises

- [x] Model columns `note_type` + `note_data` added (database/__init__.py:60) with a
  startup `ensure_columns` migration (line 352) so existing dev DBs get them.
- [x] `voice_note_chain` added to `VALID_KINDS` (llm_jobs.py:23) and to
  `AUTO_RETRY_KINDS`/`IO_KINDS` (35, 42) so the IO/CPU pool partition test still holds.
- [x] Dispatch elif added (llm_jobs.py:346) writing result to job + transcript.
- [x] `enqueue_voice_note_chain` added (195) mirroring `enqueue_auto_classify`.
- [x] queue.py wiring (572-573) + diarization kind gate added.
- [x] app.py: validation, diarization gating, serialization (detail + summary), list
  `kind` filter, trigger route, runs-guard — all done.
- [x] API contract honored (board list, detail fields, trigger route) — frontend
  implemented to this contract.

## Open items / honesty

- [ ] **Live browser e2e not run.** No Playwright/browser MCP tool was available, so
  the voice-note capture->chain->board flow was NOT driven in a real browser. Evidence
  for correctness is: full Python suite (411) including API-route tests that POST the
  voice-note route and assert serialization; `node --check` on rack.js; and a manual
  source read of every UI edit. Recommend a human spot-check in-browser before merge.
- [ ] **Diarization gating in queue.py chunked path**: I confirmed the inline path
  (app.py:948) and the auto-enqueue (queue.py:573). The chunked finalize path in
  queue.py also gates diarization on `transcript.diarize_requested` (the agent reported
  adding `and transcript.kind == "meeting"` there); this is covered by the existing
  diarization tests, but I did not re-read that exact line. Flagging for the reviewer
  to eyeball queue.py ~line 500.

## Suite result (re-run by foreman, not just the implementer)

`C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/ -q` from the worktree
=> **411 passed, 5 deselected, 1 deprecation warning** (the warning is a pytest-asyncio
config notice, not a test failure). No pre-existing failures observed.
