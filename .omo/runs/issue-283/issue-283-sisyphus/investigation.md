# Phase 1 investigation — issue #283 (voice dump schema + kind plumbing)

**Target**: #283 (from tracking issue #261), "Voice dump: schema + kind plumbing"
**Base**: origin/master @ 10bbc18 ("Merge pull request #280")
**Worktree**: C:/Claude/whisperdesk-sisyphus-283 (branch: issue-283-sisyphus)
**Main repo**: C:/Claude/whisperdesk (branch: tooling-verify-gate)

## Issue's own spec (verbatim)

- database/__init__.py: VoiceDumpItem table (id, user_id FK, transcript_id FK cascade, source_job_id FK nullable, sequence_index, note_type, title, body, structured JSON, model, provider, created_at)
- services/transcription.py: diarization default branch for voice_dump (lightest diarization, same as voice_note)
- services/llm_jobs.py: add "voice_dump" to VALID_KINDS and IO_KINDS ONLY (no dispatch yet, no AUTO_RETRY_KINDS)
- services/settings.py: add "voice_dump" to bulk_defaults.kind allowed values
- app.py: voice_dump_job: None stub in _dictation_job_fields; add "voice_dump" to bulk import kind validation
- static/rack.js: kind picker dropdown label: "Audit / stream-of-consciousness dump" → value "voice_dump". No new UI.

## Acceptance criteria (verbatim)

- Transcript.kind accepts "voice_dump"
- VoiceDumpItem table exists (no unique constraint on transcript_id — many items per transcript)
- All existing tests pass unchanged
- test_io_cpu_pools_partition_valid_kinds still passes

---

## File-by-file findings (current code, worktree)

### 1. database/__init__.py

**Transcript.kind** (line 40):
```python
kind = Column(String(16), default="meeting")  # meeting | dictation | voice_note
```

**VoiceNote model** (lines 155-177) — pattern to follow for VoiceDumpItem:
```python
class VoiceNote(Base):
    __tablename__ = "voice_notes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    note_type = Column(String(32))
    title = Column(String(255))
    body = Column(Text)
    structured = Column(JSON, default=dict)
    model = Column(String(128))
    provider = Column(String(64))
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
```

**Transcript relationship** (line 74):
```python
voice_note = relationship("VoiceNote", back_populates="transcript", uselist=False, cascade="all, delete-orphan")
```
Note: VoiceNote is uselist=False (one-to-one), but VoiceDumpItem needs many-to-one (no unique constraint on transcript_id). So the new relationship will be uselist=True.

**Cascade pattern** (lines 76-80): ORM cascade is load-bearing because SQLite foreign_keys pragma is off.

### 2. services/transcription.py

**Summarize method kind branches** (lines 188-247):
- Line 188: `if transcript.kind == "voice_note":` → returns stub "Voice note — see the Notes tab..."
- Line 222: `if transcript.kind == "dictation":` → dictation-specific prompt
- Line 235: `else:` → standard meeting prompt

**Issue says**: "diarization default branch for voice_dump (lightest diarization, same as voice_note)"

Interpretation: Add `transcript.kind == "voice_dump"` branch with the same stub treatment as voice_note. The voice_note branch returns early (line 220), so `voice_dump` should do the same. This means voice_dump transcripts won't get a meeting-style summary — they're broken into individual VoiceDumpItem notes instead.

### 3. services/llm_jobs.py

**VALID_KINDS** (lines 20-24):
```python
VALID_KINDS = (
    "correction", "summary", "rediarize", "voice_match",
    "format_markdown", "format_email", "format_coding_prompt", "classify_intent",
    "voice_note", "tagging", "assistant", "classify_pipeline",
)
```

**IO_KINDS** (line 42):
```python
IO_KINDS = ("correction", "summary", "format_markdown", "format_email", "format_coding_prompt", "classify_intent", "voice_note", "tagging", "assistant", "classify_pipeline")
```

**AUTO_RETRY_KINDS** (line 35): NOT to be modified per issue.

**CPU_KINDS** (line 43): `("rediarize", "voice_match")` — NOT to be modified.

