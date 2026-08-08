# Issue #147 — N+1 in `_serialize_transcript` — investigation

## TL;DR

The issue's premise (tape library list endpoint running 500-800 queries per
request) is **wrong** for the current code. The list endpoint goes through
`_serialize_transcript_summary` (line 498), not `_serialize_transcript`, and
`_serialize_transcript_summary` does not call `latest_job()` at all.

`_serialize_transcript` itself is only called from single-transcript endpoints
(7 sites), so the N+1 multiplier that motivates the issue isn't real. But the
function still does 3 `latest_job()` calls per call (up to 7 for dictation
transcripts), and the fix the issue proposes is a clean win even for the
single-transcript path. Apply the fix.

## Real call sites of `_serialize_transcript` (current `app.py`)

All 7 sites in `app.py` are single-transcript operations. None are per-row
in a list. Confirmed by `grep -n "_serialize_transcript" app.py`:

| Line | Endpoint | What it does | Jobs fetched per call |
|---|---|---|---|
| 1059 | `POST /api/transcribe` (sync finalize) | Final response for one transcript | 3 + 4 (if dictation) |
| 1109 | `POST /api/transcribe` (chunked finalize) | Final response for one transcript | 3 + 4 (if dictation) |
| 1191 | `GET /api/transcripts/{id}` | Detail page response | 3 + 4 (if dictation) |
| 1442 | `PATCH /api/transcripts/{id}` | Update response | 3 + 4 (if dictation) |
| 1629 | `POST /api/transcripts/{id}/speakers/rename` | Rename response | 3 + 4 (if dictation) |
| 1670 | `POST .../segments/retag` | Retag response | 3 + 4 (if dictation) |
| 1711 | `POST .../enroll-speaker` | Enroll response | 3 + 4 (if dictation) |

(List endpoint `GET /api/transcripts` at line 1179 uses
`_build_recent_transcripts` -> `_serialize_transcript_summary`. No
`latest_job()` calls there.)

So the "for the tape library listing 100 transcripts, that's 500-800
queries" line in the issue is stale - the tape library was already split
off to a lighter-weight serializer (see the docstring at
`_dictation_job_fields` line 320, which still references the old shape but
the code path no longer does).

## What `_serialize_transcript` does today

```
_serialize_transcript(db, t, include_relabel=False) -> dict
  - latest_job(db, t.id, "correction")       [always]
  - latest_job(db, t.id, "summary")          [always]
  - latest_job(db, t.id, "voice_match")      [always]
  - _dictation_job_fields(db, t):
      if t.kind == "dictation":
        - latest_job(db, t.id, "classify_intent")
        - latest_job(db, t.id, "format_markdown")
        - latest_job(db, t.id, "format_email")
        - latest_job(db, t.id, "format_coding_prompt")
  - compute_queue_status(db, t)              [TranscriptionJob scan, separate concern]
  - if include_relabel:
        - latest_relabel(db, t.id)           [RelabelHistory, also per-call but not in this issue]
```

Per single-transcript call: 3 to 7 `LlmJob` queries + 1 `TranscriptionJob`
scan + 1 `RelabelHistory` (if `include_relabel`). The issue scope covers
the `LlmJob` ones only.

## Issue's suggested approach

1. New helper `_batch_latest_jobs(db, transcript_ids) -> dict[(tid, kind), LlmJob]`
   using a `MAX(id) GROUP BY (transcript_id, kind)` subquery.
2. `_serialize_transcript` accepts the pre-fetched map and looks up jobs
   from it instead of calling `latest_job()`.
3. List endpoint uses batch fetching.

The list-endpoint claim (3) doesn't apply (see above). 1 and 2 are still
the right fix. Apply them.

## Scope of fix

- `app.py:25` - add `func` to the `from sqlalchemy import ...` line.
- `app.py` - new constant `_SERIALIZED_JOB_KINDS` listing the 7 kinds the
  serializer consumes (matches what `_serialize_transcript` + `_dictation_job_fields`
  actually use today; `rediarize` is in `VALID_KINDS` but not consumed by
  the serializer).
- `app.py` - new helper `_batch_latest_jobs(db, transcript_ids)`.
- `app.py` - `_serialize_transcript` and `_dictation_job_fields` gain a
  required keyword-only `jobs_map: dict` parameter; they look up jobs from
  the map instead of calling `latest_job()`.
- `app.py` - all 7 call sites pass a `jobs_map` built from
  `_batch_latest_jobs(db, [t.id])`. For single-transcript endpoints that's
  a 1-element list, but the cost is one batched query (2 SQL statements)
  instead of 3-7 individual `latest_job()` calls - still a net win and
  the function signature stays consistent.

## Out of scope (intentionally not changed)

- `compute_queue_status(db, t)` - also does an N+1 scan over
  `TranscriptionJob`, but that's transcription-job state, not LLM-job
  state, and the issue is scoped to `LlmJob`. Different table, different
  batch query, different PR.
- `latest_relabel(db, t.id)` - also per-call when `include_relabel=True`,
  but only for the detail and rename/retag/enroll paths, not in any list
  context. Issue doesn't mention it.
- The "list endpoint" claim in the issue - see TL;DR. The list endpoint
  already doesn't call `_serialize_transcript`.

## Risks

- The 7 kinds list is hard-coded in `_SERIALIZED_JOB_KINDS`. If a future
  PR adds a new LLM job kind and the serializer learns about it, both
  must be updated in lockstep. Mitigation: define the constant next to
  the helper with a one-line comment pointing at the serializer, so the
  link is obvious.
- The batch query uses `LlmJob.transcript_id.in_(transcript_ids) AND
  LlmJob.kind.in_(_SERIALIZED_JOB_KINDS)`. Both are indexed columns
  (`id` PK, `transcript_id` FK, `kind` not indexed but cardinality is 8
  strings so the filter is cheap). For a 50-transcript list this is
  50-350 rows in the worst case. Negligible.

## Verification plan

- Static read: every call site compiles, response dict has the same keys
  in the same order.
- Existing unit tests for the touched endpoints (transcript detail, llm
  jobs, correction, reformatting, voice match, relabel) must pass - they
  assert response shapes.
- No live browser test (no Playwright MCP available; change is
  serializer-level and isolated, matching AGENTS.md's "no browser needed
  for a backend fix scoped to one module").
