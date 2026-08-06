# Wrong directions, issue #111

Discrepancies found while executing instructions (the issue text, the issue-runner
prompt, AGENTS.md, skills). Written as they happen, not backfilled.

## Open PR check

No open PRs at Phase 0 start (`gh pr list --state open` returned `[]`), so no
merge-conflict risk against a parallel change to `services/llm_jobs.py`.

## The issue's line numbers are stale

Issue #111 cites `services/llm_jobs.py:419-421` for `voice_match` and "line 353"
for `rediarize`. Real current locations: `voice_match` post-relabel writes at
`services/llm_jobs.py:744-752`, `rediarize` at `674-687`. Consistent with the
runner prompt's warning not to trust the issue's own line numbers. No fix needed
beyond ignoring them.

## The issue names 1 of 5 affected paths

The issue presents this as a single missing line in `voice_match`. It is one
instance of a class: five paths rewrite `transcript.segments` without re-running
diarization, and none of the five updated `speaker_count`.

- `services/llm_jobs.py` `voice_match` branch (the one named)
- `app.py` `update_transcript`, `PATCH /api/transcripts/{id}` with a `segments` body
- `app.py` `rename_transcript_speaker`
- `app.py` `retag_transcript_segments`
- `app.py` `undo_last_relabel`

Fixing only the named one would have left `undo_last_relabel` as a new stale
path: undoing a voice match would restore the old labels while leaving the
freshly recomputed count in place, which is the same bug in reverse. Per the
repo's Complement Rule, all five are fixed. Recommended fix to the issue itself:
close it as covered by this PR, and note in the tracker that the report scoped
the defect to one call site when the grep-verified scope was five.

## The issue's proposed snippet is subtly wrong on the "Unknown" sentinel

The issue proposes:

```python
transcript.speaker_count = len({s.get("speaker") for s in new_segments if s.get("speaker")})
```

That filter excludes only falsy labels. But `combine_with_transcript`
(`services/diarization.py:300`) writes the literal string `"Unknown"` into a
persisted segment whenever that segment overlapped no diarization turn:

```python
"speaker": best_speaker or seg.get("speaker", "Unknown"),
```

Meanwhile all three diarization services compute `speaker_count` from the
diarization turns BEFORE that fallback is applied
(`services/diarization.py:129`, `:252`, `:420`). So on a transcript that already
has an `"Unknown"` gap from an earlier pyannote or live_stereo rediarize, the
issue's snippet would count `"Unknown"` as a real participant and report a
HIGHER number than `rediarize` itself reports for the same segment list. The
shipped helper excludes it. Recommended fix to the issue: none needed, the
snippet was a sketch, but this is the reason the fix is a shared helper rather
than the proposed one-liner.

## Residual definition gap, disclosed and NOT fixed here

There is no single definition of `speaker_count` in this codebase even after
this change:

- The diarize paths (`rediarize`, initial diarize, chunked finalize) store the
  count of diarization CLUSTERS, computed pre-merge.
- The five relabel paths now store the count of distinct LABELS present in the
  stored segments, computed post-merge.

These can still disagree without any relabel happening: if a pyannote cluster's
turns lose the overlap ranking in every transcript segment, that cluster wins no
label in `merged`, so the stored pre-merge count exceeds the post-merge label
count. Consequence: the first relabel action on such a transcript will revise
the displayed count downward, even a voice match that matched nothing.

Not fixed here because unifying it means changing `rediarize` and the two
first-diarization paths, which are not broken (they do write the field) and are
outside issue #111's scope. Filed as issue #341: define speaker_count once,
post-merge, for every writer.

## Pre-existing defect found by the Phase 1.5 (Fable) check, out of scope

`voice_match` has no cancellation handling inside its per-segment loop and no
`db.refresh(job); if job.status == "cancelled": return` guard before its
`transcript.segments = ...; db.commit()`. Contrast the `voice_dump` structuring
branch (`services/llm_jobs.py:629-632`) and the tagging branch (`:531-533`),
which both poll or guard before their dependent writes.

Effect: cancelling a running voice_match neither stops the per-segment embedding
work nor prevents the transcript from being relabeled. The job row does end
correctly `cancelled` (the per-segment `db.commit()` only flushes the dirty
`progress_done` column, so it does not clobber the concurrent status write), and
the relabel is undoable via the recorded `RelabelHistory` row, so severity is
moderate rather than data-corrupting.

Independent of `speaker_count`: it exists identically before and after this
change, and the new assignment lands in the same transaction as the segments
write, so the two can never disagree with each other in any interleaving.
Deliberately NOT folded into this PR. Filed as issue #340.

## verify_self_audit.py cannot run its build check on this machine

`python scripts/verify_self_audit.py` reported exactly 2 blocking findings, both
the same thing:

```
- BUILD [build:js]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
- BUILD [build:css]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
```

Zero findings on the `file:line` citation checks, which are the part that
audits this change.

Cause is NOT a stale bundle. `esbuild` is a devDependency in `package.json`
(`"esbuild": "^0.25.0"`), but `node_modules` is absent from the MAIN checkout,
not just from the worktree, so the binary does not exist anywhere on this
machine. The runner prompt's guidance covers the neighboring case ("if it
reports a stale build unrelated to any file you touched, that's a pre-existing
condition"), and the disposition here is the same: not introduced by this task,
not fixed as part of it, since fixing it means running `npm install` into the
main checkout.

It is also vacuous for this PR specifically: `git status --short` shows the
change touches only `app.py`, `services/llm_jobs.py`, `services/relabel.py`,
and three files under `tests/`. Nothing in `static/` is modified, so the
bundle byte-diff has nothing to catch.

Recommended fix to the workflow: have `verify_self_audit.py` downgrade the
BUILD checks from blocking to advisory when no `static/` file is modified in
the diff, or skip them with an explicit "toolchain absent" message instead of
reporting them as blocking findings that read like real defects.

## Also noted, pre-existing, out of scope

`_finish(db, job, "completed", error)` at the end of `voice_match` produces a
job row with `status == "completed"` and a non-null `error` when segments were
skipped. Any consumer inferring failure from `error is not None` would misread
it. Predates this change.
