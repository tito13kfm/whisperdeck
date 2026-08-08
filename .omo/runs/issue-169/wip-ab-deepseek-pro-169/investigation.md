# Investigation: Voice-note board (issue #169)

## Summary

The existing dictation flow already provides single-speaker recording + auto-classification + manual reformat (markdown/email/coding_prompt). The voice-note board needs a new path: single-speaker recording + auto voice-note classification (todo/idea/reminder/journal) + auto structured output + a dedicated board UI.

Key insight: adding a new `Transcript.kind = "voice_note"` is the cleanest extension — it inherits dictation's single-speaker/no-diarization behavior but triggers different post-processing and rendering.

## 1. Current architecture

### Transcript model (`database/__init__.py:31-70`)
- `kind`: `"meeting" | "dictation"` (line 38) — drives diarization default, summary prompt, available reformat actions

### LlmJob system (`services/llm_jobs.py`)
- `VALID_KINDS` (line 20-23): `correction`, `summary`, `rediarize`, `voice_match`, `format_markdown`, `format_email`, `format_coding_prompt`, `classify_intent`
- `IO_KINDS` (API-bound, max_concurrent=2): `correction`, `summary`, `format_markdown`, `format_email`, `format_coding_prompt`, `classify_intent`
- `AUTO_RETRY_KINDS` (line 34): same as IO_KINDS
- Lifecycle: `enqueue_llm_job()` → worker claims → `run_llm_job()` → result in `result_json`

### classify_intent (`services/reformatting.py:87-112`)
- `INTENT_LABELS = ("markdown", "email", "coding_prompt", "none")`
- Returns one label via JSON mode
- Never raises, falls back to `"none"`

### Auto-classify trigger (`services/llm_jobs.py:175-191`)
- `enqueue_auto_classify()` — only for `kind == "dictation"`, creates `classify_intent` LlmJob
- Called from `services/queue.py` (chunked finalize) and `app.py` (inline transcription)

### Frontend dictation handling

**State** (`rack.js:41`):
- `mode`: `"meeting" | "dictation"` — dictation skips diarization

**Mode toggle** (`rack.js:1463`):
```js
S.mode = S.mode === 'meeting' ? 'dictation' : 'meeting';
```

**Detail tabs** (`rack.js:2427`):
```js
if (detailData && detailData.kind === 'dictation') tabs.push('format');
```

**Format tab render** (`rack.js:3373`):
```js
} else if (S.detailTab === 'format' && t.kind === 'dictation') {
```

**Reformat actions** (`rack.js:3161`):
- Buttons for format_markdown, format_email, format_coding_prompt
- Shown only for dictation transcripts on detail page

**Mode display** (`rack.js:1648-1653`):
- VFD shows "DICTATION" or "MEETING"
- Diarize toggle locked to N/A in dictation mode

## 2. Complete call site enumeration (Complement Rule sweep)

Every location that checks `kind == "dictation"` or `mode === "dictation"`:

### Python — services

| File:Line | Check | Voice-note behavior |
|---|---|---|
| `services/llm_jobs.py:181` | `transcript.kind != "dictation"` → return None | Also return None (voice_note has its own auto-enqueue) |
| `services/transcription.py:187` | `transcript.kind == "dictation"` → skip diarization | Same: voice_note is single-speaker, no diarization |

### Python — app.py

| File:Line | Check | Voice-note behavior |
|---|---|---|
| `app.py:355` | `t.kind != "dictation"` | Expand to also exclude voice_note (or check `not in ("dictation", "voice_note")`) |
| `app.py:938` | `kind == "dictation"` | Add `or kind == "voice_note"` for single-speaker behavior |
| `app.py:1179` | `kind not in ("meeting", "dictation")` | Add `"voice_note"` to valid kinds |
| `app.py:1474` | `data["kind"] not in ("meeting", "dictation")` | Add `"voice_note"` to valid kinds |
| `app.py:1932` | `t.kind != "dictation"` | Expand to also exclude voice_note from format actions |
| `app.py:1987` | `t.kind == "dictation"` | Add `or t.kind == "voice_note"` |
| `app.py:2018` | `t.kind == "dictation"` | Add `or t.kind == "voice_note"` |

### JavaScript — rack.js

