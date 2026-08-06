# Investigation: Issue #309 -- No AFTER DELETE FTS trigger

Code read from WORKTREE: `C:\Claude\WhisperDeck\.claude\worktrees\issue-309-fts-delete-trigger`
(both roots on commit 723ad23; report written to MAIN checkout only, via PowerShell
`New-Item`/`Set-Content` because this is a read-only subagent session with no Write
tool loaded -- see final section of this doc for confirmation of the method used.)

---

## 1. Current FTS code, verbatim, with real line numbers

All in `database/__init__.py`.

### Virtual table (line 620)
```
620  "CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5("
621  "title,"
622  "full_text,"
623  "corrected_text,"
624  "segment_text,"
625  "content=''transcripts'',"
626  "content_rowid=''id'',"
627  "tokenize=''porter unicode61''"
628  ")"
```

### INSERT trigger (line 632)
```
632  "CREATE TRIGGER IF NOT EXISTS trg_transcripts_fts_insert "
633  "AFTER INSERT ON transcripts BEGIN "
634  "INSERT INTO transcripts_fts(rowid, title, full_text, corrected_text, segment_text) "
635  "VALUES ("
636  "NEW.id, NEW.title, NEW.full_text, NEW.corrected_text, "
637  "COALESCE((SELECT group_concat(json_extract(value,''$.text''),'' '') FROM json_each(NEW.segments)), '''')"
638  "); END"
```

### Comment block explaining the update trigger (lines 640-654)
```
640  # Trigger: AFTER UPDATE deletes the old FTS row then inserts the new
641  # one, keeping one entry per rowid. The delete is routed through
642  # INSERT INTO ... VALUES(''delete'', ...) -- the FTS5 external-content
643  # delete command -- and must include every column the table defines
644  # (title, full_text, corrected_text, segment_text), not just rowid.
645  # segment_text in the delete is computed from OLD.segments (mirroring
646  # the INSERT trigger) because the column itself is often NULL.
647  # Note: non-MATCH queries on this table are answered from the
648  # content table; use transcripts_fts_docsize for index membership
649  # checks.
650  # DROP + unconditional CREATE (not IF NOT EXISTS): this trigger''s body
651  # changed to fix #206 (stale FTS entries after UPDATE). Any database
652  # created before that fix already has a trigger named
653  # trg_transcripts_fts_update -- IF NOT EXISTS would see it and skip
654  # creating the corrected body, silently leaving old databases broken.
```

### UPDATE trigger (line 655/657, drop-then-create)
```
655  conn.execute(text("DROP TRIGGER IF EXISTS trg_transcripts_fts_update"))
656  conn.execute(text(
657  "CREATE TRIGGER trg_transcripts_fts_update "
658  "AFTER UPDATE ON transcripts BEGIN "
659  "INSERT INTO transcripts_fts(transcripts_fts, rowid, title, full_text, corrected_text, segment_text) "
660  "VALUES(''delete'', OLD.id, OLD.title, OLD.full_text, OLD.corrected_text, "
661  "COALESCE((SELECT group_concat(json_extract(value,''$.text''),'' '') FROM json_each(OLD.segments)), '''')); "
662  "INSERT INTO transcripts_fts(rowid, title, full_text, corrected_text, segment_text) "
663  "VALUES ("
664  "NEW.id, NEW.title, NEW.full_text, NEW.corrected_text, "
665  "COALESCE((SELECT group_concat(json_extract(value,''$.text''),'' '') FROM json_each(NEW.segments)), '''')"
666  "); END"
667  ))
668  populate_fts(engine)
```

### `populate_fts()` (def at line 491, full body 491-551)
Backfills `transcripts_fts`/`segment_text` for `status=''completed''` rows whose id is
absent from `transcripts_fts_docsize` (line 516: `NOT EXISTS (SELECT 1 FROM
transcripts_fts_docsize d WHERE d.id = t.id)`). Two branches: if `segment_text` is
NULL it does an `UPDATE transcripts SET segment_text = :st` which fires the UPDATE
trigger (indexes the row); if `segment_text` is already set, it does a direct
`INSERT INTO transcripts_fts(...)`.

### There is NO AFTER DELETE trigger anywhere.
```
rg -n "AFTER DELETE" database/__init__.py   ->  no output, zero matches
rg -n "CREATE TRIGGER" -- database  ->  database/__init__.py:632 (insert), database/__init__.py:657 (update)
```
Confirms the issue''s core claim: exactly one INSERT and one UPDATE trigger, no DELETE trigger.

### Verification of every `file:line` the repository-owner comment cited

