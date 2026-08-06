# wrong-directions.md, issue #309, branch worktree-issue-309-fts-delete-trigger

Written as each item was hit, not backfilled.

## 1. The issue body's proposed cleanup is the one that corrupts the database

`#309` body: "A one-off cleanup for indexes that already carry orphaned terms
should run alongside it." The obvious reading, and the one the FTS5 docs point
at, is `INSERT INTO transcripts_fts(transcripts_fts) VALUES('rebuild')`.

That approach destroys data. A rebuild re-reads the content table, so it indexes
the literal `segment_text` column, while all three triggers index a value
derived from the `segments` JSON. Every row loses its segment terms, and the
index stops agreeing with what the triggers believe is indexed, so the next
`DELETE` fails with `database disk image is malformed`. `integrity-check`
returns OK immediately after the rebuild, so a verification pass that checks
only integrity would ship it.

This was already flagged in the issue's own comment thread by the repository
owner before this run started, and this run reproduced it independently rather
than taking it on trust. Verbatim reproduction output is in `investigation.md`.

**Recommended fix to the issue text:** none needed, the comment covers it. Noted
here because the *body* still reads as though a one-line rebuild were the fix,
and a reader who skips the comments would implement exactly that.

## 2. The issue comment's "no docsize guard is needed" claim is wrong

This is the one that mattered. The owner's design comment on #309 says:

> **No docsize guard is needed on the new delete trigger.** The worry was a
> `'delete'` firing for a row that was never indexed [...] Probed it:
> `integrity-check` stays OK, other rows are unaffected, nothing is corrupted.
> So the trigger can be a plain mirror of the update trigger's delete half, no
> `WHERE EXISTS (SELECT 1 FROM transcripts_fts_docsize ...)` clause

The trigger was written that way first, as the comment prescribes, and the test
for that exact case failed:

```
FAILED tests/test_search.py::test_fts_trigger_delete_of_never_indexed_row_is_safe
E       sqlalchemy.exc.DatabaseError: (sqlite3.DatabaseError) database disk image is malformed
E       [SQL: INSERT INTO transcripts_fts(transcripts_fts, rank) VALUES('integrity-check', 1)]
```

Isolated with a four-scenario probe on the real schema via `init_db()`, SQLite
3.50.4 (probe script and full output in `investigation.md`):

| scenario | after the operation |
|---|---|
| A: unguarded `'delete'` of a never-indexed rowid | `integrity-check FAILED -> database disk image is malformed` |
| B: existing UPDATE trigger on a never-indexed row | `integrity-check FAILED -> database disk image is malformed` |
| C: guarded `'delete'` of a never-indexed rowid | `integrity-check OK` |
| D: guarded `'delete'` of an indexed row | terms removed (`MATCH beta: []`, `MATCH segterm: []`), sibling kept, `integrity-check OK` |

A and C share an identical setup and differ only in the guard, which is what
rules out the setup as the cause. The guard is required.

**Recommended fix:** post a correction on #309 so the next reader does not
implement the unguarded trigger.

## 3. Scenario B is a pre-existing bug the issue never named

Scenario B above is not the new trigger. It is the existing UPDATE trigger from
#206, which carries the same unguarded `'delete'`. It corrupts the index for any
row that was not already indexed, and that path is not hypothetical:
`populate_fts()` indexes a pre-FTS row by UPDATEing it, so every backfill ran
the unguarded delete half against an unindexed rowid.

Fixed in the same change, per AGENTS.md's Complement Rule (a guard that lands on
one entry point and not its sibling is the failure mode that rule exists for).
Disclosed as an unasked-for addition in `self-audit.md`.

## 4. A test docstring in the repo asserts the opposite of what the code does

`tests/test_search.py`, docstring of `test_populate_fts_idempotent`, before this
change:

> The per-row-delete then backfill edge case is not testable in isolation,
> delete-all on an external-content FTS5 table corrupts internal state
> permanently, and a single-row delete followed by the trigger's
> delete-on-update hits the same FTS5 limitation.

Both halves are false. `test_populate_fts_restores_deleted_index`, 30 lines
above it in the same file, uses `'delete-all'` and then repopulates
successfully, and this run's cleanup is built on `'delete-all'` plus a
trigger-consistent reinsert, with tests for idempotency and for a subsequent
real `DELETE`. Left alone, that docstring would have read as a standing warning
against the approach this change takes.

