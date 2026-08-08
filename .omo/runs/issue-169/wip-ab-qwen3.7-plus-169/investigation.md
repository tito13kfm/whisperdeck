# Investigation - Issue #169: Voice-note board

## Target Issue
Issue #169: Voice-note board: single-speaker capture with LLM intent chain and structured output

## Current State (from Phase 1 investigation)

### Transcript Model
- **File**: `database/__init__.py:31-71`
- **Two kinds**: `meeting` (multi-speaker, diarized) and `dictation` (single-speaker, no diarization)
- **Dictation features**: Reformat actions (markdown/email/coding_prompt), auto-classify hint
- **Storage**: `kind` field (String 16), `segments` JSON array, `full_text`, metadata fields

### LlmJob Queue Infrastructure
- **File**: `database/__init__.py:92-111` (model), `services/llm_jobs.py:1-499` (implementation)
- **8 job kinds**: correction, summary, rediarize, voice_match, format_markdown, format_email, format_coding_prompt, classify_intent
- **Concurrency**: IO_KINDS (max 2), CPU_KINDS (max 1)
- **Flow**: `enqueue_llm_job()` → `llm_worker_loop()` → `run_llm_job()` → result stored in `result_json`

### Current classify_intent
- **File**: `services/reformatting.py:87-112`
- **Returns**: Single label string (markdown/email/coding_prompt/none)
- **Usage**: UI hint only, stored in `LlmJob.result_json = {"format": label}`
- **No multi-step chain**: classify_intent is one-shot, doesn't trigger further processing

### Reformat Templates
- **File**: `services/reformatting.py:39-84`
- **Three targets**: format_as_markdown, format_as_email, format_as_coding_prompt
- **Each is independent**: User manually triggers via UI after seeing classify_intent hint

### UI Surfaces
- **File**: `static/rack.js`
- **Format tab**: Only shows for `kind === 'dictation'` (line 2427)
- **Three format buttons**: markdown, email, coding_prompt (line 3168-3220)
- **Classify hint**: Shows "Suggested" badge based on classify_intent result (line 2501)

## Issue #169 Requirements Analysis

### What's Asked
1. **Voice-note board**: Distinct from existing meeting/dictation flow
2. **Single-speaker capture**: Record, transcribe (diarization forced off) - already exists as `dictation` kind
3. **Multi-step LLM chain**: classify → branch → structure (NOT just one-shot classify)
4. **Structured output**: Not just reformatted text, but structured data (todo, idea, reminder, journal entry)
5. **Storage/display**: Where structured output lives, how it's shown
6. **UI surface**: Where users access this feature
7. **Reuse LlmJob**: Use existing queue infrastructure

### What's Missing / What Issue Gets Wrong

**Issue's implicit assumption**: The current `classify_intent` + reformat flow is close to what's needed. **Reality**: Current flow is one-shot classification feeding manual template selection. Issue wants automatic multi-step chain that classifies AND produces structured output.

**Gap 1: Multi-step chain doesn't exist**
- Current: classify_intent returns label → user manually picks format → format runs
- Needed: classify_intent returns label → system automatically runs structure step → structured output stored

**Gap 2: Structured output model doesn't exist**
- Current: Reformat produces plain text (markdown/email/coding_prompt)
- Needed: Structure step produces structured data (JSON with fields like title, items, due_date for todos; or entry_text, mood, tags for journal)

**Gap 3: Voice-note board UI doesn't exist**
- Current: Dictation transcripts show in main transcript list with format tab
- Needed: Separate "voice notes" board/view showing structured notes, not raw transcripts

**Gap 4: Note-type taxonomy doesn't exist**
- Current: classify_intent returns format target (markdown/email/coding_prompt)
- Needed: classify_intent returns note type (todo, idea, reminder, journal, other) then branches to type-specific structuring

## Sibling Sweep (Complement Rule)

**Searched for**: Other LlmJob kinds, other transcript kinds, other format targets, other classify functions

**Found**:
- No other classify functions besides `classify_intent` in `reformatting.py`
- No other transcript kinds besides `meeting` and `dictation`
- No other format targets besides markdown/email/coding_prompt
- No existing voice-note or note-related features

**Conclusion**: No siblings with identical shape. This is a genuinely new feature, not a missing guard or call site.

## Call Sites / Entry Points in Scope

### Backend
1. **Transcript creation**: `POST /api/transcripts` (app.py:1474) - needs voice-note kind or flag
2. **Dictation upload**: `services/queue.py:562-571` - auto-enqueue classify on completion
3. **LlmJob processing**: `services/llm_jobs.py:315-325` - classify_intent branch in run_llm_job
4. **Reformat endpoints**: `POST /api/transcripts/{id}/format/{target}` (app.py:1912) - may need new structure endpoint
5. **Transcript detail**: `GET /api/transcripts/{id}` (app.py:299) - needs structured output in response

