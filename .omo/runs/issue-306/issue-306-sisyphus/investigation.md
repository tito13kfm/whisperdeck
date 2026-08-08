# Investigation, issue #306

## Target and setup

Issue #306 is a standalone open issue, targeted directly. `gh pr view 306 --json number,title,headRefName,state` returned GraphQL error because #306 is not a PR. Fresh base: `origin/master` at `6b646319c0d5abe5c8142fb9d40295162d38672d`. Worktree: `C:/Claude/whisperdesk/.claude/worktrees/issue-306-sisyphus` on `issue-306-sisyphus`; main checkout: `C:/Claude/whisperdesk` on `master`.

Prior-work checks: `git log --all --oneline -- services/llm_jobs.py` found the original cancellation fix `dc009d3`; `gh issue list --state closed --limit 30 --search "correction progress"` found closed #104, the parent symptom, but no merged work for #306. #306 remains live.

## Current code

- `services/llm_jobs.py:364-367`, correction `progress(done, total)` assigns `job.progress_done` and `job.progress_total`, then commits, without checking cancellation.
- `services/llm_jobs.py:369-371`, `cancelled()` refreshes the job and returns whether status is `cancelled`.
- `services/llm_jobs.py:302-306`, `cancel_llm_job()` sets status to `cancelled`, zeros both counters, and commits.
- `services/correction.py:125`, correction invokes `progress_cb(done, total)` after each completed batch.
- `services/llm_jobs.py:568-570`, voice-note path already refreshes and returns before writing when cancelled.

## Call-site and sibling sweep

Correction callback is the only caller of this local `progress()` function: `services/correction.py:125`. Same-shape progress/state writers were enumerated: summary/formatting/classification single-await paths use `_finish()` (`services/llm_jobs.py:413-461`); classify-pipeline (`:463-502`), tagging (`:520-542`), voice-note (`:557-608`), voice-dump (`:618-650`), rediarize (`:656-687`), and voice-match (`:729-799`) have explicit cancellation checks; assistant paths (`:853-897`) use `_finish()`, with loop work delegated through `services/assistant.py:174`. No sibling callback writer without an existing cancellation guard was found. The investigation command/output supporting the sibling sweep is recorded in the delegated report: it used `sed` around each cited range and returned the current code.

## Issue snippet comparison

The issue's suggested approach is correct in shape but incomplete as an implementation plan: it names the guard but not a regression test that reproduces cancellation after a batch completes. The guard must refresh the job before checking status, matching the existing `voice_note` pattern, otherwise a stale ORM object can miss a cancellation committed by another session.

## Test gap and plan

`tests/test_llm_jobs.py:254` tests cancellation between batches but does not assert progress counters; `:319` tests direct cancellation zeroing but not a callback racing afterward; `:173` covers only successful correction progress. Add a regression test in `tests/test_llm_jobs.py` that simulates cancellation during the first batch, then asserts exact `status == "cancelled"`, `progress_done == 0`, and `progress_total == 0`, plus no second batch/partial output. Run red before the fix and green after it. Mutation check must replace the callback guard/body with the no-guard behavior and produce one failure, then restore and pass.

## Acceptance criteria

1. Correction callback checks cancellation before writing: pending fix.
2. Regression test reproduces and prevents re-inflated counters: pending.
3. Mutation test fails without guard: pending.
4. Existing suite remains green: pending.
5. Sibling sweep complete: yes, enumerated above.