**Fix applied:** docstring corrected in this change, saying what superseded it.

## 5. `EnterWorktree` makes the runner prompt's naming rule unsatisfiable

`.claude/issue-runner-prompt.md` says:

> **`<your-branch-name>` is the branch name, exactly.** [...] The report
> subdirectory name and the worktree directory name both match the branch,
> because `verify_self_audit.py` resolves your worktree by matching that
> directory name against `git worktree list`

`EnterWorktree` does not allow that. Given `name: issue-309-fts-delete-trigger`
it reports:

```
Created worktree at C:\Claude\WhisperDeck\.claude\worktrees\issue-309-fts-delete-trigger
on branch worktree-issue-309-fts-delete-trigger
```

The tool prefixes the branch with `worktree-` and does not prefix the directory,
so the worktree directory name and the branch name can never be equal when the
worktree is created the way the same prompt tells you to create it.

`verify_self_audit.py:129` reads the report directory name and matches it
against the branch (`find_worktree_for_branch_dir`), so the binding constraint
is report-directory == branch. This run used
`.omo/runs/issue-309/worktree-issue-309-fts-delete-trigger/` (branch name) with
the worktree at `.../worktrees/issue-309-fts-delete-trigger` (unprefixed), which
satisfies the checker.

**Recommended fix to the prompt:** drop "and the worktree directory name" from
that sentence, and add: "`EnterWorktree` prefixes the branch with `worktree-`
but not the directory. The report directory must match the BRANCH, which means
it carries the prefix and the worktree directory does not."

## 6. The `Write` tool cannot write run artifacts from a worktree session

Every artifact in this directory had to be written with PowerShell. The `Write`
tool refuses the main-checkout path:

```
This session is isolated in the worktree C:\Claude\WhisperDeck\.claude\worktrees\issue-309-fts-delete-trigger.
Edit the worktree copy of this file instead of the shared-checkout path.
```

The runner prompt requires exactly this write (run artifacts go to the main
checkout, code goes to the worktree), so the two are in direct conflict for
Claude Code sessions.

**Recommended fix to the prompt:** state up front that run-artifact writes must
use PowerShell `Set-Content` or a heredoc, because `Write`/`Edit` are sandboxed
to the worktree and will refuse the `<MAIN>` path.

## 7. Phase 1.5 (Fable completion-race check) not applicable, not skipped

The prompt makes Phase 1.5 mandatory "when Phase 1 touches a job/state
completion path". Phase 1's investigation surfaced no code that marks a
job/task/state completed and then triggers a further side effect. The change is
SQLite DDL plus a startup repair function; the delete paths are two FastAPI
route handlers doing per-row ORM deletes. No Fable call was made and none was
warranted. Recorded here so the absence is deliberate and visible rather than
looking like a dropped step.

## 8. `verify_self_audit.py` blocked on a missing dev dependency, not a stale build

First run of the checker:

```
- BUILD [build:js]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
- BUILD [build:css]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
```

Diagnosis before labelling it: `Test-Path C:\Claude\WhisperDeck\node_modules`
returned `False`. The main checkout had no `node_modules` at all, so `esbuild`
(a `devDependencies` entry in `package.json`) was not installed anywhere on this
machine. Nothing in this diff could cause that, and it is not a stale artifact,
it is absent tooling, so it would reproduce on any checkout of any branch.

Fixed properly rather than skipped: `npm install --no-audit --no-fund` in the
main checkout, `added 2 packages in 8s`. The checker then passed its build check
for real, which also confirms the committed bundles match a fresh build. Safe to
do from a worktree session: `node_modules/` is ignored at `.gitignore:24`
(`git check-ignore -v` confirms), and the main checkout still reports zero lines
from `git status --porcelain -uall` afterwards.

**Recommended fix:** the prompt's infra notes already say fresh worktrees have no
`node_modules` and to use the main checkout's binaries. Add that the main checkout
may not have them either, and that the fix is `npm install` there, not
`--skip-build-check`. Skipping would have hidden a genuine build check behind an
"out of scope, pre-existing" label when one `npm install` made it run.
