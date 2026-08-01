# Voice dump: multi-item stream-of-consciousness capture

> One-line status: Draft plan, exploratory. Not scheduled, no code written. Captured for later reference per the user's live-audit-of-WhisperDeck use case. Live-mode material (streaming STT, silence-gap turns, TTS) split out to `docs/plans/13-live-conversational-capture.md`.

## Motivation

The existing voice-note chain (`services/voice_notes.py`, `LlmJob(kind="voice_note")`) assumes one recording produces one note: classify the whole transcript into a single type (todo/idea/reminder/journal/general), then structure it. `VoiceNote` is hard 1:1 with `Transcript` (unique constraint on `transcript_id` plus a `uselist=False, cascade="all, delete-orphan"` relationship, `database/__init__.py`).

That model breaks down for a real workflow: live-auditing WhisperDeck page by page, feature by feature, dictating bugs and feature ideas continuously as they come up. One long recording, many unrelated items. Today that would get mashed into a single classified note, or the user has to stop and restart the recorder per item, which defeats the "just talk, sort it out later" point of voice capture.

This plan adds a second capture mode, "voice dump," that splits one recording into N discrete items, lets the user review/edit/reclassify/discard each one (with optional LLM-generated clarifying questions to prompt the user's own edits), then finalizes them as separate notes.

## Scope locked with the user

- **Post-hoc only, no live mid-recording interruption.** Nothing in the codebase today pauses an `LlmJob` mid-flight for user input — every job kind, including `assistant`, is fire-and-forget single-pass (`assistant.py`'s `interpret_request` explicitly folds ambiguity into a best-guess step rather than asking the user). Building a pause/resume job architecture is out of scope for this pass. The split + structure + clarifying-questions pass runs once, after recording stops.
- **In-app notes only, no auto-created GitHub issues.** Finalized items are just notes, same vocabulary as today (todo/idea/reminder/journal/general).
- **Grounding is a per-project cached brief, not live search.** Originally considered grepping a configured project's files live at clarify time; simplified to a pre-generated, cached brief doc per registered project (refreshed on demand) fed into the clarifying-question prompt. No filesystem-search subsystem, no per-request arbitrary-path reads. Prose-to-prose (a condensed brief vs. a dictated sentence) matches far better than grepping source identifiers against spoken language anyway.

## Architecture decisions and why

**New table, not a relaxed constraint, for multi-item notes.** `Transcript.voice_note = relationship(..., uselist=False, cascade="all, delete-orphan")` and the `UniqueConstraint("transcript_id", ...)` on `VoiceNote` are load-bearing for the existing 1:1 chain's rerun-in-place semantics. Flipping `uselist` and dropping the unique constraint touches a cascade-delete path with no test coverage for the multi-row case, and this repo has no migration tool (no alembic; schema evolution today is `Base.metadata.create_all()` plus hand-rolled "add column if missing" checks, which only handle additive columns — SQLite can't `ALTER ... DROP CONSTRAINT` in place, only a full table rebuild). A brand-new table needs none of that (there's existing precedent for standing up new tables via `create_all()` alone, e.g. the issue #171 table). This keeps the existing `voice_note` path completely untouched.

**Draft lives in `LlmJob.result_json`; only finalized items become rows.** This mirrors the existing split exactly: the detail page's Notes tab already reads from `t.voice_note_job.result_json` (`voiceNoteHtml` in `rack.js` never queries the `VoiceNote` table directly), while the board (`loadVoiceNotes`) reads from the table. So "draft in job result, committed rows only after Finalize" is the same job-result/table split the app already uses for the single-note case, not a new pattern. The new job kind is NOT added to `AUTO_RETRY_KINDS`: a retry would silently overwrite `result_json` and clobber a draft mid-edit.

**Segmentation and structuring are separate LLM calls, not one big call.** The existing chain's `_generate` sets `raise_on_truncation=True` with a "try a shorter recording" failure message. A 30-minute page-by-page audit asked to emit N full structured bodies in a single JSON response risks exactly that truncation. So: one segmentation call returns spans + tentative type only (cheap, small output), then one structure call per segment (reusing the existing per-type prompts), same shape as today's two-call chain just repeated per item.

**Clarifying questions are folded into the existing structure call, not a separate round-trip.** Extend the structure prompt's requested JSON keys with an optional `"clarifying_questions": [...]` (empty when the LLM isn't unsure of anything) — no new LLM call, no new job state, no pause/resume machinery. V1 grounding is just the other items in the same dump ("is this related to the earlier note about X?"); the deferred per-project-brief phase (below) adds extra prompt context later.

**User edits between job-complete and Finalize are persisted, not client-only.** A page reload before Finalize would otherwise lose an entire audit session's worth of edits. A lightweight "Save draft" action PATCHes the edited item list back into the same `LlmJob.result_json` blob — no new table, reuses the job row that already exists.

## Model selection for the split/structure/clarify chain

**STT and the voice-dump LLM are already separate, independently-configured slots — this isn't new architecture.** `backends/*.py` (`moonshine.py`, `groq.py`, `openai.py`, `assemblyai.py`, `replicate.py`, `local.py`, `builtin.py`) all implement `BaseProvider` (`backends/base.py:34`), whose contract is just `async def transcribe(audio_path, **kwargs)` (`base.py:46`) — this is the STT-only provider set (`backends/groq.py` is Groq's *Whisper* wrapper, not its chat API; same provider name string, different code path). LLM text generation goes through `services/llm_client.py:chat_completion()` (`llm_client.py:69`), resolved via `resolve_model(provider_name, model, feature_name)` (`llm_client.py:44`) — the same hook `correction_provider`/`correction_model` already plug into in `services/settings.py`'s `DEFAULT_SETTINGS`. A `voice_dump_provider`/`voice_dump_model` setting is one more entry in that existing pattern, not a new abstraction. This means the STT provider (Moonshine, local, default) and the model running the split/structure/clarify chain can already be two completely different providers with zero new plumbing.

