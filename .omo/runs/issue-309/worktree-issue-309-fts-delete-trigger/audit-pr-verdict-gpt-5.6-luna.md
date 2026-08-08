## PR Audit: #361 fix(database): add the missing FTS AFTER DELETE trigger and guard both delete paths (#309)   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: APPROVE

### Blocking
- None.

### Should fix
- None.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 30/30 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 1. False [x] found: none.
- Vacuous / loosened tests: none. The changed delete test checks FTS MATCH results and `transcripts_fts_docsize`; cleanup tests check membership, derived segment terms, idempotence, chunking, and the no-op gate.
- Undisclosed scope (diff vs claims): none. The UPDATE-trigger guard and related test changes are disclosed as decisions, and the sibling delete paths were swept.

### Read scope
- Focused read on `database/__init__.py` FTS/backfill and `init_db()` sections, `tests/test_search.py` changed sections, and delete/FTS sibling call sites. The diff is 528 lines, so changed hunks and relevant surrounding code were read rather than unrelated files start-to-finish.

### Summary
The trigger and cleanup preserve FTS membership and derived segment terms, handle already-indexed and never-indexed rows, and run atomically. The touched test file passed with 57 passed; the full default suite passed with 935 passed and 22 deselected. No correctness, regression, security, false-claim, or vacuous-test issue was found.

---

2026-08-06T23:27:01Z

## PR Audit: #361 fix(database): add the missing FTS AFTER DELETE trigger and guard both delete paths (#309)   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: BLOCK

### Blocking
- `.omo/runs/issue-309/worktree-issue-309-fts-delete-trigger/self-audit.md:149` contains a false `[x]` test-count claim. Failure scenario: the claimed command at the PR ref produces `935 passed, 22 deselected`, not the claimed `934 passed, 1 skipped, 22 deselected`, so the self-report's exact evidence is not reproducible. Fix: rerun the documented command at the final ref and correct the count and skip statement. Regression test: run `C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest -q` from the PR worktree and assert the reported summary matches the captured pytest output.

### Should fix
- None.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 30/30 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 1. False [x] found: line 149's exact full-suite count and skip claim.
- Vacuous / loosened tests: none. The changed delete test checks FTS MATCH results and `transcripts_fts_docsize`; cleanup tests check membership, derived segment terms, idempotence, chunking, and the no-op gate.
- Undisclosed scope (diff vs claims): none beyond the false test-count evidence above. The UPDATE-trigger guard and related test changes are disclosed as decisions, and the sibling delete paths were swept.

### Read scope
- Focused read on `database/__init__.py` FTS/backfill and `init_db()` sections, `tests/test_search.py` changed sections, and delete/FTS sibling call sites. The diff is 528 lines, so changed hunks and relevant surrounding code were read rather than unrelated files start-to-finish.

### Summary
The implementation and changed tests appear correct, and the touched test file passed with 57 passed. This audit still blocks because the self-audit marks its exact suite-evidence claim done, but the same command at the checked-out PR ref reports 935 passed and 22 deselected, not the claimed 934 passed, 1 skipped, and 22 deselected.
