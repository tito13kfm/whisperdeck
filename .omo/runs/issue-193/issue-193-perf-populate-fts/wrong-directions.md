# Wrong Directions — Issue #193

## Key finding: external-content FTS5 rowid pitfall

A `SELECT ... FROM transcripts_fts WHERE rowid = ?` on an external-content FTS5
table reads from the content table, not the index. It returns a row for every
existing transcript regardless of FTS index state. Both the original code's
per-row existence check and the first revision's anti-join fell into this trap.

The fix uses `transcripts_fts_docsize` — the shadow table FTS5 maintains for
index membership tracking. `NOT EXISTS (SELECT 1 FROM transcripts_fts_docsize
d WHERE d.id = t.id)` correctly identifies transcripts without FTS entries.

## Issue body's fix was wrong for the same reason

The issue's suggested SQL (`NOT EXISTS (SELECT 1 FROM transcripts_fts WHERE
rowid = t.id)`) has the same pitfall — it always returns zero missing rows.
This was not caught because no test ever verified the backfill actually ran.

## Oracle missed it too

Oracle (Phase 3.75) returned APPROVE on the original diff. The SQL is
syntactically valid — the semantic quirk of external-content mode can only
be caught empirically. Same blind spot as the tests and human review.

## FTS5 DELETE-ALL command discovered

`INSERT INTO transcripts_fts(transcripts_fts) VALUES('delete-all')` is the
canonical FTS5 command to wipe the index. It does not touch the content table
and sets docsize to 0. This made the restore test possible.

## INSERT OR IGNORE doesn't prevent duplicate FTS entries

FTS5 has no unique constraint. `INSERT OR IGNORE` never ignores anything on
FTS5 tables. Two inserts for the same rowid create duplicate entries, and
FTS5's `integrity-check` reports MALFORMED. The fix uses one entry path per
transcript: UPDATE (trigger) when segment_text is NULL, explicit INSERT when
it's already set.

## DDL auto-commit in SQLite (from earlier attempt)

(From the original test development) DDL statements (DROP/CREATE TRIGGER)
auto-commit in SQLite, committing any pending DML. This made trigger-based
test setups unreliable — the root cause of the earlier test failures.
Resolved by using `delete-all` instead of trigger manipulation.
