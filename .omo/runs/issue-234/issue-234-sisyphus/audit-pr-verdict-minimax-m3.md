## PR Audit: #256 feat: Queue and Tape Library batch grouping (closes #234)   (reviewer: minimax-m3, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)

### Should fix          (empty = none)

### Nits
- [robustness] static/rack.js:3372 Completion toast fires misleading wording after Cancel all. Failure scenario: batch has 5 active files (1 running + 4 queued), user clicks Cancel all; backend flips them to status='cancelled', frontend's post-cancel loadQueue({force:true}) re-runs the completion-toast logic with snap.active > 0 and new activeInBatch === 0 and failedInBatch === 0 (cancellation doesn't set 'failed'), so the success branch fires `toast(\`Batch complete: ${done}/${total} files transcribed\`, 'info')` where done = counts.completed + counts.cancelled = 0 + 5 = 5. User sees "Cancelled 5 files in batch" followed by "Batch complete: 5/5 files transcribed" — the second toast is factually wrong (0 were transcribed). Fix: either skip the success-path toast when counts.cancelled > 0 and counts.completed === 0, or render a different message such as "Batch cancelled" / show both completed and cancelled counts.

- [robustness] static/rack.js:3491 Cancel-all toast on already-terminal batch. Failure scenario: user clicks Cancel all on a batch where every transcript is already completed/failed; POST /api/batches/{id}/cancel returns 200 with `{cancelled: 0, already_terminal: N}`. The handler unconditionally toasts `toast('Cancelled ' + r.cancelled + ' file' + (r.cancelled !== 1 ? 's' : '') + ' in batch', 'info')` so the user sees "Cancelled 0 files in batch". Fix: check `r.cancelled === 0` and either toast nothing or include `r.already_terminal` ("No active files — already terminal").

- [feature] static/rack.js:3344 Batches with a single transcript fall through to "others". The code `if (group.length < 2) { others.push(...group); continue; }` skips the batch header for any batch_id with only one entry. Issue #234 says "For each distinct batch_id, add a collapsible batch header above the group" without a minimum-size clause, so a user who created a 1-file batch loses the BATCH indicator and batch-level Cancel/Open actions on the Queue page. Reasonable design simplification but a deviation from the spec wording.

- [robustness] static/rack.js:3280 Stale S.batchSnapshots on nav-away-and-back. Acknowledged by self-audit ("Low risk"). Batch snapshots are never pruned, so a user who navigates away from Queue while a batch is active and returns after it finished will fire a stale "Batch complete" toast. Acceptable for MVP.

### Honesty check
- self-audit.md [x] lines verified: 12/12. All cited file:line locations exist (the line numbers in the audit trail drift by a few dozen lines vs the audit cite, which is normal for a hot branch). False [x] found: none.
- Mutation checks for TestTranscriptionQueueEntryBatchId: both valid. Replacing `_transcription_queue_entry` body with `return {}` would fail `assert "batch_id" in entry` immediately on both tests.
- Vacuous / loosened tests: none. Both new tests assert concrete `entry["batch_id"] == "BATCH_QUEUE"` and `entry["batch_id"] is None` with `==`, not membership.
- Undisclosed scope (diff vs claims): none. The PR's three omitted items (Retry-all-failed button, batch creation date, live browser verification) are all explicitly listed as "[ ]" in self-audit.md with reasons. Self-report matches reality.
- Acceptance criteria walk in self-audit.md: items 1-9 each map to real code locations; verified.
- Test suite claim: 624 passed, 0 failed. Verified by running `pytest -q` against this worktree at d497924: 624 passed, 7 deselected in 59.63s.
- Static scan: no asyncio.run() hits in the new code paths (the existing ones in services/cost.py:96 and tests/test_correction_routing.py are pre-existing and unreachable from loadQueue/loadTranscripts). No bare except swallow in new code. No missing-await regression: loadQueue's internal `setTimeout(..., 50)` for open-batch is a belt-and-suspenders around the S.batchFilter = bid assignment; verify order (S.batchFilter is set synchronously after navigate() but before loadTranscripts's awaited render), so the redundant call doesn't break correctness, just wastes a fetch in the slow-network edge case.

### Read scope
- Focused read. Read changed hunks plus the call sites they touch: app.py:2801-2824 (_transcription_queue_entry body), static/rack.js:3020-3095 (Tape Library filter dropdown + pill click), static/rack.js:3276-3496 (Queue batch grouping), services/queue.py:281-309 (cancel_transcript_jobs to verify post-cancel status), app.py:1612-1649 (cancel_batch endpoint shape). Total diff is 333 lines; reads scoped to ~250 lines of changed code plus 100 lines of call-site context.

### Summary
PR delivers exactly what its title and self-audit claim: batch grouping on the Queue page with collapsible headers, completion-toast transition detection, batch filter dropdown on the Tape Library, BATCH indicator pills on tape rows, and the single-line backend change that exposes `batch_id` in queue entries. Full pytest suite (624 tests) green; both new tests have valid mutation checks. The completion toast wording bug and the cancel-on-empty toast bug are real UX defects worth fixing in a follow-up, but they don't break the cancellation flow itself and don't meet the BLOCK bar. Frontend behavior is not browser-verified, as the self-audit explicitly notes. Verdict APPROVE.
