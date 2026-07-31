# WhisperDeck Roadmap

**Keep this current.** Update this file whenever a plan lands (move it to Done)
or a new idea gets parked (add it to Parked). If a plan gets merged and this
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

## In Progress

_(nothing right now)_

## Parked (not designed yet)

- **Windows ML / native app pivot** — DirectML could unlock real AMD GPU use; needs ONNX conversion or a native-app rewrite, not a quick win.
- **Full-text search across transcripts** — cross-transcript content search endpoint + UI. Currently only title/filename list-filter and single-transcript in-page match exist.
- **Admin user-management UI** — `GET /api/admin/users`, `POST /api/admin/promote`, `/api/admin/demote` are fully implemented and documented, but there's no UI anywhere in `rack.js` for them. An admin today can only list/promote/demote via raw API calls.
- **Voice dump: multi-item stream-of-consciousness capture** — split one long dictation (e.g. a page-by-page app audit) into separate bugs/ideas/todos with a review/finalize step, instead of today's one-recording-one-note voice-note chain. Draft plan exists (`docs/plans/12-voice-dump-multi-item-capture.md`), not scheduled.

## Known accepted gaps

- Cancel/resume: a few-Python-instructions race window between the diarization-await re-check and the final commit in `_finalize_if_done` is inert under the current single-process deployment (no `await` point in the gap). Needs a guarded `UPDATE ... WHERE status != 'cancelled'` if the app ever goes multi-worker.
- Live-stereo diarization (issue #67): the pyannote inference path is verified only against monkeypatched unit tests — the dev machine has no pyannote/torch. Needs one real run on the pyannote-equipped machine (live capture with system audio playing a distinct voice, expect two labels "You"/`SPEAKER_00` and `diarization_method == "live_stereo"`). Related: mic speech bleed-dropped during loud system audio overlaps no diarization turn and keeps `speaker: None` at confidence 0.0; assess on real hardware.
- Speaker-confidence UI (issue #67): "?" markers, "N uncertain" count, and the "Undo relabel" button are unit-correct but never driven in a real browser. Heuristic-diarized transcripts always score confidence 1.0 by construction, so the markers can only fire on a pyannote/live_stereo machine.
- `LlmJob` rows are orphaned on transcript delete: same inert-FK-CASCADE pattern RelabelHistory had before PR #72 (SQLite `foreign_keys` pragma is off, no ORM cascade relationship). Harmless today (jobs are queried per existing transcript) but the same rowid-reuse hazard applies in principle; fix is one `relationship(cascade="all, delete-orphan")` line plus a test.
- Detail page for an in-progress transcript doesn't live-poll (only the dashboard recents badge does). Spec called for both; only the dashboard got built. Not blocking, not fixed.
- Theme/phosphor/motion faceplate preferences (`rack.js`) are `localStorage`-only, not synced through the existing per-user `/api/settings`, so they don't follow a user across browsers/devices.