# investigation.md — issue 169: Voice-note board

Target issue: **#169 (standalone)** — "Voice-note board: single-speaker capture with
LLM intent chain and structured output". Not a tracking issue, so no delegation
resolution needed.

## Scope decision (what this run builds)

The issue left scope open. I implement the "at minimum" list as a complete,
mergeable vertical slice:

1. **A place for notes to live** — reuse the `Transcript` model with a new
   `kind="voice_note"`. No new top-level entity; the structured note gets its
   own table (see below). voice_note behaves like `dictation` (single-speaker,
   diarization forced OFF server-side).
2. **Multi-step LLM chain (classify → branch → structure)** — new
   `services/voice_notes.py`: `classify_note_type()` then `structure_note()`,
   orchestrated by `run_voice_note_chain()`, persisted into a `VoiceNote` row.
3. **Storage + display of structured output** — new `VoiceNote` DB table
   (one-to-one with Transcript, cascade delete). Surfaced two ways:
   (a) a new "Voice Notes" board page (filtered list), and
   (b) a "Note" tab in the existing transcript Detail view.
4. **UI surface** — both the board page and the detail Note tab. The Transcribe
   page gets a "Voice note" kind option so users can actually create them.
5. **Reuse the LlmJob queue** — the chain runs as an `LlmJob` of `kind="voice_note"`
   (auto-enqueued on transcription completion for voice_note transcripts), so it
   shows live progress/cancel/rerun on the Queue screen like every other job.

### Explicitly OUT of scope (noted in the issue as "not required" / future)
- Using voice-note audio as enrollment material for the voice roster/diarization
  system. Left as a future possibility; nothing implemented.

## Kind-branch sites — full enumeration (Complement Rule sweep)

From the local `explore` audit. Every site that branches on `kind` must accept
`voice_note` (treated like `dictation` unless noted):

| File:line | What | Change for voice_note |
|---|---|---|
| `app.py:355` | `if t.kind != "dictation":` gates reformat actions (format_markdown/email/coding_prompt) + classify_intent enqueue | Extend to `not in ("dictation","voice_note")` so voice notes get reformat actions too (they are single-speaker dictations) |
| `app.py:938-939` | `if kind == "dictation": diarize = False` (server-side enforcement) | `if kind in ("dictation","voice_note"): diarize = False` |
| `app.py:1173` | `kind: str = Form("meeting")` on POST /api/transcribe | Accept "voice_note" |
| `app.py:1179-1180` | `if kind not in ("meeting","dictation"): raise 400` | add "voice_note" |
| `app.py:1473-1475` | PATCH /api/transcripts/{id}/relabel `data["kind"] not in ("meeting","dictation")` | add "voice_note" |
| `database/__init__.py:38` | `kind` column comment `meeting \| dictation` | update comment to include `voice_note` |
| `services/transcription.py:187` | `if transcript.kind == "dictation":` chooses single-speaker summary prompt | `in ("dictation","voice_note")` (so a manual Summarize on a voice note uses the single-speaker prompt) |
| `services/llm_jobs.py` `enqueue_auto_classify` (uses `kind != "dictation"` guard) | only dictation auto-classifies | leave as-is; add sibling `enqueue_voice_note` gated on `kind == "voice_note"` |
| `static/rack.js:41` | `mode: 'meeting'` default on Transcribe page; kind selector | add a "Voice note" option mirroring dictation |
| `static/rack.js` detail tabs | tabs built from `kind == "dictation"` | add 'note' tab when `kind == "voice_note"` |

Sibling sweep result: the issue text named none of these; the sweep found 9
distinct sites. All are in scope per the Complement Rule.

## Detail-view integration points (from frontend `explore` audit)

- `loadTranscriptDetail` (rack.js:2376): reset stale tab selection when opening a
  non-matching transcript.
- `detailTabsHtml` (rack.js:2425): builds `['transcript','corrected','summary']`
  plus 'format' for dictation. Add 'note' for voice_note.
- `renderDetailBody` (rack.js:3353): dispatches on `S.detailTab`. Add a
  `note` branch calling `noteHtml(t)` (mirror `summaryHtml` at rack.js:3208).
- `summaryHtml` (rack.js:3208): fetch `GET /api/transcripts/{id}/summary`, render
  cards. Mirror for `noteHtml` → `GET /api/transcripts/{id}/voice-note`.
- Job tracking: `llmJobActive` (rack.js:2828), `jobRunningUnit` (rack.js:2896),
  `exportToolbarHtml` (rack.js:3066) are reusable. Add `voice_note_job` to the
  detail fingerprint/poll so the tab live-updates.

## Proposed implementation (what Phase 2 will build)

### DB model — `database/__init__.py`
New `VoiceNote` table:
```
id, transcript_id (FK, unique, ondelete CASCADE),
note_type (String32, default "other": todo|idea|reminder|journal|other),
title (Text), body (Text), structured (JSON, default dict),
provider (String64), model (String128), error (Text nullable),
created_at, updated_at
```
Relationship on `Transcript`: `voice_note = relationship("VoiceNote",
back_populates="transcript", uselist=False, cascade="all, delete-orphan")`.
No manual migration needed: `Base.metadata.create_all` auto-creates new tables
on startup (verified `migrate_schema` only does a legacy rename; `init_db`
calls `create_all`).

