# Issue #171 — Investigation: Cross-transcript LLM auto-tagging

**Target**: Issue #171 (standalone, not tracking)
**Branch**: `wip/ab-deepseek-pro-171`
**Date**: 2026-07-27

---

## Issue summary

After transcription completes, run an LLM call to derive tags/topics from the finished transcript. Store tags against the transcript, and expose them in the transcript list view with browse/filter capability. No manual tagging required.

## Design decisions (open-ended scope)

The issue leaves the following unspecified:

1. **Free-form vs. fixed taxonomy** — I chose free-form tags (LLM generates any tag string). A fixed taxonomy would require curation and limiting the LLM's utility. Free-form matches the user's mental model of "what is this about."

2. **Multiple tags per transcript** — Yes, 1-5 tags. A transcript about "Q3 budget + vendor renewal" should have both tags, not just one. The LLM prompt will ask for 1-5 tags.

3. **Storage** — A new `transcript_tags` junction table (not a JSON column on Transcript). Reasoning: queryable for filtering, can evolve to global tag dedup/autocomplete later, and avoids SQLite JSON query headaches. Simple: `tag_id FK → tags.id`, `transcript_id FK → transcripts.id`.

4. **Auto-trigger** — All transcript kinds (meeting, dictation, voice_note) get tagged. The issue says "every transcript" and there's no reason to exclude any kind.

5. **Tag generation timing** — After transcription completes and after the voice_note/correction chain finishes (for voice_note transcripts). The LLM needs finished text. The tagging job runs as an independent `LlmJob(kind="tagging")` so it's visible on the Queue screen and can be rerun.

---

## Current infrastructure

### Models (database/__init__.py)

- **`Transcript`** (line 31): 14 columns including `kind`, `full_text`, `corrected_text`, `segments`. No `tags` column currently. Has relationships to `Summary`, `VoiceNote`, `TranscriptionJob`, `RelabelHistory`.
- **`LlmJob`** (line 93): `kind` field currently supports: `correction`, `summary`, `rediarize`, `voice_match`, `format_markdown`, `format_email`, `format_coding_prompt`, `classify_intent`, `voice_note`. `result_json` stores output payload. `dismissed` hides terminal jobs from Queue only.
- **No existing `Tag` or `TranscriptTag` table.**

### Registration tuples (services/llm_jobs.py)

All required for a new job kind:

