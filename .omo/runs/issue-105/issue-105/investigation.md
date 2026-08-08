# Investigation — Issue #105

## Issue summary

PATCH `/api/transcripts/{id}` replaces segments wholesale without clearing relabel history. Existing inverse patches are index-based against old segments. After wholesale replacement, `undo_last_relabel` stamps stale speaker labels onto unrelated lines.

## Verified locations

### The bug — `app.py:1544-1545` (PATCH endpoint)

```python
# line 1544-1545
if "segments" in data:
    t.segments = data["segments"]
```

No `clear_relabel_history` call before the assignment. `app.py` does not import `clear_relabel_history` at all (imports only `record_relabel` and `latest_relabel` from `services.relabel` at line 56).

**Issue's line numbers are slightly stale**: issue says `app.py:1250-1251` but the actual PATCH endpoint starts at line 1535 with the segment assignment at 1545. The line numbers in the issue appear to refer to an older version of app.py (the transcribe endpoint at 1250 is unrelated).

### Already guarded — `services/llm_jobs.py:489-491` (rediarize worker)

```python
# lines 489-491
from services.relabel import clear_relabel_history
clear_relabel_history(db, transcript.id)
transcript.segments = merged
```

CONFIRMED — calls `clear_relabel_history` before wholesale replacement.

### Already guarded — `services/queue.py:550-552` (queue finalize)

```python
# lines 550-552
from services.relabel import clear_relabel_history
clear_relabel_history(db, transcript.id)
transcript.segments = segments
```

CONFIRMED — calls `clear_relabel_history` before wholesale replacement. (Lines differ slightly from issue's 550-551; actual lines 550-552 in current code.)

### `clear_relabel_history` — `services/relabel.py:66-74`

Function deletes all `RelabelHistory` rows for the transcript. Docstring: "Must be called by anything that regenerates transcript.segments wholesale."

### `undo_last_relabel` — `app.py:1791-1828`

Applies index-based inverse patches. After stale history, these patches would target wrong indices in the new segment list.

## Sibling sweep — all `.segments =` assignments

### Wholesale replacements (completely new array)

| File:Line | What replaces segments | Has `clear_relabel_history`? |
|-----------|----------------------|---------------------------|
| `app.py:1195` | `transcript.segments = merged` (diarization during initial transcription) | NO (no relabel history exists yet) |
| **`app.py:1545`** | **`t.segments = data["segments"]`** (PATCH endpoint) | **NO — THIS IS THE BUG** |
| `services/queue.py:552` | `transcript.segments = segments` (queue finalize) | YES (lines 550-551) |
| `services/llm_jobs.py:491` | `transcript.segments = merged` (rediarize worker) | YES (lines 489-490) |
| `services/transcription.py:114` | `transcript.segments = [...]` (inline transcription) | NO (brand-new transcript, no history) |

### Non-wholesale replacements (individual modifications to existing segments)

These are correct to NOT call `clear_relabel_history` — they record their own inverse patches via `record_relabel`:

| File:Line | Operation | 
|-----------|----------|
| `app.py:1728` | speaker rename |
| `app.py:1784` | retag |
| `app.py:1813` | relabel-undo |
| `services/llm_jobs.py:559` | voice_match |

**No siblings missed** — all five wholesale replacements identified, and only the PATCH endpoint is unguarded.

## Fix plan

Two lines in `app.py`:

1. Line 56: Add `clear_relabel_history` to the import:
   ```python
   from services.relabel import record_relabel, latest_relabel, clear_relabel_history
   ```

2. Line 1544-1545: Add the call before the assignment:
   ```python
   if "segments" in data:
       clear_relabel_history(db, t.id)
       t.segments = data["segments"]
   ```

## Issue's suggested fix accuracy

The issue's fix suggestion is correct in substance (add `clear_relabel_history` call before the assignment) but slightly stale:
- Wrong line numbers (1250-1251 vs actual 1544-1545)
- The import change is implicit but necessary (app.py doesn't currently import `clear_relabel_history`)

## Phase 1.5: completion-race check

NOT applicable. This is a PATCH endpoint, not a job/state completion path. No oracle consult needed.
