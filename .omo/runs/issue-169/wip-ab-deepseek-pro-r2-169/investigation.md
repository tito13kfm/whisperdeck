# Investigation: Voice-Note Board (#169)

## 1. Existing Infrastructure

### 1.1 LlmJob Queue System (services/llm_jobs.py, database/__init__.py)

**Model** (database/__init__.py:92-111): LlmJob has fields id, user_id, transcript_id, kind, status, attempts, progress_done, progress_total, provider, model, error, dismissed, result_json, created_at, updated_at.

**Valid kinds** (services/llm_jobs.py:20-22):
- `correction`, `summary`, `rediarize`, `voice_match`
- `format_markdown`, `format_email`, `format_coding_prompt`
- `classify_intent`

**Dispatch flow**:
1. `enqueue_llm_job()` creates pending row (services/llm_jobs.py:93-110)
2. `llm_worker_tick()` claims pending → running (services/llm_jobs.py:438-475)
3. `run_llm_job()` dispatches by kind → calls correct_transcript, classify_intent, etc. (services/llm_jobs.py:227-435)
4. `_finish()` sets completed + result_json (services/llm_jobs.py:219-225)

**Concurrency**: IO_KINDS capped at 2, CPU_KINDS capped at 1. Auto-retry for IO_KINDS on transient failures.

### 1.2 classify_intent (services/reformatting.py:87-112)

Current classifier recognizes 4 labels: `markdown`, `email`, `coding_prompt`, `none`.
This is a one-shot classifier → reformat template. Not a multi-step chain.
The voice-note board needs a different classifier: `todo`, `idea`, `reminder`, `journal`, `none`.

### 1.3 Dictation Flow (dictation kind)

**Transcript.kind** (database/__init__.py:41): "meeting" or "dictation". Dictation:
- Diarization forced off server-side (app.py:938-940)
- Single-speaker summary prompt (services/transcription.py:187-212)
- Gets Format tab in detail view (rack.js:2427-2436)
- Gets auto-classify + auto-correct jobs after transcribe completes

### 1.4 UI Structure (static/)

SPA with 8 pages: dashboard, transcribe, transcripts, queue, detail, voices, files, settings.
Pattern for adding a page: add `<div class="page" id="page-X">` to index.html, add rail button, add to PAGES array + loaders map in rack.js, write loader function.
No voice-notes page exists.

## 2. Design Decisions

### 2.1 Storage: Extend Transcript + New VoiceNote Model

**Transcript.kind**: Add `"voicenote"` as a third kind value. This inherits the existing dictation behavior (diarization off, single-speaker) without code duplication.

**New VoiceNote model**: Separate table for structured note output. Fields:
- id, user_id, transcript_id (FK), note_type (todo|idea|reminder|journal), title, content_json (JSON with type-specific fields), created_at

**Why separate model**: Voice notes are conceptually different from transcripts — they have structured output, not segment arrays. The Transcript is the source audio record; the VoiceNote is the derived structured note. One transcript can produce one voice note.

### 2.2 LLM Chain: Two LlmJob Kinds

New job kinds: `classify_voicenote`, `structure_voicenote`.

**Scheme A (chained, preferred)**: Sequential two-job pipeline.
1. `classify_voicenote`: Takes transcript text, classifies into todo|idea|reminder|journal|none. Stores result in job.result_json.
2. `structure_voicenote`: Takes classified type + transcript text, produces structured note (title, type-specific fields). Creates VoiceNote row. Stores note_id in job.result_json.

**Why two jobs**: Each job is independently retryable. Failures at classification don't waste a structure call. The `result_json` from step 1 feeds step 2 explicitly.

**Alternative (one job)**: `voicenote_chain` does both in one call. Simpler but less granular for retry/progress tracking.

Decision: Scheme A (chained). Trigger `structure_voicenote` from `run_llm_job` when `classify_voicenote` completes successfully.

### 2.3 Structured Output by Note Type

| Note Type | JSON Fields |
|-----------|-------------|
| todo | title, priority (high|medium|low), due_date (optional), description |
| idea | title, category (optional), description, tags (list) |
| reminder | title, trigger (text description), description |
| journal | title, mood (optional), entry |

All stored in VoiceNote.content_json. The LLM prompt for structure_voicenote generates these fields in JSON mode.

