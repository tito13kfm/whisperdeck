# Investigation: Issue #169 - Voice-note board

## Target Issue
#169: Voice-note board: single-speaker capture with LLM intent chain and structured output

## Issue Summary
A quick-capture voice-note mode distinct from existing meeting/dictation flow:
- Record, transcribe (single-speaker, diarization forced off)
- Run through LLM chain: classify intent → branch → produce structured output
- Store and display structured output
- UI surface for it

## Current State Analysis

### Transcript.kind Values
- `meeting` (default): multi-speaker, diarization available, voice matching available
- `dictation`: single-speaker, diarization forced off, reformatting actions available (markdown/email/coding_prompt)

### Dictation vs Meeting Behavioral Divergences

| Location | Dictation | Meeting |
|----------|-----------|---------|
| `app.py:938-939` | `diarize=False` forced | Uses client flag |
| `services/transcription.py:187-212` | "single person's spoken dictation" prompt | "expert meeting summarizer" prompt |
| `app.py:1932-1933` | Reformatting available | Rejected with error |
| `app.py:2018-2019` | Voice matching rejected | Voice matching available |
| `app.py:355-373` | Dictation job fields populated | All null |
| `services/audio_prep.py:89-120` | No stereo processing | Stereo FLAC for live captures |

### Existing LLM Job Kinds
`VALID_KINDS` in `services/llm_jobs.py:20-23`:
- correction, summary, rediarize, voice_match
- format_markdown, format_email, format_coding_prompt, classify_intent

### UI Pages
`PAGES` array in `static/rack.js:404`:
- dashboard, transcribe, transcripts, queue, detail, voices, files, settings

### Format Tabs (Dictation Only)
`FORMAT_TARGETS` in `static/rack.js`:
- markdown → format_markdown job
- email → format_email job  
- coding_prompt → format_coding_prompt job
- classify_intent runs to suggest best format

## Design Decisions

### 1. Transcript Kind Strategy
**Decision**: Add new kind `voice-note` (not reuse `dictation`)

**Rationale**:
- Voice notes need different LLM chain (classify → branch → structure)
- Dictation's reformatting actions (markdown/email/coding_prompt) are single-shot, not chained
- Voice notes produce structured output (todo list, idea summary, journal entry, reminder) vs plain reformatted text
- Separation keeps existing dictation behavior stable

### 2. LLM Chain Design
**Decision**: Multi-step chain using existing LlmJob infrastructure

**Chain**:
1. `voice_note_classify` - Classify note type (todo, idea, journal, reminder, other)
2. `voice_note_structure` - Generate structured output based on classification

**Why not single-shot**: Issue explicitly requires "LLM figuring out what kind of note this is and then doing something structured with it"

### 3. Storage Strategy
**Decision**: Reuse `LlmJob.result_json` for structured output

**Schema**:
```json
{
  "note_type": "todo|idea|journal|reminder|other",
  "structured_output": {
    // For todo: { "items": [{"text": "...", "due": "..."}] }
    // For idea: { "title": "...", "summary": "...", "implications": [...] }
    // For journal: { "mood": "...", "themes": [...], "entry": "..." }
    // For reminder: { "text": "...", "when": "..." }
    // For other: { "content": "..." }
  },
  "raw_text": "original transcript text"
}
```

### 4. UI Surface
**Decision**: New page `page-voicenotes` in PAGES array

**Rationale**:
- Voice notes are distinct content type from meeting transcripts
- Similar to "Voice roster" (page-voices) - curated collection
- Board-style card layout for structured outputs
- Doesn't clutter existing Tape Library

## Implementation Scope

### Backend Changes
1. **database/__init__.py**: No schema change needed (kind is String(16), accepts any value)
2. **services/llm_jobs.py**:
   - Add `voice_note_classify`, `voice_note_structure` to `VALID_KINDS`
   - Add handler in `run_llm_job()` for new kinds
   - Implement chaining: classify completes → enqueue structure job
3. **services/voice_notes.py** (new file):
   - `classify_voice_note()` - returns note type
   - `structure_voice_note()` - returns structured output based on type
4. **app.py**:
   - Add `/api/transcripts/{id}/voice-note-classify` endpoint
   - Add `/api/transcripts/{id}/voice-note-structure` endpoint
   - Update `_dictation_job_fields()` to handle voice-note kind
   - Update upload endpoint to accept `kind=voice-note`
   - Force `diarize=False` for voice-note (like dictation)

### Frontend Changes
1. **static/index.html**: Add `page-voicenotes` container
2. **static/rack.js**:
   - Add `voicenotes` to `PAGES` array
   - Add `loadVoiceNotes()` function
   - Add voice-note card rendering
   - Add navigation button in rail
3. **Upload UI**: Add "Voice Note" option to kind selector

## Call Sites to Update (Complement Rule)

### Upload Endpoints
- `app.py:1173` - Form default for kind
- `app.py:1179-1180` - Validate kind values

### Serialization
- `app.py:355-373` - `_dictation_job_fields()` needs voice-note variant
- `app.py:2037` - History endpoint valid kinds

### Diarization Logic
- `app.py:938-939` - Force diarize=False for voice-note

### UI Rendering
- `static/rack.js:404` - PAGES array
- `static/rack.js` - navigate() function loaders
- Upload form kind selector

## Sibling Sweep

### Similar Patterns Found
1. **Dictation reformatting chain**: format_markdown/email/coding_prompt jobs
   - Similar single-shot LLM jobs
   - Voice notes need multi-step chain (different pattern)

2. **Summary generation**: `services/transcription.py:summarize()`
   - Single LLM call with kind-specific prompt
   - Voice notes need 2 calls (classify then structure)

3. **Correction job**: `services/correction.py`
   - Progress reporting, cancellation support
   - Voice notes should follow same pattern

### No Siblings Missed
The issue's scope (new LLM chain, new UI) doesn't have hidden siblings - it's a new feature path.

## Issue's Suggested Approach vs Reality

**Issue says**: "Reuse the existing LlmJob queue infrastructure"
**Reality**: ✅ Correct - will use existing queue

**Issue says**: "multi-step LLM chain (classify → branch → structure)"
**Reality**: ✅ Correct - will implement 2-job chain

**Issue says**: "reuse the existing transcript/kind model or something new"
**Reality**: ⚠️ Issue leaves this open - decision to add new `voice-note` kind is reasonable

**Issue says**: "somewhere to store and display the structured output"
**Reality**: ⚠️ Issue vague - decision to use LlmJob.result_json is reasonable

## Acceptance Criteria (from issue)

- [ ] Quick-capture voice-note mode distinct from meeting/dictation
- [ ] Record, transcribe (single-speaker, diarization forced off)
- [ ] Multi-step LLM chain (classify → branch → structure)
- [ ] Store structured output
- [ ] Display structured output
- [ ] UI surface for voice notes
- [ ] Reuse existing LlmJob queue infrastructure

## Testing Strategy

### Static Checks (First)
- Verify new LLM job kinds in VALID_KINDS
- Verify voice-note kind forces diarize=False
- Verify serialization includes voice-note job fields
- Verify UI page renders correctly

### Regression Test
- Upload voice-note transcript
- Verify classify job runs
- Verify structure job runs after classify
- Verify structured output stored in result_json
- Verify UI displays structured output

### Browser Tool Availability
- No Playwright MCP tool available in current environment
- Will rely on static checks + existing unit tests
- Will note this explicitly in final report