| Comment''s claim | Actual current line | Verdict |
|---|---|---|
| INSERT trigger at `:632` | line 632 = `CREATE TRIGGER IF NOT EXISTS trg_transcripts_fts_insert` | CONFIRMED |
| segment_text expr in insert trigger at `:637` | line 637 = the `COALESCE(...NEW.segments...)` line | CONFIRMED |
| comment "segment_text in the delete is computed from OLD.segments..." at `:645` | line 645 = that exact comment sentence | CONFIRMED |
| unconditional `DROP` + `CREATE` at `:655` | line 655 = `DROP TRIGGER IF EXISTS trg_transcripts_fts_update` | CONFIRMED |
| UPDATE trigger at `:657` | line 657 = `CREATE TRIGGER trg_transcripts_fts_update` | CONFIRMED |
| `:661` | line 661 = the OLD.segments COALESCE closing the ''delete'' half | CONFIRMED |
| `:665` | line 665 = the NEW.segments COALESCE closing the insert half | CONFIRMED |

All seven `file:line` references in the owner''s comment are byte-for-byte accurate
against the current worktree at commit 723ad23. Nothing has drifted.

Also verified from the issue body itself: "INSERT trigger (`database/__init__.py:632`)"
and "UPDATE trigger (`:657`)" -- both confirmed above, same lines.

---

## 2. Where schema/trigger DDL runs, and how upgrades are handled

- `database/init_db(db_path)` (def at `database/__init__.py:554`) is the single
  entry point. It is called once per process at startup -- `app.py`''s startup path
  and every test''s `db_session` fixture (`tests/conftest.py:77`, `engine, SessionLocal, _ =
  init_db(str(db_path))`) both go through it.
- Inside `init_db()`, all the transcripts_fts DDL (virtual table, both triggers)
  lives in one `with engine.begin() as conn:` block, lines 601-667, followed by a
  call to `populate_fts(engine)` at line 668.
- There is **no numbered/versioned migration list** anywhere in this codebase.
  The pattern is: idempotent DDL statements run unconditionally on every process
  startup, each written to tolerate re-running against a database that already
  has the object:
  - Fresh objects: `CREATE ... IF NOT EXISTS` (table at 620, insert trigger at 632,
    plain `ALTER TABLE ... ADD COLUMN` guarded by `ensure_columns()` at line 336).
  - Objects whose *body* must change without changing their *name* -- i.e. a
    logic fix to an existing trigger -- use the `DROP TRIGGER IF EXISTS` +
    unconditional `CREATE TRIGGER` pattern (lines 655-667), because
    `CREATE TRIGGER IF NOT EXISTS` would see the old trigger present and
    silently skip installing the corrected body. This is documented in the
    comment at 650-654 and exercised by
    `tests/test_search.py:658` `test_fts_update_trigger_migrates_existing_database`.
  - One-off backfills (`populate_fts`, `backfill_llm_job_result_snapshots`,
    `backfill_legacy_classification`) are plain functions called unconditionally
    every startup; they use an anti-join or a captured "was this column
    already there" flag to make themselves no-ops on subsequent runs.
  - Column-set/constraint changes that SQLite cannot do with `ALTER TABLE`
    use the rename-old-table -> `create_all()` recreates -> copy rows -> drop old
    pattern (`migrate_schema()` line 287 + `backfill_user_id()` line 317;
    also `ensure_nullable_llm_job_transcript_id()` line 357).