### 2.4 Audio Capture

Reuse the existing Transcribe page flow with `kind=voicenote` parameter. The issue says "quick-capture" — could add a dedicated capture button on the voice-notes page later, but for MVP, use the existing transcribe flow with a "Voice note" kind option.

### 2.5 UI: Voice Notes Board Page

New page at `/page-voicenotes`:
- Cards/grid layout showing all user's voice notes, grouped by note_type or sorted by date
- Each card shows: note_type badge, title, preview of content, created_at
- Clicking a card expands to show full structured content
- "New voice note" button → goes to transcribe page with kind=voicenote preset
- "Delete" action on each card

## 3. Sibling Sweep

Checked for other patterns similar to what we're adding:

### 3.1 Other "kind" consumers
- `enqueue_auto_classify()` (services/llm_jobs.py:175-191): gates on `kind == "dictation"`. Need to update to also trigger for `"voicenote"`, or create a parallel `enqueue_auto_classify_voicenote()`.
- Reformatting endpoints (app.py:1932-1934): gate on `kind == "dictation"`. Voice notes should NOT go through the existing reformat flow — they have their own chain.
- Summary prompt (services/transcription.py:187-212): switches on kind. Need to add voicenote case (single-speaker, distinct from dictation).
- Detail view Format tab (rack.js:2427-2436): `if (S.transcript.kind === 'dictation')`. Voice notes should NOT show the Format tab — they get their own structure chain.

### 3.2 Other LLM job dispatch sites
- `run_llm_job()` (services/llm_jobs.py:227-435): big if/elif chain by kind. Add two new elif branches.
- `enqueue_auto_correction()` (services/llm_jobs.py:158-172): gates on correction provider setting. Voice notes need auto-correction too (transcription quality matters for classification).

### 3.3 UI pages that list or filter by kind
- `loadTranscripts()` (rack.js:2063): fetches all transcripts. Voice notes should appear in the tape library (or be filterable).
- `renderTranscribe()` (rack.js:1243): needs a "Voice note" kind selector.

## 4. Implementation Scope

### Phase 2: Fix (files to change)

**Backend:**
1. `database/__init__.py`: Add VoiceNote model, add `"voicenote"` to Transcript.kind choices
2. `services/llm_jobs.py`: Add `classify_voicenote` and `structure_voicenote` to VALID_KINDS, INO_KINDS, AUTO_RETRY_KINDS. Add dispatch branches in `run_llm_job()`. Add `enqueue_voicenote_chain()` helper. Update `enqueue_auto_correction()` for voicenote kind.
3. `services/reformatting.py` (or new `services/voicenotes.py`): `classify_voicenote()` function, `structure_voicenote()` function with type-specific prompts
4. `app.py`: Add VoiceNote API endpoints (GET/POST/DELETE /api/voice-notes). Update transcribe endpoint to accept `kind=voicenote`. Update dictation gates. Add migration to create voice_notes table.

**Frontend:**
5. `static/index.html`: Add `#page-voicenotes` div, rail button
6. `static/rack.js`: Add voicenotes to PAGES/loaders, `loadVoiceNotes()` function, voice-note card rendering
7. `static/rack.css`: Voice-note card styles (if needed beyond existing .unit patterns)

## 5. Issue's Suggested Approach vs Reality

The issue says "reuse the existing transcript/kind model or something new." It doesn't provide a specific code snippet or line numbers — it's a design description, not a suggested patch. No stale line numbers to correct.

The issue says "a multi-step LLM chain (classify → branch → structure)." My investigation confirms this is implementable as two LlmJob kinds in the existing queue.

The issue mentions "somewhere to store and display the structured output." The existing `result_json` on LlmJob stores output snapshots, but a dedicated VoiceNote model is better for query/display purposes since the user needs to browse notes by type, search, etc.

## 6. Acceptance Criteria

The issue has no explicit acceptance criteria checklist. Implicit criteria from the body:
- [ ] Single-speaker capture (diarization off)
- [ ] LLM chain classifies note type (todo, idea, reminder, journal)
- [ ] LLM produces structured output per note type
- [ ] UI surface for browsing voice notes
- [ ] Reuses LlmJob queue infrastructure (not new async mechanism)
