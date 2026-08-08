# Wrong Directions — Issue #206

## delete-all on external-content FTS5

**Claim**: The issue's suggested approach and the existing test both assumed `delete-all` works on external-content FTS5 tables.

**Reality**: `INSERT INTO transcripts_fts(transcripts_fts) VALUES('delete-all')` permanently corrupts external-content FTS5 state. Subsequent `delete` commands (even with exact column values) fail with "database disk image is malformed". After `delete-all`, `integrity-check` also fails even after repopulating the index. Only `rebuild` restores the index.

**Impact**: `test_populate_fts_idempotent` had to be rewritten to test the no-op path instead of the backfill path. The backfill path is still tested by `test_populate_fts_restores_deleted_index`, but only checks MATCH queries (not integrity) since the corruption is permanent.

**Recommendation**: Add a warning to AGENTS.md or the FTS trigger comments that `delete-all` is incompatible with external-content FTS5.

## OLD.segment_text is NULL for ORM-created rows

**Claim**: The initial trigger fix used `OLD.segment_text` in the delete command.

**Reality**: The `segment_text` column is added by `ensure_columns` and is NULL for ORM-created Transcript rows. The INSERT trigger stores the computed segment_text from JSON segments. Using `OLD.segment_text` in the delete creates a mismatch — the delete with NULL doesn't match the FTS entry's non-empty tokens.

**Fix**: Use `COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') FROM json_each(OLD.segments)), '')` in the delete, mirroring the INSERT trigger's computation.

**Oracle found**: This during Phase 3.75 regression pass (ses_05181bcabffeTr2aUrBhrdp9qq).

## Pre-existing: content table segment_text vs FTS mismatch

**Finding**: When a transcript has non-empty segments, the INSERT trigger stores computed segment_text in the FTS index. But the content table's `segment_text` column remains NULL. This means `integrity-check rank=1` (which compares FTS against the content table) already fails on freshly inserted transcripts with segments.

**Impact**: `test_fts_update_old_segment_terms_removed` cannot run integrity-check on transcripts with segments. It verifies MATCH behavior instead. This is a pre-existing issue not addressed here.
