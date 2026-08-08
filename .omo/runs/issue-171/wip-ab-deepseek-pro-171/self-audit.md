# Issue #171 Self-Audit Checklist

## Implementation promises from investigation.md

- [x] New DB table `TranscriptTag` — delivered, `database/__init__.py:227-240`
- [x] Tag generation service `services/tagging.py` — delivered, `generate_tags()` with LLM prompt, never-raise guarantee, dedup/cap logic
- [x] `tagging` in VALID_KINDS — delivered, `services/llm_jobs.py:23`
- [x] `tagging` in AUTO_RETRY_KINDS — delivered, `services/llm_jobs.py:35`
- [x] `tagging` in IO_KINDS — delivered, `services/llm_jobs.py:42`
- [x] `enqueue_auto_tagging` helper — delivered, `services/llm_jobs.py:217-232`
- [x] Tagging dispatch branch in `run_llm_job` — delivered, `services/llm_jobs.py:435-464`
- [x] Auto-trigger in inline path (app.py) — delivered, `app.py:1182`
- [x] Auto-trigger in chunked path (queue.py) — delivered, `services/queue.py:583-584`
- [x] `tagging` in `_SERIALIZED_JOB_KINDS` — delivered, `app.py:271`
- [x] `tagging_job` in uniform shape — delivered, `app.py:377,383,389`
- [x] `tags` field in `_serialize_transcript_summary` — delivered, `app.py:547`
- [x] `tags` field in `_serialize_transcript` — delivered, `app.py:339`
- [x] Tag filter in `renderBankRows` — delivered, `static/rack.js:2251`
- [x] Tag display in `transcriptMeta` — delivered, `static/rack.js:832`
- [x] Tag display in `bankDetailFields` — delivered, `static/rack.js:2053`
- [x] `tagging` in `jobActiveSnapshot` — delivered, `static/rack.js:2947`
- [x] `tagging` in `KIND_LABELS` — delivered, `static/rack.js:2348`
- [x] `tagging` in `runningContainers` — delivered, `static/rack.js:2987`

## Tests

- [x] Unit tests for `generate_tags` — delivered, `tests/test_tagging.py` (10 tests, all pass)
- [x] Partition test still passes — confirmed, `tests/test_llm_jobs.py::test_io_cpu_pools_partition_valid_kinds`
- [x] Voice-note chain tests still pass — confirmed, all 22 tests pass
- [x] Existing LLM job tests still pass — confirmed, all 40 tests pass

## Issue acceptance criteria

Issue #171 lists no explicit acceptance criteria checklist. The implicit criteria are:
- [x] LLM derives tags from finished transcript — `generate_tags()` does this
- [x] Reuses `LlmJob` queue infrastructure — kind="tagging" goes through the same `enqueue_llm_job` / `run_llm_job` / worker loop
- [x] Store tags against transcript — `TranscriptTag` table, queried in serializers
- [x] UI surface to browse/filter by tag — bank search extended to match tags, tags shown in row metadata and detail fields

## NOT delivered (intentional)

- [ ] No "Tag this transcript" button on detail page — deferred, tags are auto-generated only. Manual retag UI can come later.
- [ ] No tag autocomplete or tag cloud — deferred, tags are free-form. Autocomplete requires a taxonomy or dedup layer.
- [ ] No tag display on Dashboard recent-transcripts panel — per investigation.md, dashboard is compact; tags belong in the full library view.
