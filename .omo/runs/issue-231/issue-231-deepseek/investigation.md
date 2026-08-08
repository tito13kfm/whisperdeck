# Investigation: Issue #231 — Bulk import 1/4: Backend batch infrastructure

**Date:** 2026-07-29
**Worktree:** `C:/Claude/whisperdesk-issue-231-deepseek` (branch `issue-231-deepseek`, from `origin/master` c7eebd1)
**Main checkout:** `C:/Claude/whisperdesk` (master)

## Scope summary

Backend-only: `batch_id` column, `POST /api/bulk-transcribe`, `batch_id` filter on `GET /api/transcripts`, `bulk_defaults` in user settings. No UI changes.

## Full change site inventory (14 sites across 3 files)

### A. Database — `database/__init__.py`

| # | Line | Change |
|---|------|--------|
| A1 | 59→60 | Add `batch_id = Column(String(64), nullable=True, index=True)` after `source_transcript_id` |
| A2 | 507 | Add `"batch_id": "TEXT"` to `ensure_columns(engine, "transcripts", {...})` dict |

**Rationale for position:** After `source_transcript_id` (line 59), before `created_at` (line 60) — groups nullable metadata columns together. `String(64)` matches existing `provider`/`model` sizing. `index=True` because `GET /api/transcripts?batch_id=X` filters on it; no other column currently has explicit `index=True` but `source_transcript_id` is an FK (implicit index).

**Migration:** `ensure_columns()` (lines 299-317) handles additive nullable columns via `ALTER TABLE ADD COLUMN`. Adding to the existing dict at line 507 covers both fresh DBs (via `create_all` at line 505) and existing DBs (via `ensure_columns`).

### B. Serializers — `app.py`

| # | Line | Change |
|---|------|--------|
| B1 | 316→317 | `_serialize_transcript`: Add `"batch_id": t.batch_id or None` after `source_transcript_id` |
| B2 | 574→575 | `_serialize_transcript_summary`: Add `"batch_id": t.batch_id or None` after `"id"` |

**B2 rationale:** Issue #231's spec says only `_serialize_transcript()` gets batch_id, but `_serialize_transcript_summary` feeds the Tape Library list view at lines 625/637. Issue #234 (frontend batch grouping) will need batch_id in list rows. Adding it now is forward-compatible, costs one field, and avoids a follow-up migration. The pattern mirrors B1: `t.batch_id or None`.

### C. Pipeline — `app.py`

| # | Line | Change |
|---|------|--------|
| C1 | 1025→1026 | `_run_transcription_pipeline`: Add `batch_id: Optional[str] = None` after `source_transcript_id` |
| C2 | 1200→1201 | Chunked path: Add `transcript.batch_id = batch_id` after `transcript.source_transcript_id = source_transcript_id` |
| C3 | 1223→1224 | Inline path: Add `transcript.batch_id = batch_id` after `transcript.source_transcript_id = source_transcript_id` |

**Pattern:** Both paths set `batch_id` after the initial `db.add()`/`db.commit()` in the service layer (`services/transcription.py:43` and `:83`) and before the final metadata `db.commit()`. This matches `source_transcript_id`'s pattern — the service layer doesn't know about these metadata fields.

**Two code paths identified:**
1. Chunked (lines 1162-1207): `create_transcript_stub()` → set metadata → `db.commit()` → `create_chunk_jobs()`
2. Inline (lines 1209-1271): `transcribe()` → set metadata → `db.commit()` → diarization → auto-correct

### D. New endpoint — `app.py`

| # | Line | Change |
|---|------|--------|
| D1 | 1338→1341 | Add `POST /api/bulk-transcribe` after existing `POST /api/transcribe` (ends line 1338), before `GET /api/search` (line 1341) |

**Design:**
- Accepts `files: List[UploadFile]` (multiple, indexed 0..N-1) + `settings: str = Form(...)` (JSON) + `file_settings: str = Form(None)` (JSON array)
- Generate `batch_id`: `f"{utcnow_naive().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"`
- For each file: save to UPLOAD_DIR, call `_run_transcription_pipeline(batch_id=batch_id, ...)`, collect result
- If N=0: 400
- If kind invalid: 400 (reuse validation from line 1299)
- If provider invalid: 400 (call `get_provider()` from `backends/__init__.py:40` to validate early)
- If provider in `LOCAL_PROVIDERS` AND combined file size > 500 MB: 400
- Partial failure: set failed transcript status, include errors array, continue
- All fail: 500
- Return `{"batch_id": "...", "transcripts": [...], "errors": [...]}`

**Reuses:**
- File saving pattern from `transcribe_audio` (lines 1301-1308)
- `_run_transcription_pipeline` directly
- `kind` validation from line 1299
- Provider validation via `get_provider()` / `ProviderError`

### E. GET /api/transcripts filter — `app.py`

