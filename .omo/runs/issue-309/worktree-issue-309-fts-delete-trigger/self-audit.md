# self-audit.md, issue #309, branch worktree-issue-309-fts-delete-trigger

Head this was written against: `d6fab7b`, rebased onto `origin/master` at
`3b846a0`. Every `file:line` below was re-opened at that head after the rebase,
because the rebase moved the base by three commits.

Independent review: none in-run. This workflow has no independent-model
audit pass; independent review happens via /audit-pr after the PR is opened.

## Issue acceptance criteria

- [x] "Add the AFTER DELETE trigger, symmetric with the two that exist" - delivered, `trg_transcripts_fts_delete` at database/__init__.py:787, same column set and same derived segment_text expression as the INSERT trigger and the UPDATE trigger's delete half.
- [x] "A one-off cleanup for indexes that already carry orphaned terms should run alongside it, since existing installs will not self-heal" - delivered, `cleanup_fts_orphans` at database/__init__.py:557, wired into startup at database/__init__.py:799.

## Promises made in investigation.md

- [x] Trigger placed in the idempotent DDL block so it runs on a fresh database and on an existing install - `CREATE TRIGGER IF NOT EXISTS trg_transcripts_fts_delete` at database/__init__.py:787, inside the same `with engine.begin() as conn:` block as the other two.
- [x] `IF NOT EXISTS` rather than DROP + CREATE, because no install has ever had this trigger name - database/__init__.py:787.
- [x] Cleanup is not `'rebuild'`; it is a membership-preserving reindex - `'delete-all'` at database/__init__.py:618, reinsert with the trigger-consistent expression at database/__init__.py:630.
- [x] Membership captured, not recomputed - `keep` list built from `transcripts_fts_docsize` joined to `transcripts` at database/__init__.py:613.
- [x] Whole sequence on one connection in one transaction, so a failed reinsert rolls back instead of leaving an empty index - single `engine.begin()` at database/__init__.py:605, no second connection in the function.
- [x] The comment's `CREATE TEMP TABLE _fts_keep` replaced with a Python-side id list, removing the connection-scoping hazard the Phase 1 probe found under this app's `pool_size=10` QueuePool - `_FTS_REINDEX_CHUNK` chunked reinsert at database/__init__.py:625, no TEMP TABLE anywhere in the function.
- [x] Both delete paths in scope are per-row ORM deletes, so a row trigger catches them - `DELETE /api/transcripts/{id}` and `DELETE /api/voice-notes/{id}`, enumerated in investigation.md section 3. No raw `DELETE FROM transcripts`, no `DROP TABLE transcripts`, no bulk `.delete()` on Transcript: `rg -n "DELETE FROM transcripts|DROP TABLE transcripts" --  ->  no matches outside tests`.
- [x] Sibling sweep result acted on, not just reported - the UPDATE trigger carried the same missing guard; fixed at database/__init__.py:761.
- [x] The existing delete test was vacuous and is fixed - `test_fts_trigger_delete_removes_from_search` at tests/test_search.py:374 now asserts against the index itself, not only against `search_transcripts`.

## Decisions the issue did not ask for

- [decision] Guarded the new delete trigger on `transcripts_fts_docsize` - not specified by the issue, and the issue's own design comment explicitly said no guard was needed. Added because the unguarded version failed its test with `database disk image is malformed`, isolated in investigation.md section 10 (scenarios A vs C hold the setup constant and differ only in the guard). database/__init__.py:789.
- [decision] Also guarded the pre-existing UPDATE trigger's delete half - not specified by the issue, which names only the missing DELETE trigger. Added because it is the same missing guard on the sibling entry point, and the path is live: `populate_fts()` indexes a pre-FTS row by UPDATEing it, at database/__init__.py:537. AGENTS.md's Complement Rule makes shipping one and not the other the failure mode to avoid. database/__init__.py:761.
- [decision] Corrected a docstring in `test_populate_fts_idempotent` at tests/test_search.py:552 - not specified by the issue. It asserted that `'delete-all'` corrupts FTS5 internal state permanently and that the per-row-delete-then-backfill case is untestable. Both are false, and this change is built on `'delete-all'`, so leaving it would have read as a standing warning against the approach here.
- [decision] Added an `integrity-check` assertion to the existing `test_populate_fts_restores_deleted_index` at tests/test_search.py:492 - not specified by the issue. That test passed before only because it never ran the check; it is what pins down the UPDATE-trigger fix.
- [decision] Did NOT touch `TranscriptionService.delete_transcript()` - the issue's comment notes it looks like dead code with no callers, and says in the same breath "Not part of this issue". Left alone deliberately, no behavior change either way.

