# Scheduled Background Tasks: Brainstorm (backup + archive/prune)

> Layer: pre-plan brainstorm. Read alongside docs/plans/04-scheduled-tasks.md, the draft plan this
> document interrogates. Nothing here is decided. Each major decision below lists alternatives with
> tradeoffs and a recommendation, but the draft plan's choices are treated as one candidate, not as
> settled fact.

## User-intent framing

Two distinct users are being served, and they want different things:

- **The person who never thinks about their data until it's gone.** They want backup to be silent,
  automatic, and to fail loudly if it stops working. They will not open a settings panel to check
  on it. This means "silent backup failure" is the single scariest failure mode in this whole
  feature: a backup task that has been failing for three weeks and nobody noticed is strictly worse
  than no backup feature at all, because it creates false confidence.
- **The person whose transcript list has grown to thousands of rows.** They want the list, search,
  and Queue screen to stay fast and uncluttered, without losing data they might want later. This
  person wants archive (hide), not prune (destroy), as the default behavior, and wants prune to be
  something they turn on deliberately once they trust the tool.

These two intents pull in different directions on defaults: backup should default ON (protect
against the silent-catastrophe case), archive should probably default ON too (low risk, reversible),
prune should default OFF (irreversible, and WhisperDeck currently has no working backup to fall back
on if a bad prune threshold deletes something wanted).

## Scheduler mechanism

The draft plan proposes a third asyncio interval loop, `scheduler_worker_loop`, mirroring
`queue_worker_loop` (services/queue.py, 5s tick) and `llm_worker_loop` (services/llm_jobs.py, 3s
tick), both started via `asyncio.create_task` in app.py's `lifespan` and cancelled on shutdown.