**Dispatch** (line ~306+): `run_llm_job` dispatches on job.kind. Per issue, NOT adding dispatch for "voice_dump" yet (that's #284).

**test_io_cpu_pools_partition_valid_kinds**: Must still pass. Since `voice_dump` is an I/O kind, adding it to both VALID_KINDS and IO_KINDS is correct. CPU_KINDS + IO_KINDS must exactly partition VALID_KINDS. Verify after change.

### 4. services/settings.py

**bulk_defaults** (lines 53-61):
```python
"bulk_defaults": {
    "provider": "moonshine",
    "model": "",
    "language": "auto",
    "diarize": False,
    "auto_correct": True,
    "kind": "meeting",
    "num_speakers": None,
},
```

**Issue says**: "add 'voice_dump' to bulk_defaults.kind allowed values." The bulk_defaults is a default-value dict, not a validation list. The actual kind validation happens in app.py (bulk import, single-file upload, retranscribe). The bulk_defaults.kind is just the default kind — not changing it (default meeting is correct). The "allowed values" are maintained in app.py validation sites.

### 5. app.py — complete kind-switch/validation site inventory

| Line | Site | Current values | Must add voice_dump? |
|------|------|---------------|---------------------|
| 1444 | Single-file upload validation | `("meeting", "dictation", "voice_note", "auto")` | YES |
| 1524 | Bulk import per-file override validation | `("meeting", "dictation", "voice_note", "auto")` | YES |
| 1540 | Bulk import global kind validation | `("meeting", "dictation", "voice_note", "auto")` | YES |
| 2074 | Retranscribe kind validation | `("meeting", "dictation", "voice_note", "auto")` | YES (issue doesn't name this site!) |
| 1150 | Diarization force-off | `("dictation", "voice_note")` | YES (lightest diarization = off) |
| 422-452 | `_dictation_job_fields` | dictation, voice_note, else | YES (add voice_dump_job: None in else/default branch) |

**`_dictation_job_fields`** (lines 412-452):
- Line 422: `if kind == "dictation":` → format_*_jobs, classify_intent_job
- Line 439: `if kind == "voice_note":` → voice_note_job
- Line 447-452: default (meeting, and now voice_dump) → all nulls + tagging_job

For voice_dump, the issue says to add `voice_dump_job: None` as a stub. Currently the default return (lines 447-452) already returns `"voice_note_job": None`. For voice_dump specifically, a separate field `voice_dump_job` is needed. This should be added:
- In dictation branch (line 436): add `"voice_dump_job": None`
- In voice_note branch (line 444): add `"voice_dump_job": None`
- In default branch (line 450): add `"voice_dump_job": None`

Wait — per the serializer contract (test_all_kinds_have_same_job_field_names), ALL branches must return the same field names. So this field must be present in EVERY return dict.

### 6. static/rack.js

**Bulk kind picker** (lines 2759-2764):
```html
<option value="auto" ...>Auto</option>
<option value="meeting" ...>Meeting</option>
<option value="dictation" ...>Dictation</option>
<option value="voice_note" ...>Voice Note</option>
```

**Per-file kind picker** (lines 2820-2825):
```html
<option value="auto" ...>Auto</option>
<option value="meeting" ...>Meeting</option>
<option value="dictation" ...>Dictation</option>
<option value="voice_note" ...>Voice Note</option>
```

**Issue says**: Add option with label `"Audit / stream-of-consciousness dump"` → value `"voice_dump"`. Add to both pickers (bulk defaults and per-file).

**Other kind references (no change needed for this sub-issue)**:
- Line 3703: `if (detailData && detailData.kind === 'dictation') tabs.push('format');`
- Line 3704: `if (detailData && detailData.kind === 'voice_note') tabs.push('notes');`
- Line 4684: `const kindLabel = kind === 'voice_note' ? 'Voice note' : ...`
- Line 4733: Diarize button hidden for dictation
- Line 4832: Format tab display for dictation
- Line 4842: Notes tab display for voice_note
- Line 4873: Kind cycling (meeting → dictation → voice_note → meeting)

None of these need updating — the Dump Review tab is in sub-issue #287. Voice_dump transcripts will show default tabs (Transcript, Corrected, Summary) with no special tabs until then.

---

## Sibling sweep

Checked every kind-switch/kind-validation site across the entire codebase (see table in section 5 above). The issue names most but not all. **One additional site found**: app.py line 2074 (retranscribe kind validation) uses the same tuple as line 1444 and needs the same update. No other undisclosed siblings found.

---

## Plan-vs-reality issues

1. **Issue says "services/settings.py — add voice_dump to bulk_defaults.kind allowed values"**: The bulk_defaults is a default-value dict, not a validation list. The actual "allowed values" are maintained in app.py's kind validation tuples. No change needed in settings.py beyond what's already there.

2. **Issue says "services/transcription.py — diarization default branch"**: There is no diarization logic in transcription.py. The diarization force-off for dictation/voice_note lives in app.py line 1150. Adding the voice_dump branch to transcription.py's summarize method (same stub as voice_note) and adding voice_dump to app.py's diarization force-off tuple together achieve what the issue describes.

3. **Issue says "add voice_dump to bulk import kind validation" but doesn't name single-file upload (line 1444) or retranscribe (line 2074)**: All three validation sites must be updated for consistency. Doing so.

4. **`_dictation_job_fields` contract**: The field `voice_dump_job` must appear in ALL three return branches (dictation, voice_note, default) to maintain the uniform field-name contract pinned by `test_all_kinds_have_same_job_field_names`.
