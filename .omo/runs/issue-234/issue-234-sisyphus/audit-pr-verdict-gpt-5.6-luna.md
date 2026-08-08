## PR Audit: #256 feat: Queue and Tape Library batch grouping (closes #234)   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: BLOCK

### Blocking
- `static/rack.js:3354` counts cancelled transcripts as completed (`const done = counts.completed + counts.cancelled`). Failure scenario: a batch with one completed transcript and one cancelled transcript renders `2/2` and a full green bar, then the completion toast says `2/2 files transcribed`, although one file was never transcribed. Fix: use `counts.completed` for the completed ratio and display cancelled files separately or as an incomplete/aborted terminal outcome. Regression test: render a batch containing `{status: 'completed'}` and `{status: 'cancelled'}` and assert the batch nixie is `1/2`, not `2/2`, and no success toast claims both files were transcribed.
- `static/rack.js:3343-3344` suppresses the batch header whenever a batch contains only one queue entry. Failure scenario: the user submits a one-file bulk import, which receives a `batch_id`, then opens Queue; the entry is moved to `others`, so there is no BATCH header, batch count, or batch-level Cancel all/Open batch actions. Fix: render a batch header for every `batch_id`, including one-entry batches, or explicitly prevent one-file bulk imports from creating batches. Regression test: render one transcription job with a nonempty `batch_id` and assert the output contains `.batch-group`, `Cancel all`, and `Open batch`.
- `.omo/runs/issue-234/issue-234-sisyphus/self-audit.md:10-11,15` contains false `[x]` location claims. The cited ranges do not contain the claimed grouping, completion-toast, or batch-action implementations: the implementations are at `static/rack.js:3332-3420`, `3366-3375`, and `3477-3495`. Failure scenario: a reviewer follows the self-audit's cited lines and verifies the wrong code, so the checklist's stated artifact locations are not reliable evidence for the delivered behavior. Fix: regenerate the self-audit line references against the checked-out PR commit. Regression test: a checklist-location check that opens every cited range and asserts the named symbol or behavior is present.

### Should fix
- [robustness] `static/rack.js:3333-3344` only groups batches when at least two entries survive the `/api/jobs?limit=50` response. Failure scenario: a batch has two transcripts, but one is outside the 50-entry slice, so the remaining transcript is rendered as an ordinary queue row and the batch header is absent. Fix: make the queue endpoint return a complete batch group or group by batch IDs before applying the response limit.

### Nits
- `static/rack.js:3345-3362` tracks `running` entries as active but never includes them in `statusLine` because the counter is named `processing`; an active batch can show no processing count.

### Honesty check
- self-audit.md [x] lines verified: 13/18. False [x] found: line 9, 10, 11, 13, and 15 location claims.
- Vacuous / loosened tests: none. The two new tests assert both key presence and exact batch values, and fail when the return body is replaced with `{}`.
- Undisclosed scope (diff vs claims): none found. The omitted retry action, creation date, and browser check are explicitly marked unchecked in the self-audit.

### Read scope
- Focused read on `app.py`, `static/rack.js`, `static/rack.css`, `static/rack.min.css`, `tests/test_bulk_import.py`, plus the called queue and batch API paths. The diff is 207 added/deleted lines, but `static/rack.js` is a large file.

### Summary
The backend field and focused tests pass, and the full worktree suite passed with 624 passed and 7 deselected. The Queue aggregate is nevertheless incorrect for cancelled batch members, and the self-audit includes several materially wrong line references, so this PR is not ready to merge.