| Option | How it works | Pros | Cons |
|---|---|---|---|
| **Bare asyncio interval loop** (draft's choice) | Coarse tick (minutes), compares wall-clock time since last recorded run per task | Zero new dependencies, matches the two existing loops exactly, trivial to read and test | No persistence of "next due" independent of app process; if the app is closed for days, the next tick after restart just sees "very overdue" and runs immediately, which is arguably fine for backup/archive but is a design choice, not a given; no real cron expressions (fixed intervals only) |
| **APScheduler (`AsyncIOScheduler`)** | Library schedules jobs against the running event loop, supports cron expressions and misfire policies | Real "run at 2am daily" semantics, misfire grace windows, more expressive scheduling if that's ever wanted | New dependency with its own persistence/misfire model to learn and reason about, for two jobs whose actual logic is "has N hours passed since last success"; overkill for the stated need |
| **Extend the existing queue or LLM worker loop** (piggyback on `queue_worker_tick` or `llm_worker_tick`) | Add a scheduler check inside an existing tick instead of a new loop | No new task to start/cancel in `lifespan` | Conflates unrelated concerns in one function; couples backup/archive tick cadence to whatever cadence transcription or LLM dispatch needs (5s/3s is far too frequent for a check that only needs to fire hourly/daily), and a slow backup (`VACUUM INTO` on a large DB) run inline would stall chunk dispatch or LLM claiming for its duration unless carefully offloaded, the same offloading work as a separate loop, with none of the isolation benefit |
| **External OS scheduler** (Windows Task Scheduler, cron/launchd) | App exposes a CLI/HTTP trigger; the OS invokes it on schedule | True "runs even if the app UI was never opened this session," well-understood by ops-minded users | Requires per-platform setup outside the app, contradicts "local-first, minimal infra, works the same everywhere" that WhisperDeck currently promises; also does nothing for a user who just double-clicks a launcher and never touches Task Scheduler |

**Missed-run handling deserves explicit thought regardless of which mechanism is picked**: if
WhisperDeck is only running a few hours a day (a plausible usage pattern for a local desktop-style
app, as opposed to an always-on server), an in-process loop only ever "sees" time that passes while
the process is up. A user who opens the app once a week will get a backup once a week, not daily,
no matter what `backup_interval_hours` says, unless the check is "time since last run" (which the
draft plan does specify) rather than "wall-clock schedule" (which would silently skip runs while
the app is closed with no makeup mechanism). Confirm the draft's "time since last successful run"
semantics is really what gets built, since it's easy to drift into a fixed-clock-time design once
someone asks for "back up at 2am."

**Timezone/DST**: a pure "hours since last run" model (as opposed to "at this local wall-clock
time") sidesteps DST and timezone questions entirely, since it never reasons about local time of
day, only elapsed duration. That's a point in favor of keeping the scheduling logic that simple,
worth stating explicitly as a constraint on the "which mechanism" choice above rather than leaving
it implicit.

**Recommendation**: the draft's plain asyncio loop, for the reasons the draft already gives (matches
existing idiom, zero new dependency, the actual scheduling logic really is this simple). The one
addition worth deciding explicitly: confirm "elapsed since last successful run," not "next wall-clock
occurrence," is the model, since that decision is currently implicit in the draft's phrasing rather
than stated as a constraint.

## Config scope: per-user settings vs installation-level

This is flagged in the draft's Open Questions as the biggest unresolved question, and it deserves
more scrutiny than a one-line footnote, because WhisperDeck is not actually single-user software:
`database/__init__.py`'s `User` model is a real multi-account table (`is_admin` flag, per-user
`settings` JSON blob via `services/settings.py`'s `get_user_settings`/`update_user_settings`), and
`get_or_create_fallback_user` (services/auth.py) exists only to give pre-auth-era rows an owner
during migration, not as evidence that "one operator" is the only deployment shape. A real
installation could have several distinct human users, each with their own login. Backup and
archive/prune, meanwhile, operate on the one shared SQLite file and are inherently
installation-scoped: there is exactly one database to back up and one set of stale-transcript
thresholds to apply, no matter how many `User` rows exist.

| Option | Sketch | Pros | Cons |
|---|---|---|---|
| **Store under the fallback/admin user's `User.settings`** (draft's default suggestion) | Reuse `get_user_settings`/`update_user_settings` unchanged, scoped to `get_or_create_fallback_user`'s row or the first `is_admin` user | Zero schema change, zero new settings-panel plumbing, reuses `json_patch` merge logic as-is | Semantically wrong: a genuinely per-user table pretending to hold an installation-wide value; in a real multi-user install, which user's settings panel shows/edits this? An answer has to be picked (first admin? every admin sees the same shared row?) and it's easy to end up with N admins each silently overwriting each other's schedule/threshold values through `json_patch`, with no indication whose write won |
| **New install-level settings store** (single-row table, or a `key`/`value` table) | e.g. `InstallSettings` table with one row, or a generic key-value table, read/written independent of any `User` | Semantically correct: one physical thing (the DB file), one config store; UI can gate editing to admins without pretending it's "my settings" | New table + migration + a second settings module (or an extended one) to keep straight from the per-user one; slightly more code for a feature that is otherwise deliberately small |
| **Env var / config file** (e.g. `WHISPERDECK_BACKUP_INTERVAL_HOURS`, or a `config.toml` next to the DB) | Read once at startup, no DB round-trip, no settings-panel UI needed at all for v1 | Simplest possible implementation; sidesteps the per-user-vs-install question entirely by not living in the DB | No live-editable settings panel (a restart is needed to pick up a change, unless the scheduler loop re-reads the env/file every tick, which is easy enough); doesn't fit the "small settings-panel addition" the draft plan already promises, so this option effectively contradicts the draft's UI plan unless that UI plan is dropped too |

**Recommendation leaning**: a small dedicated install-level store (even a one-row table, or reusing
the pattern of a single `key TEXT PRIMARY KEY, value JSON` table) rather than overloading
`User.settings`. Gate editing in the settings-panel UI to `is_admin` users only (that column already
exists and is already used at bootstrap to promote the first user, per `database/__init__.py`'s
`admin_count` check), so "who can see/change this" has an existing, real answer instead of an
implicit one. This is explicitly not resolved here, listed as the top item in Decisions Needed
below, because it changes the schema-and-settings phase of implementation (Phase 1 of the draft
plan) either way and should be picked before that phase starts, not discovered mid-implementation.

## Backup method

The draft's research notes already compare `VACUUM INTO` against the SQLite Online Backup API and
recommend `VACUUM INTO` for v1. Restating and sharpening that comparison, plus the parts the draft
doesn't fully cover (destination path, retention, disk-fill):

