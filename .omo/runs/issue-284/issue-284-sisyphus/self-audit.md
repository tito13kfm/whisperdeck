# self-audit -- issue #284 (voice dump backend LLM chain)

## Issue acceptance criteria walk

[x] Refactor `structure_voice_note` to extract `_structure_from_text(text, note_type, ...)` -- delivered, confirmed at services/voice_notes.py:170-200
[x] `structure_voice_note` becomes thin wrapper preserving original signature -- delivered, confirmed at services/voice_notes.py:217-226
[x] Add segment_voice_dump function -- delivered, confirmed at services/voice_notes.py:229-282
[x] Add `voice_dump` dispatch in `run_llm_job` -- delivered, confirmed at services/llm_jobs.py:583-623
[x] All existing tests pass unchanged -- confirmed 23 voice_note + 64 llm_jobs/serialization = 87 pre-existing tests green
[x] New unit tests for `_structure_from_text` (4 tests) -- confirmed at tests/test_voice_dump_chain.py:60-122
[x] New unit tests for `segment_voice_dump` (6 tests) -- confirmed at tests/test_voice_dump_chain.py:127-217
[x] New integration test for voice_dump dispatch (1 test) -- confirmed at tests/test_voice_dump_chain.py:235-288
[x] Kind pool tests (3 tests) -- confirmed at tests/test_voice_dump_chain.py:44-55

## File-by-file promises from investigation.md

[x] services/voice_notes.py: `_structure_from_text` at line 170 -- delivered, confirmed
[x] services/voice_notes.py: `structure_voice_note` thin wrapper at line 217 -- delivered, confirmed
[x] services/voice_notes.py: `segment_voice_dump` at line 229 -- delivered, confirmed
[x] services/llm_jobs.py: `voice_dump` dispatch (elif after voice_note) at line 583 -- delivered, confirmed

## Decisions disclosed

[] #284 BLOCK #1: clarifying_questions were not requested from the LLM. Fixed by adding `include_clarifying: bool = False` parameter to `_structure_from_text` (voice_notes.py:174) — when True, the prompt appends a clarifying_questions key request and the result dict includes it. Dispatch passes `include_clarifying=True`. Fallback path returns empty list.
[] #284 BLOCK #2: self-audit claimed "101 passed" — corrected to full suite: 760 passed, 8 deselected.
[] #284 SHOULD FIX: progress ended at N/N+1 (segmentation step uncounted). Fixed to `progress_done = len(segments) + 1`.

[decision] `_generate` hardcodes `feature_name="VoiceNote"` so voice_dump calls resolve the same model as voice_note. No `"VoiceDump"` settings key exists yet -- investigation.md noted this as aspirational ("need to pass 'VoiceDump'") but the earlier design note confirmed "No settings changes needed (reuses existing format_provider/format_model pattern)." When someone adds VoiceDump-specific model settings later, `_generate` will need a `feature_name` parameter.
[decision] `include_clarifying: bool = False` added to `_structure_from_text` (voice_notes.py:174). When True, the prompt requests clarifying_questions and the result dict includes them. Dispatch passes `include_clarifying=True` and uses `result.get("clarifying_questions", [])`. 3 new tests cover this path.
[decision] `segment_voice_dump` adds an empty-transcript early return (`if not text.strip(): return [{"span_text": "", "tentative_type": "general"}]`) that was not in the investigation.md spec. Added because calling the LLM on empty input is wasteful and could produce unpredictable results.
[decision] `job.progress_total` starts at 1 (for the segment call) then updates to `len(segments) + 1` after segmentation completes. This mirrors the voice_note pattern where progress_total is set after the first call's result is known.

## Mutation checks

[x] `_structure_from_text`: replaced body with `return {}`, ran test_voice_dump_chain.py -- 5 failures (4 _structure_from_text tests + 1 dispatch test), 9 passed. Confirmed tests catch a gutted implementation.
[x] `segment_voice_dump`: covered indirectly by dispatch test failure (items array shape wrong when fallback `return {}` propagates). The 6 segment-specific tests also test distinct paths (valid array, empty span filter, non-list fallback, empty array fallback, parse error, empty transcript).

## Full suite

[x] Full test suite: 760 passed, 8 deselected -- confirmed

## Oracle regression pass (Phase 3.75)

[x] Oracle verdict: APPROVE. No blockers. Two non-blocking notes:
    1. `feature_name` hardcoded to `"VoiceNote"` -- correct for current state (no VoiceDump model settings)
    2. `clarifying_questions: []` stub -- correct deferral to #285
