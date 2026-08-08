## PR Audit: #282 feat(retranscribe): route classification-aware kind per design decision 9   (reviewer: GLM 5.2, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)

### Should fix          (empty = none)

### Nits
- app.py:2113 — the else-branch comment reads "Override, pending, or failed — carry the existing kind forward", but `failed` is routed to the IF branch at line 2103 (`source_status in ("success", "uncertain", "failed")`). The else actually handles override, pending, and any other value. The word "failed" here is stale and would confuse a future reader tracing why a failed parent reclassifies. Fix: drop "failed" from the else comment, e.g. "Override, pending, or any other value — carry the existing kind forward."
- app.py:1065-1071 — the `_run_transcription_pipeline` header comment states the retranscribe entry point "always passes an already-resolved kind, never 'auto'" and "deliberately not stamping classification_status on that path." This PR deliberately violates that invariant: for auto-classified (success/uncertain/failed) parents, retranscribe now passes `kind="auto"`, which the pipeline converts to `classification_status="pending"`. The code handles it correctly (the `if kind == "auto"` branch at line 1073 fires before the `is_retranscribe` check), but the comment now actively contradicts the behavior it documents. Fix: update the comment to reflect that retranscribe now passes "auto" for reclassify parents and "override"/legacy kind for carry-forward parents, with the pipeline stamping "pending" vs leaving the column default accordingly.

### Honesty check
- self-audit.md [x] lines verified: all 14 walked. False [x] found: none. Cited line numbers drift by 2-3 (override test cited at 109, actual 107; uncertain cited 127, actual 130; failed cited 149, actual 150) but the artifacts exist at the claimed locations and do what the lines describe. The retranscribe routing citation (app.py:2102-2114) matches exactly.
- Vacuous / loosened tests: none. All four mutation checks are genuine: (1) auto-classified parent test would yield "override" instead of "pending" if the else branch always fired; (2) override test asserts BOTH `classification_status == "override"` AND `kind == "dictation"` with `==`, so an "always auto" mutation fails on both; (3) uncertain mirrors success; (4) failed test fails if "failed" is dropped from the reclassify set. No membership-assert loosening, no COUNT-proxy trap.
- Undisclosed scope (diff vs claims): none. Diff is app.py retranscribe routing (18+/1-) plus 4 tests (70+/9-), exactly what the PR body and self-audit describe. Investigation's fix-summary item #2 (chunked-path classification_status gap) was correctly retracted in wrong-directions.md #2 as already closed by #268 — no undisclosed extra change.

### Read scope
- Full read of the diff hunks plus the dependent `_run_transcription_pipeline` classification_status decision tree (app.py:1060-1079), the inline post-transcribe dispatch (app.py:1317-1345), the chunked-path classification_status stamp (app.py:1248-1249), the rediarize/voice-match effective_kind sites (app.py:2497,2615,2655), and all four new tests. Focused, not start-to-finish on the 3337-line app.py.

### Summary
The change is a small, correct, well-scoped routing fix: retranscribe now passes `kind="auto"` (re-classify) for success/uncertain/failed parents and carries the existing kind forward for override/legacy/pending, matching design decision 9. The four tests have real mutation value, the suite passes (20/20 on the touched file, matching the run's 692-passed claim direction), and the downstream re-classification enqueue is confirmed by reading the inline `enqueue_pipeline_classify` path that fires when auto_correct is off. The only findings are two stale comments in the touched code path that now contradict the implemented behavior; neither affects runtime correctness.
