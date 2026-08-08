## PR Audit: #337 fix(relabel): recompute speaker_count at every path that rewrites segments (#111)   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna   (the same slug from Phase 1 step 4, and the one in this file's name)

VERDICT: APPROVE

### Blocking
- None.

### Should fix
- [robustness] services/relabel.py:33 assumes every externally supplied segment speaker value is a string. Failure scenario: `PATCH /api/transcripts/{id}` receives a JSON segment such as `{"speaker": 7}`, and the new recount raises `AttributeError` from `.strip()`, returning a 500 instead of accepting or rejecting the payload cleanly. Fix: validate the segment shape before assignment or coerce/reject non-string speaker values with a 4xx response.

### Nits
- The PR body reports `842 passed, 1 skipped, 22 deselected`; this run observed `843 passed, 22 deselected, 1 warning` and no skipped tests.
- Static scan hit `services/llm_jobs.py:740` and `:768` for `except Exception`. Line 740 intentionally counts per-segment failures and line 768 logs the outer failure, so neither is a blocking defect here.

### Honesty check
- self-audit.md [x] lines verified: 0/0. False [x] found: none; no PR-run self-report artifacts were present in the main checkout.
- Vacuous / loosened tests: none. The changed-path tests assert exact speaker counts and the helper tests distinguish the relevant return values.
- Undisclosed scope (diff vs claims): none.

### Read scope
- Focused read on app.py changed routes and related serializers, services/llm_jobs.py voice_match and rediarize paths, services/relabel.py, services/diarization.py, services/queue.py, services/transcription.py, and all touched tests. The six-file diff was 254 added/removed lines; large files were not read start-to-finish.

### Summary
The helper is wired into all five claimed segment-rewriting paths, and the targeted suite passed 36 tests. The full suite also passed with 843 passed and 22 deselected; no blocking correctness, regression, security, false-claim, or vacuous-test issue was found.
