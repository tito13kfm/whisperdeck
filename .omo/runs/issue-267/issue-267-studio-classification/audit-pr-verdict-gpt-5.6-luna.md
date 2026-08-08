## PR Audit: #273 Add studio pipeline classification job (issue #267)   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: BLOCK

### Blocking
- `services/llm_jobs.py:417-419` A classifier failure never updates `Transcript.classification_status` to `failed`. Failure scenario: a pending transcript receives malformed JSON or an HTTP error, `classify_pipeline_kind()` raises, the job becomes failed, but the API continues reporting `classification_status="pending"`, so consumers cannot distinguish an in-progress classification from a failed one and the persisted state contradicts the documented failed state. Fix: set the transcript classification status to `failed` in this exception path, while leaving `kind` unchanged and preserving retryability. Regression test: after a malformed classifier response, assert `t.classification_status == "failed"` and the LLM job is failed with `will_retry` true.

### Should fix
- [robustness] `services/llm_jobs.py:344-351` Classification is enqueued after `_finish()` without checking whether cancellation won the completion race. Failure scenario: correction returns successfully, a concurrent cancel changes the correction job to `cancelled`, `_finish()` preserves that cancellation, then this code still creates a pending classification job for the cancelled pipeline. Fix: only enqueue classification when the correction job remains completed after `_finish()`.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 0/0. No PR-run self-report artifacts were present in the main checkout at `.omo/runs/issue-267/issue-267-studio-classification/`.
- Vacuous / loosened tests: none found in the tests inspected.
- Undisclosed scope (diff vs claims): the PR body claims failed classification is a persisted distinguishable state, but the failure path leaves the transcript pending. The issue's requested sibling sweep also lists many future routing sites, but the design explicitly assigns those to follow-up issues, so I did not treat their absence here as a defect.

### Read scope
- Focused read on the 577-line diff, including all changed files, the classification design, `services/llm_client.py`, the correction completion callers, retry worker, serializer, and cost helper. The changed files were not read as one uninterrupted full-repository pass because the diff includes large existing modules.

### Summary
The classifier and migration tests pass, including the full suite, but the persisted failure state required by the contract is not implemented. A malformed or unavailable classifier leaves the transcript indistinguishable from one that is still pending, so this should not merge until the failure path records `failed` and its test asserts that state.

---

## Re-audit: PR #273 at 854ee8e

VERDICT: APPROVE

### Prior findings verified
- `services/llm_jobs.py:425-435` now refreshes the job before handling classifier exceptions, records `transcript.classification_status = "failed"` unless cancellation already won, and still finishes the LLM job as failed, preserving retryability.
- `services/llm_jobs.py:340-359` now enqueues pipeline classification only when `_finish()` leaves the correction job in `completed`. A cancellation race therefore cannot create a downstream classification job.

### Regression coverage
- `tests/test_llm_jobs.py:585-611` asserts transcript failure state, no confidence write, failed job state, and retryability after malformed classifier output.
- `tests/test_llm_jobs.py:614-639` asserts cancellation preserves the pending transcript classification state during classifier failure.
- `tests/test_llm_jobs.py:642-671` asserts a correction cancellation race leaves the job cancelled and creates no classification job.

### Verification
- Focused regression tests: 3 passed, 47 deselected.
- Full suite from the re-audit: 648 passed, 8 deselected.
- `git diff --check`: clean.
- Static scan: no production `asyncio.run()` or `run_until_complete()` findings.

### Residual risk
- No new blocking or should-fix findings identified. Browser e2e was not required for this backend-local job-state fix; validation used the existing unit/integration suite and focused race tests.