| Option | Correctness under WAL / concurrent writers | Notes |
|---|---|---|
| **`VACUUM INTO 'path'`** | Produces one consistent, compacted snapshot as of the moment it runs, safe alongside concurrent readers/writers, including under WAL mode | Simplest, one SQL statement, also compacts (shrinks) the copy; must run on a raw connection (`engine.raw_connection()`), not the ORM session, and not inside an open transaction; target path must not already exist yet (SQLite errors on overwrite, so filenames need a fresh timestamp per run, which the draft already plans) |
| **`sqlite3.Connection.backup(target)`** (Online Backup API, stdlib) | Copies page-by-page, restarts if a writer commits mid-copy, `pages=`/`sleep=` knobs to yield periodically | More moving parts (progress callback, restart-on-conflict logic) for no benefit at WhisperDeck's current DB size; the natural swap-in later only if `VACUUM INTO`'s single-shot blocking window ever becomes big enough to noticeably stall other queries |
| **Plain file copy of the `.db` file** | **Not safe** under WAL without extra care: a bare `shutil.copy` of the main DB file while WAL/SHM files exist and writers are active can copy a torn, inconsistent snapshot, since the live data may be split across the main file and the WAL file at copy time | Rule out as a v1 option; mentioned here only because it's the naive-first-instinct approach and worth explicitly rejecting rather than silently not considering |

Left uncovered by the draft and worth deciding explicitly:

- **Destination path**: the draft scopes this to "a local filesystem path only," which is reasonable
  for v1, but doesn't say what happens if that path is unreachable (an unmounted network share, a
  removable drive not plugged in). `VACUUM INTO` will simply raise, which the `ScheduledTaskRun`
  error field should surface, but the UI needs to make that failure visible rather than just
  recorded and forgotten (see Failure Reporting below).