**Where the new AFTER DELETE trigger belongs:** a third `CREATE TRIGGER IF NOT
EXISTS trg_transcripts_fts_delete ...` statement inside the same `with
engine.begin() as conn:` block at lines 601-667 (naturally right after the
UPDATE trigger, before `populate_fts(engine)` on line 668). `IF NOT EXISTS` is
correct here (not `DROP` + unconditional `CREATE`) because no released version
of this codebase has ever created a trigger named `trg_transcripts_fts_delete` --
there is no old body for any existing install to be carrying, so nothing needs
displacing. This runs on both a fresh DB (created fresh by the same statement)
and an existing install (added by the next process restart, exactly like the
#206 update-trigger fix rolled out).

**Where a one-off orphan cleanup would hook in:** as a new function following
the shape of `populate_fts()` -- take an `engine`, run its own `engine.begin()`
block, and be called unconditionally from `init_db()` right before or after
`populate_fts(engine)` at line 668. It must be idempotent/no-op on repeat runs
(see section 8 -- probed and confirmed idempotent) the same way every other
backfill in this file already is.

---

## 3. Complement Rule -- every path that removes rows from `transcripts`

Commands run:
```
rg -n "\.delete\(" --include=*.py .              (excluding tests)
rg -n "DELETE FROM" --include=*.py .
rg -n "DROP TABLE" --include=*.py .
rg -n "delete_transcript" --include=*.py .
rg -n "ON DELETE CASCADE|ondelete=" --include=*.py .
```

Results, everywhere a `.delete(`/raw `DELETE`/`DROP TABLE` appears in the repo:

| file:line | table affected | fires the Transcript AFTER DELETE trigger? |
|---|---|---|
| `app.py:1931` `db.delete(t)` inside `delete_transcript` route (`@app.delete("/api/transcripts/{transcript_id}")` at `app.py:1909`, `async def delete_transcript` at `app.py:1910`) | **transcripts** | YES -- this is the live, reachable path. Per-row ORM `db.delete()` -> single-row `DELETE FROM transcripts WHERE id=?` -> SQLite fires the per-row trigger. |
| `services/transcription.py:165` `db.delete(t)` inside `TranscriptionService.delete_transcript()` (def at `services/transcription.py:161`) | transcripts | Would fire the trigger too, IF ever called -- but it is dead code (see below). |
| `app.py:2417` `db.delete(entry)` | RelabelHistory (undo-relabel endpoint) | N/A -- different table |
| `app.py:2492` `db.delete(profile)` | VoiceProfile (rollback on failed enroll) | N/A -- different table |
| `app.py:2914` `db.delete(n)` (`@app.delete("/api/voice-notes/{voice_note_id}")` at 2897) | this is a Transcript row (voice notes are `kind=''voice_note''` Transcripts) -- per-row ORM delete, same mechanism as 1931 | YES, same as above; it''s the same `Transcript` model, just a different route/kind filter |
| `services/hotwords.py:41` `db.delete(entry)` | HotwordEntry | N/A |
| `services/llm_jobs.py:541` `.delete(synchronize_session=False)` | TranscriptTag (re-tag replace) | N/A -- different table, and TranscriptTag has no FTS mirror |
| `services/relabel.py:83` `.delete(synchronize_session=False)` | RelabelHistory (MAX_HISTORY trim) | N/A |
| `services/relabel.py:109` `.delete(synchronize_session=False)` | RelabelHistory (`clear_relabel_history`) | N/A |
| `services/voice_id.py:297` `db.delete(clip)` | VoiceClip | N/A |
| `services/voice_id.py:425-426` `db.delete(clip)` / `db.delete(p)` | VoiceClip, VoiceProfile | N/A |
| `database/__init__.py:333` `DROP TABLE {old_table}` | `<table>_old` renamed-away tables from `migrate_schema()`/`backfill_user_id()` | N/A -- these are the old, already-copied-from tables, not `transcripts` itself |
| `database/__init__.py:402` `DROP TABLE llm_jobs_old` | llm_jobs_old | N/A |

No `DELETE FROM transcripts` raw SQL string exists anywhere in application code:
```
rg -n "DELETE FROM transcripts\b" --include=*.py .   ->  no matches
```
No `DROP TABLE` targets `transcripts` itself anywhere:
```
rg -n "DROP TABLE" --include=*.py .  ->  only :333 (<table>_old) and :402 (llm_jobs_old), never `transcripts`
```
No FK cascade deletes originate a `transcripts` row deletion -- the `ondelete="CASCADE"`
FKs all point *from* other tables (LlmJob, Summary, VoiceClip-via-source_transcript_id,
transcript_tags, RelabelHistory) *to* `transcripts.id`; SQLite''s `foreign_keys` PRAGMA
is never turned on (confirmed: no `PRAGMA foreign_keys` anywhere in
`database/__init__.py`''s `_set_sqlite_pragmas`, lines 572-583, and
`tests/test_relabel_undo.py:209` states this explicitly: "ondelete=CASCADE never
fires (SQLite foreign_keys pragma is off)") -- so cascades never trigger a
`transcripts` delete on their own.

**Conclusion:** exactly two live call sites delete `Transcript` rows --
`app.py:1909-1931` (`/api/transcripts/{id}` DELETE) and `app.py:2897-2914`
(`/api/voice-notes/{id}` DELETE, same model, `kind=''voice_note''` rows). Both are
per-row ORM `db.delete()` + `db.commit()`, i.e. a single-row `DELETE` statement
each -- both would fire a correctly-added `AFTER DELETE` trigger. There is no
bulk/batch delete, no raw `DELETE FROM transcripts`, no `DROP TABLE transcripts`,
and no test fixture that wipes the transcripts table directly.

**`TranscriptionService.delete_transcript()` dead-code claim -- verified:**
```
rg -n "\.delete_transcript\(" --include=*.py .   ->  no matches at all (zero call sites)
rg -n "delete_transcript" --include=*.py .
  ->  app.py:1910            async def delete_transcript(...)          [route handler, live]
      services/transcription.py:161   def delete_transcript(self, ...) [unused method]
      tests/test_file_inventory.py:261,279,524   test *names* only (contain the phrase, do not call the method)
      tests/test_relabel_undo.py:207              test name only
```
Confirmed: `services/transcription.py:161-167`''s `delete_transcript` has no
callers anywhere in the repo. The comment''s claim holds. The tests named
`test_delete_transcript_*` in `test_file_inventory.py` and `test_relabel_undo.py`
exercise the HTTP route (`client.delete("/api/transcripts/...")`), i.e. the
`app.py:1910` handler -- confirmed by reading `tests/test_file_inventory.py:261-300`
and `tests/test_relabel_undo.py:207-230` (both use the `client` fixture and call
`client.delete(...)`, not the service method).

---

## 4. Sibling sweep

**Other FTS5 virtual tables:**
```
rg -n "USING fts5" --include=*.py .   ->  database/__init__.py:620 only
```
Nothing else found. `transcripts_fts` is the only FTS5 table in the codebase.

**Other CREATE TRIGGER statements anywhere:**
```
rg -n "CREATE TRIGGER" --include=*.py .
  -> database/__init__.py:632  (trg_transcripts_fts_insert)
     database/__init__.py:657  (trg_transcripts_fts_update)
     tests/test_search.py:662  (comment text, not a statement)
     tests/test_search.py:678  (test''s own simulated pre-fix trigger, for test_fts_update_trigger_migrates_existing_database)
```
Only the two production triggers exist; nothing else in the app defines a
trigger of any kind. Nothing else found.

**Other derived/mirrored-index structures with the same asymmetry risk:**
Checked `VoiceProfile`/`VoiceClip` (the other place embeddings live,
`database/__init__.py:242-269`): `embedding` is a plain `JSON` column stored
directly on the row (`embedding = Column(JSON, nullable=False)`), not a
separate shadow/index table kept in sync by triggers. `VoiceProfile.embedding`
is recomputed by application code (`_recompute_profile_embedding`, called from
`services/voice_id.py:299` and inline elsewhere) at delete-time, not by a DB
trigger -- so there is no trigger-asymmetry class of bug there; it''s a
different mechanism entirely. Nothing else found.

**Anything else calling `''rebuild''`, `''delete-all''`, `''optimize''`, or `''integrity-check''`:**
```
rg -n "rebuild|delete-all|optimize|integrity-check" --include=*.py .
  -> tests/test_search.py:500   INSERT INTO transcripts_fts(transcripts_fts) VALUES(''delete-all'')   [inside test_populate_fts_restores_deleted_index]
     tests/test_search.py:560   VALUES(''integrity-check'', 1)   [inside test_populate_fts_idempotent]
     tests/test_search.py:587   VALUES(''integrity-check'', 1)   [inside test_fts_update_integrity_check_passes]
     tests/test_search.py:623   VALUES(''integrity-check'', 1)   [inside test_fts_update_idempotent]
```
No production code (`database/__init__.py`, `services/*.py`, `app.py`) calls
any of these four FTS5 special commands -- only test code does, deliberately,
to simulate a wiped index (`''delete-all''`) or verify structural soundness
(`''integrity-check''`). `''rebuild''` and `''optimize''` are never invoked anywhere
in this repo, in production or tests. So the corruption hazard the comment
describes (`''rebuild''` desyncing the index from what the triggers believe) is
currently latent/theoretical against this codebase''s actual code -- it would
only become live if someone implements the issue''s proposed one-off cleanup
using `''rebuild''`, which is exactly the trap the comment is warning the next
implementer away from. Today, nothing in the shipped code calls it.

**Existing integrity-check / repair routine for the FTS index:**
There is no production repair routine. `populate_fts()` (`database/__init__.py:491`)
is a *backfill*, not an orphan-repair: it only adds missing entries
(`NOT EXISTS ... transcripts_fts_docsize`), it never removes stale/orphaned
entries for rows that no longer exist in `transcripts`. No code anywhere
(production or test) queries for orphaned `transcripts_fts_docsize` rows --
the query `SELECT d.id FROM transcripts_fts_docsize d WHERE NOT EXISTS (SELECT 1
FROM transcripts t WHERE t.id = d.id)` proposed in the issue comment does not
exist anywhere in the current codebase:
```
rg -n "NOT EXISTS.*transcripts t WHERE t.id" --include=*.py .   -> no matches
```
**Sibling-sweep verdict: nothing else found** beyond the single named gap --
one FTS5 table, two triggers (missing the third), no other mirrored-index
structure, no production caller of the dangerous FTS5 special commands, and
no existing orphan-detection routine.

---

## 5. Existing tests

`tests/test_search.py:374` -- `test_fts_trigger_delete_removes_from_search`, full body verbatim:
```python
def test_fts_trigger_delete_removes_from_search(db_session):
    """Deleting a transcript excludes it from search_transcripts results
    even if the FTS index still has a stale entry."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="hello world")
    rows = db_session.execute(
        text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
        {"q": ''"hello"''},
    ).fetchall()
    assert len(rows) == 1
    db_session.delete(t)
    db_session.commit()
    results = search_transcripts(db_session, user.id, "hello")
    assert len(results) == 0
```
The `"hello"` MATCH query only runs **before** the delete (as a precondition,
asserting the row was indexed to begin with, lines 379-383). After the delete
(lines 384-385) the only assertion is against `search_transcripts(...)` -- which
already filters `Transcript.status == "completed"` and `Transcript.id.in_(matching_ids)`
(`services/search.py:74-76`) via an ORM JOIN, so it returns `[]` regardless of
whether the raw FTS index still has a stale "hello" entry. **The comment''s
"vacuous" claim holds against the current code exactly as described**: deleting
the trigger''s entire body (i.e. adding no DELETE trigger at all, today''s actual
state) does not fail this test, because nothing after the `db_session.delete(t)`
re-queries `transcripts_fts` directly or checks `transcripts_fts_docsize`.

Every other FTS-trigger-related test in the repo:
```
rg -n "^def test_" tests/test_search.py | rg -i "trigger|fts|delete|update|insert|integrity|rebuild|corrupt|orphan"
```
| file:line | test |
|---|---|
| `tests/test_search.py:152` | `test_fts5_percent_is_token_separator` |
| `tests/test_search.py:164` | `test_fts5_underscore_is_token_separator` |
| `tests/test_search.py:344` | `test_fts_trigger_insert_populates_index` |
| `tests/test_search.py:356` | `test_fts_trigger_update_syncs_index` |
| `tests/test_search.py:374` | `test_fts_trigger_delete_removes_from_search` (the vacuous one) |
| `tests/test_search.py:390` | `test_fts_trigger_segment_text_indexed` |
| `tests/test_search.py:476` | `test_populate_fts_restores_deleted_index` |
| `tests/test_search.py:531` | `test_populate_fts_idempotent` |
| `tests/test_search.py:567` | `test_populate_fts_empty_db_is_noop` |
| `tests/test_search.py:576` | `test_fts_update_integrity_check_passes` (issue #206 regression) |
| `tests/test_search.py:591` | `test_fts_update_old_terms_removed` (issue #206 regression) |
| `tests/test_search.py:607` | `test_fts_update_idempotent` (issue #206 regression) |
| `tests/test_search.py:627` | `test_fts_update_old_segment_terms_removed` (issue #206 regression) |
| `tests/test_search.py:658` | `test_fts_update_trigger_migrates_existing_database` (issue #206 regression, exercises the DROP+CREATE migration pattern) |

No DELETE-trigger equivalent of `test_fts_update_integrity_check_passes` /
`test_fts_update_old_terms_removed` / `test_fts_update_idempotent` exists --
there is no test that runs `''integrity-check''` after a delete, and no test
that checks `transcripts_fts_docsize` membership after a delete. This is the
gap the fix needs to close in the test suite, exactly as the comment states.

**How tests build a database:** `tests/conftest.py:71-83`, fixture `db_session(tmp_path)`:
creates a real on-disk SQLite file (`tmp_path / "test.db"`, not in-memory) and
calls the *same* `database.init_db(str(db_path))` the production app uses
(docstring at line 73-75: "built through the same init_db() path the real app
uses"). Returns `(engine, SessionLocal, _)`, opens one `SessionLocal()` session,
yields it, closes session + disposes engine on teardown. `client` fixture
(line 86) wraps `db_session` with a `TestClient` whose `get_db` dependency is
overridden to yield the same session. A new test exercising the broken/corrupted
state can either (a) call `init_db()` directly against a `tmp_path` file and then
manipulate the raw connection (as `test_fts_update_trigger_migrates_existing_database`
at line 658 already does, via `create_engine(f"sqlite:///{db_path}")` on the same
file after disposing the first engine), or (b) use the `db_session`/
`engine = db_session.get_bind()` pattern already used by every `populate_fts`/
`integrity-check` test above.

---

## 6. SQLite version actually in use

```
C:\Claude\WhisperDeck\.venv\Scripts\python.exe -c "import sqlite3; print(sqlite3.sqlite_version)"
```
Output: `3.50.4`

(Issue references SQLite 3.45.3; comment says the underlying behavior "is not
version-specific, it follows from external-content mode semantics" -- the repro
below was run against 3.50.4 and reproduces the same failure, supporting that claim.)

---

## 7. Independent re-verification of the comment''s corruption claim

Script run (saved to
`C:\Users\T1B92~1.KUR\AppData\Local\Temp\claude\C--Claude-WhisperDeck\546c5f64-b5e4-48d3-93c4-f6de03ad6c56\scratchpad\repro_rebuild_corruption.py`,
identical to the comment''s repro plus a try/except around the final DELETE to
capture the exact exception text):

Verbatim output:
```
segwordone before rebuild: [1]
segwordone after rebuild: []
integrity-check after rebuild: OK
DELETE raised sqlite3.DatabaseError: database disk image is malformed
```

**The corruption claim reproduces exactly as described.** `''rebuild''` silently
drops the segment terms from the index (`[1]` -> `[]`), `''integrity-check''`
passes anyway (corruption is latent, not detected), and the very next `DELETE
FROM transcripts` -- going through the comment''s insert+delete trigger pair
that mirrors this codebase''s real trigger bodies -- raises
`sqlite3.DatabaseError: database disk image is malformed`. This is on SQLite
3.50.4, the version this project''s venv actually uses, confirming the
mechanism is not specific to the 3.45.3 the issue names.

---

## 8. Probing the comment''s proposed cleanup sequence

Script run (`...\scratchpad\probe_cleanup.py`), built against a synthetic
schema mirroring the real one (insert/update/delete triggers with the same
derived-segment-text expression as `database/__init__.py`), simulating: two
normal rows, a `''rebuild''` (to match the issue''s "index already carries
orphaned/desynced terms" scenario), and a genuine orphan (a third row deleted
with the delete trigger temporarily removed, to simulate a pre-fix install
that has a stale docsize entry with no backing `transcripts` row) -- then
running the comment''s proposed detect -> `_fts_keep` -> `''delete-all''` ->
reindex-restricted-to-`_fts_keep` sequence.

Verbatim output:
```
after rebuild: segwordone -> []  segwordtwo -> []
row3 indexed before delete: [3]
row3 still indexed after delete (orphan): [3]
detected orphans: [(3,)]
_fts_keep membership before wipe: [(1,), (2,), (3,)]
docsize after delete-all: 0
after reindex: segwordone -> [1]
after reindex: segwordtwo -> [2]
after reindex: segwordthree (orphan, should be gone) -> []
docsize membership after reindex: [(1,), (2,)]
integrity-check after cleanup: OK
orphans on second detect pass: []
after second cleanup run: segwordone -> [1]
after second cleanup run: segwordtwo -> [2]
real DELETE after cleanup succeeded, no corruption
segwordone after real delete of row1: []
integrity-check after real delete: OK
```

Every claim the comment makes about this sequence is confirmed:
- Orphan (rowid 3, never in `transcripts` after its delete) is dropped by the
  `delete-all` + `_fts_keep`-restricted reindex -- `docsize membership after
  reindex: [(1,), (2,)]`, orphan gone.
- Survivors (rowid 1, 2) keep their segment terms -- `segwordone -> [1]`,
  `segwordtwo -> [2]` after reindex, even though the earlier `''rebuild''` had
  wiped them (`-> []`).
- `integrity-check` passes after cleanup.
- Idempotent -- running detect+wipe+reindex a second time changes nothing
  (`orphans on second detect pass: []`, same MATCH results before/after).
- A subsequent real `DELETE FROM transcripts WHERE id = 1` after cleanup does
  **not** corrupt the database (no exception) and `integrity-check` still
  passes afterward, and the deleted row''s term (`segwordone`) is correctly
  gone from the index.

The "tested clean" cleanup sequence tests clean in this independent run too.

---

## 9. What the comment''s proposed fix gets wrong or misses

**`CREATE TEMP TABLE _fts_keep` and connection pooling.** `database/init_db()`
creates the engine with `pool_size=10, max_overflow=20` (`database/__init__.py:565-570`),
i.e. a real `QueuePool` with up to 30 possible underlying DBAPI connections --
not a single persistent connection. A SQLite `TEMP TABLE` is scoped to the
*physical* connection that created it, not to the SQLAlchemy `Engine`. Probed
this directly (`...\scratchpad\probe_temp_table_pool.py`):
  - Sequentially checking a connection out, creating the temp table, closing
    it, then checking out a *new* `engine.connect()` in a single-threaded
    script: SQLAlchemy''s pool handed back the *same* underlying DBAPI
    connection (`connA` and `connB` both reported the same
    `id(conn.connection)`), so the temp table was still visible.
  - Holding **two connections open concurrently** (`connC`, `connD`) -- which
    is the realistic case for a pooled engine under any concurrent access --
    produced genuinely different underlying connections
    (`connC.connection is connD.connection` -> `False`), and a temp table
    created on one was **not visible** on the other:
    `connD (concurrently held) does NOT see _fts_keep2: OperationalError ...
    no such table: _fts_keep2`.
  - **Implication for the design:** the comment''s three-statement sequence
    (create `_fts_keep`, `''delete-all''`, reindex-from-`_fts_keep`) is only
    safe if all three statements execute against the *same* connection
    object, i.e. inside one `with engine.begin() as conn:` block reusing that
    one `conn` for every statement -- exactly the idiom this file already uses
    everywhere else for multi-statement DDL (lines 601-667, `populate_fts`''s
    SQL at 509-551). The comment''s write-up does not state this requirement
    explicitly; a naive implementation issuing each statement via a fresh
    `engine.connect()`/`engine.begin()` call (rather than reusing one
    connection object) would non-deterministically fail to see `_fts_keep`
    from the reindex step whenever the pool hands back a different physical
    connection -- silently reindexing zero rows rather than raising an error
    (the `JOIN _fts_keep k` would just match nothing). This is a real gap in
    the comment''s proposal, verified by direct probe, that is not present in
    this codebase''s other migration code only because that code already keeps
    everything in one `engine.begin()` scope by convention.
- **Transactionality:** the cleanup should run inside a single transaction for
  the same reason the existing DDL block does (lines 601-667 are one
  `with engine.begin() as conn:`) -- so a failure partway (e.g. after
  `''delete-all''` but before the reindex completes) rolls back to the
  pre-cleanup state instead of leaving the index empty. The comment does not
  discuss transaction boundaries; an implementation needs to make this
  explicit, using the existing single-`engine.begin()` convention.
- **Concurrent writers during cleanup:** the app sets `PRAGMA journal_mode=WAL`
  and `busy_timeout=5000` (`database/__init__.py:580-581`). `''delete-all''` on
  an external-content FTS5 table plus a bulk reindex is exactly the kind of
  multi-statement write that benefits from running during the single-threaded
  `init_db()` startup phase (where `populate_fts()` and every other backfill
  already run, before the app begins serving requests / before
  `queue_worker_loop` starts) rather than from a request handler or background
  job -- the comment does not say where to run the cleanup, but the existing
  precedent (every other one-off backfill in this file runs unconditionally
  during `init_db()`) strongly implies that is also the correct place for
  this one, and it avoids the concurrent-writer problem largely by not
  needing to solve it (no other connection is active yet at that point in
  startup).
- **`transcripts_fts_docsize` existence -- confirmed as a real, queryable
  table**, not merely asserted: `SELECT COUNT(*) FROM transcripts_fts_docsize`
  is already used by five existing tests (`tests/test_search.py:510, 526, 549,
  556, 618`) and was queried directly in both probe scripts in sections 7-8
  above, returning real integer results each time (docsize counts of 0, 1, 2,
  3 depending on state) -- it is a genuine FTS5 shadow table in this schema,
  not a hypothetical.
- **One correctness point in the comment holds up and is worth restating as a
  design constraint, not a flaw:** membership must be captured via
  `_fts_keep` (i.e. "whatever was indexed before"), not recomputed via
  `WHERE status=''completed''`, because the insert trigger indexes every row
  regardless of status while `populate_fts()` only backfills
  `status=''completed''` rows (confirmed directly: `database/__init__.py:632-638`,
  the insert trigger, has no `WHERE` clause / status filter at all, so every
  `INSERT INTO transcripts` gets indexed unconditionally, while
  `populate_fts()`''s backfill SELECT at line 515 has `WHERE t.status =
  ''completed''`). Reindexing "all rows matching some predicate" instead of
  "whatever docsize already had" would change which non-completed rows are
  indexed, which the comment correctly flags as a scope change beyond what
  this issue should do.

No other design flaw was found in the proposed detect/preserve/wipe/reindex
sequence itself -- sections 7 and 8 above independently reproduced both the
corruption hazard and the cleanup''s correctness, matching the comment''s
claims exactly. The two points this investigation adds beyond the comment are
(a) the connection-pooling hazard around `CREATE TEMP TABLE` under a pooled
engine, which is a hazard specific to *this* application''s `pool_size=10`
config and not visible when probing with a single ad-hoc `sqlite3` connection
as the comment''s own probe did, and (b) the explicit recommendation to run the
whole three-step sequence, and the new trigger''s installation, from inside
`init_db()`''s existing single-connection DDL block rather than as a
standalone script, both matching this codebase''s existing migration idiom.

---

## Report-writing method note

This subagent session has no `Write` tool loaded (read-only investigation
role). This file was created with the PowerShell tool''s `New-Item -ItemType
Directory -Force` (to ensure the target directory exists) followed by a
sequence of PowerShell single-quoted here-strings piped to `Set-Content`/
`Add-Content -Encoding utf8` (split into multiple calls because one combined
call exceeded the process spawn argument-length limit), run against the MAIN
checkout path
`C:\Claude\WhisperDeck\.omo\runs\issue-309\worktree-issue-309-fts-delete-trigger\investigation.md`,
per the fallback instructions in the task.

---

## 10. Phase 2 finding: the comment's "no docsize guard is needed" claim is refuted

Sections 1-9 above were written before any fix code existed. This section was
added during Phase 2, when the prescribed design failed its own test.

The delete trigger was first written exactly as the issue comment prescribes, a
plain mirror of the update trigger's delete half with no membership guard. The
test for the never-indexed-row case failed:

```
FAILED tests/test_search.py::test_fts_trigger_delete_of_never_indexed_row_is_safe
E       sqlalchemy.exc.DatabaseError: (sqlite3.DatabaseError) database disk image is malformed
E       [SQL: INSERT INTO transcripts_fts(transcripts_fts, rank) VALUES('integrity-check', 1)]
1 failed, 55 passed, 1 warning in 9.24s
```

### Isolation probe

Script: `probe_unindexed.py`, in this directory. It builds each scenario against
the real schema through `init_db()`, not a synthetic table, and runs
`integrity-check` with `rank=1` after each operation. Verbatim output:

```
=== A: unguarded delete of never-indexed row ===
  docsize before: [1]
  before delete: integrity-check FAILED -> DatabaseError: (sqlite3.DatabaseError) database disk image is malformed
  docsize after : [1]
  after delete of never-indexed row: integrity-check FAILED -> DatabaseError: (sqlite3.DatabaseError) database disk image is malformed
  keeper id: 1

=== B: update of never-indexed row (existing trigger) ===
  before update: integrity-check FAILED -> DatabaseError: (sqlite3.DatabaseError) database disk image is malformed
  docsize after : [1, 2]
  after update of never-indexed row: integrity-check FAILED -> DatabaseError: (sqlite3.DatabaseError) database disk image is malformed

=== C: guarded delete of never-indexed row ===
  docsize after ghost delete: [1]
  after guarded delete of never-indexed row: integrity-check OK

=== D: guarded delete of indexed row ===
  docsize after : [1] (keeper is 1 )
  MATCH beta: []
  MATCH segterm: []
  MATCH alpha: [1]
  after guarded delete of indexed row: integrity-check OK
```

### Reading it

The `before` failures in A and B are a setup artifact and prove nothing about
the triggers: while an unindexed row sits in the content table, `rank=1`
integrity-check compares index against content and fails on the mismatch alone.

The decisive comparison is A-after against C-after. Both scenarios build the
identical state (a keeper row indexed, a ghost row present in content and absent
from the index, then the ghost deleted). After the delete the content table and
the index agree on membership in both. A, unguarded, still fails. C, guarded,
returns OK. The setup is held constant, so the spurious `'delete'` for a rowid
the index does not hold is the cause.

D confirms the guard does not break the actual fix: the deleted row's terms are
gone from the index, including its segment-only term, the sibling row is
untouched, and integrity-check is OK.

### Consequence 1: the new trigger needs a guard

`WHEN EXISTS (SELECT 1 FROM transcripts_fts_docsize WHERE id = OLD.id)`, on the
trigger, because the entire body is conditional.

This is not a hypothetical case. `populate_fts()` skips rows with
`status != 'completed'`, so a pre-FTS row in any other status is absent from the
index for the whole life of the install. Without the guard, this change would
have introduced index corruption on exactly those installs, on the first delete.

### Consequence 2: the existing UPDATE trigger has the same bug (sibling sweep hit)

Scenario B is the existing `trg_transcripts_fts_update` from #206, not the new
trigger. Its delete half is the same unguarded `'delete'`, and it corrupts the
index the same way for a row that was not already indexed.

That path is exercised in production: `populate_fts()` indexes a pre-FTS row by
UPDATEing it (`database/__init__.py:537-540`), which fires the update trigger's
delete half against an unindexed rowid. Every backfill on an upgraded install
ran it.

Fixed in the same change, per AGENTS.md's Complement Rule. The guard has to sit
on the statement rather than on the trigger here:

```sql
INSERT INTO transcripts_fts(transcripts_fts, rowid, title, full_text, corrected_text, segment_text)
SELECT 'delete', OLD.id, OLD.title, OLD.full_text, OLD.corrected_text,
       COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') FROM json_each(OLD.segments)), '')
WHERE EXISTS (SELECT 1 FROM transcripts_fts_docsize WHERE id = OLD.id);
```

A trigger-level `WHEN` would skip the insert half too, and that insert is how
the backfill gets an unindexed row into the index in the first place.

`test_populate_fts_restores_deleted_index` now asserts integrity-check at the
end of the backfill, which is what pins this down. It passed before only because
it never ran the check.

### Consequence 3: one test in the repo had to be reshaped

`test_fts_trigger_delete_of_never_indexed_row_is_safe` was renamed to
`test_fts_trigger_delete_of_never_indexed_row_keeps_index_valid` and asserts
integrity-check only after the delete. Asserting it before is asserting the
setup artifact described above, which would make the test fail for a reason that
has nothing to do with the trigger.
