# Investigation — Issue #171: Cross-transcript tagging

**Target issue:** #171 (standalone feature issue, not a tracking issue).
**Variant:** hy3. **Branch:** `wip/ab-hy3-171`. **Run type:** A/B (no PR, push-only).

## Goal (from issue)
LLM auto-assigns tag(s)/topic label(s) to each transcript after processing; user can
browse and filter the transcript list by tag. Scope left open: free-form vs taxonomy,
one vs many tags per transcript — I picked a reasonable approach (below).

## Design decisions
- **Storage:** a new `TranscriptTag` table (one row per `transcript_id`+`tag`), NOT a
  JSON blob on `Transcript`. Reason: "browse all tags with counts" and "filter by tag"
  are SQL group-by / membership queries; a normalized table is the queryable shape the
  codebase already uses for derived artifacts (see `VoiceNote`, `Summary`). Mirrors the
  existing pattern: `Base.metadata.create_all` in `init_db` auto-creates the table on
  next boot (no manual migration; `ensure_columns` only covers additive columns, which a
  whole new table does not need).
- **Tags:** free-form, multiple allowed (max 8), lowercased + whitespace-collapsed,
  capped at 64 chars, de-duplicated. No fixed taxonomy.
- **Job kind:** new `LlmJob` kind `"tags"`. Reuses the existing queue (VALID_KINDS,
  IO_KINDS, AUTO_RETRY_KINDS, run_llm_job dispatch). IO-bound provider call → goes in
  IO_KINDS + AUTO_RETRY_KINDS (transient network failures auto-retry, like summary).
- **Provider/model for tagging:** `format_provider` / `format_model` from user settings
  (same LLM that powers the dictation reformat + voice-note chain). If no API key is
  saved, the job is pre-failed with a skip message (visible + rerunnable in Queue),
  exactly like `enqueue_auto_correction`.
- **Auto-trigger:** enqueue a `tags` job for EVERY kind after processing completes, at
  the same two hooks where correction/classify/voice-note are enqueued (see Call sites).
- **In-place update on re-run:** the tags handler deletes the transcript's existing
  `TranscriptTag` rows and re-inserts, mirroring `VoiceNote` overwrite semantics.
- **Manual tag add/remove:** small API surface + UI so auto-tags can be corrected.

## Real file / function names and line numbers (current code)
- `database/__init__.py`
  - `Transcript` model, lines 31-71 (relationships at 63-71). Add `tags` relationship.
  - `LlmJob` model, lines 93-112.
  - `VoiceNote` model (template for new table), lines 155-179.
  - `init_db`, lines 339-396 (`Base.metadata.create_all(engine)` at 371 creates new
    table). `__all__` at 399-401.
- `services/llm_jobs.py`
  - `VALID_KINDS` (20-24), `AUTO_RETRY_KINDS` (35), `IO_KINDS` (42). Add `"tags"`.
  - `enqueue_llm_job` (94-111) — dedupes one active job per transcript+kind.
  - `enqueue_auto_correction` (159), `enqueue_auto_classify` (176),
    `enqueue_auto_voice_note` (195) — templates for new `enqueue_auto_tags`.
  - `run_llm_job` dispatch (249-526): `if/elif` chain on `job.kind`. Add `elif job.kind
    == "tags":` branch near the voice_note branch (349-416).
  - `llm_worker_tick` (529-580) — claims pending jobs by IO/CPU kind pools; no change
    needed beyond adding `"tags"` to IO_KINDS.
- `services/transcription.py` — inline transcribe finalize path (status="completed" at
  108). Auto-enqueue for the inline path actually happens in `app.py` POST /api/transcribe
  (see below), NOT here. (Confirmed: transcription.py's `_run_inline_pipeline` returns at
  line 136 with no enqueue; the enqueue is in app.py:1171.)
- `services/queue.py` — chunked finalize: `if new_status in ("completed","partial")`
  block at 562-581. voice_note branch (572-573) enqueues voice-note chain; `else`
  (574-581) enqueues correction+classify. **Add `enqueue_auto_tags` here for ALL kinds.**
- `app.py`
  - `POST /api/transcribe` inline-path auto-enqueue at 1171-1176 (`if kind != "voice_note"
    ... else enqueue_auto_voice_note`). **Add `enqueue_auto_tags` here for ALL kinds**
    (call it unconditionally before/after the kind branch).
  - `list_transcripts` GET /api/transcripts (1248-1250) → `_build_recent_transcripts`
    (547-556). **Add `tag` filter param** to both.
  - `_serialize_transcript_summary` (514-544) — list/dashboard payload. **Add `tags`.**
  - `_serialize_transcript` (301-350) — detail payload. **Add `tags` + `tags_job`.**
  - `_dictation_job_fields` (353-387) — kind-gated, leave alone; `tags_job` goes in the
    base `data` dict (all kinds), like `correction_job`/`summary_job` at 340-342.
  - Voice-note endpoint templates: GET /api/transcripts/{id}/voice-note (2111),
    GET /api/voice-notes (2135), DELETE /api/voice-notes/{id} (2163),
    POST .../voice-note/rerun (2185-2212). Mirror for tags.
  - Imports at line 50: add `enqueue_auto_tags`, `TranscriptTag`.

