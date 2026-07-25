# WhisperDeck expansion: mobile capture, intent routing, storage backends

Status: exploratory. Not scheduled, not in ROADMAP.md, not referenced from anywhere else on
purpose. This is a holding pen for an idea that started from a voice-note brainstorm and got
worked over once, not a commitment to build any of it.

## The actual problem

Tim (the primary and, for now, only real user) hits genuine friction capturing voice notes when
away from the desktop machine WhisperDeck runs on. Constraints that rule out the obvious
shortcuts:

- Won't expose the app to the public internet before it gets a real security pass.
- Doesn't want a mobile-browser-over-VPN round trip: connection setup, latency, general
  jankiness.
- Doesn't want a routing workflow that requires copy-pasting a summary into email/GitHub by
  hand; the point is to avoid that step.

## Original pitch (recap)

Server-client split, Windows desktop binary and web client, native iOS/Android apps doing
on-device STT with offline queue and sync-when-connected, and a backend routing layer that
turns transcribed notes into tasks, reminders, emails, or GitHub issues/PRs based on
user-configured rules.

## Where the pitch didn't hold up, and what we landed on instead

### 1. Capture and sync path

Native on-device STT was the wrong design: WhisperDeck's whole value is its transcription
pipeline (Moonshine/faster-whisper, pyannote diarization, hotword-glossary correction). Doing
STT a second time on-device means reconciling two different transcripts of the same audio, for
no benefit.

Given the "no internet exposure, no VPN" constraint, the sync path is LAN-only: capture audio,
queue it locally, push it to the server only when on the home network. That's a much smaller
problem than "mobile app with sync," and it might not need a custom app at all:

- iOS Shortcuts has a "when I connect to [home WiFi]" automation that can walk a folder and
  POST new files to the server's existing `/api/transcribe`.
- Android has Tasker, or a plain LAN-first sync tool like Syncthing pointed at a watched
  folder, which already handles offline queuing and reconnect.

Before writing a line of custom mobile code: try one of those. If it's good enough in daily
use, a custom app isn't justified. If it's genuinely too janky, that's real evidence a purpose
built app earns its cost, not just an assumption going in.

### 2. Intent routing / autonomous action execution

The risky part of the original pitch: an LLM parses an unstructured, rambling voice note and
autonomously executes the inferred intent, including external, hard-to-undo actions (send this
email, open this GitHub issue/PR). Ambiguous input plus autonomous irreversible action is a bad
combination, especially against rambling audio rather than clean dictation.

Refinements from discussion:

- A confirmation step before any action fires is a hard requirement, not optional.
- Route the transcript through a reasoning/thinking-mode LLM pass specifically for intent
  extraction, separate from the correction/summary passes that already exist.
- Split by reversibility rather than by target system: fully local, cheap-to-undo actions
  (an in-app task or reminder) can auto-commit. Anything that leaves the app's boundary and
  can't be unsent (email, GitHub issue/PR, an invite to another person) should land as a
  pending, reviewable item, not fire automatically.

Suggested implementation shape: don't build a new automation engine from scratch. WhisperDeck
already has a job-queue and run-history pattern (`services/queue.py`, `services/llm_jobs.py`,
the Queue screen with cancel/rerun/dismiss). A new job type that produces a proposed action
record, reviewed the same way a correction or summary run is reviewed today, reuses that
infrastructure instead of inventing a parallel one.

### 3. Storage backend evolution

Original idea (offered loosely, acknowledged as not fully thought through): standalone Windows
installer keeps shipping SQLite as-is; a server-oriented install could ship a lighter SQL
server option, plus a bring-your-own-database option in the installer.

Concrete blocker found while looking at this: there is no migration framework in the codebase
today. Schema changes are hand-rolled (`Base.metadata.create_all()` plus manual
`ALTER TABLE ... ADD COLUMN`, and a rename-to-`_old`/recreate/copy-data dance for anything more
invasive, in `database/__init__.py`). That approach is written around SQLite's specific
limitations and doesn't transfer to Postgres or SQL Server as-is. Supporting a second database
engine on top of it means every future schema change has to be hand-written and tested twice,
or it silently breaks on whichever engine didn't get exercised.

Prerequisite, in order: adopt a real migration tool (Alembic or equivalent) against the
existing SQLite backend first, as a standalone, invisible-to-users change. Only revisit a
second database backend once migrations are engine-agnostic.

## Suggested validation order, if this ever gets picked back up

1. Try an OS-native automation (Shortcuts/Tasker/Syncthing) for LAN-only capture-and-sync
   before writing any app code.
2. Add Alembic migrations against current SQLite. Pure risk reduction, no user-visible change.
3. Build the proposed-action job type on the existing queue infrastructure. Ship local-only
   actions (task/reminder) first; add external actions (email, GitHub) only once the
   intent-extraction step has proven reliable against real, messy, rambling audio, not just
   clean test recordings.
4. Only after 1 to 3: decide whether a custom mobile app and a second database backend are
   still worth building, informed by what step 1 actually showed about the OS-native route.

## Explicitly not decided

- Whether a custom mobile app gets built at all.
- Which actions beyond "task/reminder" vs "email/GitHub" are in scope, and how each is
  reviewed before firing.
- Which second database engine (if any) to support, and whether "bring your own database" in
  the installer is worth the support burden it implies.
