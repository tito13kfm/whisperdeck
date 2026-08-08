# Wrong directions: Issue #234

## AGENTS.md Lemonade Server section

**Claim**: "As of 2026-07-28: `atlas` and the `writing` category are mapped to local Lemonade models."
**Actual**: Checked `~/.config/opencode/oh-my-openagent.json` fresh. `atlas` = `lemonade/Qwen3.5-4B-MTP-GGUF` (local). `writing` = `lemonade/Bonsai-8B-gguf` (local). `explore` = `openrouter/inclusionai/ling-3.0-flash:free` (cloud). All correct as of 2026-07-30.
**Resolution**: No discrepancy. AGENTS.md snapshot is still accurate.

## Issue spec "Retry all failed" button

**Claim**: Batch header should include `<button data-bact="retry" data-bid="...">Retry all failed</button>`.
**Actual**: No `POST /api/batches/{batch_id}/retry` endpoint exists. Only `cancel` is available. Individual transcripts can be retried via the per-entry buttons inside the expanded batch group.
**Resolution**: Skipped for this PR. Speculated that retry will be added later, or per-transcript retry suffices.

## Issue spec "Created Jul 29" date in batch header

**Claim**: Batch header should show "Created Jul 29" in the status line.
**Actual**: `_transcription_queue_entry` doesn't include a batch-level creation time. Individual entries have `created_at` which could be used as the earliest, but this adds complexity for limited value.
**Resolution**: Using status counts (X done, Y processing, etc.) in the status line instead. Batch creation date is cosmetic.

## No sub-agent delegation

Per the delegation exception rule, investigation.md contained a complete, unambiguous implementation plan (exact files, exact snippets). Implementation was done directly. No explore/deep/ultrabrain agents dispatched.