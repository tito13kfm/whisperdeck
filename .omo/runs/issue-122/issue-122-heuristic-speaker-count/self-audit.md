# Self-Audit — Issue #122

## Deliverables from investigation.md

[x] `diarize_heuristic()` updated with unused-label distribution pass — `services/diarization.py:129-141`
[x] Regression test `test_diarize_heuristic_distributes_unused_labels` — `tests/test_diarize_heuristic_no_segments.py:52-74`

### Mutation check transcript

[x] `test_diarize_heuristic_distributes_unused_labels` — mutation check:
    ran: C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/test_diarize_heuristic_no_segments.py::test_diarize_heuristic_distributes_unused_labels -q  ->  1 passed
    mutated: `if len(used) < len(speaker_labels)` -> `if False`; reran  ->  1 failed
        E       AssertionError: expected 4 speakers, got 1: {'Speaker 1'}
    restored: reran  ->  1 passed

## Full test suite

[x] Full suite: 940 passed, 22 deselected — `C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest C:/Claude/whisperdesk/.claude/worktrees/issue-122-heuristic-speaker-count/tests/ -q`

## Six checks

### Value-space exhaustiveness

- `speakers` list: can be empty (all-gap case) — handled, `used` set is empty, `len(used) < len(speaker_labels)` is True, `unused` covers all labels, loop doesn't execute (no segments to reassign), `speaker_set` reports the actual count
- `num_speakers` = 0: `max(num_speakers, 2)` = 2, so labels always >= 2
- `num_speakers` = None: type error at call site, caught by type checker
- `speaker_labels` length = len(used): condition is False, pass skipped — no-op, correct
- All segments identical speaker: handled (all are "Speaker 1", distribution pass reassigns first N)

### Boundary cardinality

[x] num_speakers=4, no gaps, 8 segments — tested
N/A: no pagination endpoint involved

### Delivery chain

N/A, `git diff --stat` shows no frontend file (only services/diarization.py + tests/)

### done == total on progress counters

N/A — no progress counters changed

### Every deferral matched against the issue text

No deferrals. Fix addresses the issue's option 1: force-assign all num_speakers labels.

### Suite count tied to invocation

[x] Full suite: 940 passed, 22 deselected — `C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest C:/Claude/whisperdesk/.claude/worktrees/issue-122-heuristic-speaker-count/tests/ -q`

## Main checkout check

[x] Main on master: `git -C C:/Claude/whisperdesk rev-parse --abbrev-ref HEAD` → `master`

Independent review: Oracle (Phase 3.75) - APPROVE, fix correctly addresses the issue; edge cases handled (fewer segments than speakers, all labels already used); no regressions in gap-based alternation logic or merge algorithm.