## Mutation-check transcripts

Provenance, stated plainly: the M1 to M4 transcripts were observed by the Phase 3
verification agent (Sonnet) on the same tree, before two tests were reworked and
before the rebase. Neither the rebase (`.gitignore` and one docs file only:
`git diff --stat HEAD origin/master` showed `2 files changed, 92 insertions`) nor
the rework touched any test named under M1 to M4, so that evidence stands at this
head. The two reworked tests were mutation-checked by me, at this head, under M5
and M6 below.

- [x] `test_fts_trigger_delete_removes_from_search` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M1, commented out the `conn.execute(text(...))` that creates `trg_transcripts_fts_delete` (the pre-fix state); reran  ->  7 failed, 50 passed
          FAILED tests/test_search.py::test_fts_trigger_delete_removes_from_search
          E       AssertionError: term 'hello' still in the FTS index after delete
          E       assert [(1,)] == []
      restored: reran  ->  57 passed
- [x] `test_fts_trigger_delete_removes_segment_terms` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M1, same removal of the `trg_transcripts_fts_delete` creation; reran  ->  7 failed, 50 passed
          FAILED tests/test_search.py::test_fts_trigger_delete_removes_segment_terms
          E       assert [1] == []
      restored: reran  ->  57 passed
- [x] `test_fts_trigger_delete_leaves_sibling_rows_indexed` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M1, same removal of the `trg_transcripts_fts_delete` creation; reran  ->  7 failed, 50 passed
          FAILED tests/test_search.py::test_fts_trigger_delete_leaves_sibling_rows_indexed
          E       assert [1, 2] == [1]
      restored: reran  ->  57 passed
- [x] `test_fts_trigger_delete_of_never_indexed_row_keeps_index_valid` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M2, deleted the `WHEN EXISTS (SELECT 1 FROM transcripts_fts_docsize WHERE id = OLD.id)` line from the delete trigger; reran  ->  1 failed, 56 passed
          FAILED tests/test_search.py::test_fts_trigger_delete_of_never_indexed_row_keeps_index_valid
          E       sqlalchemy.exc.DatabaseError: (sqlite3.DatabaseError) database disk image is malformed
          E       [SQL: INSERT INTO transcripts_fts(transcripts_fts, rank) VALUES('integrity-check', 1)]
      restored: reran  ->  57 passed
- [x] `test_fts_trigger_update_of_never_indexed_row_keeps_index_valid` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M3, reverted the UPDATE trigger's delete half from `INSERT ... SELECT ... WHERE EXISTS` back to the unguarded `INSERT ... VALUES('delete', ...)`; reran  ->  2 failed, 55 passed
          FAILED tests/test_search.py::test_fts_trigger_update_of_never_indexed_row_keeps_index_valid
          E       sqlalchemy.exc.DatabaseError: (sqlite3.DatabaseError) database disk image is malformed
      restored: reran  ->  57 passed
- [x] `test_populate_fts_restores_deleted_index` (changed: added an `integrity-check` assertion) - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M3, same revert of the UPDATE trigger's delete half to the unguarded form; reran  ->  2 failed, 55 passed
          FAILED tests/test_search.py::test_populate_fts_restores_deleted_index
          E       sqlalchemy.exc.DatabaseError: (sqlite3.DatabaseError) database disk image is malformed
      restored: reran  ->  57 passed
- [x] `test_cleanup_fts_orphans_removes_orphan_and_is_idempotent` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M4, replaced the body of `cleanup_fts_orphans` with `return 0`; reran  ->  4 failed, 53 passed
          FAILED tests/test_search.py::test_cleanup_fts_orphans_removes_orphan_and_is_idempotent
          E       assert 0 == 1
      restored: reran  ->  57 passed
