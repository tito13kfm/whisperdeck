## PR Audit: #325 fix(diarization): surface non-fatal failure in UI as partial status   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: BLOCK

### Blocking
- `tests/test_diarization_failure.py:75-79` The `test_diarization_failure_keeps_segments_undiarized` mutation claim is false and the test is vacuous. Failure scenario: if `_finalize_if_done` is replaced with `return None`, the transcript retains its initial empty `segments` list, the loop executes zero times, and the test still passes. Fix: assert the expected pre-diarization segment count and contents, for example `assert transcript.segments == [{"start": 0, "end": 5, "text": "hello world", "speaker": None, "confidence": None, "no_speech_prob": None}]` after refresh, or assert `len(transcript.segments) == 1` before checking the speaker. Regression test: replace `_finalize_if_done` with `return None` and assert this test fails because the finalized transcript must contain one undiarized segment.

### Should fix
- None.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 16/17. False [x] found: line 14 claims the undiarized-segments test fails under a return-only mutation, but it still passes vacuously.
- Vacuous / loosened tests: `tests/test_diarization_failure.py:75-79`, as described above. No loosened membership assertion found for the issue's stated values.
- Undisclosed scope (diff vs claims): none. The diff matches the claimed two caller fixes and the three regression tests.

### Read scope
- Full read of the 118-line diff, plus the changed functions and one level into the three `diarize_and_merge()` callers and related status/error handling.

### Summary
The production changes correctly surface diarization failures in both inline and chunked paths, and the full suite passed with 798 passed and 22 deselected. BLOCK remains necessary because the self-audit contains a false mutation claim and the changed regression test does not prove that the finalized undiarized segments are preserved.

---

UTC 2026-08-04T02:20:00Z

## PR Audit: #325 fix(diarization): surface non-fatal failure in UI as partial status   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: APPROVE

### Blocking
- None.

### Should fix
- None.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 17/17. False [x] found: none.
- Vacuous / loosened tests: none. The added `assert len(transcript.segments) > 0` makes the undiarized-segments mutation check non-vacuous.
- Undisclosed scope (diff vs claims): none.

### Read scope
- Full read of the 119-line diff, plus the changed functions and related diarization callers and status/error handling.

### Summary
The follow-up commit fixes the only blocking issue from the prior audit. The targeted test passes, and the full suite passes with 798 passed and 22 deselected. The production changes and regression coverage now match the PR and self-audit claims.
