# Investigation: Issue #285 — Voice dump: API endpoints + serialization

## Target
Issue #285, standalone, from tracking issue #261. Sub-issues #283 (schema) and #284 (LLM chain) are complete.

## Worktree paths
- Main repo: `C:/Claude/whisperdesk` (master)
- Worktree: `C:/Claude/whisperdesk-sisyphus-285` (issue-285-sisyphus)

## Files in scope

### 1. `services/llm_jobs.py`
- **Add `enqueue_auto_voice_dump`** (lines 200-224 pattern: `enqueue_auto_voice_note`): gated on `effective_kind(transcript) == "voice_dump"`, uses `format_provider`/`format_model`, produces `kind="voice_dump"` LlmJob. Mirror identical shape except kind check and error message prefix.
- **Add `voice_dump` to `AUTO_RETRY_KINDS`** (line 35): `voice_note` is already there; `voice_dump` is an IO-bound LLM call, same failure profile, should retry.

### 2. `services/queue.py`
- **Add `enqueue_auto_voice_dump` call** at line 614 (chunked finalize path), mirroring the `enqueue_auto_voice_note` call at line 615:
  ```python
  if effective_kind(transcript) == "voice_dump":
      enqueue_auto_voice_dump(db, transcript, user_settings)
  ```
- Update import at line 600.

### 3. `app.py`
- **Import `enqueue_auto_voice_dump`** (line 53).
- **Add inline finalize call** (line 1412): mirror voice_note call at 1413.
- **Add `voice_dump` branch in `_dictation_job_fields`** (lines 412-455): new `if kind == "voice_dump":` block that populates `voice_dump_job` (all other fields None except `tagging_job`).
- **Add `_serialize_voice_dump_item`** (mirror `_serialize_voice_note` lines 2782-2795): VoiceDumpItem has additional fields: `source_job_id`, `sequence_index`. Use shape:
  ```python
  def _serialize_voice_dump_item(item: VoiceDumpItem) -> dict:
      if not item: return None
      return {
          "id": item.id, "transcript_id": item.transcript_id,
          "source_job_id": item.source_job_id,
          "sequence_index": item.sequence_index,
          "note_type": item.note_type, "title": item.title or "",
          "body": item.body or "", "structured": item.structured or {},
          "model": item.model or "", "provider": item.provider or "",
          "created_at": item.created_at.isoformat() if item.created_at else None,
      }
  ```
- **Routes to add (all mirror voice_note equivalents):**
  - `POST /api/transcripts/{id}/voice-dump/rerun` — mirror `rerun_voice_note_chain` (line 2872)
  - `POST /api/transcripts/{id}/voice-dump/save-draft` — reads `job.result_json`, patches items back
  - `POST /api/transcripts/{id}/voice-dump/finalize` — inserts `VoiceDumpItem` rows, filters discarded
  - `GET /api/transcripts/{id}/voice-dump-items` — all finalized items for one transcript
  - `GET /api/voice-dump-items` — board listing across all transcripts

### 4. `tests/test_voice_dump_route.py` (new file)
Mirror `tests/test_voice_note_route.py`. Tests for:
- Upload with `kind=voice_dump` persists kind
- voice_dump_job field appears on serialized transcript
- GET per-transcript items (empty, populated)
- GET board listing (empty, populated)
- DELETE item
- Rerun endpoint (enqueues job, rejects non-voice_dump, rejects no-key, 404 for others)
- Save-draft round-trip
- Finalize with discarded item
- Existing voice_note endpoints unaffected

### 5. `tests/test_serialize_transcript_contract.py`
- Lines 91-95 already check `voice_dump_job is None` for meeting/dictation/voice_note/voice_dump (no job yet in fixture). After fix, voice_dump transcript WITH a voice_dump job should show it as non-null.

## Sibling sweep
- `enqueue_auto_voice_note` call sites: 3 total (app.py:1413, services/queue.py:615, services/llm_jobs.py:485). All 3 need `enqueue_auto_voice_dump` added for voice_dump kind.
- `_dictation_job_fields` already has `voice_dump_job: None` in all three branches. Need to add a fourth branch for `kind == "voice_dump"` that populates it.
- `AUTO_RETRY_KINDS` includes `voice_note` but not `voice_dump`. Add it.
- `IO_KINDS` already includes `voice_dump` (confirmed line 42). No change needed.

## Existing stubs to fill
- `voice_dump_job` field exists in `_dictation_job_fields` but always returns `None`. Fill it for `kind == "voice_dump"`.
- `enqueue_auto_voice_dump` does not exist yet. Mirror `enqueue_auto_voice_note`.
- No voice_dump API routes exist. Add all 6.
