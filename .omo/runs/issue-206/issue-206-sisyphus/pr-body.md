Fix the FTS5 update trigger to delete the old FTS row before inserting the new one, keeping one entry per rowid in the external-content table.

**Before**: `trg_transcripts_fts_update` inserted a new FTS row on every transcript UPDATE without removing the old one. Duplicate rowids failed `integrity-check` and stale terms from old text versions persisted in search results.

**After**: The trigger issues an FTS5 `delete` command using exact column values (including computed segment_text from `OLD.segments`) before inserting the new row.

**Tests (red-green cycle verified)**:
- `test_fts_update_integrity_check_passes`: integrity-check passes after UPDATE
- `test_fts_update_old_terms_removed`: old terms no longer match after UPDATE
- `test_fts_update_idempotent`: double UPDATE keeps one entry (docsize=1)
- `test_fts_update_old_segment_terms_removed`: old segment-only terms removed

Full suite: 589 passed, 1 skipped (e2e).

Closes #206