**Lemonade is a live option for the LLM half.** `docs/LEMONADE.md` documents WhisperDeck's existing integration with a local Lemonade server (`http://localhost:13305`, OpenAI-compatible, `Local / Custom` provider). Lemonade serves distinct model IDs simultaneously — a chat model, a Whisper model, a TTS model — all independently callable under one running server; the `LMX-Omni-5.5B-Lite` "Omni" bundle is an *alternative* single-checkpoint option, not the mechanism that enables multi-model use (Lemonade already runs several separate small models concurrently without it).

**Gotcha that will actually bite: JSON mode + reasoning-model think-traces.** `services/llm_client.py:15` — `JSON_MODE_PROVIDERS = ("groq", "openai", "openrouter")` — `"local"` (Lemonade) is not in that tuple, so JSON mode is never requested from it. The voice-dump chain's segmentation and per-item structure calls both need strict JSON output (same as the existing `voice_notes.py` chain's `json_mode=True` calls). Point a reasoning model (`Qwen3-*`, `DeepSeek-R1-*`, `gpt-oss-20b`) at those calls through Lemonade and it emits a thinking trace before any JSON, which `local` doesn't strip — the parser sees `"Okay, let's start by..."` and the call fails, same failure mode `docs/LEMONADE.md` already documents for correction/summary jobs today, just at new call sites. Mitigation: point the voice-dump provider at a non-reasoning Lemonade model (`Bonsai-8B-gguf`, per the doc) or a hosted JSON-mode provider for that slot — can reuse the user's existing `correction_provider` value, or a distinct `voice_dump_provider` if a smaller/faster model is wanted for the higher call volume (segmentation + N structure calls per dump, vs. 2 calls for the existing single-note chain).

**Resource-contention gotcha (verified, not assumed).** `services/queue.py:723` creates `local_provider_lock = asyncio.Semaphore(1)` once per worker tick, threaded only into the transcription chunk path (`_process_transcript_jobs`/`_run_chunk_job`, `queue.py:411/589`) — it serializes Moonshine STT calls against each other, per `backends/moonshine.py:10,23`. It does **not** extend to `services/llm_jobs.py`, which has its own independent concurrency caps (`_MAX_CONCURRENT_IO_JOBS = 2`, `_MAX_CONCURRENT_CPU_JOBS = 1`, `llm_jobs.py:44-45`) and no shared lock with the transcription side. Concretely: a Moonshine transcription (in-process, ~980MB resident) and a Lemonade LLM call (separate process, multi-GB depending on model) can run fully concurrently and unserialized on the same box, both contending for CPU. Not a correctness bug, but a real performance consideration on a single machine worth naming rather than assuming the existing lock covers it — it was never designed to.

## Proposed approach

### Phase 1: Split → structure → review → finalize (core feature)

