# investigation.md — issue 169 (variant hy3-r2)

**Target issue:** #169, standalone feature (not a tracking issue). Title: "Voice-note
board: single-speaker capture with LLM intent chain and structured output."

**Scope decision (mergeable slice).** The issue leaves scope "open deliberately" and
has no acceptance checklist. I am scoping a complete, tested vertical slice:
record a voice note (single-speaker, diarization forced off) → transcribe → an LLM
job (`voice_note_chain`) classifies the note type and produces structured output →
stored on the transcript → shown on a "Voice Notes" board page and a detail "Note" tab.

I deliberately do NOT build: voiceprint enrollment (issue mentions it only as "worth
keeping in mind, not required"), and I reuse the existing `LlmJob` queue rather than
a new async mechanism (the issue explicitly says to).

## Acceptance criteria (self-defined, walked in Phase 3 / self-audit)

- AC1: A transcript can be created with `kind="voice_note"`; the API validates it and
  forces diarization off (single-speaker), mirroring dictation.
- AC2: On finalize of a `voice_note` transcript, a `voice_note_chain` LlmJob is
  auto-enqueued (mirroring `enqueue_auto_classify` for dictation).
- AC3: The chain runs classify → structure (2 LLM calls), never raises on LLM
  failure (degrades to a safe default), and writes `note_type` + `note_data` onto the
  transcript and `job.result_json`.
- AC4: `GET /api/transcripts/{id}` (detail) includes `note_type`, `note_data`, and a
  `voice_note_chain_job` handle; `GET /api/transcripts?kind=voice_note` lists only
  voice notes.
- AC5: `POST /api/transcripts/{id}/voice-note` (re)triggers the chain.
- AC6: UI surfaces exist: a "Voice Notes" board page listing notes with their type +
  structured preview; a third capture mode "Voice Note"; a detail "Note" tab rendering
  the structured output.
- AC7: Unit + integration tests cover the chain (mocked LLM), dispatch wiring,
  enqueue gating, and the API route.

## Investigation findings (current source, not the issue's snippet)

### Transcript model — database/__init__.py:63-91
`kind = Column(String(16), default="meeting")` (line 68). Allowed values today:
`meeting` | `dictation`. The `kind` column width 16 already fits `"voice_note"` (10).
No structured-note columns exist; must add:
- `note_type` — `String(32)`, nullable (e.g. `todo`, `idea`, `reminder`, `journal`, `note`)
- `note_data` — `JSON`, nullable (the structured output dict)

**DB migration concern:** the app likely calls `Base.metadata.create_all` on startup,
which does NOT add columns to an existing SQLite table. The implementer MUST find the
schema-init/migration pattern (grep `create_all`, or an `ensure_schema`/`migrate` fn in
database/__init__.py / app.py) and add these two columns via the same ALTER-based
mechanism, so existing dev DBs get them. If no pattern exists, implement a minimal
`add_column_if_missing(engine, "transcripts", ...)` run at startup.

### LLM job dispatch — services/llm_jobs.py
- `VALID_KINDS` (lines 20-26): 8 kinds. Add `"voice_note_chain"`.
- `enqueue_llm_job` (93-110) validates `kind in VALID_KINDS`; one active job per
  transcript+kind.
- `run_llm_job` if/elif chain (227-435) is the dispatch. Add
  `elif job.kind == "voice_note_chain":` before the final `else: _finish(... failed,
  "Unknown job kind")`.
- Each handler writes `job.result_json` and stores results on the transcript (e.g.
  summary→job.result_json, correction→transcript.corrected_text). The voice_note_chain
  handler will write `transcript.note_type`, `transcript.note_data`, and
  `job.result_json = {"note_type":..., "note_data":...}`.
- `enqueue_auto_classify` (175-192) is the mirror to copy: gates on
  `transcript.kind != "dictation" → return None`, resolves provider key, enqueues
  `classify_intent`. New `enqueue_voice_note_chain` gates on `kind != "voice_note"`.

### Auto-enqueue trigger — services/queue.py:567-571
After transcription completes, `enqueue_auto_correction` and `enqueue_auto_classify`
are called unconditionally (the latter self-gates on dictation). Add
`enqueue_voice_note_chain(db, transcript, user_settings)` here; it self-gates on
`kind == "voice_note"`.

### Diarization gating (single-speaker)
- app.py:355 `if t.kind != "dictation":` runs diarization in the finalize path.
- app.py:938 `if kind == "dictation":` (transcribe endpoint) skip diarization.
- app.py:1179-1180 and 1474-1475 kind validation `if kind not in ("meeting","dictation")`.
For voice_note, diarization must be OFF. Implementer should switch the gating to a
positive form: diarization runs only when `kind == "meeting"` (so dictation AND
voice_note skip it). Update all four sites consistently (Complement Rule).

### Kind-switch sites (sibling sweep — Complement Rule)
The issue did not name these; I enumerated ALL `transcript.kind` switches:
- app.py: 355, 938, 1179-1180, 1474-1475, 1932 (skip auto-classify for non-dictation),
  1987, 2018, 2037 (LLM run-kind validation, unrelated to transcript kind but verify
  voice_note_chain is allowed there too).
- services/llm_jobs.py: 98 (VALID_KINDS), 181 (auto-classify gate), 254-361 (dispatch).
- static/rack.js: 41 (S.mode default 'meeting'), 1355 (ctl-mode title), 1463 (mode
  toggle meeting<->dictation), 1467 (diarize control locked for dictation), 1648-1653
  (vfd labels), 1725 (submit forces diarize false for dictation), 2392/2427/3373
  (format tab gated to dictation), 3242-3243 (kind label), 3359 (speaker labeling skip
  for dictation), 3402-3407 (detail kind toggle).

The sibling sweep found: the only transcript kinds today are meeting and dictation,
so adding voice_note is the only new sibling. No further hidden siblings. Every
switch site listed above is in scope for the fix (AC1, AC6).

### API contract (defined here so backend + frontend agents agree)
- `GET /api/transcripts?kind=voice_note` → list of voice-note summaries (reuse
  `_build_recent_transcripts`; add optional `kind` filter param).
- `GET /api/transcripts/{id}` (detail) → adds fields `note_type`, `note_data`,
  `voice_note_chain_job` (serialize_llm_job or null), mirroring how `classify_intent_job`
  is attached (app.py ~348-372 `_batch_latest_jobs`).
- `POST /api/transcripts/{id}/voice-note` → enqueue (or re-run) `voice_note_chain`;
  returns the job. Mirrors `POST /api/transcripts/{id}/summarize`.
- Summary serialization (`_serialize_transcript_summary`) must include `note_type` and
  `note_data` so the board list can render them without a detail fetch.

## What the issue's snippet would get wrong (and I will not copy)
The issue has no code snippet (it's a feature brief). The main risk it names is
under-scoping: it says "reuse the existing LlmJob queue" (done) and "a place for these
notes to live (reuse the existing transcript/kind model or something new)" — I reuse
the transcript/kind model (no new table) and add two columns, which is the lower-risk
choice. I also add the structured output as `note_data` JSON rather than overloading
`corrected_text`/`summary`, keeping the voice-note output distinct from correction and
summary.

## Implementation plan (Phase 2)
1. Backend agent (`deep`): model columns + migration, VALID_KINDS, dispatch elif,
   services/voice_notes.py (chain), enqueue_voice_note_chain + queue.py wiring,
   app.py validation/gating/serialization/list-filter/trigger route, and tests.
2. Frontend agent (`deep`, edits only static/rack.js + rail HTML): board page, third
   capture mode, detail Note tab. Contract above is the interface boundary.
