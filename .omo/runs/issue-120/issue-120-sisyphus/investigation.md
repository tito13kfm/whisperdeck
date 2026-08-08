# Investigation: Issue #120 — Silent diarization failure in _finalize_if_done

**Target**: Issue #120 (standalone, not a tracking issue)
**Worktree**: `C:/Claude/whisperdesk-issue-120-sisyphus` (branch `issue-120-sisyphus`, off `origin/master` at `cfa3b78`)
**Main checkout**: `C:/Claude/whisperdesk` (branch `master`, at `290e5f7`)

## Summary

When diarization fails in `_finalize_if_done` (`services/queue.py:558-559`), the exception is caught with only a `print()` statement. The transcript finalizes as "completed" with no speaker labels and zero user-facing indication that diarization was attempted but failed. The same silent-failure pattern exists in the inline transcription path (`app.py:1395-1398`).

## The Bug: queue.py `_finalize_if_done` (lines 486-619)

### Relevant code (lines 527-587):

```python
speaker_count = None
diarization_method = None
if transcript.diarize_requested and segments and transcript.audio_path:
    db.rollback()
    # ... fetch user settings ...
    try:
        merged, speaker_count, diarization_method = await diarization_service.diarize_and_merge(...)
        segments = merged
    except Exception as e:
        print(f"[queue] non-fatal diarization failure for transcript {transcript_id}: {e}")
    # Re-fetch transcript after await
    db.expire_all()
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript or transcript.status == "cancelled":
        return

# Finalization (lines 574-587):
clear_relabel_history(db, transcript.id)
transcript.segments = segments        # original, undiarized segments
transcript.full_text = full_text
transcript.duration_seconds = duration_seconds
transcript.status = new_status        # "completed" or "partial"
if speaker_count is not None:         # skipped because speaker_count is still None
    transcript.speaker_count = speaker_count
    transcript.diarization_method = diarization_method
transcript.updated_at = utcnow_naive()
db.commit()
```

### What goes wrong

1. `new_status` is set at lines 520-525 (before diarization block): "completed" if no failed chunks.
2. Diarization fails → except block only prints → `speaker_count` and `diarization_method` stay `None` → line 584 guard skips setting those fields.
3. Transcript finalizes as "completed" with undiarized segments (no speaker labels).
4. User sees green "done" badge in Queue UI, no error, no indication diarization was attempted.
5. Only way to discover the failure: check server logs.

## Sibling Sweep: Same pattern in app.py inline path

### app.py `_run_transcription_pipeline` (lines 1382-1398):

```python
if diarize and transcript.segments:
    try:
        merged, speaker_count, diarization_method = await diarization_service.diarize_and_merge(...)
        transcript.segments = merged
        transcript.speaker_count = speaker_count
        transcript.diarization_method = diarization_method
        db.commit()
    except Exception as e:
        print(f"[diarization] non-fatal failure for transcript {transcript.id}: {e}")
```

This path has the same bug but with a different timing: `transcript.status = "completed"` is set at `services/transcription.py:108` inside `TranscriptionService.transcribe()`, BEFORE the diarization block runs. So when diarization fails:
- The transcript is already "completed"
- No speaker labels
- No `diarization_method` recorded
- No error message
- Same silent failure as queue.py

### llm_jobs.py re-diarize path (lines 636-659) — NOT buggy

```python
except Exception as e:
    _finish(db, job, "failed", str(e))
```

This path already marks the LlmJob as permanently failed with the error message. The transcript's existing data is not modified. This is correct behavior — the user requested re-diarization, it failed, the job shows "failed" in the Queue UI. No fix needed here.

### No other callers

`diarize_and_merge()` has exactly 3 callers: `app.py:1384`, `queue.py:552`, `llm_jobs.py:637`. The first two are buggy, the third is fine.

## Transcript model fields available

From `database/__init__.py:33-83`:

| Field | Type | Used for |
|---|---|---|
| `status` | String(32) | "completed", "partial", "failed", "cancelled" |
| `error` | Text, nullable | General error message |
| `diarization_method` | String(32), nullable | "pyannote", "heuristic", "live_stereo", NULL |
| `speaker_count` | Integer, default=0 | Number of speakers |

No dedicated `diarization_error` field exists. The `error` field is the general-purpose failure field and is already passed to the frontend by `_transcription_queue_entry` (app.py:2995-3018, line `"error": t.error`).

## Queue UI rendering

From `loadQueue()` in `static/rack.js`:
- Status "completed" → green badge + full bargraph
- Status "partial" → amber badge + partial bargraph
- Status "failed" → red badge + ERR text
- If `j.error` is truthy → meta line turns `var(--red)` and shows error text
- No diarization-specific info is displayed on Queue page (only on Detail page)

## Fix Plan

Apply the same fix to both `queue.py` and `app.py`:

1. **Log full traceback** (replace `print(...)` with `traceback.print_exc()` + descriptive message)
2. **Record the failure** on the transcript:
   - `transcript.diarization_method = "failed"`
   - `transcript.error = f"Diarization failed: {str(e)}"` (or similar concise message)
3. **Change status to "partial" if it was "completed"** — makes failure visible as amber badge in Queue UI
4. **For queue.py**: also need to `db.add(transcript)` before commit since the session was expired + re-fetched

Files to change:
- `services/queue.py` — except block at line 558-559
- `app.py` — except block at line 1395-1398

Tests to add:
- `tests/test_diarization_failure.py` — test that diarization failure sets transcript.error, diarization_method="failed", and status changes to "partial" (from "completed")
