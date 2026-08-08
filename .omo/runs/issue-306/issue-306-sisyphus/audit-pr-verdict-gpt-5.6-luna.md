## PR Audit: #353 fix(llm): guard correction progress after cancel   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna   (the same slug from Phase 1 step 4, and the one in this file's name)

VERDICT: BLOCK

### Blocking
- tests/test_llm_jobs.py:339-375 is a vacuous regression test for the new callback guard. Failure scenario: with the guard removed, cancellation during the second and final batch makes `correct_transcript()` return `"ok"` because it checks cancellation only before a batch; `run_llm_job()` then calls `_finish()`, which sees the cancelled status and resets the counters to zero, so the test still passes even though the callback wrote `progress_done == 2`. Fix: arrange for a later cancellation check, for example use at least three batches and cancel during batch two, or cancel during batch one of a two-batch run. Regression test: `test_progress_callback_no_op_after_cancel` should cancel during batch two of a three-batch correction, then assert the final counters are zero, so removing the callback guard leaves the nonzero progress written by batch two and fails.

### Should fix
- None.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 14/14 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 0. False [x] found: line 5/7's mutation-check claim is false because the cited test remains green without the guard; line 13 also incorrectly says `tests/test_llm_jobs.py:254` covers single-batch behavior, while that test uses 40 segments and exercises cancellation between batches.
- Vacuous / loosened tests: `tests/test_llm_jobs.py:339-375` is vacuous for the new guard for the reason stated in Blocking. No loosened value assertion found.
- Undisclosed scope (diff vs claims): none in the PR body. The self-audit's claimed mutation failure and single-batch coverage are inaccurate.

### Read scope
- Focused read on `services/llm_jobs.py`, `services/correction.py`, `tests/test_llm_jobs.py`, and the cancellation entry point. The 42-line diff was small; called correction and cancellation paths were traced outside the diff.

### Summary
The implementation guard matches the requested cancellation check, and the changed-path tests passed: 56 LLM-job tests, 35 correction tests, and the full suite with 919 passed and 22 deselected. I am blocking because the only regression test does not fail when the guard is removed, so the PR's central mutation-check claim is false and the fix is not protected against removal.
