## PR Audit: #277 Replace mode-dependent routing with classification predicates (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)
- None.

### Should fix          (empty = none)
- None.

### Nits                (empty = none)
- `services/transcription.py:187-221` remains a raw-kind guard, but the summary fallback deliberately treats unresolved auto rows as the meeting-shaped default and the route preserves summary availability while pending, so this is not a defect in this PR.
- Static anti-pattern scan found existing `asyncio.run()` in `services/cost.py:96`, but its running-loop guard at lines 89-94 makes it safe. Test files also contain `asyncio.run()` only as synchronous pytest drivers.

### Honesty check
- self-audit.md [x] lines verified: 0/16. The available artifact is `.omo/runs/issue-267/issue-267-studio-classification/self-audit.md`, not a PR #277 self-report. Its cited artifacts exist, and the PR body claim of 663 tests was independently confirmed by the full test run, but its issue #267 claims cannot be counted as PR #277 claims.
- Vacuous / loosened tests: none found in the changed tests reviewed. The new pending/accepted guard tests use discriminating fixtures and exact status assertions.
- Undisclosed scope (diff vs claims): none found.

### Read scope
- Focused read on the 14 changed files and the called paths in `services/transcription.py`, `services/correction.py`, `services/settings.py`, `services/cost.py`, and `scripts/verify_self_audit.py`. Full suite and all PR-touched tests were run.

### Summary
The changed routing paths and their sibling entry points are consistent with the approved design, and the full suite passed, 663 passed and 8 deselected. The self-audit script's build portion could not be reproduced in this fresh worktree because `esbuild` is not installed, so that is an infrastructure limitation rather than a code defect.
