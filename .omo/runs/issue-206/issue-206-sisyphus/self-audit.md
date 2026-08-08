# Self-Audit for Issue #206

## Investigation promises

[x] Phase 1 investigation.md written — confirmed at .omo/runs/issue-206/issue-206-sisyphus/investigation.md
[x] Sibling sweep: only two triggers on transcripts (insert + update). No other FTS tables. No other call sites need changes.
[x] Trigger fix: delete-before-insert using computed OLD.segments — database/__init__.py:562-576
[x] Oracle finding addressed: OLD.segment_text changed to computed value from OLD.segments — confirmed at database/__init__.py:568-569

## Tests

[x] test_fts_update_integrity_check_passes — mutation check: fails with old trigger (double rowid → malformed), passes with fix. Confirmed via red-green cycle.
[x] test_fts_update_old_terms_removed — mutation check: fails with old trigger (old term still matches), passes with fix. Confirmed via red-green cycle.
[x] test_fts_update_idempotent — mutation check: fails with old trigger (double update → malformed), passes with fix. Confirmed via red-green cycle.
[x] test_fts_update_old_segment_terms_removed — new test for Oracle-found edge case (segments with non-NULL computed text). Old terms removed, new terms present.
[x] test_populate_fts_idempotent — updated to test no-op path (integrity-check passes after populate_fts on already-indexed DB). Rank-1 integrity-check added.
[x] test_fts_trigger_update_syncs_index — docstring updated to reflect new behavior (old terms removed).
[x] test_populate_fts_restores_deleted_index — unchanged, passes (698a5f4f5dae6ac0fdb707a4de2cafaf... wait, this test doesn't check integrity after delete-all; pre-existing limitation).

## Acceptance criteria walk

[x] "After updating a transcript's text, integrity-check passes" — test_fts_update_integrity_check_passes (full_text update → integrity-check ok)
[x] "A term present only in the pre-update text no longer matches" — test_fts_update_old_terms_removed (MATCH old term → 0 rows)
[x] "Regression test must construct the broken state first" — red-green cycle: tests ran against old code (failed), then against fixed code (passed)

## Suite results

[x] Full test suite: 589 passed, 1 skipped (e2e) — tests/
[x] No e2e tests changed — this is a backend-only change, no UI surface

## Main repo check

[x] git -C main diff --stat: clean (no unintended edits to main checkout)

## Oracle verdict

[x] Oracle ran (ses_05181bcabffeTr2aUrBhrdp9qq), flagged NEEDS-DISCUSSION for OLD.segment_text vs computed segments mismatch
[x] Fix applied: delete now uses COALESCE((SELECT group_concat(...) FROM json_each(OLD.segments)), '') matching INSERT trigger
[x] Re-ran full suite after fix: all pass