**Schema**
- Add `"voice_dump"` as a 4th `Transcript.kind` value (alongside `meeting`/`dictation`/`voice_note`). Plain string value on an existing column, no migration, but every kind-switch site needs a branch: `services/transcription.py` (diarization defaults), `services/voice_notes.py`/`services/llm_jobs.py` (chain dispatch), `services/queue.py` (auto-enqueue on finalize), `app.py` (serialization, bulk-import kind validation), `services/settings.py` (`bulk_defaults.kind`). Use the lightest-diarization / voice_note-like defaults (personal monologue, not a meeting).
- New table `VoiceDumpItem`: `id, user_id (FK), transcript_id (FK transcripts.id, ondelete=CASCADE), source_job_id (FK llm_jobs.id, nullable), sequence_index (int), note_type, title, body, structured (JSON), model, provider, created_at`. No unique constraint on `transcript_id` — many rows per transcript by design.

**Backend — new job kind**
- `VALID_KINDS += "voice_dump"`, add to `IO_KINDS` (network-bound LLM calls, like `assistant`/`voice_note`), NOT `AUTO_RETRY_KINDS`.
- Refactor `structure_voice_note` (`services/voice_notes.py`) to hoist the `_transcript_text(transcript)` call: expose a text-taking inner function (e.g. `_structure_from_text(text, note_type, ...)`) that `structure_voice_note` becomes a thin wrapper around. `_structure_prompt` is already text-parameterized so this is a small, safe extraction — the per-item voice-dump path calls `_structure_from_text` directly with each segment's span text, no transcript object needed per item.
- New `segment_voice_dump(transcript, ...)`: one call, prompt asks the LLM to split the raw transcript into an ordered list of `{span_text, tentative_type}` — spans/labels only, deliberately not full bodies (truncation risk, see above).
- New `run_voice_dump_job` dispatch (mirrors `run_assistant_job` in `services/llm_jobs.py`): segment → loop `_structure_from_text` per span (each call also asks for `clarifying_questions`) → assemble `result_json = {"items": [{index, type, title, body, structured, clarifying_questions}, ...]}`. `progress_total` = segment count + 1, incremented per completed call.
- New endpoints in `app.py`, mirroring the existing voice-note routes:
  - Auto-enqueue on transcript finalize when `kind == "voice_dump"` (mirror the existing `enqueue_auto_voice_note` gate in `services/queue.py`).
  - `POST /api/transcripts/{id}/voice-dump/rerun` — mirrors `rerun_voice_note_chain`; re-enqueues the split job, producing a fresh draft. Does not touch already-finalized `VoiceDumpItem` rows from a prior finalize.
  - `POST /api/transcripts/{id}/voice-dump/save-draft` — overwrites `job.result_json` with the client's edited item list (reload-safety net).
  - `POST /api/transcripts/{id}/voice-dump/finalize` — body is the (possibly edited) item list, filtered of any client-side "discard" flags; inserts N `VoiceDumpItem` rows; does not delete/clear the job or its `result_json` (keep the audit trail).
  - `GET /api/transcripts/{id}/voice-dump-items` — all finalized items for one source transcript.
  - `GET /api/voice-dump-items` — board listing across all transcripts.
  - Expose `voice_dump_job` on the serialized transcript payload alongside the existing kind-gated `voice_note_job` field.

