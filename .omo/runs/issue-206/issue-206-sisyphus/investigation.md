# Issue #206 — FTS5 update trigger corrupts index integrity

**Target**: #206 (standalone)
**Branch**: issue-206-sisyphus
**Worktree**: C:/Claude/whisperdesk-issue-206-sisyphus
**Main repo**: C:/Claude/whisperdesk
**Base**: origin/master @ 650ba6d

## Summary

The `trg_transcripts_fts_update` trigger (database/__init__.py:562-570) inserts a new FTS row on every transcript UPDATE without deleting the old one. With external-content FTS5, duplicate rowids leave the index failing `integrity-check`. The existing code comment at lines 554-561 already acknowledges this.

## Files and functions in scope

| File | Function/Trigger | Lines |
|------|-----------------|-------|
| `database/__init__.py` | `trg_transcripts_fts_update` | 562-570 |
| `database/__init__.py` | `trg_transcripts_fts_insert` | 545-553 |
| `database/__init__.py` | `populate_fts` | 410-470 |
| `database/__init__.py` | FTS5 table creation | 534-543 |
| `tests/test_search.py` | FTS trigger tests | 342-403 |
| `tests/test_search.py` | `populate_fts` tests | 477-574 |
| `services/search.py` | `search_transcripts` / `search_transcripts_snippets` | 1-188 |

## Root cause

The trigger on line 562:
```sql
CREATE TRIGGER IF NOT EXISTS trg_transcripts_fts_update
AFTER UPDATE ON transcripts BEGIN
  INSERT INTO transcripts_fts(rowid, title, full_text, corrected_text, segment_text)
  VALUES (NEW.id, NEW.title, NEW.full_text, NEW.corrected_text, ...);
END
```

Every UPDATE to a transcript row inserts a new FTS entry with the same rowid. FTS5 external-content mode with `content='transcripts'` requires one entry per rowid. The fix inserts a `delete` command first:

```sql
INSERT INTO transcripts_fts(transcripts_fts, rowid, title, full_text, corrected_text, segment_text)
VALUES('delete', OLD.id, OLD.title, OLD.full_text, OLD.corrected_text, OLD.segment_text);
```

Note: The comment at lines 555-557 claims the FTS5 delete path "was unreliable here" - this was likely a misdiagnosis. With external-content FTS5 (`content='transcripts'`), the `'delete'` command works correctly against any FTS5 table. The comment references "needs exact old values" which is a non-issue: the trigger has access to `OLD.*` references precisely.

## Sibling sweep

- **Other triggers on `transcripts`**: Only `trg_transcripts_fts_insert` (INSERT) and `trg_transcripts_fts_update` (UPDATE). No other triggers. No DELETE trigger exists (external-content mode auto-deletes on content row deletion).
- **Other FTS tables**: `transcripts_fts` is the only FTS5 virtual table in the schema.
- **Other update paths with index duplication risk**: None. Only the single `trg_transcripts_fts_update` trigger handles UPDATE sync.
- **`populate_fts`**: Already uses `transcripts_fts_docsize` for idempotency checks and anti-join. No change needed there - the trigger fix is self-contained.
- **INSERT trigger**: Correct as-is - each INSERT creates exactly one row.

## Call sites that trigger the bug

Every UPDATE to a `transcripts` row triggers the bug. Call sites include:
1. Transcription completion (services/transcription.py:135, services/queue.py finalize)
2. LLM correction completion writing `corrected_text` (services/llm_jobs.py)
3. Diarization writing `segments` (services/diarization.py)
4. Speaker relabeling (services/relabel.py)
5. Any manual PATCH via API endpoint (`PATCH /api/transcripts/{id}`)
6. `populate_fts()` UPDATE branch (database/__init__.py:456-459) - only fires on first backfill

## Issue snippet accuracy

The issue's analysis is accurate. `INSERT INTO transcripts_fts(transcripts_fts) VALUES('integrity-check')` does fail with duplicate rowids (reproduced by the issue author). `MATCH` queries still return correct results because FTS5 matches rowids in its b-tree regardless of duplication.

The suggested fix (option 1: delete before insert) is correct. The comment's claim about unreliability on SQLite 3.45.3 is no longer relevant - current Python sqlite3 reports version 3.50.4.

## Acceptance criteria

1. After updating a transcript's full_text or corrected_text, `integrity-check` passes.
2. A term present only in the pre-update text no longer matches the transcript via FTS5 MATCH.
3. Regression test must construct the broken state first (update a transcript, verify old-term matches on current code, then fix, then re-verify old-term no longer matches).

SQLite version: 3.50.4 — `'delete'` command is well-supported.
