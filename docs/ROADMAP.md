# WhisperDeck Roadmap

**Keep this current.** Update this file whenever a plan lands (move it to Done),
a plan finishes its design pass (add it to "Designed, not scheduled"), or a new
idea gets parked (add it to Parked). If a plan gets merged and this
file isn't touched in the same session, that's a bug — fix it before moving on.

## Done

- Per-user auth (`docs/superpowers/plans/2026-06-30-per-user-auth.md`)
- Audio chunking + queue (`docs/superpowers/plans/2026-07-01-audio-chunking-and-queue.md`)
- Transcription UX: cancel/resume, queue status, HF token consolidation, unified model selection (`docs/superpowers/plans/2026-07-01-transcription-ux-improvements.md`)
- Diarization torchcodec bypass (pyannote reads via soundfile, not torchaudio)
- Moonshine as default local provider (`docs/superpowers/plans/2026-07-02-moonshine-local-provider.md`)
- Hotword glossary + LLM correction pass (`docs/superpowers/plans/2026-07-02-hotword-glossary-and-correction-pass.md`)
- Hotword correction UI (`docs/superpowers/plans/2026-07-03-hotword-correction-ui.md`)
- Voice clip roster / voice identification (`docs/superpowers/plans/2026-07-04-voice-clip-roster.md`)
- Portable build / release packaging (`docs/superpowers/plans/2026-07-04-portable-build.md`)
- E2E browser-driven UX audit (`docs/superpowers/plans/2026-07-04-e2e-ux-audit.md`)
- E2E test app / regression testing (`docs/superpowers/plans/2026-07-04-e2e-test-app.md`)
- E2E browser-driven followup handoff (`docs/superpowers/plans/2026-07-04-e2e-browser-driven-followup-handoff.md`)
- Audit fixes (`docs/superpowers/plans/2026-07-05-audit-fixes.md`)
- Queue clear / dismiss (`docs/superpowers/plans/2026-07-05-queue-clear.md`)
- Run history: export metadata (`docs/superpowers/plans/2026-07-06-run-history-phase1-export-metadata.md`)
- Run history: correction diff (`docs/superpowers/plans/2026-07-06-run-history-phase2-correction-diff.md`)
- Run history: transcription versions (`docs/superpowers/plans/2026-07-06-run-history-phase3-transcription-versions.md`)
- Run history: summary / re-diarize diff (`docs/superpowers/plans/2026-07-06-run-history-phase4-summary-rediarize-diff.md`)
- Queue audit: cross-transcript parallelism (`docs/superpowers/plans/2026-07-07-queue-audit-cross-transcript-parallelism.md`)
- Queue audit: enqueue dedupe constraint (`docs/superpowers/plans/2026-07-07-queue-audit-enqueue-dedupe-constraint.md`)
- Queue audit: LLM job auto-retry (`docs/superpowers/plans/2026-07-07-queue-audit-llmjob-auto-retry.md`)
- Queue audit: split concurrent job pools (`docs/superpowers/plans/2026-07-07-queue-audit-split-concurrent-job-pools.md`)
- pyannote.audio voice-ID embedding backend (`docs/superpowers/plans/2026-07-21-pyannote-voice-id-backend.md`)
- Diarization misidentification fixes, issue #67: metadata persistence, channel-aware live-stereo diarization, undo for bulk speaker relabels (closes #55), per-line speaker-confidence signal, post-review hardening (`docs/superpowers/plans/2026-07-22-issue-67-diarization.md`) — merged via PR #72. Phase 5 (contingent repro runbook) only runs if over-splitting persists in production.
- Voice dump: multi-item stream-of-consciousness capture (`docs/plans/12-voice-dump-multi-item-capture.md`) — closed as #261; one long dictation splits into separate bugs/ideas/todos through a review/finalize step

## In Progress

