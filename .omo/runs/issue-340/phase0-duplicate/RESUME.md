# Resume note, 2026-08-05 end of work session

Everything from this session is merged and pushed. Nothing is local-only.
Pick up at "Next" below.

## State on disk

- `C:/Claude/WhisperDeck` on `master` at `a6dcb38`, clean, in sync with
  `origin/master`.
- One worktree only: the main checkout. The `issue-runner-prior-work-check`
  worktree was removed after merge.
- Local branches: `master`, plus `backup-issue109-preexisting` (`e33fb90`).
  That backup branch has one commit not on master and predates this session,
  so it was left alone. Delete it if you know it is no longer needed.
- `worktree-issue-109-voiceid-fallback` was deleted, fully merged at `1526157`.
- Untracked and expected: `.skill-observations/`. See "Loose end" below.

## What happened

Ran `/issue-claude 340`. It stopped at Phase 0 because #340 was a duplicate.

**#340: closed as not planned.** Duplicate of #330, fixed by `51c3b1d` / PR
#331, merged 2026-08-04 17:48 UTC. #340 was filed 2026-08-05 02:14 UTC, about
eight hours later, from the #111 / PR #337 investigation branch, which was cut
before that merge. Both guards it asks for are on master at
`services/llm_jobs.py:747-749` and `:794-796`. The closing comment on #340
carries the full evidence and a per-claim verdict.

**#346: filed, open, unstarted.** `rediarize: cancelling a running job wipes
relabel history and rediarizes anyway`. This came out of the complement sweep
#340 asked for and nobody had done.

**PR #347: merged** (`a6dcb38`). Two fixes to Phase 0's prior-work check in
both `.claude/issue-runner-prompt.md` and `.omo/issue-runner-prompt.md`. CI
green, branch deleted both remote and local.

## Next: issue #346

Ready to run as `/issue-claude 346`. Everything the investigation needs is
already in the issue body, including the full per-branch sweep, so Phase 1
should be short.

The defect, in one paragraph: `rediarize` (`services/llm_jobs.py:655-689`) is
the last `run_llm_job` branch with a long-running body and destructive writes
to another entity and no cancel check at all. `diarize_and_merge` runs for
minutes with nothing polling `job.status`, then `clear_relabel_history(db,
transcript.id)` at `:679` deletes every stored inverse patch, then
`transcript.segments` / `speaker_count` / `diarization_method` are overwritten.
Worse than the #330 case it mirrors: #330 was undoable precisely because
`record_relabel` had already stored an inverse patch, whereas here the first
write destroys the undo record, so a cancelled rediarize leaves the user with
a relabeled transcript and nothing to revert it with.

Fix shape: mirror `voice_note`, which is the closest sibling (one long await,
then an artifact write). Add `db.refresh(job)` / `if job.status ==
"cancelled": return` immediately after the `diarize_and_merge` await and before
`clear_relabel_history`, inside the existing `try`. There is no useful
loop-level poll to add, since `diarize_and_merge` is a single opaque await, so
the pre-write guard is the whole fix.

Open question the issue records but does not decide: whether
`diarize_and_merge` should grow a `cancel_cb` like `correction` has, so a
cancel actually stops paying for the diarization instead of only discarding
its result. Larger change, probably separate.

Also in #346, separate and minor: `job.error` currently means both "why this
failed" and "non-fatal notes about a success", and `static/rack.js:3488` and
`:3518` color the Queue meta line on `j.error` with no status check, so a
completed `voice_match` that skipped a segment renders red. Cosmetic, no false
failure toast (the poller gates on `j.status === 'failed'` at `rack.js:909`,
`:920`). Narrow fix is a status check in those two expressions. Structural fix
is a separate `job.notes` field. Not decided.

## Loose end, unrelated and small

`.gitignore:59` says `skill-observations/` but the directory is
`.skill-observations/`, with a leading dot. Git matches path components
literally, so the ignore rule does not apply and the directory shows up as
untracked in every `git status`. Predates this session. Either fix the rule to
`.skill-observations/` or track the directory on purpose.

## Run artifacts from this session

Same directory as this file:

- `investigation.md` - Phase 0 resolution, per-claim verdict, full sweep table
- `wrong-directions.md` - the two workflow gaps, which became PR #347
- `token-usage.md` - zero `Agent()` calls, the run never reached the
  delegating phases
