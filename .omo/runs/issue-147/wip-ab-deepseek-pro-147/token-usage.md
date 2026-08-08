# Issue #147 — Token Usage (deepseek-pro run)

## What worked well

### codegraph_explore
Two calls covered 90% of the investigation — returned verbatim source for `_serialize_transcript`, `latest_job`, `compute_queue_status`, `_build_recent_transcripts`, `_serialize_transcript_summary`, and `_dictation_job_fields` plus blast radius and call graphs. Avoided ~8 direct file reads.

### Direct read for truncated codegraph output
One codegraph call hit budget truncation on `_build_recent_transcripts` body. Switched to direct `Read` of the worktree file at the known offset — one call, 60 lines, resolved immediately. Applied the 2-attempt rule correctly.

### No agent dispatches needed
Investigation was straightforward — issue described a well-scoped bottleneck in two functions. Direct tools (codegraph + read + grep) sufficed. No local Lemonade agents were fired, saving both VRAM and round-trip time.

### Test suite ran from main repo venv
Worktree has no .venv. Used main repo's `C:/Claude/whisperdesk/.venv/Scripts/python.exe` — all 379 tests passed in 39s. No need to pip install in the worktree.

## What could be improved

### LSP diagnostics unavailable across worktrees
The `lsp_diagnostics` tool rejected worktree paths outside workspace root. Had to skip static diagnostics and rely on test suite alone. For a larger refactor spanning more files, this would be higher risk.

### Stale docstring not caught by tests
The `_dictation_job_fields` docstring at line 320 still claims `_serialize_transcript` runs per-row in list_transcripts. The test suite passed because the tests verify behavior, not docstring accuracy. A docstring-audit lint rule would catch this.