### `services/voice_notes.py` (new)
- `NOTE_TYPES = ("todo","idea","reminder","journal","other")`
- `classify_note_type(transcript, api_key, provider_name, provider_config, model) -> str`
  json_mode LLM call; returns a NOTE_TYPES label; never raises (falls back
  "other"). Mirrors `classify_intent` in services/reformatting.py for the
  call shape (imports `chat_completion, resolve_model, transcript_text_for_prompt`
  from `services.llm_client`; uses `feature_name="Reformatting"` to reuse the
  existing model-resolution path; `raise_on_truncation=True`).
- `structure_note(transcript, note_type, api_key, provider_name, provider_config, model) -> dict`
  json_mode LLM call; returns `{"title": str, "body": str, "structured": dict}`
  where `structured` is type-specific (e.g. todo: checklist[...] + due;
  reminder: when; journal/idea: {}). Never raises (returns a sane default on
  failure).
- `run_voice_note_chain(db, transcript, api_key, provider_name, provider_config, model) -> VoiceNote`
  classify → structure → upsert VoiceNote by transcript_id (one row per
  transcript). Commits.

### `services/llm_jobs.py`
- Add `"voice_note"` to `VALID_KINDS` and `AUTO_RETRY_KINDS` (it is network/LLM,
  like classify_intent). NOT in `CPU_KINDS`.
- `run_llm_job`: add `elif job.kind == "voice_note":` branch — progress_total=1,
  call `run_voice_note_chain`, store `result_json={"note_type","title"}`,
  `_finish(completed)`; on exception `_finish(failed)`. Import
  `run_voice_note_chain` lazily inside the branch.
- New `enqueue_voice_note(db, transcript, user_settings) -> LlmJob | None`:
  returns None unless `kind=="voice_note"`; uses `format_provider`/`format_model`
  settings (same as reformat/classify); resolves key; enqueues via
  `enqueue_llm_job(..., "voice_note", ...)` with a missing-key skip error like
  `enqueue_auto_classify`.

### Trigger — completion hook
`enqueue_auto_classify` has 3 callers (app.py + services/queue.py) invoked after a
transcript reaches `completed`. At each caller, add:
`if transcript.kind == "voice_note": enqueue_voice_note(db, transcript, user_settings)`
alongside the existing auto_classify/auto_correction calls. (auto_correction stays
for all kinds; auto_classify stays dictation-only.)

### `app.py` routes
- `GET /api/voice-notes` — list current user's transcripts with `kind=="voice_note"`,
  joined with their `VoiceNote` (or null) and latest `voice_note` LlmJob. Returns
  array (see contract).
- `GET /api/transcripts/{id}/voice-note` — if kind==voice_note and VoiceNote exists,
  return it; else 404 `{"detail":"no voice note"}`.
- Extend the transcript serializer so voice_note transcripts carry
  `voice_note` (dict|null) and `voice_note_job` (dict|null). Non-voice-note
  transcripts get `voice_note: null, voice_note_job: null`.

### API contract (shared by backend + frontend)
`GET /api/voice-notes` item:
```
{ id, title, created_at, duration_seconds, status,
  note_type|null, note_title|null, note_body|null, note_structured|null,
  voice_note_job|null }
```
`GET /api/transcripts/{id}/voice-note`:
```
{ note_type, title, body, structured, provider, model, created_at, updated_at }
```
Transcript serialize extension (voice_note transcripts only meaningfully):
```
voice_note: {note_type,title,body,structured,provider,model,updated_at}|null
voice_note_job: {id,status,progress_done,progress_total,provider,model,error}|null
```

### Frontend
- `static/index.html`: add rail button `data-nav="voicenotes"` (label "Voice notes")
  + `<div class="page" id="page-voicenotes">`.
- `static/rack.js`:
  - add `'voicenotes'` to `PAGES`; add `loadVoiceNotes` to the `navigate()` loaders
    map; implement `loadVoiceNotes()` → fetch `/api/voice-notes`, render a board of
    note cards (type badge, title, body preview, structured fields, date); click →
    `navigate('detail', id)`.
  - Transcribe page: add a "Voice note" kind option to the meeting/dictation
    selector (mirror dictation); it sets `S.mode='voice_note'` and sends
    `kind=voice_note` on upload.
  - Detail view: add 'note' tab (kind==voice_note) per the integration points
    above; implement `noteHtml(t)` mirroring `summaryHtml`.

## Acceptance criteria (self-defined, since the issue has no checklist)

The issue has no explicit "Definition of Done", so I use the "at minimum" list:
- [ ] voice_note is a usable Transcript kind (upload path accepts it, diarization
      forced off) — met by app.py + database changes.
- [ ] A multi-step chain classifies then structures the note — met by
      services/voice_notes.py + LlmJob kind.
- [ ] Structured output is stored — met by VoiceNote table + run_voice_note_chain.
- [ ] Structured output is displayed — met by Voice Notes board + Detail Note tab.
- [ ] Reuses LlmJob queue — met (kind="voice_note", Queue UI works unchanged).
- [ ] Regression test exercises the chain end-to-end (mocked LLM) and the enqueue
      trigger — required by Phase 3.

## Issue's suggested approach vs reality
The issue gave no code snippet (open scope), only the prose above. No stale
snippet to distrust. The one design risk the issue flags: the existing
`classify_intent` is "a one-shot classifier feeding existing reformat
templates" — explicitly NOT what we want. So we do NOT reuse `classify_intent`;
we add a richer two-step chain. Confirmed correct.
