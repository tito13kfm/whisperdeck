# wrong-directions.md — issue #271

## 1. Plan item 3 ("Apply shared capability predicates to rediarize/voice-match") is a no-op

The issue's plan lists applying `effective_kind()` predicates from #268 to rediarize and voice-match as item 3. Both endpoints already use `effective_kind()` — #268 applied these predicates. Confirmed by reading app.py:2598 and app.py:2638.

Fix: mark item 3 as "already done by #268" in investigation.md. No code change needed.

## 2. Chunked-path classification_status gap doesn't need a fix

Investigation initially found that the chunked path in `_run_transcription_pipeline` might not apply `classification_status`. On re-inspection, lines 1248-1249 already have the check (`if classification_status is not None: transcript.classification_status = classification_status`). The gap was already closed by #268, not visible in the initial codegraph excerpt.

## 3. LSP unavailable for worktree diagnostics

Expected per infra notes — LSP runs on the main checkout only, not the worktree. Static check done manually instead. No actual issue.

## 4. Issue's cited line numbers are stale

The issue references lines 1825-1865 and 2337-2368 for retranscribe/rediarize. Actual lines are 2071-2111 (retranscribe) and 2584-2622 (rediarize) — drifted due to intermediate PRs. No action needed; investigation read the actual code.

## 5. verify_self_audit.py: BUILD findings are esbuild-not-found, not citation errors

Script reports 2 blocking BUILD findings because the worktree has no `node_modules` (esbuild not installed). My changes touch only `.py` files, no frontend bundling needed. Pre-existing infra condition, not caused by this issue.