**Frontend**
- Extend the record-kind picker (wherever `meeting`/`dictation`/`voice_note` are chosen at record start) with `voice_dump` ("Audit / stream-of-consciousness dump"). Reuses `startLiveCapture()` unchanged.
- New "Dump Review" detail-tab branch, gated like the existing `notes` tab gate but for `t.kind === 'voice_dump'`, rendering from `t.voice_dump_job.result_json.items`.
- This is the app's first-ever inline edit UI (no edit/save/contenteditable exists anywhere today): per item, an editable title input, editable body textarea, a type dropdown (reclassify), a discard checkbox, and — when present — the item's `clarifying_questions` rendered as plain prompts with a text input each (the answer is appended to that item's body by the user; no second LLM refinement call in v1).
- "Save draft" button → `save-draft` endpoint. "Finalize" button → `finalize` endpoint, then navigate to the new dump-items list.
- New board section (separate from `loadVoiceNotes()`/`GET /api/voice-notes`, not merged into it — avoids touching the existing, tested serialization contract): a `loadVoiceDumpItems()` reusing the existing `NOTE_TYPE_LABELS`/`NOTE_TYPE_COLORS` since the type vocabulary is identical.

### Phase 2 (deferred): per-project grounding brief

- Add `projects: [{name, path, brief}]` to `User.settings` (JSON list — mirrors the existing `bulk_defaults` nested-dict precedent, no new table).
- `brief` is a cached markdown blob, generated once (manually pasted, or via a small one-off LLM job that reads a project's `docs/**/*.md` + `ROADMAP.md` + `CLAUDE.md`/`AGENTS.md` and condenses them) and refreshed on demand — not live-searched per clarifying question.
- A per-dump project selector (record time or review time) picks which brief, if any, gets folded into the clarifying-question prompt for that dump's items.
- Deferred: highest-uncertainty piece of the whole feature, not needed for the core loop to be useful on day one.

### Phase 3 (deferred idea, captured not designed): auto-linking notes into a memory map

The eventual want is for notes (including finalized `VoiceDumpItem`s) to automatically link to each other and to broader project scope, and eventually across projects — a "memory map." Not designed here, just recorded:

- Substantially overlaps with the already-designed-but-not-built entity-extraction / topic-grouping / entity-pages work under issue #241 (`docs/plans/07-entity-extraction-core.md`, `09-topic-grouping.md`, `08-entity-pages-ui.md`, `10-graph-decay-retrieval.md`, `11-staleness-curation.md` — polymorphic `Entity` table, topic entities with Jaccard-based merge, graph-decay one-hop retrieval). Voice-dump items would be a new *source* feeding that same entity graph, not a reason to build a second linking system.
- Cross-project relation ("how projects relate to each other") is a further reach than #241's current single-project entity graph and may be a natural fit for a 3rd-party integration (e.g. a cross-session "brain"/corpus tool) rather than an in-house build.
- No action beyond recording the idea and its link to #241's existing design.

### Explicitly out of scope (documented, not built)

- Live mid-recording clarification/interruption, streaming transcription, silence-gap turn-taking, and spoken (TTS) responses — the whole live-conversational cluster is designed separately in `docs/plans/13-live-conversational-capture.md`, since none of it has a consumer until live mode exists (this doc's Phase 1 is post-hoc, text-only).
- Auto-created GitHub issues from detected bugs/feature-ideas.
- Any live filesystem search over a project's source/docs.

## Code touchpoints (files + symbols, no line numbers)

- `database/__init__.py`: new `Transcript.kind` value `voice_dump`; new `VoiceDumpItem` model.
- `services/voice_notes.py`: `_structure_from_text` extraction, new `segment_voice_dump`.
- `services/llm_jobs.py`: `VALID_KINDS`, `IO_KINDS`, `run_voice_dump_job` dispatch.
- `services/queue.py`: auto-enqueue gate for `kind == "voice_dump"`.
- `services/settings.py`: `bulk_defaults.kind` allowed values; new `voice_dump_provider`/`voice_dump_model` settings (mirrors `correction_provider`/`correction_model`); later, `projects` list for Phase 2.
- `services/llm_client.py`: `resolve_model()`, `JSON_MODE_PROVIDERS` — the voice-dump provider must resolve to a non-reasoning model when pointed at `local`/Lemonade, see the JSON-mode gotcha above.
- `services/transcription.py`: diarization-default branch for the new kind.
- `app.py`: new voice-dump endpoints (rerun/save-draft/finalize/list), `voice_dump_job` serialization field.
- `static/rack.js`: record-kind picker, new "Dump Review" tab + inline edit UI, new board section, reuse of `NOTE_TYPE_LABELS`/`NOTE_TYPE_COLORS`.

## Verification (when this is built)

- Unit: segmentation-call parsing, per-item structure+clarify parsing, truncation fallback; confirm `_structure_from_text` extraction doesn't change `structure_voice_note`'s existing behavior (existing `test_voice_note_chain.py`/`test_llm_jobs.py` must still pass unchanged).
- New tests mirroring `tests/test_voice_note_route.py`: rerun before/after finalize, save-draft round-trip, finalize with a discarded item, `voice_dump_job` field present/absent per kind (mirror `test_serialize_transcript_contract.py`'s kind-gated field pattern).
- `VALID_KINDS`/`IO_KINDS`/`CPU_KINDS` partition test must still pass with `voice_dump` added.
- Runtime: real browser pass — record a multi-topic dump, confirm segmentation produces separate items, edit a title/body, discard one item, save-draft + reload confirms persistence, finalize confirms N separate notes appear on the new board section, confirm the existing `voice_note` record flow and board are completely unaffected.
