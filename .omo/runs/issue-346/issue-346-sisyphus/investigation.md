# investigation.md — issue #346

**Target**: #346 — standalone issue
**Worktree**: `C:/Claude/whisperdesk/.claude/worktrees/issue-346-sisyphus`
**Main checkout**: `C:/Claude/whisperdesk`
**Base commit**: `e2cb966` (origin/master)

## Summary

The `rediarize` branch of `run_llm_job` (`services/llm_jobs.py:655-689`) has no cancellation guard after the long-running `diarize_and_merge` await. If a cancel lands during that await, the code still executes `clear_relabel_history` (deletes all undo data) and overwrites `transcript.segments` before `_finish` notices the cancel. The job row says `cancelled`, but the transcript is fully rediarized with no undo history left.

## Real line numbers (current file)

- `rediarize` handler: `services/llm_jobs.py:655-689`
- `diarize_and_merge` await: line 667-673
- `clear_relabel_history` call: line 679
- `transcript.segments` assignment: line 680
- `_finish(db, job, "completed")`: line 687
- `voice_note` guard (pattern to mirror): `services/llm_jobs.py:568-570`
- `_finish` function: `services/llm_jobs.py:319-330`
- `cancel_llm_job`: `services/llm_jobs.py:295-307`

## Fix plan

Insert after line 673 (after the `diarize_and_merge` await closes):
```python
db.refresh(job)
if job.status == "cancelled":
    return
```

This mirrors `voice_note`'s guard at lines 568-570. Must land before `clear_relabel_history` at line 679.

## Sibling sweep

| Branch | Guarded? | Line(s) | Notes |
|--------|----------|---------|-------|
| correction | Yes | 370-371 | cancel_cb + _finish race guard |
| summary | Yes | — | Single await, only writes job.result_json, _finish handles cancel |
| format_markdown | Yes | — | Same as summary |
| format_email | Yes | — | Same as summary |
| format_coding_prompt | Yes | — | Same as summary |
| classify_intent | Yes | — | Same as summary |
| classify_pipeline | Yes | 475-476, 486-488 | Guard in except + guard after await |
| tagging | Yes | 531-533 | Guard after await, before TranscriptTag delete |
| voice_note | Yes | 568-570 | Guard after await, before VoiceNote write |
| voice_dump | Yes | 630-632 | Per-segment guard in loop |
| **rediarize** | **NO** | **—** | **BUG: no post-await guard** |
| voice_match | Yes | 747-749, 794-796 | Per-segment + post-loop guard |
| assistant | Yes | — | _finish handles cancel race |

Issue's sweep claim confirmed. rediarize is the only unguarded destructive-write branch.

## Absence checks (commands + output)

- **rediarize JOB logic only in run_llm_job**: `rg -n "diarize_and_merge" --type py` — 3 callers: app.py:1384 (initial upload, not a job), services/llm_jobs.py:667 (the bug), services/queue.py:566 (chunked transcription finalization, not rediarize). The rediarize job's specific combination (diarize_and_merge + clear_relabel_history + transcript.segments update) only exists at services/llm_jobs.py:655-689.

- **no tests covering rediarize cancellation**: `rg -n "cancel" tests/ --type py | rg -i "rediarize"` → no output.

- **no dedicated test file**: `ls tests/ | rg -i "rediarize"` → no output. Tests are in test_llm_jobs.py, test_posthoc_reprocess.py, test_relabel_undo.py, etc.

- **clear_relabel_history callers**: `rg -n "clear_relabel_history" --type py` → 3 callers: app.py:2088 (manual route), services/llm_jobs.py:679 (the bug), services/queue.py:600 (transcription queue). Only the llm_jobs.py call is in a cancel-vulnerable job handler.

## Phase 1.5 concern

**No.** `_finish(db, job, "completed")` at line 687 is the last action in the try block. No chained dispatch (classification trigger, follow-up job enqueue) after it. The except block only calls `_finish(db, job, "failed", str(e))`. No completion-race.

## Minor second issue (cosmetic, separate from the fix)

`voice_match` at `services/llm_jobs.py:810-824` builds `error` from skipped/degraded/unmatchable counts and passes it to `_finish(db, job, "completed", error)`. So a fully completed voice_match can have non-null `error` (e.g., "3 segment(s) skipped...").

`static/rack.js:3488` and `:3518` both render the Queue meta line as:
```js
color:${j.error ? 'var(--red)' : 'var(--label-dim)'}
```
No status check. A completed voice_match with notes shows in red.

Cosmetic only — the poller that toasts failures gates on `j.status === 'failed'` (rack.js:909, 920). Not fixing here; filing for separate consideration.

## Acceptance criteria (from the issue body)

1. [ ] Cancelling a running rediarize job must not clear relabel history — return before `clear_relabel_history`
2. [ ] Cancelling must not overwrite transcript.segments — same guard stops this
3. [ ] _finish must still leave job.status as "cancelled" — it already does (line 321-326)
4. [ ] Guard pattern must mirror voice_note (db.refresh + status check) — same pattern
