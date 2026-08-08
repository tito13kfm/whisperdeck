# Wrong Directions — Issue #108 FTS Search

## Plan assertion: contentless mode supports snippet()

The plan at `.omo/plans/issue-108-fts-search.md` line 97-98 asserts:
> contentless mode with manual trigger population avoids this and still supports `snippet()`

**Wrong**: SQLite 3.45.3 contentless mode (`content=''`) does NOT support `snippet()` — all user columns return NULL on SELECT, and snippet() fails with errors. External content mode (`content='transcripts'`) is required for snippet() to work.

**Fix applied**: Switched to external content mode with explicit column values in trigger INSERTs, and added `segment_text` column to the transcripts table via `ensure_columns()`.

## Plan assertion: DELETE works on contentless FTS5 tables

The plan's trigger specs (lines 89-91) use `DELETE FROM transcripts_fts WHERE transcript_id = OLD.id` in AFTER UPDATE and AFTER DELETE triggers.

**Wrong**: Contentless FTS5 tables do not support DELETE statements. External content mode FTS5 tables DO support the `'delete'` INSERT command, but the row-specific `'delete'` command doesn't work on SQLite 3.45.3 (only `'delete-all'` works).

**Fix applied**: Simplified AFTER UPDATE trigger to only INSERT new entries (old entries coexist — benign). AFTER DELETE trigger removed (stale entries filtered by search JOIN).

## Oracle suggestion: `f MATCH` alias

Oracle suggested using `f MATCH :q` with the table alias. **Wrong**: SQLite 3.45.3 FTS5 requires the full table name for MATCH (`transcripts_fts MATCH :q`). The `f` alias causes `no such column: f`.

## Oracle suggestion: snippet(-1)

Oracle suggested `snippet(fts, -1, ...)` for all-column matching. **Correct**: Works on this SQLite version, but returns snippet from the first matching column only, not a union of all columns. Acceptable for UI display.