- **Retention/rotation**: the draft specifies a retention count (keep N newest, delete older). Worth
  deciding whether rotation is purely count-based or also time-based (e.g. "keep the last 7 daily
  plus 4 weekly," grandfather-father-son style). Count-based is simpler and probably right for v1;
  flagging grandfather-father-son as a plausible later-phase enhancement, not a v1 requirement.
- **Disk-fill risk**: a snapshot of a growing DB, taken repeatedly, on a disk that's already tight on
  space, can itself cause an out-of-space condition, which is an ugly way to discover the backup
  feature exists. Worth a pre-flight check (e.g. `shutil.disk_usage` on the destination, comparing
  free space against the source DB's current size with some safety margin) before attempting
  `VACUUM INTO`, failing fast with a clear "not enough disk space" error into `ScheduledTaskRun`
  rather than letting the OS fail the write mid-snapshot. Not in the current draft at all; worth
  adding regardless of which other options are picked.

## Archive vs prune

The draft's two-threshold design (soft `archived` flag at `archive_after_days`, hard delete at a
longer opt-in `prune_after_days`) against `TERMINAL_TRANSCRIPT_STATUSES`
(`("completed", "failed", "partial", "cancelled")`, services/queue.py) is a reasonable shape. Two
things worth surfacing before committing to it:

**Blast radius of hard-delete is bigger than the draft states.** The draft's Data Model section
says pruning "cascades to its `TranscriptionJob`/`LlmJob`/`RelabelHistory` rows via the existing ORM
cascade config." Checking `database/__init__.py`'s `Transcript` class: it declares ORM-level
`cascade="all, delete-orphan"` relationships for `summary`, `voice_note`, `jobs` (→
`TranscriptionJob`), and `relabel_history`, but **not** for `LlmJob`. `LlmJob.transcript_id` has
`ondelete="CASCADE"` at the column-FK level, but that never fires: SQLite's `foreign_keys` pragma is
never enabled by this app (the `relabel_history` relationship's own comment says so explicitly, to
explain why it needs an ORM-level cascade instead of relying on the FK). The existing
`DELETE /api/transcripts/{id}` endpoint (app.py's `delete_transcript`, and
`services/transcription.py`'s `TranscriptionService.delete_transcript`) does a plain `db.delete(t)`
today, so this orphaning is a pre-existing property of manual delete, not something the scheduled
prune task introduces. But a scheduled prune task that runs unattended, deleting rows nobody is
watching in real time, is exactly the context where a slow accumulation of orphaned `LlmJob` rows
(referencing dead `transcript_id`s) is more likely to go unnoticed than a one-off manual delete
would be. Whatever prune implementation gets built should either add `LlmJob` to an explicit cleanup
step (delete matching `LlmJob` rows before/alongside the `Transcript` row) or fix the missing ORM
relationship generally, rather than silently inheriting the same gap.

**Mirrored-pair filter (Complement Rule).** The draft correctly identifies that the transcript list
endpoint (app.py's `list_transcripts` / `_build_recent_transcripts`) and the search functions
(services/search.py's `search_transcripts` and `search_transcripts_snippets`) both read the same
underlying `Transcript` rows through two separate entry points, and both need an `include_archived`
filter added together. This is worth restating as a hard requirement, not a nice-to-have: shipping
the filter on only one side produces a confusing, hard-to-diagnose symptom (archived transcripts
invisible in the browse list but still turning up in search results, or vice versa) rather than an
obvious bug. Grep both files for every `Transcript.query`/`db.query(Transcript)` filter site when
implementing, not just the primary list function, since `_build_recent_transcripts` may have more
than one query path (e.g. count query vs page query) that both need the same filter applied.

**Thresholds and defaults**: the draft's opt-in-only stance on prune (`prune_enabled` default
`False`) while archive defaults to `True` reflects the asymmetric risk correctly, and is probably
right; restated here as a real recommendation, not just an observation, given the earlier
user-intent framing (irreversible action defaults off, especially before backup itself is proven to
work reliably).

## Failure reporting

| Option | Sketch | Pros | Cons |
|---|---|---|---|
| **New `ScheduledTaskRun` table** (draft's choice) | Small table: `kind` (backup/archive), `status`, `detail` JSON, `error`, `started_at`/`finished_at` | Clean fit: doesn't force backup/archive attempts into a table (`LlmJob`) whose columns and dispatch logic are LLM-specific | One more table, one more migration, one more thing to query for the settings-panel "last run" readout |
| **Reuse `LlmJob`** | Add `kind="backup"`/`kind="archive"` to `VALID_KINDS` (services/llm_jobs.py) | Reuses one existing observable idiom and its Queue-screen UI | `LlmJob.kind` is validated against a closed enum (`VALID_KINDS`) consumed by `llm_worker_tick`'s `IO_KINDS`/`CPU_KINDS` pool-routing and `AUTO_RETRY_KINDS` backoff logic, so every one of those partitions would need "backup"/"archive" classified as I/O or CPU work for concurrency-pool purposes, and auto-retry-or-not decided, none of which map cleanly onto "ran a `VACUUM INTO`" or "flipped some flags." The table also carries `provider`/`model`/`transcript_id` columns that are meaningless for a whole-DB backup, and a fixed `test_io_cpu_pools_partition_valid_kinds`-style invariant test (referenced in llm_jobs.py's own comments) would need updating for a kind that fits neither pool's actual meaning. Forcing the fit costs more than building a small dedicated table. |

**Recommendation**: agree with the draft, a small dedicated `ScheduledTaskRun` table. The harder
part isn't the table, it's making a *failure actually visible*: a status column nobody looks at is
functionally the same as no reporting at all. Concretely:

- The settings-panel "last backup / last archive" readout the draft already plans (status, error,
  timestamp) is necessary but not sufficient: it's opt-in, the user has to go look. Consider
  whether a failed backup (specifically backup, since it's the "protect against catastrophe" one)
  should also surface somewhere more ambient the user is already likely to see (a banner on the main
  view, a badge), at least after N consecutive failures, so "silently broken for three weeks" is
  actually hard to achieve. Not resolved here, flagged as worth deciding.
- Record every attempt, not just failures, including successes, so "when did this last actually
  succeed" is answerable without cross-referencing timestamps against a separate log.
- Consider whether N consecutive backup failures should auto-disable further attempts (stop
  hammering an unreachable destination every tick) versus just keep trying and keep recording
  failures. Either is defensible; not deciding here.

## Risks and failure modes (summary)

- **Silent backup failure** (discussed above): the scary one. Detection is only as good as whatever
  surfaces `ScheduledTaskRun.status == "failed"` to a human who will actually see it.
- **Disk fills up** from repeated snapshots on a tight destination: needs a pre-flight check (see
  Backup Method above), not currently in the draft.
- **Orphaned `LlmJob` rows** after hard-prune, from the missing ORM cascade (see Archive vs Prune
  above): a correctness bug that predates this feature but that an unattended prune task is more
  likely to make worse, faster, and more invisibly than the existing manual-delete path does.
- **Wrong-scope config drift** in a multi-user install if the per-user-settings-row option is picked
  for config scope: two admins independently opening a settings panel that appears to be "mine" but
  is actually shared could each believe they set the schedule, with the other's edit silently
  overwritten via `json_patch`.
- **Missed runs during long app-closed periods**: not a bug given "elapsed since last run" semantics,
  but a UX surprise worth documenting ("daily" backup really means "at most once per `interval_hours`
  of app uptime," not "once per calendar day regardless of whether the app was running").
- **Archive/search asymmetry** if the `include_archived` filter lands on only one of the two mirrored
  entry points (see Archive vs Prune above).

## Recommended MVP slice

1. **Manual-trigger backup only**, no scheduler yet: a settings-panel button that calls `run_backup()`
   synchronously (or as a one-off background task) and reports success/failure immediately. Proves
   out `VACUUM INTO`, the destination-path handling, retention logic, and the `ScheduledTaskRun`
   table, all without yet touching `lifespan` or adding a new long-running loop. Cheapest possible
   way to get real backup files existing and get failure-reporting UI validated against a real error
   (bad path, full disk) before anything runs unattended.
2. **The interval loop**, wired into `lifespan` next to the two existing worker tasks, driving the
   same `run_backup()` from step 1 on a schedule. This is where the config-scope decision (per-user
   row vs install-level store vs env/file) has to be settled, since the loop needs somewhere to read
   `backup_enabled`/`backup_interval_hours` from.
3. **Archive** (soft flag) as its own phase after backup is proven reliable in the field, not bundled
   into the same release as backup's first rollout: archive is much lower-stakes than prune, but
   sequencing it after backup means there's already a safety net by the time anything (even a
   reversible flag flip) starts happening unattended.
4. **Prune** (hard delete) last, opt-in, after both backup and archive have been running quietly and
   correctly for a while. This is the step where "if something silently deletes data the user wanted,
   was there a backup to recover it from" actually matters, so it's the one most worth deferring
   until the earlier phases are trusted.

Later phases (not v1): audio-directory backup (`UPLOAD_DIR`/`VOICES_DIR`) alongside the DB, the
Online Backup API swap-in if DB size ever makes `VACUUM INTO` noticeably slow, grandfather-father-son
retention, cloud backup destinations, an ambient (not just settings-panel) failure indicator.

## Decisions needed from the human

- **Config scope**: per-user settings row (fallback/admin user), a new install-level settings store,
  or env/config file. Affects Phase 1 schema work directly; the three options above are genuinely
  different amounts of code and different UX, not a detail to default silently.
- **Who can see/edit this config** in a multi-user install: gate to `is_admin` users, or something
  else? (Only matters if a DB-backed store is chosen over env/file.)
- **Scheduling semantics**: confirm "elapsed time since last successful run" (draft's implicit model)
  over "wall-clock schedule" (e.g. "daily at 2am"), since they behave differently when the app isn't
  always running, and the draft doesn't say this out loud as a constraint.
- **Backup destination failure handling**: what should happen after backup has failed N times in a
  row (keep retrying every tick forever, or back off / stop and require a manual re-enable)?
- **Ambient failure visibility**: is a settings-panel readout enough, or does a failed backup deserve
  a more visible indicator elsewhere in the UI?
- **LlmJob orphaning on hard-delete**: fix the missing `Transcript` → `LlmJob` cascade as part of this
  feature (since prune will exercise the codepath repeatedly and unattended), or treat it as a
  separate, pre-existing bug to file independently? Either is reasonable, but it should be a decision,
  not an oversight discovered after prune ships.
- **Retention model for backups**: pure count-based cap (draft's proposal) versus
  grandfather-father-son, for v1 versus later.
- **Archived visibility in search**: the draft recommends excluding archived transcripts from search
  by default with an explicit toggle; confirm that's the desired default before building it.
- **Audio directories in scope**: confirmed out of scope for v1 per the draft (DB only); reconfirm
  that's still acceptable before committing to "backup" meaning "DB only" in any user-facing copy.
