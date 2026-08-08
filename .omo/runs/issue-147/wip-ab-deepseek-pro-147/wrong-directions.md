# Issue #147 — Wrong Directions (deepseek-pro run)

## 1. Issue body is stale — list endpoint already optimized

**What the issue says:**
> `_serialize_transcript()` (app.py:229) runs 5-8 separate `latest_job()` queries per transcript row. For the tape library listing 100 transcripts, that's 500-800 individual DB queries per request.

**What the code actually does:**
The list endpoint `/api/transcripts` calls `_build_recent_transcripts` (line 489), which calls `_serialize_transcript_summary` (line 456) — NOT `_serialize_transcript`. The summary serializer has zero `latest_job()` calls. The N+1 for LlmJobs in the list endpoint doesn't exist.

All 7 callers of `_serialize_transcript` are single-transcript endpoints (get_transcript, transcribe completion, rename/retag/voice-match responses). No N+1 scenario.

**Recommendation:** Close the issue as already resolved by the `_serialize_transcript_summary` refactor (which happened after the issue was filed). The `batch_latest_jobs()` infrastructure added in this run is clean but not load-bearing — it's future-proofing for if/when someone adds batch serialization.

## 2. Line number drift

Issue says `app.py:229` for `_serialize_transcript`. Current code has it at line 262. 33 lines of drift.

## 3. Stale docstring in `_dictation_job_fields`

Line 320: "Matters because _serialize_transcript runs per-row in list_transcripts (up to 50 rows)."

This docstring is now false — `_serialize_transcript` does NOT run per-row in list_transcripts. Only `_serialize_transcript_summary` does. The docstring should be updated to reflect the current call pattern.

## 4. LSP diagnostics blocked across worktrees

LSP `lsp_diagnostics` tool refused paths in the worktree (`C:\Claude\whisperdesk-wip-ab-deepseek-pro-147\app.py`) because they're outside the workspace root (`C:\Claude\whisperdesk`). Had to rely on test suite alone for verification. This is a tool limitation, not a code issue.
