# self-audit — issue #283 (voice dump schema + kind plumbing)

## Issue acceptance criteria walk

[x] Transcript.kind accepts "voice_dump" — confirmed at app.py:1447,1527,1543,2077 (all 4 validation sites)
[x] VoiceDumpItem table exists (no unique constraint on transcript_id) — confirmed at database/__init__.py:195-216
[x] All existing tests pass unchanged — confirmed 745 passed, 8 deselected
[x] test_io_cpu_pools_partition_valid_kinds still passes — confirmed 1 passed
[x] voice_dump kind accepts "voice_dump" — VALID_KINDS at services/llm_jobs.py:23, IO_KINDS at :42

## File-by-file promises from investigation.md

[x] database/__init__.py: VoiceDumpItem model added at lines 191-216 — confirmed
[x] database/__init__.py: Transcript.voice_dump_items relationship added at line 75 — confirmed
[x] services/transcription.py: voice_dump branch in summarize method, same stub as voice_note — confirmed at line 222
[x] services/llm_jobs.py: "voice_dump" in VALID_KINDS line 23 — confirmed
[x] services/llm_jobs.py: "voice_dump" in IO_KINDS line 42 — confirmed
[x] services/llm_jobs.py: NOT in AUTO_RETRY_KINDS — confirmed (line 35 unchanged)
[x] services/llm_jobs.py: NOT in CPU_KINDS — confirmed (line 43 unchanged)
[x] app.py: voice_dump_job: None in all 3 _dictation_job_fields branches (dictation line 437, voice_note line 446, default line 453) — confirmed
[x] app.py: "voice_dump" in diarization force-off tuple line 1153 — confirmed
[x] app.py: "voice_dump" in single-file upload validation line 1447 — confirmed
[x] app.py: "voice_dump" in per-file override validation line 1527 — confirmed
[x] app.py: "voice_dump" in global bulk validation line 1543 — confirmed
[x] app.py: "voice_dump" in retranscribe validation line 2077 — confirmed (sibling found in sweep, not named by issue)
[x] static/rack.js: bulk defaults dropdown option at line 2764 — confirmed
[x] static/rack.js: per-file dropdown option at line 2826 — confirmed
[x] tests/test_serialize_transcript_contract.py: EXPECTED_KEYS includes "voice_dump_job" line 39 — confirmed
[x] tests/test_serialize_transcript_contract.py: voice_dump_job: None assertions for all 3 kinds lines 83-85 — confirmed

## Decisions disclosed

[decision] retranscribe validation (app.py:2077) was not named by the issue but was updated per Complement Rule sweep — same tuple as other 3 validation sites
[decision] No change to services/settings.py needed — issue said "add to bulk_defaults.kind allowed values" but bulk_defaults is a default-value dict, not a validation list. Actual validation lives in app.py.
[decision] No change to rack.js tab logic (lines 3703, 4842, etc.) — the Dump Review tab is sub-issue #287's scope
[decision] No change to kindLabel formatting (line 4684) — "voice_dump" doesn't need "voice_" prefix stripping since it renders as "Voice dump" naturally with capitalize

## Mutation checks

[x] test_serialize_transcript_contract tests: add "voice_dump_job" to EXPECTED_KEYS — mutation check: if voice_dump_job field removed from _dictation_job_fields, key-set assertion fails? YES (extra/missing key in EXPECTED_KEYS vs actual keys). If voice_dump_job set to non-None, per-kind assertions at lines 83-85 catch it.

## Full suite

[x] Full test suite: 745 passed, 8 deselected — confirmed

## Oracle regression pass (Phase 3.75)

[x] Oracle verdict: APPROVE. Two non-blocking watch-outs deferred to #284:
    1. POST /summarize route doesn't explicitly block voice_dump (service stub mitigates)
    2. _run_transcription_pipeline enqueues correction + classify for voice_dump (voice_note skips these; voice_dump should too per #284 design)
