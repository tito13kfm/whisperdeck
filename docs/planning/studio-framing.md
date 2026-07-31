# Studio framing: record/import as the front door, auto-routed pipeline behind it

Status: exploratory. Not scheduled, not in ROADMAP.md. Source idea came from a
Granola-assisted brainstorm (recapped below), checked against the current codebase.
Treat the comparisons here as a starting point for a design pass, not a spec.

## Original pitch (recap)

- Reframe the app as a "recording studio": the primary action is always record or
  import, and everything else is a transformation of that input.
- Inputs: video files, audio files, microphone, a planned mobile app.
- Pipeline as a layered transformation chain: clean/process audio, split by channel
  or downsample to mono, initial transcription, diarization (only when needed),
  correction pass, then a classification phase that decides what happens next.
- Fewer upfront choices: one interface (record or upload), the app takes over from
  there instead of the user configuring a pipeline first.
- Original audio always preserved as the canonical source; a recording can be
  rerouted to a different pipeline later, including a full redo.

## What already matches this today

Worth stating plainly so the design pass doesn't rebuild what exists:

- **Audio and video already share one code path.** `POST /api/transcribe` probes
  the upload; video files get transcoded to an audio stream for the pipeline while
  the original stays browsable via `GET /api/transcripts/{id}/video`
  (`app.py:1080-1081`, `2038-2071`; `services/audio_prep.py:134`). There's no
  separate "video mode."
- **Mono downsampling already happens on every upload**: `transcode_for_upload`
  converts to 16kHz mono MP3 before transcription (`services/audio_prep.py:44-87`).
  Channel-aware handling exists too, but only for live-stereo capture: a second
  16kHz 2-channel FLAC copy feeds the diarizer's channel-aware path
  (`services/audio_prep.py:89-121`; `services/diarization.py:65-74`, `371-415`).
- **Diarization already has a "only when needed" cascade**, not just a flag.
  `diarize_and_merge` (`services/diarization.py:51-84`) tries live-stereo
  channel-aware pyannote first if a stereo copy exists, falls back to pyannote on
  mixed audio, and falls back further to a pause-gap heuristic if pyannote isn't
  installed. The framing in the pitch describes behavior that's largely already
  there; it's just not narrated as a pipeline stage anywhere in the UI.
- **Correction pass already runs automatically by default.** `enqueue_auto_correction`
  fires unless the transcript kind is `voice_note`, gated on a user setting that
  defaults to on (`services/llm_jobs.py:159-173`). Already matches the pitch.
- **Original audio is already preserved indefinitely, and rerouting already works.**
  `Transcript.audio_path` is never deleted after processing. `retranscribe`
  (`app.py:1984-2025`) creates a new transcript row from the stored original
  (linked via `source_transcript_id`) and `rediarize` (`app.py:2496-2528`) reruns
  diarization in place on the same stored audio. Both are live features today, not
  proposals, see `docs/superpowers/plans/2026-07-06-run-history-phase4-summary-rediarize-diff.md`,
  which is about adding run-history/diff on top of already-working retranscribe and
  rediarize. **The "reusable audio source" bullet in the pitch needs no new backend
  work.** If anything is missing, it's a more discoverable entry point framed as
  "send this recording through a different pipeline" rather than today's
  recovery-flavored "retranscribe" / "rediarize" actions buried in the transcript
  detail page.
- **Mobile input has already been explored and deliberately not committed to.**
  See `docs/planning/server-client-mobile-expansion.md`: a native on-device-STT
  mobile app was rejected in favor of LAN-only sync of raw audio into the existing
  pipeline (OS-native automation first: iOS Shortcuts, Tasker, Syncthing), because
  running STT twice on the same audio has no benefit. That conclusion still holds
  under the studio framing: a mobile app is just another way raw audio arrives at
  the front door, same as a file upload. Nothing here should reopen that
  exploration; it should just cite it when "planned mobile app" comes up as an input.

## Where the pitch implies real change

### The upfront Mode picker is exactly the choice this pitch wants to remove