- **Post-meeting follow-up session** (#253) — after a meeting summary, an LLM pass asks clarifying questions about ambiguous action items and decisions, the owner answers inline on the Summary tab, and a second pass rewrites each item into a self-contained, typed row in a new `followup_items` table. Design locked in `docs/plans/14-followup-session.md`; shipping as two PRs (backend, then UI).
- **Agent-runner tooling fixes** — `docs/retrospectives/2026-08-04-agent-runner-retrospective.md` reviewed every `/issue`, `/issue-claude` and `/audit-pr` run archived since 2026-07-28 and ranked 26 changes. Tier 1 (8 changes) merged in #338. Tier 2 (12) and Tier 3 (6 open questions, including the browser-verification gap and the non-green e2e baseline) are still open. Section 7 of that doc is the work list.

## Designed, not scheduled

Plans that have been through a full design pass and are written up, but have no
code yet. These are ready to pick up; they are not ideas.

- **Meeting knowledge layer** (#241, master tracker) — after a transcript finishes, extract structured entities (people, projects, decisions, action items, topics) so they become browsable, cross-linked pages: "Project X: every meeting that touched it, decisions made, open action items". Five parts, designed as a sequence, each with its own plan doc and issue:
  1. **Entity extraction core** (#245, `docs/plans/07-entity-extraction-core.md`) — `Entity`/`EntityMention` schema, the background extraction job, and deterministic (non-LLM) merge with voice-roster reconciliation. The load-bearing rule for the whole layer: the LLM only ever *proposes* extractions, Python decides what merges with what. Backend only. Everything below depends on it.
  2. **Entity pages UI** (#247, `docs/plans/08-entity-pages-ui.md`) — the browsable payoff: per-entity pages, a new top-level nav item, and one additive column (`Entity.status`, open/done for action items). Blocked on part 1. (#249 is a follow-on: reverse co-occurrence on the entity detail page.)
  3. **Topic grouping** (#248, `docs/plans/09-topic-grouping.md`) — topics as a fifth entity type, plus retirement of today's `services/tagging.py`. Mostly amendments to parts 1 and 2, already folded into their docs. Blocked on parts 1 and 2.
  4. **Graph-decay retrieval** (#250, `docs/plans/10-graph-decay-retrieval.md`) — one-hop, cross-transcript expansion on `GET /api/search`: a hit on transcript A surfaces transcript B, which shares an entity but never matched the query, at a decayed score. Opt-in query parameter, no UI. Depends on part 1 only, not on 2 or 3.
  5. **Staleness curation** (#251, `docs/plans/11-staleness-curation.md`) — a periodic scan that flags stale entities into a review queue the user disposes of by hand; nothing is ever auto-deleted. Blocked on parts 1 and 2, soft-depends on 3, and needs the scheduler from `docs/plans/04-scheduled-tasks.md` (itself designed, not built) or the fallback that plan names.

  Related but separate: **#242 "Ask your meetings"** (cross-transcript search and grounded Q&A) is the retrieval-facing sibling of this layer and is not one of the five parts.

- **Semantic RAG search** (`docs/plans/01-semantic-rag-search.md`), **global quick-capture hotkey** (`docs/plans/02-global-quick-capture-hotkey.md`), **daily review and recall** (`docs/plans/03-daily-review-recall.md`), **scheduled tasks** (`docs/plans/04-scheduled-tasks.md`), **webhooks** (`docs/plans/05-webhooks.md`), **user identity context** (`docs/plans/06-user-identity-context.md`) — six standalone plan docs from the same design pass. Spot-checked as unbuilt on 2026-09-09: there is no `services/scheduler.py`, no webhook module, and search is still FTS5-only. `04-scheduled-tasks.md` is the one with a dependent (part 5 above).

- **Live conversational capture** (`docs/plans/13-live-conversational-capture.md`) — streaming transcription, silence-gap-triggered turns, and spoken (TTS) responses layered on the voice-dump flow. Capability mapping only, less settled than the plans above.

## Parked (not designed yet)

- **Windows ML / native app pivot** — DirectML could unlock real AMD GPU use; needs ONNX conversion or a native-app rewrite, not a quick win.
- **Full-text search across transcripts** — cross-transcript content search endpoint + UI. Currently only title/filename list-filter and single-transcript in-page match exist.
- **Admin user-management UI** — `GET /api/admin/users`, `POST /api/admin/promote`, `/api/admin/demote` are fully implemented and documented, but there's no UI anywhere in `rack.js` for them. An admin today can only list/promote/demote via raw API calls.
- **Studio framing** — reframe the app around record/import as the front door with an auto-routed pipeline behind it, replacing today's upfront Mode picker. Exploratory writeup checked against current code exists (`docs/planning/studio-framing.md`), not designed, tracked in issue #264.

## Known accepted gaps

- Cancel/resume: a few-Python-instructions race window between the diarization-await re-check and the final commit in `_finalize_if_done` is inert under the current single-process deployment (no `await` point in the gap). Needs a guarded `UPDATE ... WHERE status != 'cancelled'` if the app ever goes multi-worker.
- Live-stereo diarization (issue #67): the pyannote inference path is verified only against monkeypatched unit tests — the dev machine has no pyannote/torch. Needs one real run on the pyannote-equipped machine (live capture with system audio playing a distinct voice, expect two labels "You"/`SPEAKER_00` and `diarization_method == "live_stereo"`). Related: mic speech bleed-dropped during loud system audio overlaps no diarization turn and keeps `speaker: None` at confidence 0.0; assess on real hardware.
- Speaker-confidence UI (issue #67): "?" markers, "N uncertain" count, and the "Undo relabel" button are unit-correct but never driven in a real browser. Heuristic-diarized transcripts always score confidence 1.0 by construction, so the markers can only fire on a pyannote/live_stereo machine.
- `LlmJob` rows are orphaned on transcript delete: same inert-FK-CASCADE pattern RelabelHistory had before PR #72 (SQLite `foreign_keys` pragma is off, no ORM cascade relationship). Harmless today (jobs are queried per existing transcript) but the same rowid-reuse hazard applies in principle; fix is one `relationship(cascade="all, delete-orphan")` line plus a test.
- Detail page for an in-progress transcript doesn't live-poll (only the dashboard recents badge does). Spec called for both; only the dashboard got built. Not blocking, not fixed.
- Theme/phosphor/motion faceplate preferences (`rack.js`) are `localStorage`-only, not synced through the existing per-user `/api/settings`, so they don't follow a user across browsers/devices.