| File:Line | Check | Voice-note behavior |
|---|---|---|
| `rack.js:41` | `mode: 'meeting'` comment | Add `voice_note` as third mode |
| `rack.js:1463` | `S.mode === 'meeting' ? 'dictation' : 'meeting'` | 3-way toggle: meeting → dictation → voice_note → meeting |
| `rack.js:1467` | `S.running \|\| S.mode === 'dictation'` | Add `\|\| S.mode === 'voice_note'` |
| `rack.js:1648-1653` | mode/diarize display | Add voice_note display (label, VFD, diarize lock) |
| `rack.js:1725` | `S.mode === 'dictation' ? 'false'` | Add `\|\| S.mode === 'voice_note'` |
| `rack.js:2392` | `detailData.kind !== 'dictation'` | Add `&& detailData.kind !== 'voice_note'` |
| `rack.js:2427` | `detailData.kind === 'dictation'` → format tab | voice_note gets a "voice note" tab instead |
| `rack.js:3282` | `t.kind === 'dictation' ? ''` | Same: hide speaker count for voice_note |
| `rack.js:3311` | toggle-kind button label | Show "Voice Note" as a label option |
| `rack.js:3359` | `t.kind !== 'dictation'` | Add `&& t.kind !== 'voice_note'` |
| `rack.js:3373` | `S.detailTab === 'format' && t.kind === 'dictation'` | Add voice_note tab rendering |
| `rack.js:3403` | `t.kind === 'dictation' ? 'meeting' : 'dictation'` | 3-way cycle to include voice_note |
| `rack.js:4215` | settings description | Update to mention voice_note |

### Detailed behavior analysis for each check

I need to read each of these app.py lines to understand what they do before deciding on voice_note behavior. Let me trace through.

**app.py:355** — Likely a guard on some action (correction? summarization?). Need to read.
**app.py:938** — In the transcribe route? Setting diarization behavior based on kind.
**app.py:1179** — Validation: `kind not in ("meeting", "dictation")` — reject invalid kinds.
**app.py:1474** — PATCH /api/transcripts/{id} validation for kind field.
**app.py:1932** — Format route guard: don't allow format actions for non-dictation.
**app.py:1987** — Some dictation-specific behavior.
**app.py:2018** — Some dictation-specific behavior.

### Sibling sweep: other timers/pollers that might need clearing

The issue is a feature addition, not a bug fix, so the sibling sweep for "missed timers" doesn't apply the same way. But checking: are there any cleanup/unload paths that handle `kind` transitions? The `toggle-kind` handler (`rack.js:3403`) does a PATCH + page reload — no cleanup needed. No dangling timers found related to kind switching.

## 3. Proposed design

### 3a. New kind: `voice_note`

Add `"voice_note"` to `Transcript.kind` valid values. Distinguished from `"dictation"` by:
- Different post-processing chain (voice_note vs classify+format)
- Different UI surface (board vs detail/format tab)
- Different mode toggle behavior (3-way)

### 3b. New LlmJob kind: `voice_note`

Single LlmJob that does classify+structure in one LLM call:
- Prompt classifies: todo, idea, reminder, journal_entry, other
- Prompt also produces structured JSON output based on classification
- Added to `VALID_KINDS`, `IO_KINDS`, `AUTO_RETRY_KINDS`

Why one job, not a chain? One LLM call can do both classify and structure with modern models. No benefit to splitting into two calls (same input, same model). If later we want different models for classification vs structuring, we can split then.

### 3c. New reformatting function: `generate_voice_note()`

In `services/reformatting.py`, new function similar to `format_as_markdown()` but with a voice-note-specific prompt:

- Accept: transcript text
- Return: JSON with `{type, title, content, metadata}`
  - type: "todo" | "idea" | "reminder" | "journal_entry" | "other"
  - title: extracted/suggested title
  - content: structured write-up (for todos: checklist items; for reminders: time/context; for ideas: elaboration; for journal: narrative)
  - metadata: type-specific fields (todo: priority; reminder: when; idea: category)

### 3d. New auto-enqueue: `enqueue_voice_note()`

In `services/llm_jobs.py`, new function (mirroring `enqueue_auto_classify`):
- Triggered for `kind == "voice_note"` after transcription completes
- Uses same `format_provider` / `format_model` settings (or new dedicated settings)

