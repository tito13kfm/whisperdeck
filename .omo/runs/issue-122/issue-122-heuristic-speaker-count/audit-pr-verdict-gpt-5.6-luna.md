## PR Audit: #372 fix(diarization): distribute unused speaker labels when heuristic under-assigns   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: BLOCK

### Blocking
- `services/diarization.py:137-141` The distribution pass can overwrite labels already present, so it does not always produce all requested labels. Failure scenario: with `num_speakers=4` and four sorted segments whose gap pattern initially assigns `Speaker 1, Speaker 2, Speaker 2, Speaker 2`, `unused` is `[Speaker 3, Speaker 4]`; the pass rewrites the first two segments, yielding `Speaker 3, Speaker 4, Speaker 2, Speaker 2`, losing `Speaker 1` and returning `speaker_count == 3`. Fix: assign unused labels only to segments whose current labels are retained, or otherwise preserve every label already in `used` while filling unused labels. Regression test: call `diarize_heuristic('', num_speakers=4, segments=[{'start': 0, 'end': 1}, {'start': 3, 'end': 4}, {'start': 4.1, 'end': 5}, {'start': 5.1, 'end': 6}])` and assert `{s.speaker for s in result.segments} == {'Speaker 1', 'Speaker 2', 'Speaker 3', 'Speaker 4'}`.

### Should fix
- None.

### Nits
- `services/diarization.py:129` contains a whitespace-only blank line.

### Honesty check
- self-audit.md [x] lines verified: 7/7 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 0. False [x] found: none.
- Vacuous / loosened tests: none. The added test fails when the distribution condition is disabled and asserts the exact requested count for its covered input.
- Undisclosed scope (diff vs claims): the self-report's guarantee that the pass ensures all requested labels is broader than the implementation for mixed-label inputs, as described in the Blocking item. No other undisclosed file or feature scope found.

### Read scope
- Focused read on `services/diarization.py` (changed function and callers), `app.py`'s diarization endpoint, `tests/test_diarize_heuristic_no_segments.py`, and sibling `speaker_count` call sites.

### Summary
The focused regression passes, including the full worktree suite: 940 passed, 22 deselected, with one warning. The implementation still fails a valid mixed-gap case because it overwrites an existing label while trying to add unused labels, so the requested speaker-label guarantee is not complete.

---
2026-08-07T22:24:45Z

## PR Audit: #372 fix(diarization): distribute unused speaker labels when heuristic under-assigns   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna
RE-AUDIT of commit f259b61 (squashed tip), prior run blocked on the overwritten-label bug at the original tip b3eadc2.

VERDICT: APPROVE

### Blocking
- None.

### Should fix
- None.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 7/7 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 0. False [x] found: none. The self-audit was not updated for this push (line references now shifted and the new mixed-gap test is undocumented there), but no [x] line is false: the distribution pass exists, the first regression test exists, the mutation check transcript is reproducible, and the full-suite count (now 941, one more for the new test) still holds.
- Vacuous / loosened tests: none. Mutation check on commit f259b61: replaced `if len(used) < len(speaker_labels)` with `if False` and both `test_diarize_heuristic_distributes_unused_labels` and `test_diarize_heuristic_preserves_existing_labels_in_mixed_gaps` failed; restored, both pass. The new mixed-gap test asserts the exact set `== {"Speaker 1", "Speaker 2", "Speaker 3", "Speaker 4"}`, not a loosened count.
- Undisclosed scope (diff vs claims): the diff adds the new mixed-gap regression test beyond the original self-audit's single test claim, which is more coverage than claimed, not less. No undisclosed feature scope.

### Read scope
- Focused read on `services/diarization.py:129-148` (new distribution pass), `tests/test_diarize_heuristic_no_segments.py:51-99` (both regression tests), `app.py:2504-2541` (heuristic endpoint caller), and sibling `speaker_count`/`num_speakers` call sites across `services/llm_jobs.py`, `services/queue.py`. Empirical edge-case probes run against the worktree: prior-failing mixed-gap case, fewer-segments-than-speakers, num_speakers=1, all-labels-used. Static scan: no asyncio.run/run_until_complete, no bare except, no missing-await in the new code (it is synchronous data manipulation inside an async function).

### Summary
The prior Blocking bug is fixed: the distribution pass now counts segment labels and only reassigns from labels with count > 1, preserving at least one segment per existing label. The prior failing case now returns all 4 speakers. The added mixed-gap regression test catches exactly the bug I flagged, with an exact set equality assertion. Full suite green at 941 passed. I am confident this addresses issue #122's option 1 (force-assign all requested labels) without regressing the gap-based alternation.
