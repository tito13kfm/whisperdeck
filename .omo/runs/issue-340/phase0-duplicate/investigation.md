# Issue #340 - stopped at Phase 0, duplicate

Run date: 2026-08-05. No worktree created, no branch, no code changed, no PR.

## Resolution

#340 is a standalone issue, not a tracking issue. Its headline defect was
already fixed before it was filed.

- `51c3b1d` "fix(voice_match): honor a cancel instead of writing anyway (#331)",
  merged 2026-08-04 17:48 UTC, closes #330 "voice_match commits relabel and
  segment overwrite after a cancel".
- #340 created 2026-08-05 02:14 UTC, about eight hours later.
- Cause of the duplicate: #340 was filed from the #111 / PR #337 investigation,
  running on a branch cut before #331 merged. Not a filing-discipline problem.

Verified on master, both guards #340 asks for exist:

- `services/llm_jobs.py:747-749` - `db.refresh(job)` / `if job.status ==
  "cancelled": return` at the top of the per-segment loop, outside the
  per-segment `try`.
- `services/llm_jobs.py:794-796` - same guard immediately before
  `record_relabel` and `transcript.segments = new_segments`.

## Per-claim verdict

The issue body carries three claims. Each was checked separately, because
"already fixed" is a per-claim verdict, not a per-issue verdict.

1. Headline (no loop poll, no pre-write guard in `voice_match`) - FIXED, see
   above.
2. Complement note (sweep the other `run_llm_job` branches) - NOT DONE, and
   doing it finds a real gap. Filed as #346.
3. `_finish(db, job, "completed", error)` producing a completed job with a
   non-null `error` - REAL, and the predicted consumer misread does occur.
   Recorded in #346.

## Complement sweep, full result

Guarded: `correction` (`:369-371`), `classify_pipeline` (`:476`, `:487`),
`tagging` (`:532`), `voice_note` (`:569`), `voice_dump` (`:631`), `voice_match`
(`:748`, `:795`).

Unguarded but fine: `summary` (`:412`), `format_markdown` / `format_email` /
`format_coding_prompt` (`:432`), `classify_intent` (`:451`). Single await, then
a write to the job's own `result_json` only. `_finish` already refuses to
overwrite a `cancelled` status, so the only residue is a stale `result_json`.

Unguarded and broken: `rediarize` (`:655-689`). Long `diarize_and_merge` await
with no poll, then `clear_relabel_history(db, transcript.id)` at `:679` followed
by overwrites of `transcript.segments`, `speaker_count`, `diarization_method`.
Worse than the #330 case it mirrors: #330 was undoable because `record_relabel`
had already stored an inverse patch, whereas rediarize's first write deletes
every inverse patch, so a cancelled rediarize destroys the undo history and
relabels anyway with nothing left to undo it with.

## Error-field misread, evidence

`static/rack.js:3488` and `:3518`:

```js
color:${j.error ? 'var(--red)' : 'var(--label-dim)'}
```

No status check, so a completed `voice_match` carrying skipped/degraded notes
renders its Queue meta line in the failure color. Cosmetic only: the background
poller gates on `j.status === 'failed'` (`rack.js:909`, `:920`), so no false
failure toast fires.

## Actions taken

- Filed #346 "rediarize: cancelling a running job wipes relabel history and
  rediarizes anyway", carrying the full sweep result and the error-field note.
- Commented on #340 with the evidence above and closed it as not planned
  (duplicate of #330).

Chosen by the user from four presented options.
