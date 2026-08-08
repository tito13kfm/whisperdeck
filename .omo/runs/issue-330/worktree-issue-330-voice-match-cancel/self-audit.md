# self-audit.md — issue #330 (PR #331)

Branch `worktree-issue-330-voice-match-cancel`, rebased onto `master` after #327 landed (`83f661e`). Written after the fact rather than during, because this PR was opened directly rather than through `/issue-claude`; the #332 audit correctly flagged the absence of one. Every `[x]` was re-confirmed by opening the cited file at the cited line on the rebased tree, not from memory. Full suite run before any test box was checked.

## What #330 reported, item by item

[x] `voice_match` was the only job kind with no cancellation check — confirmed against the pre-fix branch; the fix adds the first two, at `services/llm_jobs.py:740` and `services/llm_jobs.py:777`. Sibling guards it now matches: `voice_dump` at `services/llm_jobs.py:630`, `tagging` at `services/llm_jobs.py:531`, `classify_pipeline` at `services/llm_jobs.py:486`, `voice_note` at `services/llm_jobs.py:568`.

[x] "A cancel does not cancel: it silently completes the write" — fixed by the guard immediately before the dependent writes, `db.refresh(job)` / `if job.status == "cancelled": return` at `services/llm_jobs.py:777-779`, which sits ahead of the `if changed:` block at `services/llm_jobs.py:780` that calls `record_relabel`.

[x] "Every remaining segment still burns ffmpeg and embedding CPU after the user asked to stop" — fixed by the loop-top guard at `services/llm_jobs.py:740-742`, inside `for i, seg in enumerate(segments):` at `services/llm_jobs.py:732`. Asserted directly: `test_voice_match_cancel_mid_loop_stops_and_leaves_transcript_unchanged` checks `cancel_then_extract.calls == 1` of three segments.

[ ] "Progress keeps advancing on a cancelled job" — **PARTIALLY delivered.** Progress no longer advances for the remaining segments, but the iteration already in flight when the cancel lands still runs its own `job.progress_done = i + 1; db.commit()`, so a cancelled job can end showing a non-zero count that `cancel_llm_job` had zeroed. See the `[decision]` below.

[ ] `classify_pipeline`'s pre-`_finish` enqueues (`services/llm_jobs.py:486-516`) — NOT delivered, listed in #330 as a weaker adjacent item, not this PR's scope. Disclosed in the PR body.

[ ] `_finish` overwriting an already-`"completed"` job with `"failed"` from the outer handler — NOT delivered, same reason.

## Tests, with mutation checks

[x] `test_voice_match_cancel_mid_loop_stops_and_leaves_transcript_unchanged` — `tests/test_voice_match_job.py:282`. Red-green confirmed before the fix existed; the red failure was the reported symptom exactly, `AssertionError: assert 'Alice' == 'SPEAKER_00'` on a cancelled job. Asserts status, the extraction call count, all three speaker labels, and no `RelabelHistory` row.

[x] `test_voice_match_cancel_after_a_committed_segment_still_skips_the_writes` — `tests/test_voice_match_job.py:327`. Added after review pointed out that the other two tests both cancel before the loop body has committed anything. Cancels during the second of three extractions, after segment 0's `db.commit()` has flushed the session (`transcript` included), pinning down that the guard's `return` leaves nothing partially durable.

[x] `test_voice_match_cancel_during_final_segment_still_skips_the_writes` — `tests/test_voice_match_job.py:373`. Covers the case the loop guard structurally cannot: a cancel during the last iteration has no further iteration to catch it.

[x] Mutation checks, run for real on the rebased tree, each guard killed only by the tests the other does not cover:

```
loop guard removed      -> mid_loop, after_a_committed_segment
pre-write guard removed -> during_final_segment
```

Neither guard is redundant, and neither became redundant once #327's pre-flight guard landed upstream of both.

[x] `_relabel_rows` (`tests/test_voice_match_job.py:277`) asserts `== []`, which could pass for the wrong reason (wrong `transcript_id`). Positive control: `tests/test_relabel_undo.py::test_voice_match_records_relabel_history` still passes, proving a row IS written on a non-cancelled run. Confirmed by running that test by name, not just as part of the suite.

[x] A test asserting only `job.status == "cancelled"` would have been vacuous here, since that already passed before the fix. Stated in the PR body so the reader does not mistake the status assertions for the substance.

[x] Full suite run before checking any test box — `838 passed, 22 deselected` at the time of writing (that figure includes #332's branch; on #331's own branch it is `833 passed, 22 deselected`). CI green on the exact head `dc8a06a39`.

## Decisions the issue did not ask for

[decision] Left the in-flight iteration's `progress_done` commit alone, so a cancelled job can show a stale non-zero count — not specified by the issue as a required fix, and `voice_dump` behaves identically. Matching the sibling's shape beat inventing a third behavior for a cosmetic counter. Flagged in the PR body with an explicit offer to zero it if wanted, rather than left silent.

[decision] Put the loop guard OUTSIDE the per-segment `try` block — not specified by the issue, because the block's `except Exception: skipped += 1` would otherwise swallow a `db.refresh` failure into the per-segment skip count and report it as a failed extraction.

[decision] Copied `voice_dump`'s `db.refresh(job)` / `if job.status == "cancelled": return` shape verbatim rather than designing a cancellation helper — not specified by the issue. A shared helper across five job kinds is a wider refactor than this fix warrants, and the repo's own guidance is to find the existing pattern and reuse its shape.

[decision] Did not add cancellation checks to the other job kinds — they already have them. Verified rather than assumed, by listing every `db.refresh(job)` / `"cancelled"` pair in the file (cited above).

## Rebase disclosure

[x] Rebased onto `master` after #327 merged. The conflict was real: both PRs inserted tests at the same anchor in `tests/test_voice_match_job.py` and git interleaved them into five hunks. Resolved by taking master's version of the file wholesale and re-adding this PR's three tests plus `_relabel_rows` as one block. All of #327's tests verified present afterwards. `services/llm_jobs.py` auto-merged cleanly. The follow-up commit was dropped by the rebase ("patch contents already upstream") since its content folded into the resolution.

[x] `_enrolled_profile`'s signature is now #327's version, and these tests call it with defaults, which resolve to the running backend's model id. So they pass through #327's new pre-flight guard rather than around it. Verified by re-running, not assumed.

## Review status

[x] **No independent-model audit has run on this PR yet.** This file is self-review only. Independent review happens via `/audit-pr 331`.

[x] No `static/` file touched, so no bundle rebuild is implicated and no e2e selector changes are needed. `scripts/verify_self_audit.py` reports `OK: no stale builds, no suspect line citations.` on this branch.
