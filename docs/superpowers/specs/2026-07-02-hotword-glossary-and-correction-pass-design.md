# Hotword Glossary + Post-Hoc Correction Pass

## Background

Two prior ideas were on the table for improving transcription accuracy on names/jargon:

1. A same-audio Whisper-tiny "pre-pass" feeding Groq's `prompt` param (see memory
   `project_whisperdesk_prepass_idea`). Rejected during this design session: it's
   self-referential — if the tiny model mishears a rare name, it primes the real
   model with the *wrong* spelling (net negative), and if it hears correctly, the
   real model likely would have too (marginal). Whisper's prompt param only earns
   its keep with external ground truth the audio itself can't supply.
2. An LLM-based post-processing correction pass (see memory
   `project_llm_transcript_correction_idea`), previously undesigned.

This spec builds (2), sourcing the "external ground truth" vocabulary from a
user-maintained glossary rather than from a doomed same-audio guess. Whisper's
transcription-time `prompt` param is explicitly **not** touched by this work.

## Scope

- Persistent, self-building hotword glossary per user.
- Optional per-transcript context doc (agenda, notes) that auto-extracts terms
  into that glossary.
- A non-fatal, automatic post-hoc LLM correction pass that cleans up the final
  transcript text using the glossary.
- Raw transcript output is never overwritten — correction is additive.

Out of scope for this spec: Whisper `prompt` param changes, same-audio pre-pass,
voice_id.py speaker-name integration, per-segment correction/re-alignment.

## Components

### 1. Persistent hotword glossary

New table:

```python
class HotwordEntry(Base):
    __tablename__ = "hotword_entries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    term = Column(String(255), nullable=False)
    source = Column(String(16), default="manual")  # "manual" | "extracted"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
```

New endpoints (mirrors existing settings/provider-config CRUD style):

- `GET /api/hotwords` — list current user's glossary
- `POST /api/hotwords` — add a term (`{"term": "..."}`), source="manual"
- `DELETE /api/hotwords/{id}` — remove a term (must belong to current_user)

Dedup on insert: skip if an entry with the same `term` (case-insensitive) already
exists for that user, regardless of source.

### 2. Per-transcript context doc → auto-extraction

`POST /api/transcribe` gains an optional form field:

```python
context_doc: Optional[str] = Form(None)
```

Same field is accepted by the chunked-stub creation path (same route, same form).

If `context_doc` is non-empty:
- After the transcript record is created (stub or inline), fire an LLM extraction
  call using the same provider/key resolution pattern as
  `TranscriptionService.summarize()` (groq default, openai/local supported).
- Prompt: given the doc text, return a JSON array of candidate proper nouns,
  names, and jargon terms likely to appear in a related meeting recording.
- Each returned term is merged into `HotwordEntry` for the user with
  `source="extracted"`, subject to the same dedup rule as manual entries.
- Failure here is non-fatal: log and continue: transcription proceeds
  regardless of whether extraction succeeded. This is a glossary-building side
  effect, not a blocking step.

LLM-assisted (not local heuristic) because the doc text is clean human-written
input, unlike noisy ASR output — an LLM call here is precise and cheap (one
short completion, not per-chunk).

### 3. Correction pass

New `services/correction.py`:

```python
async def correct_transcript(
    db, transcript, api_key: str, provider_name: str = "groq",
    model: str = "llama-3.3-70b-versatile",
) -> None:
    """Non-fatal: sets transcript.corrected_text on success, or
    transcript.correction_error on failure. Never raises."""
```

Behavior:
- Loads all `HotwordEntry.term` for `transcript.user_id`.
- Builds a correction prompt: `transcript.full_text` + the glossary (if any) +
  instruction to fix likely mistranscribed words and grammar/coherence issues
  *without changing meaning or adding content*. Same JSON-forcing pattern as
  `summarize()` where applicable (or plain text response — text in, text out,
  no JSON needed here).
- On success: `transcript.corrected_text = <result>`.
- On failure (API error, no key configured, etc.): set
  `transcript.correction_error = str(e)`, leave `corrected_text` null. Do not
  raise — matches the existing diarization non-fatal pattern in `app.py`.

