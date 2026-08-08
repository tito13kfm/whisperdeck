# PR Audit: #256 feat: Queue and Tape Library batch grouping (closes #234)   (reviewer: glm-5.2, independent third family)

VERDICT: BLOCK

### Blocking            (empty = none)
- static/rack.js:3357 + 3472-3473 Batch group expand/collapse state is lost on every poll cycle. Failure scenario: user clicks a batch header to expand it, Queue poll fires 3s later, `batchOpen = S.expandedBatches.has(bid)` reads the stale JS Set (the post-render sync at 3472-3473 only re-reads the DOM state that was JUST rendered, it never captures the user's native `<details>` toggle that happened between polls), so the group renders CLOSED again. A user cannot keep a batch group expanded while the Queue is actively polling. Fix: read batch-group open state from the DOM BEFORE `root.innerHTML` replacement, the same way `openIds` (rack.js:3330) already does for individual entries, e.g. `const expandedBatchIds = new Set([...root.querySelectorAll('.batch-group[open]')].map(d => d.dataset.bid));` at the top of loadQueue, then use `expandedBatchIds.has(bid)` for `batchOpen` and sync `S.expandedBatches` from that pre-render read. Regression test: browser-driven check that opens a `.batch-group` details, waits one Queue poll tick (3s), and asserts the group still has the `open` attribute (no pytest can catch this; it is vanilla-JS UI behavior).

### Should fix          (empty = none)
- [feature] static/rack.js:3345,3350,3361 The batch header status line never shows the processing count. Failure scenario: a batch with 1 actively-transcribing transcript (status "running", mapped from `t.status == "processing"` in `_transcription_queue_entry`, app.py:2807) is counted in `activeInBatch` (correct, drives the ACTIVE badge) but the `counts` dict key is "processing" while the actual status value is "running", so `counts["running"]` is undefined and the else-if branch only matches "queued"/"waiting" not "running", leaving `counts.processing` permanently 0 and the statusLine silently omitting "X processing" that issue #234 explicitly requested ("3 complete . 1 processing . 1 pending"). Fix: either add a "running" key to the counts dict, or map status "running" into counts.processing in the tally loop (e.g. handle `j.status === 'running'` explicitly: `counts.processing++`).

### Nits                (empty = none)
- static/rack.js:3354 The `done` variable used for the nixie/bargraph and the success toast includes `counts.cancelled`, so a fully-cancelled batch shows "5/5 files transcribed" in the completion toast (rack.js:3370) and a full bargraph, which is misleading. Consider excluding cancelled from the "transcribed" count in the toast text, or relabel.
- static/rack.js:3355 `Math.max(1, Math.round(done / total * 11))` forces at least 1 lit LED even when done is 0, so a brand-new all-pending batch shows 1 lit cell. Cosmetic.
- self-audit.md line-number citations for rack.js are off by ~34 lines (e.g. state vars cited at 3246-3248, actual 3280-3282), but the artifacts all exist, so this is not a false claim, just stale offsets.

### Honesty check
- self-audit.md [x] lines verified: 10/10 delivered items (line offsets noted above, all artifacts present). 2/2 mutation checks valid (replace body with `return {}` fails both tests). Acceptance criteria walk: 8/9 verified.
- False [x] found: 1. Acceptance criterion 9 ("Expanded batch state preserved across polls -- S.expandedBatches Set, openIds pattern") is false: the openIds pattern is NOT applied to batch groups; batch groups use a separate S.expandedBatches mechanism that does not capture user toggles between renders, so expand state is reset every poll.
- Vacuous / loosened tests: none. `test_batch_id_in_queue_entry` asserts `== "BATCH_QUEUE"` with `==`, `test_batch_id_null_in_queue_entry` asserts `is None`. Both fail if the field is absent or wrong.
- Undisclosed scope (diff vs claims): none. "Retry all failed" action and live browser verification are honestly disclosed as not delivered ([ ]).

### Read scope
- Focused read on app.py:2801-2825 (backend change), static/rack.js:3029-3500 (queue + library changes), tests/test_bulk_import.py:455-487 (new tests), static/rack.css:868-875 (CSS). CSS/min CSS diffs skipped start-to-finish (minified, cost guard); verified the batch selectors exist via grep. Complement sweep: confirmed two callers of `_transcription_queue_entry` (app.py:669 and 2862), both get the additive batch_id field with no breakage.

### Summary
The backend change and tests are correct and honestly reported. The block is a single frontend bug: batch group expand state is not preserved across Queue polls because S.expandedBatches is read at render time and only re-synced from the just-rendered DOM (not the user's toggle), so every 3s poll collapses any group the user opened. This is also a false [x] in the self-audit's acceptance criteria. A secondary should-fix: the batch header status line never shows the processing count because the counts dict key ("processing") never matches the actual queue status value ("running").
