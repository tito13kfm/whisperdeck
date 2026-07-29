# Scheduled Background Tasks: Backup and Archive

> One-line status: Draft plan. Idea inspired by Blinko (github.com/blinkospace/blinko), concept only, no code copied.

## Motivation

WhisperDeck has no scheduled maintenance of any kind today. The SQLite database is the sole source of truth for every transcript, tag, summary, and job, there is no automatic backup, so a bad migration, a disk failure, or an accidental `rm` on the data directory loses everything. Separately, the transcript list and job tables only grow: terminal transcripts and their jobs accumulate forever unless a user manually dismisses or deletes them one at a time. Both problems are exactly the kind of "should happen quietly in the background" housekeeping that a local-first app should provide without asking the user to remember.

## What Blinko does (attribution)

Blinko (github.com/blinkospace/blinko), a self-hosted note-taking app, ships scheduled background tasks for automatic backups (periodic snapshots of its data) and archiving (auto-moving stale notes into an archived state) via its own internal scheduler. We borrow only the idea, "run configurable, in-process, scheduled maintenance jobs", not any of Blinko's code or implementation details, which were not inspected for this plan.

## Proposed approach

Add a third asyncio worker loop, `scheduler_worker_loop`, that lives alongside the existing `queue_worker_loop` (services/queue.py) and `llm_worker_loop` (services/llm_jobs.py), started the same way from app.py's `lifespan`. Unlike those two loops (5s and 3s ticks, respectively, because they dispatch latency-sensitive work), the scheduler tick can be coarse, every few minutes is plenty, since it only needs to notice "an hour/day boundary has passed."

Each tick:
1. Read scheduler config via services/settings.py.
2. For each task (backup, archive/prune), check whether enough time has passed since its last recorded run.
3. If due, run it, recording the attempt as a `ScheduledTaskRun` row (see below) so success/failure is visible and inspectable later, the same way `LlmJob`/`TranscriptionJob` make the two existing background systems observable.

**Backup task.** Use SQLite's `VACUUM INTO 'path'` to write a consistent, compacted snapshot of the live database file to the configured destination, executed off the event loop (`loop.run_in_executor`, the same pattern already used for the blocking `voice_id_service.identify()` call in services/llm_jobs.py). Filenames get a timestamp; a retention count caps how many snapshots are kept, deleting the oldest beyond the cap.

