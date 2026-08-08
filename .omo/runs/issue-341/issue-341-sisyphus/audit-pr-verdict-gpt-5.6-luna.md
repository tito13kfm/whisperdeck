## PR Audit: #345 fix(diarization): unify speaker_count on post-merge count_distinct_speakers   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: BLOCK

### Blocking
- tests/test_posthoc_reprocess.py:364-379 The changed rediarize test does not distinguish the new post-merge calculation from the old pre-merge value: its fake returns `speaker_count == 2` and its merged segments contain two distinct speaker labels, so both implementations pass. Failure scenario: a diarizer reports two clusters but merge assigns every stored segment to one cluster, and the old code persists 2 instead of the required post-merge count 1. Fix: make the fixture return two clusters but merged segments containing one real speaker (or `Unknown`) and assert `t.speaker_count == 1`. Regression test: set `merged = [{"speaker": "SPEAKER_01"}, {"speaker": "SPEAKER_01"}]`, keep the fake result count at 2, then assert `t.speaker_count == 1`.

### Should fix
- None.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 0/0. No self-audit.md, investigation.md, wrong-directions.md, or token-usage.md artifact was present, so there were no [x] claims to verify.
- Vacuous / loosened tests: `tests/test_posthoc_reprocess.py:379` is vacuous for the changed speaker-count behavior because it also passes with the pre-change assignment.
- Undisclosed scope (diff vs claims): none. The four-file diff matches the PR body claim.

### Read scope
- Full read of the 9-line diff and focused reads of `app.py`, `services/queue.py`, `services/llm_jobs.py`, `services/relabel.py`, `services/diarization.py`, and `tests/test_posthoc_reprocess.py`.

### Summary
The production changes consistently use `count_distinct_speakers(merged)` in the three named paths, and the targeted tests passed, 39 passed with one deprecation warning. The only changed regression test does not exercise the behavior that changed, so the PR's correctness claim is not protected against the original bug.
