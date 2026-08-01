# Studio classification/routing contract — design

Status: approved design, ready for implementation planning.
Resolves the open decisions in issue #264 (tracking) and issue #266 (design/research).
Scope: issues #266-#271. Out of scope: category expansion, project-linking,
keyword extraction (see "Explicitly out of scope" below).

## Background

`docs/planning/studio-framing.md` proposed reframing WhisperDeck around a
"record or import, the app figures out the rest" model. Issue #264 tracks
that idea; issues #266-271 break the implied work into a dependency chain:

- #266 — design/research (this document resolves it)
- #267 — depends on #266: persisted classification + classifier service + job
- #268 — depends on #266, #267: replace kind guards with predicates
- #269 — depends on #266, #267, #268: front door UI (Auto default)
- #270 — depends on #266, #267: unified audio-cleanup stage (#236-239)
- #271 — depends on #266, #267, #268: retranscribe/rediarize classification-aware

#266's own acceptance criteria requires no implementation to start until this
design is approved. This document is that approval record.

## Decisions

### 1. Diarization eligibility: cheap pre-pass, audio features only

A separate, cheap pre-pass decides whether diarization runs, using only
audio-level signals already available before transcription: duration,
channel count (stereo vs. mono), and silence/pause pattern from the existing
`chunk_audio()` / `detect_silence_midpoints()` machinery
(`services/audio_prep.py:271-342`). No transcript text is required for this
decision, so it can run early, independent of the main classification pass.

This resolves the ordering snag from `docs/planning/studio-framing.md`
("diarization listed before classification, but diarization eligibility is
itself a classification judgment"): the two decisions use different inputs
and run at different times, so neither blocks the other.

### 2. Main classification: full corrected text, async job

The main classification pass (deciding `kind`: `meeting` / `dictation` /
`voice_note`, same three values as today) runs after the correction pass
finishes, against the full corrected text. It executes as an async `LlmJob`,
dispatched through the same registry/worker pattern as existing jobs
(`services/llm_jobs.py:20,176-213,273-374`), with identical dispatch for
inline and chunked upload completion (no duplicate enqueue across the two
paths).

Rationale: corrected text is the cleanest signal available and correction
already runs by default for everything except voice notes, so classification
naturally slots in as the next stage rather than needing its own
noise-tolerant logic against raw ASR output.

### 3. Classifier returns a confidence score; a threshold gates acceptance

The classifier call returns `{kind, confidence}`. A new tunable setting,
`classification_confidence_threshold` (default: conservative — high enough
that a low-confidence guess is rejected rather than accepted), gates whether
the result is accepted. Below threshold, the result is stored but the
transcript's effective status is `uncertain`, which is treated identically to
`pending`/`failed` for fallback purposes (see decision 8). A wrong-but-
confident-looking auto-kind is worse than staying in the safe fallback state;
the user can always reclassify manually afterward.

### 4. Provider/model: new per-task setting, same shape as correction's

Classification gets its own `classification_provider` / `classification_model`
settings, in the same settings location and following the same pattern as
today's `correction_provider` / `correction_model` (`services/settings.py`).
Default: local Lemonade, matching correction's default. Every model/provider
choice is user-configurable; only the default is opinionated.

### 5. Mode picker: stays visible, defaults to Auto

The existing Meeting/Dictation/Voice Note picker UI is not removed. Its
default selection changes from a forced choice to "Auto." A user who already
knows what they're recording can still pick a kind explicitly before
starting, identical to today's flow. Picking explicitly (via UI or API/
upload/PATCH parameter, `app.py:1301,1384,1399-1401,1773-1781`) is recorded
as a manual override and behaves exactly as today's explicit-kind path
always has — no API compatibility break.

### 6. Persisted state model

Each transcript stores:

- `kind` — `meeting` / `dictation` / `voice_note` (schema-versioned; the enum
  is deliberately not hard-coded as final, see "Explicitly out of scope").
- `status` — `pending` / `success` / `uncertain` / `failed` / `override`.
- `confidence` — float, present when `status` is `success` or `uncertain`,
  absent for `override` and legacy-migrated records.
- `provenance` — `{provider, model, schema_version, classified_at}`, or
  `{legacy-migration}` for backfilled records.
- Override records additionally carry `overridden_by`, `overridden_at`, and
  the prior value (if any), so an override that supersedes a real
  classification result doesn't lose that result.

### 7. Migration: existing transcripts become permanent overrides

Every existing transcript's current `kind` value becomes a manual-override
record (`status=override`, `provenance={legacy-migration}`, no confidence
score). None are retroactively reclassified. All existing behavior for
already-processed transcripts stays byte-for-byte the same; auto-
classification only applies to new recordings going forward.

### 8. Pending/uncertain/failed fallback: always the conservative behavior

While classification is pending, below the confidence threshold, or has
failed, the pipeline behaves like today's Dictation: diarization stays off,
auto-correction still runs (matches today's default-on correction), and the
voice-note-only chain, voice-match, and rediarize stay locked until a real
kind (accepted auto-classification or manual override) is known. The
fallback never defaults toward the more permissive Meeting behavior — a
missing or uncertain classification can never silently enable a speaker-
identity-adjacent capability (diarization, voice-match). This directly
satisfies #268's acceptance criterion that "classification failure cannot
silently enable an unsafe capability."

### 9. Retranscription semantics

When a transcript is retranscribed (new transcript from the same stored
audio, e.g. after a provider/model change):

- If the original was auto-classified (`success` or `uncertain`),
  classification re-runs against the new corrected text — new text may
  legitimately classify differently.
- If the original was a manual override (including all legacy-migrated
  transcripts), the override carries forward unchanged to the new
  transcript. A user's explicit choice is never silently discarded by a
  re-run.

Rediarize and voice-match apply the same shared capability predicates
defined in #268, enforced server-side, regardless of entry point.

### 10. Audio cleanup (#236-239): one unified stage, per-step opt-in

The four audio-cleanup issues ship as one coherent pipeline stage with a
defined order: loudnorm/denoise (#236) → VAD (#237) → chunking → transcribe
→ post-hoc hallucination filter (#238), with Demucs vocal isolation (#239)
as a separate opt-in pre-step for noisy local recordings. Each step keeps its
own on/off setting and a safe fallback to the original audio if it fails —
this is architectural coherence, not a forced always-on bundle.

## Explicitly out of scope (tracked separately)

Category expansion beyond the three routing kinds, automatic relation to
existing projects, and keyword extraction for cross-note linking are real
ideas but belong to the knowledge layer (#253's territory or a new future
issue), not pipeline routing. The `kind`/classification schema here is
version-tagged specifically so this can build on top of it later without a
breaking migration, but #266-271 do not implement any of it.

## What this unblocks

With this document as the approved design, #267-271 can proceed in
dependency order: #267 first (persisted model + classifier job), then #268
(routing predicates) and #270 (audio cleanup) in parallel (both depend only
on #266+#267), then #269 and #271 (both depend on #268 as well).
