# self-audit.md — issue #271

Issue #271: "Studio pipeline: make retranscribe and rediarize classification-aware"
Branch: `issue-271-sisyphus`
Worktree: `C:/Claude/whisperdesk-sisyphus-271`

## Investigation.md promises walk

[x] `retranscribe_transcript` routing by `classification_status` — delivered at `app.py:2102-2114`
[x] Auto-classified parent (`success`/`uncertain`/`failed`) → `kind="auto"` → re-classification — delivered at `app.py:2103-2111`
[x] Override parent (including legacy NULL → "override") → carry forward — delivered at `app.py:2113`
[x] Chunked path `classification_status` application — already present at `app.py:1248-1249` (by #268), verified correct
[x] Rediarize endpoint predicate check — already uses `effective_kind()` at `app.py:2598` (by #268), verified correct
[x] Voice-match endpoint predicate check — already uses `effective_kind()` at `app.py:2638` (by #268), verified correct

## Test promises walk

[x] `test_retranscribe_auto_classified_parent_reclassifies` — delivered at `tests/test_posthoc_reprocess.py:85`
[x] `test_retranscribe_override_parent_carries_kind_forward` — delivered at `tests/test_posthoc_reprocess.py:109`
[x] `test_retranscribe_uncertain_parent_also_reclassifies` — delivered at `tests/test_posthoc_reprocess.py:127`
[x] `test_retranscribe_failed_parent_reclassifies_not_overrides` — delivered at `tests/test_posthoc_reprocess.py:149` (Oracle review finding)

## Mutation checks

[x] `test_retranscribe_auto_classified_parent_reclassifies` — mutation: always "override"? Fails ("pending" assert fails)
[x] `test_retranscribe_override_parent_carries_kind_forward` — mutation: always "pending"? Fails ("override" + "dictation" asserts fail)
[x] `test_retranscribe_uncertain_parent_also_reclassifies` — mutation: only "success" reclassifies? Fails ("pending" with "uncertain" fails)
[x] `test_retranscribe_failed_parent_reclassifies_not_overrides` — mutation: "failed" stays override? Fails ("pending" assert fails)

## Acceptance criteria walk

[x] "reruns never silently lose a manual override" — verified: `test_retranscribe_override_parent_carries_kind_forward` asserts both `classification_status == "override"` AND `kind == "dictation"` on child
[x] "old transcripts remain processable" — verified: legacy NULL `classification_status` → `or "override"` fallback at `app.py:2102`. Override path unchanged; auto-classified use case is additive only
[x] "every post-hoc entry point applies the same classification policy as initial processing" — verified: retranscribe now routes by `classification_status` matching design decision 9. Rediarize/voice-match already correct (#268)
[x] "failures are visible and retryable without corrupting the original transcript" — verified: `failed` parents now trigger re-classification (Oracle finding, `test_retranscribe_failed_parent_reclassifies_not_overrides`). Original transcript never mutated (retranscribe creates new row)

## Disclosed decisions

[decision] `"failed"` included in re-classify set alongside `"success"`/`"uncertain"` — Oracle review finding: a failed auto classification on retranscribe should get a fresh attempt, not be silently converted to an override. Acceptance criterion "failures are visible and retryable" requires this. `test_retranscribe_failed_parent_reclassifies_not_overrides` added.

[decision] `"pending"` status falls through to override (carry forward existing kind) — not specified by issue. Rationale: `pending` on a completed transcript is a broken state (transcription completes before classification runs). If it somehow occurs, preserving the existing kind is the safer fallback. Oracle flagged this but acknowledged it's a theoretical edge case; re-classifying from a `pending` state could create conflicting classification jobs (original may still be pending).

## Verifications

[x] Full test suite: 692 passed, 0 failed (`python -m pytest tests/ -q --ignore=tests/test_voice_id.py`)
[x] Main repo checkout clean: `git -C C:/Claude/whisperdesk diff --stat` — empty
[x] All four self-report files exist: `investigation.md`, `self-audit.md`, `wrong-directions.md`, `token-usage.md`

## Oracle regression review

[x] Oracle verdict: NEEDS-DISCUSSION → `"failed"` added to reclassify set, test added → re-verified → APPROVE (implied by fix)