## Call sites / entry points in scope (Complement Rule)
Every entry point that touches a transcript's tags or the new job kind:
1. `database/__init__.py` — `TranscriptTag` model + `Transcript.tags` rel + `__all__`.
2. `services/llm_jobs.py` — add `"tags"` to VALID_KINDS, AUTO_RETRY_KINDS, IO_KINDS;
   new `enqueue_auto_tags`; new `elif job.kind == "tags"` dispatch branch.
3. `services/tagging.py` (NEW) — `generate_tags(transcript, ...)` LLM call + normalize.
4. `app.py` auto-enqueue HOOK A: `POST /api/transcribe` ~1171 (inline path) — add
   `enqueue_auto_tags`.
5. `app.py` auto-enqueue HOOK B: `services/queue.py` ~562 (chunked finalize) — add
   `enqueue_auto_tags`.
6. `app.py` `GET /api/transcripts` — add `tag` filter param (server-side filter).
7. `app.py` `GET /api/tags` (NEW) — browse list of `{tag, count}`.
8. `app.py` `POST /api/transcripts/{id}/tags` (NEW) — manual add.
9. `app.py` `DELETE /api/transcripts/{id}/tags/{tag}` (NEW) — manual remove.
10. `app.py` `POST /api/transcripts/{id}/tags/rerun` (NEW) — re-run tagging.
11. `app.py` serialization — `tags` in `_serialize_transcript_summary` + `_serialize_transcript`;
    `tags_job` in `_serialize_transcript`.
12. `static/rack.js` — list: fetch `/api/tags`, render filter chips, render per-row tag
    chips, send `?tag=`; detail: Tags panel (chips + add/remove + Retag), wire
    `act === 'retag'`.

## Sibling sweep (step 3)
The issue only names "after processing" + "browse/filter by tag". I swept for siblings:
- **Auto-enqueue: TWO completion hooks, not one.** `services/queue.py:562` (chunked) and
  `app.py:1171` (inline). Missing the inline hook would mean short recordings never get
  auto-tagged — a silent regression. Both in scope (items 4, 5).
- **All three kinds (meeting/dictation/voice_note) must get tags.** voice_note already
  gets a voice-note chain; tags are independent and must still run for it. `enqueue_auto_tags`
  is kind-agnostic (no `if kind !=` guard), unlike the other auto-helpers which are
  kind-gated.
- **Filter param vs search box.** The list already has a client-side title/filename search
  (`bank-search`). The tag filter is a SERVER-side `?tag=` param. They compose: server
  filters by tag, client further narrows by text. Both kept.
- **Serialization in two places.** `_serialize_transcript_summary` (list/dashboard) AND
  `_serialize_transcript` (detail) must both carry `tags`, else the list chips and
  detail chips diverge (mirror-path bug, CLAUDE.md #1). `tags_job` only needed in the
  detail payload (full), not the summary.
- **CSRF:** all new POST/DELETE endpoints go through the existing `api()` helper which
  already attaches `X-CSRF-Token` (voice-note rerun uses it unmodified), so no new
  auth plumbing.
- **Deletion cascade:** `TranscriptTag` FK uses `ondelete="CASCADE"` + relationship
  `cascade="all, delete-orphan"`, matching `VoiceNote`, so deleting a transcript cleans
  up its tags (no orphan rows).

## What the issue's snippet gets wrong / misses
The issue has no code snippet (it's a feature brief), but it leaves scope open. My
additions beyond the literal ask that are REQUIRED for a correct, non-regressive feature:
- Two auto-enqueue hooks (not one) — see sibling sweep.
- A separate `TranscriptTag` table rather than a JSON column, for efficient browse/count.
- Manual add/remove + rerun endpoints/UI (so imperfect auto-tags are correctable).
- `tags_job` in serialization so the detail page can show tagging status + Retag.

## Phase 1.5 completion-race check
NOT required. The confirmed recurring bug class is: a job-completion handler that marks a
job "completed" and THEN fires a side effect, where a later guard checks only "cancelled"
and not "completed". My two new paths are NOT that shape:
- The auto-enqueue (`enqueue_auto_tags`) runs at *transcript finalize*, creating a fresh
  PENDING job — no guard-dependent side effect after a completion.
- The tags job handler (`elif job.kind == "tags"`) only writes tags then calls
  `_finish(db, job, "completed")` — no further enqueue/side effect.
So no oracle consult needed; documented here to justify the skip.

## Acceptance criteria (issue has none explicit; derived from the brief)
- [ ] After a transcript finishes processing, a `tags` LlmJob is auto-enqueued.
- [ ] The tags job calls the LLM, derives tags, and stores them in `TranscriptTag`.
- [ ] `GET /api/transcripts?tag=X` returns only transcripts carrying tag X.
- [ ] `GET /api/tags` returns all distinct tags with counts for the user.
- [ ] The transcript list UI shows each row's tags and a tag filter (chips).
- [ ] The detail UI shows tags with manual add/remove + a Retag (re-run) action.
- [ ] Re-running the tags job replaces the previous tags in place.
- [ ] Deleting a transcript removes its tags (cascade).
