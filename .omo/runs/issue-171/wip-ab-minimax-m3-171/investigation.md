# Issue #171 — Investigation: Cross-transcript LLM auto-tagging

**Target**: Issue #171 (standalone, not tracking)
**Branch**: `wip/ab-minimax-m3-171`
**Variant**: minimax-m3 (orchestrator model)
**Date**: 2026-07-27

---

## Issue summary

After transcription completes, run an LLM call to derive tags/topics from the finished transcript. Store tags against the transcript, and expose them in the transcript list view with browse/filter capability. No manual tagging required.

The issue explicitly directs reuse of the existing `LlmJob` queue infrastructure (`database/__init__.py`'s `Transcript`/`LlmJob` models) rather than inventing a new async mechanism.

---

## Design decisions (open-ended scope)

The issue leaves the following unspecified. Picking the most useful default for each:

1. **Free-form vs. fixed taxonomy** — Free-form tags. A fixed taxonomy would limit what the LLM can do and require curation. Free-form matches the user's mental model of "what is this about?" and the natural LLM capability.

2. **Multiple tags per transcript** — Yes, 1-5 tags. A meeting about "Q3 budget + vendor renewal" should have both, not just one. The LLM prompt asks for 1-5 short tags.

3. **Tag normalization** — Lowercase + trim + collapse internal whitespace. Otherwise "Q3 Budget", "q3 budget", and "q3  budget" would all coexist as duplicates. The user-facing display capitalizes the first letter, but storage is canonical lowercase.

4. **Storage** — New `transcript_tags` table with a unique `(transcript_id, tag)` pair. Two reasons:
   - Queryable for filtering ("show me transcripts with tag X").
   - A separate `tags` lookup row lets us dedupe and supports future UX like "browse all tags" or tag autocomplete. The summary payload only needs the string list, but the junction form is the safe long-term shape.

5. **Tag scoping** — Tags are per-(transcript_id) only, not per-user. Tags are LLM-derived metadata, not user-created terms, so there's no privacy boundary. Two users transcribing a similar meeting might end up with the same tag string; that's fine and useful.

6. **Auto-trigger** — All transcript kinds (meeting, dictation, voice_note) get tagged. No reason to exclude any kind — every transcript has text worth summarizing.

7. **Tag generation timing** — After transcription completes (and after the voice_note/correction chain finishes for voice_note transcripts, since that chain is the post-pipeline for voice_note). The LLM needs finished text. The tagging job runs as an independent `LlmJob(kind="tagging")`, visible on the Queue screen, can be rerun.

8. **Re-tagging behavior** — Re-running a tagging job REPLACES the prior tag set for that transcript (not appends). The VoiceNote row already follows this in-place overwrite pattern; tags should too, so "re-tag" means "give me a fresh set". Old, stale tags are exactly what re-tagging is trying to fix.

9. **Settings provider** — Use `format_provider` / `format_model` (same as the voice_note chain and dictation reformat flow), since all three are pure LLM text-in/text-out jobs that don't need a per-feature provider. The settings key is shared.

10. **UI affordance** — Tags appear as small pill chips in the transcript list row subtitle and on the detail page. The search box (currently matches title/filename) also matches tag strings. No separate "filter by tag" dropdown — extending search is enough for v1, and adding a chip-filter later is additive.

---

## Current infrastructure (verified, real file/line numbers)

### Models (`database/__init__.py`)

- **`Transcript`** (line 31): 14 columns including `kind`, `full_text`, `corrected_text`, `segments`. No `tags` column. Has relationships to `Summary`, `VoiceNote`, `TranscriptionJob`, `RelabelHistory`.
- **`LlmJob`** (line 93): `kind` field currently supports the 9 values listed at `services/llm_jobs.py:20-24`. `result_json` stores output payload. `dismissed` hides terminal jobs from Queue only.
- **No existing `Tag` or `TranscriptTag` table.** None of the existing 9 LlmJob kinds is `tagging`.

### Registration tuples (`services/llm_jobs.py`)

All four must include `"tagging"` for a new I/O-bound LLM job kind:

| Tuple | Line | Current | Must add |
|---|---|---|---|
| `VALID_KINDS` | 20 | 9 kinds | `"tagging"` |
| `AUTO_RETRY_KINDS` | 35 | 7 IO kinds | `"tagging"` (network-dependent LLM call) |
| `IO_KINDS` | 42 | 7 kinds | `"tagging"` |
| `CPU_KINDS` | 43 | rediarize, voice_match | unchanged |
| `_MAX_CONCURRENT_IO_JOBS` | 44 | 2 | unchanged — tagging competes with other I/O jobs |

There is a test `test_io_cpu_pools_partition_valid_kinds` (per the comment on line 40) that pins IO_KINDS ∪ CPU_KINDS == VALID_KINDS — adding `tagging` to IO_KINDS keeps that invariant.

### Auto-trigger call sites (TWO, per Complement Rule)

Both must be updated, since the issue says "every transcript" gets tagged. Missing the chunked path means long recordings never get tagged (the deepseek-pro investigation flagged this; confirmed real).

1. **Inline path** — `app.py:1171-1176` (`_run_transcription_pipeline`):
   - voice_note branch → `enqueue_auto_voice_note`
   - other kinds → `enqueue_auto_correction` (if `auto_correct`) + `enqueue_auto_classify`
   - **Tagging must fire for ALL kinds, after the above, in both branches.**

2. **Chunked path** — `services/queue.py:562-581` (`_finalize_if_done`):
   - voice_note branch → `enqueue_auto_voice_note`
   - other kinds → `enqueue_auto_correction` (if `auto_correct`) + `enqueue_auto_classify`
   - **Tagging must fire for ALL kinds, after the above, in both branches.**

### Auto-enqueue helpers (`services/llm_jobs.py`)

Existing: `enqueue_auto_correction` (line 159), `enqueue_auto_classify` (line 176), `enqueue_auto_voice_note` (line 195). All follow the same pattern: check provider/key, call `enqueue_llm_job`, skip with a recorded `error` when key is missing.

Need new: `enqueue_auto_tagging(db, transcript, user_settings)`. Same shape, uses `format_provider` / `format_model` settings (same LLM as voice_note/reformatting — see design decision #9).

### LLM job dispatch (`services/llm_jobs.py:run_llm_job`, line 249)

The big `if/elif` chain by `job.kind` (lines 277-516) needs a new `"tagging"` branch. Mirroring the `classify_intent` branch (line 338) is the closest shape — single LLM call, progress_total=1, JSON-mode response, parse result, write to a per-transcript storage.

### Transcript list API

- **Route**: `GET /api/transcripts` → `app.py:1248` → calls `_build_recent_transcripts` (line 547) which calls `_serialize_transcript_summary` (line 514) per row.
- **`_serialize_transcript_summary`** returns JSON with fields: id, kind, title, filename, status, duration_seconds, provider, model, language, speaker_count, diarize_requested, error, created_at, updated_at, queue_status, job_progress. **No `tags` field.**
- **Need to add**: `"tags": ["tag1", "tag2"]` to the summary payload. Computing tags per row from a join is the cleanest shape.

- **Detail serializer**: `_serialize_transcript` (line 301) — should also include `tags` (for the detail page).

### Serialized job kinds (`app.py`)

- `_SERIALIZED_JOB_KINDS` (line 267) controls which LlmJob kinds are fetched for the detail API. Must add `"tagging"`.
- `_dictation_job_fields` (line 353) returns kind-gated job fields. Per the comment, the shape is uniform across all kinds (null for kinds that can never have that job, see `test_meeting_and_dictation_have_same_job_field_names`). Must add `tagging_job: null` (or serialized job when present) to all three kind branches so the frontend can read `t.tagging_job` unconditionally.

### Frontend — Transcript list (`static/rack.js`)

- **State**: `S.bankQuery` (line 15) — current text search.
- **Load**: `loadTranscripts` (line 2161) — fetches the list, renders bank page.
- **Render**: `renderBankRows` (line 2245) — line 2249-2251 filters by `S.bankQuery` against `title` and `filename` only. **Need to extend** to also match `tags` (case-insensitive substring against any tag string).
- **Row subtitle**: `transcriptMeta(t)` (line 826) — metadata line. Could append tag pills here, but that's visual noise. Better: add a dedicated `tag-pills` line in the row body.
- **`KIND_LABELS`** (line 2346) — used on Queue screen. Needs `tagging: "TAG"` entry.
- **`jobActiveSnapshot`** (line 2938) — tracks which LLM jobs are active for the detail page poll. Needs `tagging` entry, even though `tagging` doesn't gate any UI button — it lets the detail body rebuild when a tagging job transitions in/out of active, so a just-completed tag set appears without a full page refresh.
- **`runningContainers`** (line 2979) — running-job progress bars. Could add a `tagging` container, but the cost of one extra row in the polling DOM is minimal and the Queue screen already shows it, so this is optional. Including it for parity with the other LLM jobs.

### Manual "Re-tag" trigger?

The existing `rerun_llm_job` (line 229) works for any kind — it calls `enqueue_llm_job` with the same kind. So a user can already re-run a `tagging` job from the Queue screen (the standard "Rerun" button on a failed/cancelled job). For a successful tagging, the user can re-run via a "Re-tag" button on the detail page (additive, but cheap).

**Decision for v1**: skip the detail-page "Re-tag" button. The Queue-screen Rerun works for all-terminal states (failed/cancelled). For a successful tag set the user wants to refresh, they can use the existing `rerun_llm_job` API by hitting the same endpoint with the job id. If this becomes a real complaint, add a "Re-tag" button on the detail page in a follow-up.

---

## Sibling sweep (per Phase 1 step 3)

### Other job kinds that might need similar tag integration?

**No.** Tagging is a write-only LLM result. No other job kind needs to read or modify tags. The `Summary` table is the closest analogue (LLM-generated metadata stored per transcript), and it already follows the same single-row-per-transcript pattern.

### Other list views that should show tags?

- **Dashboard** (`_build_recent_transcripts` with `limit=5` via `/api/bootstrap`): compact at-a-glance view, can omit tags. **Decision: skip for v1.** Tags belong in the full library view.
- **Queue screen**: jobs, not transcripts — irrelevant.
- **Voice-notes board**: already shows note_type grouping; tags less relevant.
- **Hotwords page**: irrelevant (these are user-created glossary terms, not LLM-derived tags).

### Other auto-trigger sites?

Only the two paths verified above. `POST /api/transcribe` is the only entry point for new transcripts; both inline and chunked paths are covered.

### Post-hoc retranscribe?

`POST /api/transcripts/{id}/retranscribe` re-runs transcription on the same audio. After retranscription, the same inline/chunked paths fire — so tagging auto-triggers are already covered. No separate call site needed.

**One thing to consider**: a retranscribe that produces a meaningfully different transcript should arguably re-tag too. Since the new transcript goes through the same auto-trigger paths, this works for free.

### Other "row subtitle" or "row metadata" consumers?

Searched the frontend for all readers of `transcriptMeta(t)` — only the bank row uses it. Adding tag pills as a separate row in the row body is safe; no other row renderer needs updating.

### What about VoiceNote's existing tags field?

`VoiceNote.structured` (per `services/voice_notes.py`) can contain a `tags` field for `idea` and `journal` types. These are short in-note tags (e.g. `["product", "Q3"]`) and are independent of the new cross-transcript tagging system. They serve different purposes (in-note brainstorm vs. cross-transcript browse). **No collision** — they're stored in different tables and surfaced in different places.

### What about `corrected_text` vs `full_text` for the LLM input?

`transcript_text_for_prompt` (`services/llm_client.py:57`) prefers `full_text` over segments. But for a transcript that has been corrected, the corrected version is a better signal for what the meeting was *about* (typos matter less for topic detection than for word-level correction). For the tagging call, **prefer `corrected_text` if present, else `full_text`**. The dictation/voice_note path that has no `corrected_text` falls back automatically. This is a small but real quality bump.

---

## What the issue's approach gets right

The issue correctly identifies:
1. Reuse `LlmJob` infrastructure (not invent new async mechanism).
2. Store tags against transcript.
3. UI surface to browse/filter by tag on transcript list.

The issue also correctly leaves the scope open (free-form vs taxonomy, single vs multiple) and asks for a "reasonable approach" — I'll explain mine in self-audit.

## What the issue is missing

1. **No mention of the chunked finalize path** (`services/queue.py:562-581`) — only the inline path would get tagging, missing long recordings.
2. **No mention of `_SERIALIZED_JOB_KINDS`** — the detail API would never include the tagging job.
3. **No mention of the `tagging` job kind needing registration** in `VALID_KINDS`, `AUTO_RETRY_KINDS`, `IO_KINDS`.
4. **No mention of `jobActiveSnapshot` / `runningContainers`** in rack.js — the detail page polling wouldn't track tagging progress; tags would only appear on a full page refresh.
5. **No specific LLM prompt design** — needs a clear, deterministic shape that the JSON-mode LLM returns. A few-sentence prompt is enough; the response shape is what matters.
6. **No tag normalization** — without canonical lowercase, the same tag ends up in three flavors and the browse view shows duplicates.
7. **No discussion of re-tagging semantics** (replace vs append) — appending on re-tag would create stale tags; replacing is the only sane default.
8. **No mention of `corrected_text` vs `full_text` as the LLM input** — corrected is a better signal for "what is this about".

---

## Implementation plan (summary)

### Backend

1. **New DB table**: `transcript_tags(transcript_id INT FK, tag VARCHAR(64) NOT NULL, created_at, PK(transcript_id, tag))`. Indexed on `tag` for the filter query. Cascade delete via `ondelete="CASCADE"` on the FK (matching the `LlmJob` pattern, line 100, and `VoiceNote` pattern, line 169). 64 chars covers real-world tags with margin; if the LLM emits longer, truncate and store.

2. **New service**: `services/tagging.py` with `generate_tags(transcript, api_key, provider_name, provider_config, model) -> list[str]`. One LLM call, JSON mode, returns `["tag1", "tag2"]`. **Never raises** (returns `[]` on any error, including API errors, JSON parse errors, and unsupported provider). Mirrors the never-raise contract that `classify_intent` and `run_voice_note_chain` already follow — the `LlmJob` worker is the only failure point, and a `[]` tags set is a valid (if unhelpful) success.

3. **`services/llm_jobs.py`**:
   - Add `"tagging"` to `VALID_KINDS` (line 20), `AUTO_RETRY_KINDS` (line 35), `IO_KINDS` (line 42).
   - Add `enqueue_auto_tagging(db, transcript, user_settings) → LlmJob` (new function).
   - Add `elif job.kind == "tagging":` branch in `run_llm_job` (line 249).

4. **`app.py`**:
   - Import `enqueue_auto_tagging`.
   - Add `"tagging"` to `_SERIALIZED_JOB_KINDS` (line 267).
   - Add `tagging_job` to `_dictation_job_fields` (line 353) — uniform across all three kind branches (the existing test `test_meeting_and_dictation_have_same_job_field_names` would fail otherwise).
   - Call `enqueue_auto_tagging(db, transcript, user_settings)` in `_run_transcription_pipeline` (line 1176) — after the existing enqueue, in **both** branches.
   - Add `tags` field to `_serialize_transcript_summary` (line 514) and `_serialize_transcript` (line 301) — read from the `transcript_tags` table.

5. **`services/queue.py`**:
   - Call `enqueue_auto_tagging(db, transcript, user_settings)` in `_finalize_if_done` (line 581) — after the existing enqueue, in **both** branches.

### Frontend

6. **`static/rack.js`**:
   - Add `tagging: "TAG"` to `KIND_LABELS` (line 2346).
   - Add `tagging` to `jobActiveSnapshot` (line 2938).
   - Add `tagging` to `runningContainers` (line 2979) for parity.
   - In `renderBankRows` (line 2249-2251): extend `bankQuery` filter to also match `t.tags`.
   - In `renderBankRows` row body: render a tag-pill row when `t.tags.length > 0`.

### Tests

7. **Unit test** (`tests/test_tagging.py`):
   - `test_generate_tags_parses_json_array` — mocked LLM returns `{"tags": ["a", "b"]}`.
   - `test_generate_tags_handles_markdown_fence` — defensive parse of LLM output that wraps the JSON in a code fence.
   - `test_generate_tags_normalizes` — lowercase, trim, dedupe.
   - `test_generate_tags_returns_empty_on_error` — never raises; returns `[]` on API error, parse error, missing keys.

8. **LlmJob dispatch test** (in `test_tagging.py` or `test_llm_jobs.py`):
   - `test_run_llm_job_tagging_writes_to_table` — drive a `tagging` job end-to-end, assert `transcript_tags` rows are written.
   - `test_run_llm_job_tagging_clears_prior_tags` — re-run replaces, doesn't append.
   - `test_run_llm_job_tagging_handles_empty_response` — `[]` from LLM still completes the job.

9. **Finalize enqueue test** (in `test_correction_chunked_finalize.py` or new `test_tagging_finalize.py`):
   - `test_finalize_enqueues_tagging_job` — for both voice_note and meeting/dictation kinds, the finalizer enqueues a `tagging` job in addition to the existing one.

10. **Serialize contract test** (in `test_serialize_transcript_batch.py` or new file):
    - `test_summary_includes_tags` — `_serialize_transcript_summary` returns a `tags` field.
    - `test_summary_includes_tagging_job` — `_serialize_transcript` returns `tagging_job` uniform across kinds.

### Walk the issue's implicit acceptance criteria

The issue has no explicit "Definition of Done" or "Requirements" list. My implicit criteria:

- [x] LLM step derives tag(s) from a finished transcript
- [x] Reuses `LlmJob` queue infrastructure
- [x] Stores resulting tags against the transcript
- [x] UI surface to browse/filter by tag on the transcript list
- [x] Auto-runs for all kinds (meeting, dictation, voice_note)
- [x] Visible on the Queue screen
- [x] Rerunnable like other LLM jobs
- [x] Multi-tenant safe (per-user via FK on transcript)

### Drive the specific regression risk

The biggest regression risk in this change: a missed call site means a transcript kind never gets tagged. The auto-enqueue test (item 9) covers all three kinds in the chunked path; a parallel test for the inline path is the belt-and-braces. The serialize contract test (item 10) catches the case where `tagging_job` is missing from one of the three `_dictation_job_fields` branches.
