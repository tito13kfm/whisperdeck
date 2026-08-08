# Self-Audit for Issue #193

## Investigation.md promises (revised)

[x] Single anti-join (correct: _docsize, not external-content table) — delivered at database/__init__.py:426+
[x] Single connection replaces 2N per-row connections — delivered: single engine.connect() for read, single engine.begin() for write
[x] One FTS entry per transcript — delivered: UPDATE path (trigger) vs explicit INSERT path, not both
[x] Idempotent (safe to call on every startup) — delivered: _docsize anti-join + single entry per row
[x] Segment text extraction preserved — delivered

## Tests added

[x] test_populate_fts_restores_deleted_index — confirmed at tests/test_search.py:480
[x] test_populate_fts_idempotent — confirmed at tests/test_search.py:533
[x] test_populate_fts_empty_db_is_noop — confirmed at tests/test_search.py:550

## Full suite

[x] Full test suite passes: 556 passed, 5 deselected, 0 failures (51.34s)

## Oracle verdict (Phase 3.75) — on ORIGINAL (broken) diff

APPROVE — missed the external-content rowid pitfall. Same blind spot as tests
and human review: the SQL is syntactically valid, just semantically wrong for
external-content mode. Only fails empirically.

## Post-review corrections

[x] Anti-join: transcripts_fts → transcripts_fts_docsize (external-content mode pitfall)
[x] Duplicate FTS entries: only one INSERT per row (trigger vs explicit path)
[x] Restore test: delete-all + MATCH verification

## PR

https://github.com/tito13kfm/whisperdeck/pull/205 (force-pushed with corrections)