- [x] `test_cleanup_fts_orphans_does_not_index_previously_unindexed_rows` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M4, `cleanup_fts_orphans` body replaced with `return 0`; reran  ->  4 failed, 53 passed
          FAILED tests/test_search.py::test_cleanup_fts_orphans_does_not_index_previously_unindexed_rows
          E       assert 0 == 1
      restored: reran  ->  57 passed
- [x] `test_cleanup_fts_orphans_reindexes_across_chunks` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M4, `cleanup_fts_orphans` body replaced with `return 0`; reran  ->  4 failed, 53 passed
          FAILED tests/test_search.py::test_cleanup_fts_orphans_reindexes_across_chunks
          E       assert 0 == 1
      restored: reran  ->  57 passed
- [x] `test_init_db_cleans_pre_309_orphans_on_restart` - mutation check:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M4, `cleanup_fts_orphans` body replaced with `return 0`; reran  ->  4 failed, 53 passed
          FAILED tests/test_search.py::test_init_db_cleans_pre_309_orphans_on_restart
          E       assert [2] == []
      restored: reran  ->  57 passed
- [x] `test_cleanup_fts_orphans_noop_when_no_orphans` - mutation check, run by me at this head:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M5, deleted the `if not orphan_count: return 0` gate at database/__init__.py:610 so the wipe runs unconditionally; reran  ->  1 failed
          FAILED tests/test_search.py::test_cleanup_fts_orphans_noop_when_no_orphans
          E       AssertionError: index was wiped on a database with no orphans: ["INSERT INTO transcripts_fts(transcripts_fts) VALUES('delete-all')"]
          E       assert ["INSERT INTO...delete-all')"] == []
      restored: reran  ->  57 passed
- [x] `test_cleanup_fts_orphans_without_fts_tables_is_noop` - mutation check, run by me at this head:
      ran: C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest tests/test_search.py -q  ->  57 passed
      mutated: M6, deleted the `inspect(engine)` table-existence guard at database/__init__.py:597; reran  ->  1 failed
          FAILED tests/test_search.py::test_cleanup_fts_orphans_without_fts_tables_is_noop
          E       sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such table: transcripts_fts_docsize
          E       [SQL: SELECT COUNT(*) FROM transcripts_fts_docsize d WHERE NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.id = d.id)]
      restored: reran  ->  57 passed

### Two tests were replaced because they had no guarding mutation

Phase 3 reported this, and it is worth recording rather than burying. As first
written, `test_cleanup_fts_orphans_noop_when_no_orphans` compared a fingerprint
over `transcripts_fts_data` before and after, on the stated theory that a wipe
plus reinsert rewrites those blocks. That theory is false for a one-document
index: the agent instrumented the mutated function, confirmed the wipe genuinely
executed (`VERIFY_M5 delete-all executed`), and still got byte-identical blocks,
so the test passed under M5. It now asserts on the statements the function emits
instead, via a `before_cursor_execute` listener, and does fail under M5 as shown
above. The old `test_cleanup_fts_orphans_empty_db_is_noop` was a no-raise smoke
test that no mutation could fail; it was replaced by
`test_cleanup_fts_orphans_without_fts_tables_is_noop`, which exercises the
table-existence branch and fails under M6.

Both mutations were restored with the inverse edit, never `git checkout` or
`git stash`. `git diff --stat` after each restore showed only the intended two
files, and `git status --porcelain` showed no stray file.

## The six checks