### 3e. New frontend: Voice Notes page

New rail page "Voice Notes" showing a card-based board of completed voice-note transcripts:
- Each card: note type icon, title, preview text, timestamp
- Click → navigate to detail page
- Filter by note type
- New note button → navigates to Transcribe page in voice_note mode

### 3f. Detail page changes

For `kind === "voice_note"` transcripts:
- Show "Voice Note" tab with structured output
- Hide format buttons (markdown/email/coding_prompt)
- Mode toggle cycles: meeting → dictation → voice_note → meeting

### 3g. Transcribe page changes

- Mode toggle becomes 3-way: Meeting → Dictation → Voice Note
- Voice Note mode: same as Dictation (single-speaker, no diarization) but submits with `kind: "voice_note"`

## 4. Implementation plan

### Phase 2a: Backend (Python)

1. **`database/__init__.py`**: Update `Transcript.kind` comment to include `voice_note`
2. **`services/reformatting.py`**: Add `generate_voice_note()` function and `VOICE_NOTE_LABELS`
3. **`services/llm_jobs.py`**: 
   - Add `"voice_note"` to `VALID_KINDS`, `IO_KINDS`, `AUTO_RETRY_KINDS`
   - Add `enqueue_voice_note()` function
   - Add voice_note case to `run_llm_job()` dispatch
4. **`services/transcription.py`**: Handle `voice_note` like `dictation` for diarization skip
5. **`app.py`**: 7 locations — add `"voice_note"` to kind checks, add voice_note auto-enqueue trigger

### Phase 2b: Frontend (JavaScript)

1. **`static/rack.js`**: 
   - Mode toggle: 3-way (meeting ↔ dictation ↔ voice_note)
   - Mode display: add "VOICE NOTE" VFD/UI
   - Detail tabs: add voice_note tab instead of format tab
   - Detail render: show structured voice-note output
   - New Voice Notes page: card board UI
   - Rail navigation: add "Voice Notes" nav item
2. **`static/index.html`**: Add page-voice-notes container
3. **`static/rack.css`**: Voice-note card styles (reuse existing theme variables)

### Phase 2c: Settings (optional, nice-to-have)

- New user settings: `voice_note_provider`, `voice_note_model`
- Fall back to `format_provider`/`format_model` if not set

## 5. Sibling sweep findings

Checked every location that switches on `kind` or `mode` — enumerated all 22 call sites above. All need updating. 

No missed siblings: the only two kind values are "meeting" and "dictation". Adding "voice_note" as a third value means touching every `kind` check.

The existing "dictation" format actions (format_markdown, format_email, format_coding_prompt) are NOT relevant to voice_note — we explicitly exclude voice_note from those guards and add a separate voice_note tab.

## 6. Testing strategy

Per AGENTS.md testing tiers:
- **Unit tests**: Add tests for `generate_voice_note()` (new function)
- **Integration tests**: Extend `test_reformatting.py` with voice_note kind upload + auto-enqueue
- **Static source-level check** (Phase 3 first): Verify all 22 call sites are updated
- **e2e**: Not required for initial implementation (feature, not regression)
  - If browser tool available: test voice_note capture → auto-process → board display
  - If not: rely on unit/integration + static check

## 7. Issue acceptance criteria walkthrough

No explicit checklist in the issue body. Map requirements → implementation:

| Requirement | How met |
|---|---|
| "quick-capture voice-note mode" | 3-way mode toggle on Transcribe page, new `voice_note` kind |
| "record, transcribe (single-speaker, diarization forced off)" | Inherits dictation's single-speaker path |
| "chain of LLM calls that figures out what kind of note it is" | `generate_voice_note()` classifies into todo/idea/reminder/journal |
| "produces a structured write-up" | JSON output with type-specific structure |
| "not just plain reformatted text" | Type-specific JSON, not plain prose |
| "a place for these notes to live" | LlmJob result_json + detail tab display |
| "reuse the existing LlmJob queue infrastructure" | New `voice_note` kind in existing queue |
| "a UI surface for it" | New Voice Notes board page + detail tab |
| "enrollment material for voice roster" | Noted for future, not in scope |
