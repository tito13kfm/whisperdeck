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

The front end this capture app needs, if it does get built, is small: one button, tap to start
recording when a meeting starts, tap to stop, file gets queued for LAN sync. No on-device STT,
no in-app playback or editing, WhisperDeck does the actual work later.

Whether that button needs to be a custom app or can just be the phone's own voice recorder is
part of the same OS-native validation, and there's a concrete snag on iOS worth checking
directly: Voice Memos recordings live in the app's own sandboxed storage, not a Files/iCloud
Drive folder a Shortcuts automation can passively watch. Getting a recording out today means an
explicit "share to Files" tap per meeting, which brings back the exact per-note friction the
one-button idea is trying to remove. If that turns out to be true in practice, the concrete
case for a minimal custom app is "save directly somewhere the LAN sync can pick up
automatically," not "start recording" itself. Android is less of a concern here since app
storage is generally more open to Tasker/Syncthing.

Two more things to validate for real, not assume:

- **Background recording for the length of an actual meeting.** iOS needs a specific
  background-audio entitlement to keep recording once the app isn't in the foreground, and
  interruptions (phone calls, lock screen, battery drain over an hour-plus) are the kind of
  thing that's fine in a two-minute demo and flaky in practice.
- **Phone-mic audio quality against diarization.** Pyannote's speaker separation is sensitive to
  mic quality and placement; a phone lying flat on a conference table is a different signal
  than whatever source has been feeding diarization so far. Not assumed to be a blocker, just
  another case for the "validate against real, messy audio" step below, not a clean test
  recording.

### 2. Intent routing / autonomous action execution

The risky part of the original pitch: an LLM parses an unstructured, rambling voice note and
autonomously executes the inferred intent, including external, hard-to-undo actions (send this
email, open this GitHub issue/PR). Ambiguous input plus autonomous irreversible action is a bad
combination, especially against rambling audio rather than clean dictation.

First refinement: a confirmation step before any action fires, split by reversibility rather
than target system, reusing the existing job-queue/run-history pattern
(`services/queue.py`, `services/llm_jobs.py`) for a proposed-action record instead of building
a new automation engine.

Second refinement, and the one we settled on: drop execution entirely. The job outputs a
copyable text block per action, nothing fires automatically, ever. A `gh issue create` command
with the right flags, or an email draft (To/Subject/Body), that the user pastes and runs or
sends themselves. This removes the entire confirm/reject UI, the external API integration, and
any credential storage, while still doing the actual work (figuring out what was meant and
drafting it). The existing correction/summarize job type already proves the underlying
capability, turning hours of rambling, multi-person meeting audio into an accurate account of
what was discussed and decided.

What still needs real design, even without execution:

- **Summarizing and extracting actionable intent aren't the same task**, even sharing plumbing.
  A summary is descriptive and forgiving; a generated command is something that might get
  pasted and run without a close reread, especially as trust in the tool grows. The output
  format should call out the fields most likely to be wrong and most costly if unnoticed
  (which repo, which email address), not just follow each target's normal command syntax.
- **A single ramble usually contains more than one intent** ("remind me to call Bob, also file
  an issue for the login bug, also send Sarah the notes"). That needs structured, multi-item
  extraction (a JSON-schema'd pass with one entry per action, each typed by target), then a
  render step per type, not a reuse of the free-text summarize prompt as-is.
- **Target resolution needs somewhere to live.** "Email Sarah" is only a usable draft if the
  system knows Sarah's address; there's no contacts concept in the app today. Smallest fix:
  extend the existing hotword glossary (already `name -> canonical term`) to optionally carry
  `name -> email`, rather than building a separate address book. GitHub repo targeting has the
  same open question: does the user have to say the repo out loud, or is there a per-user
  default set in Settings?
- **Silent misses are worse than false positives here.** Nothing executes automatically, so a
  spurious suggested action just gets ignored at no cost. A missed intent means the whole point
  of rambling into the app was lost with no signal that it happened. Low-confidence extractions
  should be shown, flagged as uncertain, rather than dropped.

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
   before writing any app code. Specifically check whether a recording can reach the watched
   sync folder without a manual per-file export step (the Voice Memos snag above), whether
   background recording survives a real hour-plus meeting, and how diarization holds up on
   phone-mic audio.
2. Add Alembic migrations against current SQLite. Pure risk reduction, no user-visible change.
3. Build the intent-extraction job type on the existing queue infrastructure: structured,
   multi-item output, rendered per target as copyable text, nothing executed automatically.
   Start with whichever target is cheapest to get right (likely a plain reminder/task line)
   and validate against real, messy, rambling audio, not just clean test recordings, before
   adding targets that need resolution data (email addresses, repo names).
4. Only after 1 to 3: decide whether a custom mobile app and a second database backend are
   still worth building, informed by what step 1 actually showed about the OS-native route.

## Explicitly not decided

- Whether a custom mobile app gets built at all.
- Which action targets beyond "task/reminder" are in scope (email, GitHub, others), and where
  target-resolution data (contacts, default repo) lives.
- Which second database engine (if any) to support, and whether "bring your own database" in
  the installer is worth the support burden it implies.