| Tuple | Line | Current values | Must add |
|---|---|---|---|
| `VALID_KINDS` | 20 | 9 kinds | `"tagging"` |
| `AUTO_RETRY_KINDS` | 35 | 7 kinds (IO-bound) | `"tagging"` (it's an LLM call = network-dependent) |
| `IO_KINDS` | 42 | 7 kinds | `"tagging"` |
| `_MAX_CONCURRENT_IO_JOBS` | 44 | 2 | No change — tagging competes with other I/O jobs |

### Auto-trigger pattern (two call sites)

Both must be updated:

1. **Inline path** — `app.py` lines 1161-1176: After transcription completes, checks `kind`:
   - `voice_note` → `enqueue_auto_voice_note(db, transcript, user_settings)`
   - Others → `enqueue_auto_correction` + `enqueue_auto_classify`
   - **Tagging should fire for ALL kinds** after these existing enqueues.

2. **Chunked path** — `services/queue.py` lines 562-581: Same branching after chunked transcription finalizes.
   - **Tagging should fire for ALL kinds** after these existing enqueues.

### Auto-enqueue helpers (services/llm_jobs.py)

Existing: `enqueue_auto_correction` (line 159), `enqueue_auto_classify` (line 176), `enqueue_auto_voice_note` (line 195).
All follow the same pattern: check provider/key, call `enqueue_llm_job`.

Need new: `enqueue_auto_tagging(db, transcript, user_settings)` — follows same pattern, uses `format_provider`/`format_model` settings (same LLM as voice_note/reformatting).

### LLM job dispatch (services/llm_jobs.py `run_llm_job`)

The `run_llm_job` function (line 249) has a big `if/elif` chain by `job.kind`. Need a new branch for `"tagging"`:
- Reads transcript text (use `transcript_text_for_prompt` from `services/llm_client`)
- Runs one LLM call with a tag-generation prompt, JSON mode
- Parse tags from response
- Store to `transcript_tags` table
- Write `result_json` with the tags for run-history view

### Transcript list API

- **Route**: `GET /api/transcripts` → `app.py` line 1248 → calls `_build_recent_transcripts` (line 547)
- **Serialization**: `_serialize_transcript_summary` (line 514) returns JSON with fields: id, kind, title, filename, status, duration_seconds, provider, model, language, speaker_count, diarize_requested, error, created_at, updated_at, queue_status, job_progress. **No tags field.**
- **Need to add**: `"tags": ["tag1", "tag2"]` to the summary payload.
- **Detail serialization**: `_serialize_transcript` (line 301) — should also include tags for the detail view.

### Serialized job kinds (app.py)

- `_SERIALIZED_JOB_KINDS` (line 267): controls which LlmJob kinds are fetched for the detail API. Must add `"tagging"`.
- `_dictation_job_fields` (line 353): kind-specific job fields. Should add `tagging_job` to the uniform shape (null for all kinds currently, non-null when a tagging job exists). Keep `tagging_job` outside the `if kind ==` branches since tagging runs on all kinds.

### Frontend — Transcript list (static/rack.js)

- **State**: `S.bankQuery` (line 15) — current text search. `S.bankSort` (line 16).
- **Load**: `loadTranscripts` (line 2161) — fetches transcripts, renders bank page.
- **Render**: `renderBankRows` (line 2245) — maps over `bankListCache`, filters by `S.bankQuery` (title/filename match), renders `<details>` rows.
- **Filter**: No tag filter currently. Search only matches title or filename (line 2250). Need to extend.
- **Row detail**: `bankDetailFields(t, sv)` (line 2045) — renders per-status detail fields. Could add tags here for the "done" state.
- **Row subtitle**: `transcriptMeta(t)` (line 826) — metadata line. Could show tags here.
- **KIND_LABELS**: line 2346 — used on Queue screen. Needs `tagging: "TAG"` entry.

### Frontend — Job polling

- `jobActiveSnapshot` (line 2938): tracks active jobs per kind. Needs `tagging` entry.
- `updateDetailJobStatus` (line 2955): updates job-gated action buttons. `tagging` doesn't gate any button, but the running-container list (line 2979) should show tagging progress.

---

## Sibling sweep

Per Phase 1 step 3: actively search for patterns the issue doesn't name.

### Other job kinds that might need similar tag integration?

**No.** Tagging is a write-only LLM result. No other job kind needs to read or modify tags. The Summary table is the closest analogue (LLM-generated metadata stored per transcript) and it already follows the same pattern.

### Other list views that should show tags?

- **Dashboard** (`_build_recent_transcripts` with `limit=5`): The dashboard recent-transcripts panel. Should show tags? **Decision: not for now.** The dashboard is a compact at-a-glance view; tags belong in the full library view. Can be added later.
- **Queue screen**: Tags irrelevant here — this shows jobs, not transcripts.
- **Voice-notes board**: Already shows note_type grouping; tags less relevant.

### Other auto-trigger sites?

Only two: inline path (`app.py` ~1173) and chunked path (`services/queue.py` ~572). Both confirmed. No other transcription-completion paths exist — `POST /api/transcribe` is the only entry point.

### Post-hoc retranscribe?

`POST /api/transcripts/{id}/retranscribe` (line 1551) re-runs transcription on the same audio. After retranscription completes, the same inline/chunked paths fire — so tagging auto-triggers are already covered. No separate call site needed.

### Manual rerun?

The existing `rerun_llm_job` function (line 229) already works for any kind — it calls `enqueue_llm_job` with the same kind. No special handling needed for `tagging`.

### Database migration?

`TranscriptTag` is a new table. Existing transcripts won't have tags. The LLM tagging job can be run manually (via a "Tag this transcript" button on the detail page) for old transcripts. No backfill migration needed — tags are generated on-demand, not required for existing data to function.

---

## What the issue's approach gets right

The issue correctly identifies:
1. Reuse `LlmJob` infrastructure (not invent new async mechanism)
2. Store tags against transcript
3. UI surface to browse/filter by tag on transcript list

## What the issue is missing

1. **No mention of the chunked finalize path** (`services/queue.py`) — only the inline path would get tagging, missing long recordings that go through chunked transcription.
2. **No mention of `_SERIALIZED_JOB_KINDS`** — the detail API would never include the tagging job.
3. **No mention of the `tagging` job kind needing registration** in `VALID_KINDS`, `AUTO_RETRY_KINDS`, `IO_KINDS`.
4. **No mention of `jobActiveSnapshot`** in rack.js — the detail page polling wouldn't track tagging progress.
5. **No specific LLM prompt design** — the voice_note chain shows a working pattern (single LLM call with JSON schema, fallback on error, never-raise guarantee).
6. **No tag filter UI design** — needs to extend the existing `bankQuery` search to also match tags, or add a separate tag filter dropdown.

---

## Implementation plan (summary)

### Backend changes

1. **New DB tables**: `Tag` (id, name) + `TranscriptTag` (transcript_id FK, tag_id FK, unique constraint on pair)
2. **New service**: `services/tagging.py` with `generate_tags(transcript, api_key, ...) → list[str]` — one LLM call, JSON mode, never raises (returns empty list on error)
3. **`services/llm_jobs.py`**: 
   - Add `"tagging"` to `VALID_KINDS`, `AUTO_RETRY_KINDS`, `IO_KINDS`
   - Add `enqueue_auto_tagging(db, transcript, user_settings)`
   - Add `"tagging"` branch in `run_llm_job`
4. **`app.py`**:
   - Import `enqueue_auto_tagging`
   - Add to `_SERIALIZED_JOB_KINDS`
   - Add `tagging_job` to `_dictation_job_fields` (uniform across all kinds)
   - Call `enqueue_auto_tagging` in inline path (after line 1176, unconditionally)
   - Add `tags` field to `_serialize_transcript_summary` and `_serialize_transcript`
   - Add `tagging_job` to detail serialization
5. **`services/queue.py`**:
   - Call `enqueue_auto_tagging` in chunked-finalize path (after line 581, unconditionally)

### Frontend changes

6. **`static/rack.js`**:
   - Add `tagging: "TAG"` to `KIND_LABELS`
   - Add `tagging` to `jobActiveSnapshot`
   - Add `tagging` to `runningContainers` in `updateDetailJobStatus`
   - In `renderBankRows`: extend `bankQuery` filter to also match tags
   - In `bankDetailFields`: add tags display for "done" status
   - In `transcriptMeta`: add tag display when tags exist
   - Add a tag filter UI element (extend search, or add tag-chip filter)

### Tests

7. **Unit test**: `tests/test_tagging.py` — test `generate_tags` with mocked LLM responses
8. **Integration test**: Verify `enqueue_auto_tagging` fires after transcription in both paths
9. **Frontend test**: Verify tag display in list rows