Today, `Meeting` / `Dictation` / `Voice Note` is a user-selected upfront choice
(`mfdCatDefs`, `static/rack.js:1724-1743`) that determines the downstream branch:
Dictation and Voice Note force diarization off server-side (`app.py:1050-1051`),
Voice Note skips auto-correction and instead runs its own fixed LLM chain
(`enqueue_auto_voice_note`, `services/llm_jobs.py:195-213`), and Meeting is the only
kind eligible for diarization at all. This is precisely the upfront configuration
step the pitch wants to eliminate ("single interface: record or upload, then the
app takes over"). Removing or backgrounding it is the biggest change implied here,
not a framing change, an architecture change: it means something has to infer
`kind` after the fact instead of the user declaring it before recording starts.

### The pitch's own step order has a logical snag

The pitch lists diarization (step 4, "only when needed") before classification
(step 6, "determine next steps"). But knowing whether a recording needs
diarization is itself a classification judgment, today it's answered by the
upfront Mode choice, not inferred. As written, the pitch's classification step
happens too late to be the thing that decides whether diarization already ran.
Two ways to resolve this, not chosen yet:

1. **A cheap pre-pass** (duration, channel count, silence pattern, maybe first
   N seconds of partial transcript) decides diarization eligibility before the
   rest of the chain runs, separate from a richer LLM classification afterward
   that decides summarize vs. voice-note structuring vs. tagging.
2. **Diarization becomes cheap/default-on enough** that gating on "need" stops
   mattering and the method cascade that already exists (channel-aware -> pyannote
   -> heuristic) just always runs, with cost being the only reason to skip it.

This needs to be resolved before any implementation plan, not glossed over.

### Classification-that-routes doesn't exist yet, and is distinct from issue #253

Two things currently answer to "classification" in the codebase, and neither
routes the pipeline:

- `classify_intent` (`services/llm_jobs.py:364-374`) is dictation-only, produces a
  `{"format": label}` hint surfaced in a reformat tab. It doesn't branch anything.
- Issue #253 ("Research: Post-meeting follow-up session") classifies pieces of an
  *already-produced summary* for the knowledge layer (action item vs. decision vs.
  reference). That's classifying content after the fact for storage/retrieval, not
  classifying a raw recording upfront to decide which pipeline branch to run.

What the pitch actually wants is upstream of both: a pass that looks at a new
recording (or its early transcript) and decides `kind` instead of the user
selecting it. That's new work, and it should stay clearly separated from #253's
scope when either gets designed, they solve different problems even though both
use an LLM to label things.

### "Clean and process audio" doesn't exist yet, but four issues already cover pieces of it

No noise-reduction or loudness-normalization step exists anywhere in the pipeline
today (`services/audio_prep.py` only transcodes format/rate). Four open issues
already scope pieces of exactly this pitch stage:

- #236 Audio pre-cleanup: ffmpeg loudnorm/denoise filter chain
- #237 Expose and tune VAD settings for the builtin provider
- #238 Post-hoc hallucination heuristic for faster-whisper segments
- #239 Opt-in Demucs vocal isolation for noisy local recordings

None are implemented; #236 explicitly says it needs a research/design pass before
any code. The studio framing gives these a shared home ("stage 1 of the chain")
instead of four independent opt-in toggles, which might be a reason to design them
together rather than separately, but that's a sequencing question, not a
commitment made here.

## Explicitly not decided

- Whether the Mode picker gets removed, backgrounded behind an "Auto" default with
  manual override, or left as-is with classification only feeding metadata/tags.
- Whether upfront classification runs on raw audio features, a partial transcript,
  or the full corrected text, and therefore where in the chain it actually sits.
- Whether "clean and process audio" ships as one unified always-on stage or stays
  as the four separate opt-in issues above.
- Which model/provider runs the classification call (local Lemonade vs. cloud) and
  whether it's synchronous/instant or an async `LlmJob` like today's
  `classify_intent`.
- Whether provider/model selection (currently an upfront user choice too) stays
  exposed in an "Advanced" panel under the simplified flow, or gets a smart default
  with no user-visible choice at all.

## Suggested validation order, if this gets picked back up

1. Resolve the diarization-vs-classification ordering snag above; it blocks any
   sane implementation plan for "fewer upfront choices."
2. Decide whether the four audio-cleanup issues (#236-#239) get one combined design
   pass under this framing or stay independent.
3. Prototype the smallest version of upfront classification: keep the existing
   Mode picker as a manual override, default to "Auto," and see whether a cheap
   classification pass actually guesses `kind` correctly on real (not clean test)
   recordings before touching any UI simplification.
4. Only after 1-3: revisit whether the Mode picker should be removed outright, and
   whether "reroute to a different pipeline" deserves a first-class UI entry point
   beyond today's retranscribe/rediarize actions.
