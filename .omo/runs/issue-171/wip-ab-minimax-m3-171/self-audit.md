# Issue #171 — Self-Audit Checklist

**Target**: Issue #171
**Branch**: `wip/ab-minimax-m3-171`
**Variant**: minimax-m3

This is a re-verification of every concrete promise from `investigation.md` and
the issue's implicit acceptance criteria. Each `[x]` is marked only after the
artifact was re-opened and confirmed to exist (not from memory of what I
intended to write).

## Implementation promises (from investigation.md)

Backend:
- [x] New DB table `transcript_tags` with FK cascade — confirmed at
      `database/__init__.py:198-211` (TranscriptTag class) and
      `database/__init__.py:393-405` (idempotent CREATE TABLE IF NOT EXISTS
      + index in `init_db`).
- [x] New service `services/tagging.py` with `generate_tags()` (never-raise,
      JSON-mode, JSON extraction defensive against fences/prose, normalization)
      — confirmed at `services/tagging.py`. Imports cleanly, AST-parsed.
- [x] Added `"tagging"` to `VALID_KINDS` (line 23), `AUTO_RETRY_KINDS` (line
      38), `IO_KINDS` (line 45) in `services/llm_jobs.py`.
- [x] New `enqueue_auto_tagging` helper at `services/llm_jobs.py:223-238`.
- [x] New `tagging` branch in `run_llm_job` at `services/llm_jobs.py:380-409`
      (REPLACE not append; cancel guard; writes `result_json` with tag list).
- [x] Added `"tagging"` to `_SERIALIZED_JOB_KINDS` (line 271) in `app.py`.
- [x] Added `tagging_job` to all three branches of `_dictation_job_fields` in
      `app.py:357-403` (uniform shape — see test `test_all_kinds_have_same_job_field_names`).
- [x] `enqueue_auto_tagging` called in `_run_transcription_pipeline` (inline
      path) at `app.py:1219` after the kind-specific enqueue.
- [x] `enqueue_auto_tagging` called in `_finalize_if_done` (chunked path) at
      `services/queue.py:582-584` after the kind-specific enqueue.
- [x] `tags` field added to `_serialize_transcript` (line 343) via
      `_tags_for_transcript` helper.
- [x] `tags` field added to `_serialize_transcript_summary` (line 562),
      threaded via `_build_recent_transcripts` (line 573) using the batch
      helper `_tags_for_transcripts` (avoids N+1).

Frontend:
- [x] `tagging: "TAG"` added to `KIND_LABELS` in `static/rack.js:2351`.
- [x] `tagging` added to `jobActiveSnapshot` (line 2946) so detail-page
      polling detects a completed tagging job and rebuilds the body.
- [x] `tagging` added to `runningContainers` (line 2986) for in-flight
      progress parity with the other LLM jobs.
- [x] `bankQuery` filter extended to also match tag strings —
      `static/rack.js:2253-2257`.
- [x] Tag pill row rendered in the bank row body when `t.tags.length > 0` —
      `static/rack.js:2289-2290, 2302`.
- [x] `scheduleDetailPoll` extended to include `tagging_job` so the detail
      page refreshes when a tagging job runs — `static/rack.js:2518`.
- [x] Tag row rendered in the detail body top section —
      `static/rack.js:3574-3584`.

## Test promises (from investigation.md, items 7-10)

- [x] `tests/test_tagging.py` — 30 tests covering:
      - registration/tuple membership (4)
      - internal helpers `_extract_json_object` and `_normalize` (10)
      - `generate_tags` end-to-end via mocked httpx (7)
      - `enqueue_auto_tagging` shape + keyless skip + every-kind (4)
      - `run_llm_job` dispatch (writes rows, REPLACE not append, empty list
        is valid completed, cancel-during-LLM skips write) (4)
      Confirmed at `tests/test_tagging.py` — 30/30 passing.
- [x] Chunked-finalize test (item 9): two new tests at
      `tests/test_correction_chunked_finalize.py:85-142` covering meeting
      kind and voice_note kind enqueue. Both passing.
