# Wrong directions encountered during issue #147

## 1. Issue body claims list endpoint uses `_serialize_transcript`

**What the issue said:**
> `_serialize_transcript()` (app.py:229) runs 5-8 separate `latest_job()` queries per transcript row. For the tape library listing 100 transcripts, that's 500-800 individual DB queries per request.

**What's actually true:**
The list endpoint (`GET /api/transcripts`) uses `_serialize_transcript_summary` (app.py:456-486), NOT `_serialize_transcript`. The summary variant does not call `latest_job()` at all. It only calls `compute_queue_status(db, t)`, which short-circuits to `None` for non-"processing" transcripts.

**Impact on the fix:**
The "500-800 queries for 100 transcripts" claim is wrong. The N+1 `latest_job()` calls only affect `_serialize_transcript`, which is used for single-transcript detail views (GET /api/transcripts/{id}) and mutation endpoints (rename, retag, undo). For a single transcript, 5-8 queries is not catastrophic, but batching them into 1 query is still a win.

**Recommendation for future issues:**
Verify which function the list endpoint actually uses before claiming N+1 impact. The issue's line numbers were also stale (said app.py:229, but `_serialize_transcript` is now at line 262).

## 2. `explore-hard` agent not available as subagent_type

**What AGENTS.md said:**
AGENTS.md lists `explore` and `explore-hard` as distinct agents for Phase 1 investigation.

**What's actually true:**
The `task()` tool only accepts `explore` as a subagent_type. `explore-hard` is not a valid subagent_type value. The config at `~/.config/opencode/oh-my-openagent.json` defines both `explore` and `explore-hard` as agent keys with different models, but the `task()` tool's subagent_type parameter only recognizes `explore`.

**Impact on the workflow:**
Had to use `explore` for both agents instead of `explore-hard` for the reasoning-heavy complement analysis. The agents still completed successfully, but may have been less thorough on the call-site enumeration.

**Recommendation:**
Either add `explore-hard` as a valid subagent_type in the task tool, or update AGENTS.md to clarify that only `explore` is available as a subagent_type and `explore-hard` is only available as a direct agent invocation (if at all).

## 3. Edits applied to main repo instead of worktree

**What happened:**
I applied the code edits (to `services/llm_jobs.py` and `app.py`) in the main repo (`C:\Claude\whisperdesk`) instead of the worktree (`C:\Claude\whisperdesk-147-qwen37plus`). The worktree showed "nothing to commit" after I thought I'd made the changes.

**Root cause:**
The `edit` tool operates on the file path provided. I provided paths in the main repo, not the worktree. The worktree is a separate directory with its own copy of the files.

**Impact:**
Had to re-apply all edits to the worktree files after realizing the mistake. Wasted ~5 minutes of tool calls.

**Recommendation:**
Always verify the working directory context before applying edits. When using a worktree, ensure all file paths point to the worktree directory, not the main repo.

## 4. Test execution required file swapping between main repo and worktree

**What happened:**
The worktree doesn't have its own `.venv`. To run tests against the worktree's code, I had to copy the modified files from the worktree to the main repo, run tests, then restore the originals.

**Impact:**
Added ~3 minutes of file-copying overhead. Risk of accidentally leaving the main repo in a modified state if the restore failed.

**Recommendation:**
Either:
- Create a symlink from the worktree to the main repo's `.venv` (so the worktree can run tests directly)
- Or use `PYTHONPATH` to point pytest at the worktree's code while running from the main repo's venv
- Or accept the file-swapping overhead as a one-time cost per worktree session

## 5. Acceptance criteria "List endpoint uses batch job fetching" is N/A

**What the issue's acceptance criteria said:**
- [ ] List endpoint uses batch job fetching

**What's actually true:**
The list endpoint uses `_serialize_transcript_summary`, which does not call `latest_job()` at all. There's nothing to batch in the list endpoint for LlmJob queries. The only per-transcript query in the list endpoint is `compute_queue_status()`, which is not part of this issue's scope.

**Impact:**
This acceptance criterion cannot be satisfied because it's based on a false premise (that the list endpoint uses `_serialize_transcript`). The fix addresses the actual N+1 problem in `_serialize_transcript` (detail views), not the list endpoint.

**Recommendation:**
Update the acceptance criteria to reflect the actual scope:
- [x] `_serialize_transcript` uses batch job fetching (instead of individual `latest_job()` calls)
- [x] `_serialize_transcript` accepts pre-fetched job data (optional `latest_jobs` param)
- [x] No behavioral change in the API response (verified by test suite)
- [x] Existing tests still pass (379 passed)