### Frontend
1. **Transcript list**: `static/rack.js` - needs voice-note board view
2. **Detail tabs**: `static/rack.js:2425` - needs structured output tab for voice notes
3. **Format tab**: `static/rack.js:3168-3220` - may need restructuring for voice notes

### Database
1. **Transcript model**: `database/__init__.py:31-71` - may need voice_note flag or new kind
2. **LlmJob model**: `database/__init__.py:92-111` - may need new job kind for structure step
3. **New model?**: May need VoiceNote or StructuredNote model for structured output

## Acceptance Criteria (from issue)

Issue doesn't list explicit acceptance criteria, but implies:
1. ✅ Voice-note capture mode distinct from meeting/dictation
2. ✅ Single-speaker transcription (diarization forced off)
3. ✅ Multi-step LLM chain (classify → branch → structure)
4. ✅ Structured output (not just reformatted text)
5. ✅ Storage for structured notes
6. ✅ Display/UI for voice-note board
7. ✅ Reuse LlmJob queue infrastructure

## Implementation Approach (High-Level)

### Option A: Extend existing dictation kind
- Add `voice_note` flag to Transcript model
- Extend classify_intent to return note_type (todo/idea/reminder/journal) instead of format target
- Add new LlmJob kind `structure_note` that runs after classify_intent
- Store structured output in new JSON field on Transcript or new model
- Add "Voice Notes" view in UI that filters to voice_note transcripts

**Pros**: Reuses existing infrastructure, minimal schema changes
**Cons**: Conflates dictation (reformat target) with voice notes (structured output), classify_intent semantics change

### Option B: New transcript kind `voice_note`
- Add third kind to Transcript model: `meeting`, `dictation`, `voice_note`
- Voice_note kind forces diarization off, auto-enqueues classify+structure chain
- New LlmJob kinds: `classify_note_type`, `structure_note`
- Structured output stored in new JSON field or model
- New "Voice Notes" board UI

**Pros**: Clean separation of concerns, clear semantics
**Cons**: More schema changes, need to update all kind-checking code

### Option C: Separate VoiceNote model
- Keep Transcript model as-is
- New VoiceNote model with fields: transcript_id, note_type, structured_data, created_at
- VoiceNote created after transcription completes, linked to transcript
- New LlmJob kinds for voice-note processing
- New "Voice Notes" board UI

**Pros**: Cleanest separation, doesn't touch existing transcript logic
**Cons**: More models, more complexity, need to manage transcript↔note relationship

**Recommendation**: Option B (new transcript kind) balances reuse with clean semantics. Voice notes are fundamentally a different use case from dictation (structured output vs. reformatted text), so they deserve their own kind.

## Files to Modify (if Option B)

### Backend
1. `database/__init__.py` - Add `voice_note` to kind enum/validation
2. `services/reformatting.py` - Add `classify_note_type()` and `structure_note()` functions
3. `services/llm_jobs.py` - Add `classify_note_type` and `structure_note` to VALID_KINDS, implement processing
4. `services/queue.py` - Auto-enqueue classify_note_type on voice_note completion
5. `app.py` - Add endpoints for voice-note board, structured output retrieval
6. `services/settings.py` - Add voice_note_provider, voice_note_model settings

### Frontend
1. `static/rack.js` - Add "Voice Notes" board view, structured output rendering
2. `static/rack.js` - Update detail tabs to show structured output for voice_note kind

### Tests
1. `tests/test_reformatting.py` - Add tests for classify_note_type and structure_note
2. `tests/test_llm_jobs.py` - Add tests for new job kinds
3. `tests/test_app.py` - Add tests for voice-note endpoints

## Open Questions

1. **Note type taxonomy**: What note types to support? Issue mentions todo, idea, reminder, journal entry. Should we start with these four, or add more?
2. **Structured output schema**: What fields per note type? Todo needs items/due_date, journal needs entry_text/mood/tags, etc.
3. **UI location**: Where does "Voice Notes" board live? New top-level nav item, or tab in existing view?
4. **Migration path**: Should existing dictation transcripts be convertible to voice notes, or are they separate from day one?

## Next Steps

1. Get user confirmation on Option B (new transcript kind) vs. alternatives
2. Define note type taxonomy and structured output schema
3. Implement backend: model changes, LLM chain, endpoints
4. Implement frontend: voice-note board, structured output display
5. Add tests for new functionality
6. Update documentation
