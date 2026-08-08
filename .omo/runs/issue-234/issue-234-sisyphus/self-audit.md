# Self-audit: Issue #234 — Queue + Tape Library batch grouping

**Date**: 2026-07-30
**Branch**: issue-234-sisyphus

## Investigation.md promises delivered

[x] app.py: Add batch_id to _transcription_queue_entry() — delivered at app.py:2816
[x] rack.js: Add S.batchFilter, S.batchSnapshots, S.expandedBatches — delivered at rack.js:3246-3248
[x] rack.js: Batch grouping in loadQueue() with collapsible headers — delivered at rack.js:3294-3408
[x] rack.js: Batch completion toast detection in queue poll — delivered at rack.js:3321-3329
[x] rack.js: Batch filter dropdown in loadTranscripts() header — delivered at rack.js:3032-3048
[x] rack.js: Batch indicator pill in renderBankRows() rows — delivered at rack.js:3253-3255
[x] rack.js: Batch pill click handler (filter to batch) — delivered at rack.js:3090-3096
[x] rack.js: Batch action handlers (cancel all, open batch) — delivered at rack.js:3445-3459
[x] rack.css: .batch-group, .batch-entry CSS — delivered at rack.css:868-872
[x] rack.css: .batch-pill hover — delivered at rack.css:874
[x] rack.min.css: rebuilt — delivered at rack.min.css (25.5kb)
[x] tests: TestTranscriptionQueueEntryBatchId — delivered at tests/test_bulk_import.py:457-482

## Mutation checks

[x] test_batch_id_in_queue_entry — mutation check: fails with body replaced by return {}? yes ("batch_id" not in entry, KeyError)
[x] test_batch_id_null_in_queue_entry — mutation check: fails with body replaced by return {}? yes ("batch_id" not in entry, KeyError)

## Acceptance criteria walk

Issue #234 acceptance criteria:

1. "In loadQueue, group transcripts by batch_id" — ✓ batch headers inserted, entries show under header
2. "Batch header with aggregate counts, LED bargraph, nixie" — ✓ bargraph(cells), nixie(X/Y), status-badge
3. "Batch-level actions: Cancel all, Open batch" — ✓ Cancel all→POST /api/batches/{id}/cancel, Open batch→navigate to transcripts filtered
4. "Non-batch entries render as before" — ✓ otherRows uses same rendering as original
5. "Batch completion toast" — ✓ S.batchSnapshots comparison on each poll cycle
6. "Tape Library batch filter dropdown" — ✓ #bank-batch-filter with All/In batch/Single uploads/specific batches
7. "Batch indicator on transcript rows" — ✓ .batch-pill with BATCH text, click filters to that batch
8. "Existing tests must still pass" — ✓ 624 passed, 0 failed
9. "Expanded batch state preserved across polls" — ✓ S.expandedBatches Set, openIds pattern

## Issues acknowledged (not delivered, with reason)

[ ] "Retry all failed" batch action — NOT delivered: no POST /api/batches/{batch_id}/retry endpoint exists. Per-investigation finding #4, skipped for MVP.
[ ] Live browser verification — NOT delivered: full test suite (624 tests) passes. Browser MCP not available in this run. Per workflow: "If a live-browser check genuinely isn't possible after one real attempt, do the static check plus the existing unit/integration suite and report the actual error."
[ ] Batch header "Created Jul 29" date — NOT delivered: batch creation timestamp not available in queue entries (transcription entries don't include created_at from batch perspective). Using status line instead.

## Full suite result

624 passed, 0 failed, 7 deselected (63.44s)

## Main repo cleanliness

git -C C:/Claude/whisperdesk diff --stat → (no output) ✓

## Self-report files

[x] investigation.md — .omo/runs/issue-234/issue-234-sisyphus/investigation.md
[x] self-audit.md — .omo/runs/issue-234/issue-234-sisyphus/self-audit.md
[x] wrong-directions.md — .omo/runs/issue-234/issue-234-sisyphus/wrong-directions.md
[x] token-usage.md — .omo/runs/issue-234/issue-234-sisyphus/token-usage.md

## Oracle verdict (Phase 3.75)

**APPROVE** — no regressions found. Minor notes (non-blocking):
- S.batchSnapshots grows per batch, never pruned. Low risk.
- Toast already handles both success and failure cases correctly.
- batch_id truthiness check (`j.batch_id`) correct, won't create group for empty string.

## PR

https://github.com/tito13kfm/whisperdeck/pull/256 — Closes #234