- [x] Value-space exhaustiveness - the value that decides both new code paths is a row's index membership, and both cases are covered: present in `transcripts_fts_docsize` (tests/test_search.py:784) and absent from it (tests/test_search.py:801 for DELETE, tests/test_search.py:833 for UPDATE). For `segments`, the values reaching the derived expression are NULL, `[]`, and a list of dicts; all three are exercised (tests/test_search.py:766 covers a populated list, the other delete tests pass `[]`). `title`, `full_text` and `corrected_text` can each be NULL or a string, and the cleanup passes them raw rather than coalescing, matching all three triggers, so a later trigger `'delete'` supplies the same values that were indexed: database/__init__.py:630.
- [x] Value-space exhaustiveness, the one value that raises - `segments` holding invalid JSON makes `json_each` raise, which aborts the trigger or the cleanup. Pre-existing and unchanged by this diff: the same expression is already in the INSERT trigger at database/__init__.py:725 on master. Not newly reachable here. It is also the case the single transaction was chosen for, since `engine.begin()` at database/__init__.py:605 rolls the wipe back rather than committing an emptied index.
- [x] Boundary cardinality - a collection of one (one surviving row, tests/test_search.py:864 and tests/test_search.py:911), a collection of zero (no transcript rows, and no FTS tables at all, tests/test_search.py:1016), and the function's own pagination-equivalent limit `_FTS_REINDEX_CHUNK` (database/__init__.py:554) driven past its boundary at tests/test_search.py:983 with the chunk size forced to 2 against 3 rows. Proven to run more than one iteration, not merely to pass: instrumented, observed `start=0 chunk_ids=[1, 2]` then `start=2 chunk_ids=[3]`, instrumentation reverted.
- [x] Delivery chain to what the browser executes: N/A, `git diff --name-only` prints `database/__init__.py` and `tests/test_search.py` and nothing else, so there is no frontend source, no bundle, and no service-worker cache in this change. Confirmed no e2e test selects on anything touched: `grep -rn "fts|transcripts_fts|full_text|corrected_text" tests/e2e/ -i` returns only ORM attribute assignments in fixture setup, never a selector, role, or label.
- [x] `done == total` progress counters: N/A, no counter and no progress state in this diff. The one number the change produces is `cleanup_fts_orphans`'s return value, and it is pinned at both ends with exact-value assertions rather than a range: `== 1` at tests/test_search.py:864 and `== 0` at tests/test_search.py:911, against `return orphan_count` at database/__init__.py:639.
- [x] Every deferral matched against the issue text - the issue asks for exactly two things and both are delivered (see the acceptance-criteria section). The one thing left undone, `TranscriptionService.delete_transcript()`, is not required by the issue body; it appears only in a comment that itself says "Not part of this issue". Verified it is genuinely uncalled rather than taking that on trust: `rg -n "\.delete_transcript\("` returns only the definition and the unrelated route handler. No stub, no partial implementation, nothing deferred that the issue asked for.
- [x] A full suite count tied to the invocation that produced it - `C:\Claude\WhisperDeck\.venv\Scripts\python.exe -m pytest -q` run from the worktree at this head printed `934 passed, 1 skipped, 22 deselected, 1 warning in 270.46s`. Unfiltered invocation, no `-k` and no path argument. The 22 deselected are the browser tier: `pytest.ini:12` sets `addopts = -m "not e2e"`, so this is the repository's default suite, not a suite that includes e2e. The e2e tier run separately gives `6 passed, 935 deselected, 16 errors`, all 16 being `urllib.error.HTTPError: HTTP Error 429: Too Many Requests` from the process-wide rate limiter; reproduced identically on a clean `origin/master` worktree (`6 passed, 925 deselected, 16 errors`, same test ids), so pre-existing and untouched by this diff, which changes no file under `services/`.

## Testing tier

AGENTS.md's testing tiers put a backend database and SQL change with no UI
surface at the unit and integration tier, which is what ran. No browser tier was
required, and the delivery-chain box above carries the command that establishes
there is no frontend surface to drive.

## Main checkout state

    git -C C:\Claude\WhisperDeck rev-parse --abbrev-ref HEAD   ->  master
    git -C C:\Claude\WhisperDeck status --porcelain -uall      ->  (empty)

Clean and on master. Run artifacts under `.omo/runs/` do not appear because
`.omo/*` is ignored at `.gitignore:62`, confirmed with
`git check-ignore -v .omo/runs/issue-309/worktree-issue-309-fts-delete-trigger/investigation.md`.

## Not done

- [ ] Re-running M1 through M4 myself at the final head - not delivered: those four transcripts are the Phase 3 agent's observations on a tree whose relevant tests and mutated code regions are byte-identical to this head. Stated as provenance above rather than presented as my own runs. Re-running them would add first-hand confirmation and no new information.
