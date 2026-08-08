# Issue #193 Investigation: populate_fts() N+1 connection overhead

## Target

**Issue #193** (standalone) — `populate_fts()` opens a new database connection per
completed transcript row, causing 2N connections for N rows.

## Root cause (corrected)

### The silent no-op — external-content FTS5 rowid pitfall

`transcripts_fts` is an FTS5 external-content table (`content='transcripts'`).
On external-content tables, **non-MATCH queries (rowid lookups, scans) read
from the content table, not the FTS index**. `SELECT 1 FROM transcripts_fts
WHERE rowid = ?` returns a row for every transcript that exists — even when
the FTS index is empty. So the per-row existence check always returns "exists,"
and the anti-join always returns zero rows.

Both the old code (per-row SELECT 1 check → `if exists: continue`) and the
original anti-join (`NOT EXISTS (SELECT 1 FROM transcripts_fts WHERE
rowid = t.id)`) had this bug. PR #190's backfill never actually ran.
This PR (originally #205, first revision) optimized a silent no-op into
a faster silent no-op.

### Correct fix: _docsize shadow table

FTS5 maintains a `transcripts_fts_docsize(id INTEGER PRIMARY KEY, sz BLOB)`
shadow table when `columnsize=1` (the default). This is the reliable
indicator of FTS index membership. Anti-join against this table:

```sql
NOT EXISTS (SELECT 1 FROM transcripts_fts_docsize d WHERE d.id = t.id)
```

### Duplicate FTS entries from trigger + explicit INSERT

The old code always executed both `UPDATE transcripts SET segment_text` (which
fires `trg_transcripts_fts_update`, inserting an FTS row) AND an explicit
`INSERT INTO transcripts_fts`. This created two FTS entries per transcript
with NULL segment_text. `INSERT OR IGNORE` doesn't help — FTS5 has no unique
constraint. Two entries for same rowid mark the index malformed per
`integrity-check`.

Fix: when segment_text is NULL, only UPDATE (let trigger handle FTS insert).
When segment_text already exists, use explicit INSERT.

## Current code (worktree)

### `populate_fts()` — database/__init__.py

After fix (final):
- **Line 426+**: Single query with `NOT EXISTS (SELECT 1 FROM transcripts_fts_docsize d WHERE d.id = t.id)` — correct anti-join
- **Line 446+**: Single `engine.begin()` transaction for all rows
- **Line 458**: `if not existing_st:` — UPDATE path (trigger indexes)
- **Line 462**: `else:` — explicit INSERT path (one FTS entry)

### Call sites

Single call site: `init_db()` line 567.

### Sibling sweep

Full codebase scan found zero other N+1 connection pattern instances.

## Phase 1.5 check

Not needed. Startup backfill, not a job/state completion path.

## Test coverage

1. **test_populate_fts_restores_deleted_index** — creates transcripts, wipes
   FTS index with `delete-all`, clears segment_text on one row, runs
   `populate_fts`, verifies MATCH queries find both rows. Exercises both
   UPDATE (trigger) and explicit INSERT paths.
2. **test_populate_fts_idempotent** — verifies no duplicate entries on repeat
   calls.
3. **test_populate_fts_empty_db_is_noop** — verifies no crash on empty DB.