**Archive/prune task.** Two independent, softer-to-harder thresholds against terminal transcripts (`TERMINAL_TRANSCRIPT_STATUSES` in services/queue.py, completed, failed, partial, cancelled):
- Older than `archive_after_days`: flip a new `Transcript.archived` flag. Non-destructive, same spirit as the existing `queue_dismissed` flag (services/queue.py's `dismiss_transcript_queue_entry`), just hides it from default views.
- Older than `prune_after_days` (a separate, longer window, opt-in): hard-delete the transcript row (cascades to its `TranscriptionJob`/`LlmJob`/`RelabelHistory` rows via the existing ORM cascade config) and its audio files on disk.

**Outcome reporting.** Reuse the job-model *shape* (status/error/timestamps), not the `LlmJob` table itself: `LlmJob.kind` is a closed enum of LLM-specific work (`VALID_KINDS` in services/llm_jobs.py) dispatched through `llm_worker_tick`'s IO/CPU pool routing, and carries `provider`/`model`/`transcript_id` columns that don't apply to "backed up the whole DB." A small dedicated table keeps the same observable idiom (status, error, started/finished timestamps) without forcing an awkward fit into the LLM job pipeline or its Queue-screen UI, which is scoped to per-transcript work.

## Code touchpoints (files + symbols, no line numbers)

- **services/scheduler.py** (new), `scheduler_worker_tick`, `scheduler_worker_loop`, `run_backup`, `run_archive_and_prune`. Shape mirrors services/queue.py's `queue_worker_tick`/`queue_worker_loop` pair.
- **services/settings.py**, extend `DEFAULT_SETTINGS` (or add a small parallel `get_app_settings`/`update_app_settings` pair, see Open Questions on scoping) with: `backup_enabled`, `backup_interval_hours`, `backup_retention_count`, `backup_destination`, `archive_enabled`, `archive_after_days`, `prune_enabled`, `prune_after_days`.
- **database/__init__.py**, new `ScheduledTaskRun` model; `ensure_columns` entry adding `Transcript.archived`; wire both into `init_db`'s existing migration calls the same way `llm_jobs`/`transcripts` columns are added today.
- **app.py**, import `scheduler_worker_loop`; start it in `lifespan` via `asyncio.create_task` next to `worker_task`/`llm_worker_task`; cancel it on shutdown the same way.
- **static/**, small settings-panel addition for the new config keys plus a read-only "last backup / last archive: time, status, error" readout. Not a new Queue-screen entry (see above).
- **Transcript list + search endpoints** (app.py's list endpoint and services/search.py's `search_transcripts`/`search_transcripts_snippets`), both need an `include_archived` filter, default off. These are a mirrored pair (list view and search hit the same underlying data from two entry points), so update both together, missing one leaves archived transcripts invisible in the list but still surfaced by search, or vice versa.

## Data model / schema changes

- New table `ScheduledTaskRun`: `id`, `kind` (`backup` | `archive`), `status` (`pending`/`running`/`completed`/`failed`, same vocabulary as `LlmJob.status` for consistency), `detail` (JSON, e.g. `{"path": ..., "bytes": ...}` for backup, `{"archived_count": ..., "pruned_count": ...}` for archive), `error`, `started_at`, `finished_at`.
- `Transcript.archived` (Boolean, default False), soft-hide flag, same non-destructive spirit as `Transcript.queue_dismissed`.
- No changes to `LlmJob`/`TranscriptionJob`/`RelabelHistory` beyond being subject to the existing cascade-delete relationships when a transcript is hard-pruned.

## Research notes

**SQLite backup options**, both viable with this stack (SQLAlchemy over the stdlib `sqlite3` driver, single file at `DATA_DIR/whisperdesk.db`):
1. `VACUUM INTO 'path'`, one SQL statement (SQLite ≥ 3.27), produces a compacted, consistent snapshot from a live database, safe with concurrent readers/writers. Constraints: the target path must not already exist (SQLite errors on overwrite), and it can't run inside an already-open transaction, needs a raw connection (`engine.raw_connection()`, not the ORM session).
2. `sqlite3.Connection.backup(target)`, the SQLite Online Backup API, stdlib since Python 3.7. Copies page-by-page, restarts if a writer commits mid-copy, and exposes `pages=`/`sleep=` knobs to yield control periodically instead of blocking in one shot.

For a database this size, `VACUUM INTO` is simpler (one statement, also compacts) and is the better v1 choice; the Online Backup API is the natural swap-in later if the DB ever grows large enough that a single-shot `VACUUM INTO` noticeably stalls other queries, not a concern at today's scale, so not built now.

**Scheduler shape.** Three options considered:
- **APScheduler**, its `AsyncIOScheduler` integrates into an existing event loop and gives real cron expressions plus misfire handling. Rejected as unnecessary weight: a new dependency with its own persistence/misfire semantics to reason about, for two jobs whose entire scheduling logic is "has enough wall-clock time passed since the last successful run", a plain timestamp comparison.
- **External cron / OS scheduler** (Task Scheduler on Windows, cron/launchd elsewhere), rejected because WhisperDeck runs as a self-contained local app with a portable launcher across platforms; requiring per-OS manual scheduler setup contradicts "local-first, minimal infra."
- **Plain asyncio interval loop** (chosen), WhisperDeck already runs two of these (`queue_worker_loop`, `llm_worker_loop`), started identically from `lifespan`. A third is zero new dependencies and reads like the rest of the codebase; consistency with the existing idiom outweighs any cron-expression flexibility we don't currently need.

Blinko's actual implementation was not inspected, this plan's design (in-process asyncio loop, `VACUUM INTO`, soft-archive plus opt-in hard-prune) is derived independently from WhisperDeck's own codebase and SQLite's documented backup facilities, not ported from Blinko.

## Open questions

- Backup/archive config is inherently installation-level (one SQLite file, one scheduler process), but services/settings.py's only mechanism today (`get_user_settings`/`update_user_settings`) is scoped per-user via `User.settings`. Simplest option for v1: store scheduler config under the admin/fallback user's settings (the primary deployment shape is single-operator, per `get_or_create_fallback_user`); this needs an explicit decision before implementation, not just an assumption, since it affects where the settings-panel UI reads/writes from.
- `backup_destination`, a local filesystem path only for v1 (including a second local drive or network share reachable by path). No cloud target is in scope.
- Should archived transcripts be excluded from full-text search (services/search.py) by default? Recommend yes, with an explicit "include archived" toggle, matching "archived = hidden by default", confirm before building.
- Audio files (`UPLOAD_DIR`, `VOICES_DIR`) are explicitly NOT part of the backup target for v1, only the SQLite DB, which is the source of truth for transcript text, segments, tags, and summaries. Confirm this scope is acceptable, or whether a later phase should also snapshot audio directories.
- Prune is destructive (deletes rows and files); recommend `prune_enabled` defaults to `False` (opt-in) while `archive_enabled` can reasonably default to `True`, given the asymmetric risk of the two actions.

## Rough phasing / checklist

**Phase 1, schema and settings**
- [ ] Add `ScheduledTaskRun` model to database/__init__.py, wire into `init_db`/migration path
- [ ] Add `Transcript.archived` column via `ensure_columns`
- [ ] Extend services/settings.py with the new config keys; resolve the per-user-vs-installation scoping question above

**Phase 2, backup task**
- [ ] services/scheduler.py: `run_backup()` using `VACUUM INTO` via `loop.run_in_executor`
- [ ] Enforce `backup_retention_count` by deleting oldest snapshot files beyond the cap
- [ ] Record each attempt as a `ScheduledTaskRun` row (completed/failed + `detail`)

**Phase 3, archive/prune task**
- [ ] `run_archive_and_prune()`: flip `Transcript.archived` for terminal transcripts older than `archive_after_days`
- [ ] Hard-delete (row + audio files) for transcripts older than `prune_after_days`, gated by `prune_enabled`
- [ ] Record each run as a `ScheduledTaskRun` row

**Phase 4, wiring and surface**
- [ ] Start `scheduler_worker_loop` from app.py's `lifespan` (create_task + cancel on shutdown), mirroring the existing two loops
- [ ] Settings panel: expose the new config keys plus a last-run status/error readout
- [ ] Add `include_archived` filter (default off) to both the transcript list endpoint and services/search.py's search functions together

## Testing considerations

- Unit-test `run_backup`/`run_archive_and_prune` against a temp SQLite file and temp directories, no need to run the full app.
- Vacuous-test guard: a backup test must mutate the source DB *after* taking the snapshot and assert the snapshot is unaffected, proves it copied data rather than taking a no-op path (same lesson as the PR #205 backfill test: construct real state that a stub implementation would fail against).
- Archive test must construct a transcript that is genuinely old and terminal (backdated `updated_at`), and separately confirm a transcript that is terminal-but-recent, or old-but-still-processing, is NOT archived, the boundary condition matters more than the happy path.
- Retention test: seed more backup files than `backup_retention_count` and assert exactly the oldest are removed, newest kept.
- This adds a third long-running task to app.py's `lifespan`; a smoke check that the app still starts and shuts down cleanly (task cancellation on lifespan exit, no orphaned task) is warranted. Full browser e2e is not required per AGENTS.md's testing tiers, this is backend-only apart from a settings-panel addition, which if it introduces new labels/controls needs the usual e2e selector grep-and-update in the same change.