Store which provider/model produced the current `corrected_text` in a new
`Transcript.correction_model` column (e.g. `"groq/llama-3.3-70b-versatile"`),
so the UI can show what generated the current correction and users can compare
across reruns.

**Manual re-run endpoint:** `POST /api/transcripts/{transcript_id}/correct`,
mirroring the existing manual `POST /api/transcripts/{transcript_id}/summarize`
route — same `provider`/`model` form fields, same `ProviderConfig` lookup
pattern. Lets a user rerun correction against the same raw `full_text` with a
different model at any time (transcript must already be `completed`/`partial`,
same as summarize's status check), to compare which model/provider produces
the best correction for their audio. Each rerun overwrites `corrected_text`
and `correction_model` with the new result — `full_text` is never touched, so
reruns are always working from the same untouched source, and are free to try
as many models as the user has keys for.

Called automatically once `transcript.status == "completed"`, gated by the
`auto_correct` user setting (default `True`):

- **Inline path** (`services/transcription.py: TranscriptionService.transcribe`):
  after the existing diarization block in `app.py`'s inline branch, call
  `correct_transcript` the same way diarization is called (best-effort, wrapped
  in try/except, non-fatal print-and-continue).
- **Chunked path** (`services/queue.py: _finalize_if_done`): after the
  transcript is finalized to `completed`/`partial`, call `correct_transcript`
  the same way.

Scope: `full_text` only. Segments (`transcript.segments`, used for timestamps
and diarization/speaker display) are never touched by the correction pass —
avoids the alignment problem of mapping LLM-rewritten text back onto original
segment boundaries.

### 4. Storage

New columns on `Transcript`:

```python
corrected_text = Column(Text, nullable=True)
correction_error = Column(Text, nullable=True)
correction_model = Column(String(128), nullable=True)  # e.g. "groq/llama-3.3-70b-versatile"
```

`full_text` and `segments` remain the untouched, authoritative provider output.

### 5. Settings

`services/settings.py: DEFAULT_SETTINGS` gains:

```python
"auto_correct": True,
```

Same pattern as the existing `hf_token` setting — stored per-user, toggled via
the existing `PUT /api/settings` route, no new settings plumbing needed.

## Frontend (implementation-plan level, not detailed here)

- Settings page: simple list UI to view/add/delete glossary terms
  (`GET/POST/DELETE /api/hotwords`).
- Upload form: optional "Meeting context / agenda" textarea, submitted as
  `context_doc`.
- Transcript view: when `corrected_text` is present, show it by default with a
  toggle back to the raw `full_text`. When `correction_error` is present (and
  no `corrected_text`), show raw text with no error surfaced to the user
  (best-effort feature, silent on failure — consistent with diarization's
  non-fatal failure handling). Show `correction_model` alongside the corrected
  text (small caption, e.g. "Corrected with groq/llama-3.3-70b-versatile") and
  a "Try a different model" control (provider + model picker, same pattern as
  the existing manual summarize control) that calls the new
  `POST /api/transcripts/{id}/correct` endpoint.

## Error handling

- Context-doc extraction failure: silent, non-fatal, transcription proceeds.
- Correction pass failure: silent, non-fatal, transcript stays `completed`
  with raw text only.
- No LLM key configured for the correction pass's chosen provider: same as
  above — `correction_error` set, no user-facing error.

## Testing

- Unit: `HotwordEntry` CRUD (dedup on case-insensitive match, ownership scoping
  to `user_id`).
- Unit: `correct_transcript` — mock LLM response, verify `corrected_text` set;
  mock LLM failure, verify `correction_error` set and no exception propagates.
- Integration: full inline transcribe → correction pass runs → `corrected_text`
  populated (with a stubbed/mocked LLM call, not a live API call).
- Integration: chunked transcribe → finalize → correction pass runs once on
  the merged full_text, not per-chunk.
- Manual: submit a `context_doc` with a distinctive name, confirm it lands in
  `hotword_entries` with `source="extracted"`, confirm it appears in the next
  correction pass's prompt.
- Integration: `POST /api/transcripts/{id}/correct` with a different
  provider/model than the automatic pass used, confirm `corrected_text` and
  `correction_model` are overwritten and `full_text` is untouched.
