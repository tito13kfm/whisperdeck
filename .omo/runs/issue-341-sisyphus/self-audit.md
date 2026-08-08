# self-audit.md — issue #341 (sisyphus)

## Investigation promises

[x] Complete writer site table (investigation.md:25-39) — 13 sites enumerated, verified against grep output
[x] Sibling sweep (investigation.md:86-103) — all segment-modifying paths checked, hallucination filter gap noted, no writer sites missed
[x] Open questions answered (investigation.md:152-207) — `DiarizationResult.speaker_count` still consumed by `/api/diarize`, test fixture breakage predicted, backfill deferred

## Implementation

[x] `app.py:1392`: `transcript.speaker_count = count_distinct_speakers(merged)` — delivered, confirmed at app.py:1392
[x] `services/queue.py:606`: `transcript.speaker_count = count_distinct_speakers(merged)` — delivered, confirmed at services/queue.py:606
[x] `services/queue.py:599`: `from services.relabel import clear_relabel_history, count_distinct_speakers` — delivered, confirmed at services/queue.py:599
[x] `services/llm_jobs.py:681`: `transcript.speaker_count = count_distinct_speakers(merged)` — delivered, confirmed at services/llm_jobs.py:681
[x] `services/llm_jobs.py:678`: `from services.relabel import clear_relabel_history, count_distinct_speakers` — delivered, confirmed at services/llm_jobs.py:678

## Test

[x] `merged` fixture self-consistent (2 distinct speakers: `"SPEAKER_01"`, `"SPEAKER_02"`) — delivered at tests/test_posthoc_reprocess.py:364-369
[x] `test_run_llm_job_rediarize_merges_in_place_without_key` — mutation check:
    ran: C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/test_posthoc_reprocess.py::test_run_llm_job_rediarize_merges_in_place_without_key -q  ->  1 passed
    mutated: `transcript.speaker_count = count_distinct_speakers(merged)` -> `transcript.speaker_count = 0` in services/llm_jobs.py:681; reran  ->  1 failed
    restored: reran  ->  1 passed

[decision] No new test for `speaker_count` computation — `count_distinct_speakers` already has dedicated tests in `tests/test_relabel_speaker_count.py` (38 passed). The changed test exercises the integration: rediarize path now calls `count_distinct_speakers` instead of using pre-merge value.
[decision] No backfill for existing rows — value converges next time any writer touches the transcript. The issue explicitly marked this as acceptable.
[decision] `DiarizationResult.speaker_count` kept on dataclass — still consumed by `/api/diarize` endpoint (`app.py:2537`), which returns raw diarization results with no segments to count from.

## Acceptance criteria walk (from issue #341)

[x] All three diarize paths (inline diarize, chunked finalize, rediarize) use post-merge `count_distinct_speakers(merged)` — delivered at app.py:1392, services/queue.py:606, services/llm_jobs.py:681
[x] Family B paths (relabel, voice_match) already use `count_distinct_speakers` — unchanged, verified at investigation.md:70-77
[x] No remaining Family A writers — grep confirmed: only 3 writer sites, all changed to Family B definition
[x] No regressions — 892 tests pass, 1 pre-existing failure (`test_voice_match_recomputes_speaker_count_on_merge`, mocks `identify` but code calls `identify_detailed` — pre-dates this PR)

## Pre-existing test failure

[ ] `test_voice_match_recomputes_speaker_count_on_merge` — pre-existing: test mocks `voice_id_service.identify` but the voice_match path calls `voice_id_service.identify_detailed` instead. Fails identically on clean master. Not in scope for this issue.

## Oracle regression pass (Phase 3.75)

[x] Oracle verdict: **APPROVE** — "Diff correctly unifies all diarization writers on the post-merge definition. All three production write sites covered, scope safe."

## Main repo checkout check

[x] `git -C C:/Claude/whisperdesk rev-parse --abbrev-ref HEAD` = `master`
[x] `git -C C:/Claude/whisperdesk status --porcelain -uall` — only `.omo/runs/` files

## PR

[x] PR #345: https://github.com/tito13kfm/whisperdeck/pull/345