| # | Line | Change |
|---|------|--------|
| E1 | 1360 | `list_transcripts`: Add `batch_id: str \| None = Query(None)` after `q` param |
| E2 | 609 | `_build_recent_transcripts`: Add `batch_id: str \| None = None` param after `query` |
| E3 | 616 | Query branch: Add `.filter(Transcript.batch_id == batch_id)` (conditional on batch_id) |
| E4 | 629 | Non-query branch: Add `.filter(Transcript.batch_id == batch_id)` (conditional on batch_id) |

**Two branches in `_build_recent_transcripts`:**
1. FTS5 search path (lines 610-625): Filter on `Transcript.id.in_(matching_ids)` at line 616. Add `.filter(Transcript.batch_id == batch_id)` when provided.
2. Direct query path (lines 627-633): Filter on `user_id` at line 629. Add `.filter(Transcript.batch_id == batch_id)` when provided.
3. `batch_id` is threaded through the function to `_build_recent_transcripts` where both branches filter.

### F. Settings — `services/settings.py`

| # | Line | Change |
|---|------|--------|
| F1 | 31→32 | Add `"bulk_defaults": {...}` to `DEFAULT_SETTINGS` dict, before closing `}` |

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

**Validation:** `update_user_settings()` at line 113 filters against `DEFAULT_SETTINGS` keys — adding `bulk_defaults` auto-validates. SQLite's `json_patch()` merges nested objects, so partial updates work.

## Sibling sweep

### Other models with nullable indexed string columns?
**None.** Scanned all models in `database/__init__.py` (User, Transcript, TranscriptionJob, Summary, VoiceNote, LlmJob, RelabelHistory, HotwordEntry, TranscriptTag, VoiceProfile, VoiceClip, ProviderConfig). `batch_id` on Transcript is the first nullable indexed string column.

### Serializer summary batch_id?
**Yes, included** (B2). Not in the issue's spec, but forward-compatible for issue #234 (frontend batch grouping on Tape Library). Adding now costs one field and avoids a future migration.

### Retranscribe batch_id handling?
**Decision: `batch_id=None` (don't inherit).** POST `/api/transcripts/{id}/retranscribe` (line 1697) calls `_run_transcription_pipeline` without `batch_id`. The default `None` means retranscribed files get `batch_id=NULL`. A retranscribe creates a new row for A/B comparison; inheriting would silently pull it into batch operations. The parent's `batch_id` is available at line 1684 (`t.batch_id`) if needed later.

### All Transcript() instantiation sites?
**Two production sites** in `services/transcription.py`:
1. `create_transcript_stub()` at line 43 — chunked path
2. `transcribe()` at line 83 — inline path

Both called from `_run_transcription_pipeline`. `batch_id` is set on the transcript object AFTER the service call and BEFORE the metadata `db.commit()`. Test sites (97 across 35 files) create Transcript with explicit kwargs — `batch_id` defaults to `None`, all safe.

### Other settings consumers?
`get_user_settings()` (line 87) merges stored over defaults. No other consumer touches `bulk_defaults` yet.

### Completion-race check?
**Not applicable.** The bulk endpoint processes files through `_run_transcription_pipeline` which sets transcript status to "completed" and then enqueues auto-correct. No side-effect-after-completion race path.

## Provider validation

- `LOCAL_PROVIDERS = ("builtin", "moonshine")` at `backends/__init__.py:37`
- `PROVIDER_REGISTRY` at lines 24-33 has 8 providers
- Provider name validation via `get_provider()` which raises `ProviderError`
- Single file size check at `app.py:1142` (`os.path.getsize(save_path)`) — no combined-size pattern exists. The bulk endpoint needs new logic.
- 500 MB threshold: no existing constant. Define inline in the endpoint.

## Test infrastructure

| Fixture | Line | Description |
|---------|------|-------------|
| `db_session` | conftest.py:72-83 | Fresh SQLite per test |
| `client` | conftest.py:87-122 | TestClient, authenticated as testuser/testpass123, CSRF token attached |

**Multipart upload pattern** (from existing tests):
```python
files={"file": ("name.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")}
```

**Multi-file upload** (for bulk test):
```python
files=[
    ("files", ("a.wav", io.BytesIO(b"fake"), "audio/wav")),
    ("files", ("b.wav", io.BytesIO(b"fake"), "audio/wav")),
]
```

**Wave file helpers:** `tests/test_audio_prep_stereo.py:14-22` (stereo WAV) and `tests/test_speaker_naming.py:332-339` (mono tone).

## What the issue gets right

- Column definition, type, and index: correct
- Serializer field addition: correct (just one extra field for the summary serializer, forward-compatible)
- Migration approach: correct (SQLAlchemy + ensure_columns)
- Test fixtures: correct (client fixture exists, multipart patterns exist)
- DEFAULT_SETTINGS validation: correct (auto-validated by existing update_user_settings)

## What the issue misses

- **_serialize_transcript_summary** (B2): Not in the spec, but needed for list view batch grouping (#234). Added here.
- **Retranscribe batch_id**: Not addressed. Decision documented above (batch_id=None).
- **Combined file size validation pattern**: No existing pattern — designed inline.
- **Early provider validation**: The transcribe handler doesn't validate provider early (relies on pipeline to fail), but bulk should validate before saving all files.