## PR Audit: #291 feat(llm): add voice_dump LLM chain dispatch (#284)   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: BLOCK

### Blocking
- services/voice_notes.py:102-108, services/llm_jobs.py:614 The voice-dump structure prompt does not request or parse `clarifying_questions`; the dispatch always writes `"clarifying_questions": []`. Failure scenario: a span is ambiguous and the model has a question to ask, but the saved item exposes no question. Fix: add the optional field to the voice-dump structure prompt, parse and validate it, and carry the returned list into `result_json`. Regression test: mock a structure response containing `clarifying_questions: ["Which date?"]`, then assert the completed item's list equals `["Which date?"]` and the request prompt asks for that field.
- .omo/runs/issue-284/issue-284-sisyphus/self-audit.md:36 The `[x]` claim labels 101 targeted tests as the "Full test suite". The repository suite run against the checked-out worktree produced 760 passed, 8 deselected, not 101. This is a false self-report claim about verification scope.

### Should fix
- [feature] services/llm_jobs.py:596-619 The progress total includes the segmentation call, but `progress_done` is never advanced for that call and finishes at `len(segments)` instead of `len(segments) + 1`. Failure scenario: a two-span job reports `2/3` while processing or just before completion. Fix: set `progress_done = 1` after segmentation and finish at `len(segments) + 1`.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 15/17 artifact claims verified. False [x] found: line 36 mislabels a focused 101-test run as the full suite. Line 40's Oracle claim is not independently verifiable from the checked-out code.
- Vacuous / loosened tests: none found in the changed tests. The direct structure tests fail against a constant empty return, and the dispatch test fails if segmentation returns an empty object.
- Undisclosed scope (diff vs claims): clarifying-question generation and parsing are claimed by the PR body and issue acceptance criteria but are not implemented. The self-audit discloses the empty stub, but does not mark the missing acceptance behavior as incomplete.

### Read scope
- Full read of the changed files, plus the called LLM client, existing voice-note tests, serialization references, and sibling call sites.

### Summary
The main requested behavior is incomplete: every voice-dump item hardcodes an empty clarification list, despite the PR claiming that the per-item call requests clarifying questions. The self-report also overstates test coverage, so this cannot be approved even though the relevant tests and the full repository suite pass.

---

2026-08-02T06:02:00Z

## PR Audit: #291 feat(llm): add voice_dump LLM chain dispatch (#284)   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)
- None.

### Should fix          (empty = none)
- None.

### Nits
- `.omo/runs/issue-284/issue-284-sisyphus/self-audit.md:5-8,17-20` retains stale line ranges after the clarifying-question edits. The referenced symbols exist, but updating the ranges would make the report easier to audit.
- Static scan hits are safe in context: `asyncio.run()` is in tests or the synchronous cost helper, and the changed `except Exception` handlers are intentional fallback or job-failure boundaries.

### Honesty check
- self-audit.md [x] lines verified: 16/17. False [x] found: none. The Oracle verdict line is a process claim that cannot be independently verified from the checked-out source.
- Vacuous / loosened tests: none found. The new clarification tests assert returned question values, fallback behavior, and the dispatch result; the full suite also exercises the changed path.
- Undisclosed scope (diff vs claims): none found.

### Read scope
- Full read of the changed files, plus relevant callers, the LLM client, existing voice-note tests, serialization references, and sibling call sites.

### Summary
The prior blocking issues are fixed in the current PR tip. Clarifying questions are requested only for voice-dump structure calls, parsed with a safe fallback, propagated into `result_json`, and progress now reaches `N+1/N+1`. Targeted tests passed with `104 passed`, and the full worktree suite passed with `763 passed, 8 deselected`.