- [x] Serializer contract test (item 10): updated
      `tests/test_serialize_transcript_contract.py:36-39, 76-83` to assert
      `tagging_job` and `tags` fields. All three kind variants passing.
- [x] Pre-existing test `test_finalize_skips_correction_when_setting_disabled`
      updated to scope the assertion to "no correction/classify jobs" rather
      than "no LlmJobs" — confirmed at
      `tests/test_correction_chunked_finalize.py:58-72`. Passing.

## Implicit acceptance criteria (from investigation.md)

- [x] LLM step derives tag(s) from a finished transcript — `services/tagging.py`.
- [x] Reuses `LlmJob` queue infrastructure — added as a new kind, all four
      registration tuples updated, dispatch in `run_llm_job`.
- [x] Stores resulting tags against the transcript — `transcript_tags` table
      with FK to `transcripts.id` (cascade delete on transcript removal).
- [x] UI surface to browse/filter by tag on the transcript list — bank row
      pill display + extended `bankQuery` filter.
- [x] Auto-runs for all kinds (meeting, dictation, voice_note) — helper is
      kind-agnostic, called in both inline and chunked paths; tested.
- [x] Visible on the Queue screen — `KIND_LABELS` shows "TAG", serialized
      job is part of the queue payload.
- [x] Rerunnable like other LLM jobs — `rerun_llm_job` works for any
      `VALID_KINDS` entry, no special handling needed.
- [x] Multi-tenant safe — per-user via FK on transcript (transcript already
      has `user_id`; cascade from transcript removal covers tags too).

## Regression check (drive the specific risk)

The Complement Rule risk here: a missed call site means a transcript kind
never gets tagged. Three concrete tests pin this:
- [x] `test_enqueue_auto_tagging_fires_for_every_kind` — helper is called
      for meeting/dictation/voice_note (loop test).
- [x] `test_finalize_enqueues_tagging_job_for_meeting` — chunked path
      meeting/dictation enqueue.
- [x] `test_finalize_enqueues_tagging_job_for_voice_note` — chunked path
      voice_note enqueue.

The serializer shape risk: a missing `tagging_job` from one of the three
`_dictation_job_fields` branches would fail `test_all_kinds_have_same_job_field_names`.
- [x] Test updated to assert `tagging_job` is `None` in the no-job fixture
      for all three kinds — passing.

The replace-not-append risk: a re-tag with no REPLACE step would accumulate
stale tags.
- [x] `test_run_llm_job_tagging_replaces_not_appends` — pre-seeds an "old tag"
      and asserts only "new tag" remains after re-run. Passing.

## Full suite results

`/c/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/e2e -q`
ran 464 tests, 0 failures, 1 unrelated warning (httpx/Starlette deprecation).

The full suite is the gate. Targeted tests (test_tagging.py, test_serialize_transcript_contract.py, test_correction_chunked_finalize.py, test_bootstrap.py, test_serialize_transcript_batch.py, test_smoke.py, test_voice_note_chain.py, test_llm_jobs.py, test_reformatting.py) all pass; no other test was broken by these changes.

## Browser-tier skip (declared, not silent)

The change adds a new UI affordance (bank row pill, detail row tag section,
search-box filter extension, new `tagging` job in the Queue screen). The
AGENTS.md testing-tiers rule says a UI-visible change warrants at least a
targeted browser flow check. This run did NOT run the e2e-regression-http
suite (16-scenario Playwright flow) because no Playwright MCP browser tool
is available in this session. The static + unit test coverage stands in
for the live browser pass. Logging this explicitly in
`wrong-directions.md` per the instructions, not silently skipping.

If a Playwright tool becomes available before merge, the flow to drive is:
1. Upload a transcript
2. Wait for the `tagging` job to land completed
3. Confirm the bank row shows the tag pills
4. Type a known tag string into the search box
5. Confirm the row stays matched
6. Open the detail page, confirm the tag row appears
7. Trigger a re-tag via the Queue screen Rerun button
8. Confirm the displayed tag set reflects the LLM's new output (REPLACE)
