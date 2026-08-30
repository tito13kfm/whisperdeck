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

### 8. Pending/uncertain/failed fallback: conservative, but does not touch diarization

Diarization is governed solely by decision 1's pre-pass and never by
classification status — that independence is the entire point of decision 1,
and this fallback does not re-couple them. What actually waits on a
confirmed `kind` (accepted auto-classification or manual override) is the
set of capabilities that are genuinely kind-dependent: the voice-note-only
chain, voice-match, rediarize, and reformat stay locked while classification
is pending, below the confidence threshold, or has failed. Auto-correction
still runs regardless (matches today's default-on behavior). The fallback
never treats a pending/uncertain/failed classification as `voice_note` or
`dictation` becoming true — a missing or uncertain classification can never
silently unlock voice-match or rediarize. This satisfies #268's acceptance
criterion that "classification failure cannot silently enable an unsafe
capability" — read as applying to the kind-dependent capability set, not to
diarization, which was never gated on `kind` in this design to begin with.

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

### 11. Guard mapping (#266's required deliverable — every current kind guard, marked changed/preserved/deprecated)

Verified against current code, not the child issues' own cited line numbers
(several have already drifted). `Transcript.kind` and `LlmJob.kind` are two
different columns that share a name — this table is about `Transcript.kind`
only; `LlmJob.kind` (`"correction"`, `"summary"`, `"rediarize"`, etc., the
job-type discriminator) is untouched by this design.

| Site | Current behavior | Future predicate | Change |
|---|---|---|---|
| `database/__init__.py:38` | `Transcript.kind` column, default `"meeting"` | unchanged value set; add `status`, `confidence`, `provenance` columns alongside (decision 6) | **changed** (additive) |
| `app.py:1050` (`if kind in ("dictation","voice_note","voice_dump"): diarize = False`) | diarization forced off by kind | decision 1's pre-pass **ANDed onto** the existing kind veto, not replacing it — `diarize = diarize and prepass.eligible and kind not in (...)`. See the amendment below. | **changed** (pre-pass added; kind veto retained for explicit overrides) |
| `app.py:1269-1277` (correction/classify/voice-note-chain/tagging dispatch) | `if kind != "voice_note": correction+classify else: voice-note chain`; tagging always | correction always runs; voice-note chain gated on **accepted** kind == `voice_note` (deferred while pending, triggered retroactively on acceptance, see #267 item 3 and #270/queue.py:565 sibling); tagging unchanged | **changed** |
| `app.py:1307`, `app.py:1400` (kind validation on upload / bulk-transcribe) | must be one of the 3 kinds | add `"auto"` as a valid sentinel meaning "defer to classifier"; explicit values behave exactly as today (recorded as override) | **changed** (additive, back-compat preserved) |
| `app.py:369-399` (`_dictation_job_fields` serializer) | branches on `t.kind` for job-field shape | branches on **effective kind** (accepted classification or override); shape/fields unchanged | **preserved** (input source changes, output contract doesn't) |
| `app.py:2023` (retranscribe: `kind=t.kind or "meeting"`) | blindly copies old kind forward | copies **status-aware**: override carries forward unchanged; auto-classified (`success`/`uncertain`) triggers a fresh classification job against the new transcript's corrected text instead of copying the old value (decision 9) | **changed** |
| `app.py:2352` (`if t.kind == "voice_note": block summary`) | blocks summary for voice notes | same check against **accepted** kind; while pending, `t.kind` isn't `voice_note` yet so summary stays available (no safety concern here, unlike voice-match/rediarize) | **preserved** |
| `app.py:2394-2397` (reformat: dictation-only, voice_note gets its own message) | blocks non-dictation | same check against accepted kind; blocked while pending (unlocks once classified, not a safety issue, just not applicable yet) | **preserved** |
| `app.py:2511-2514` (rediarize: blocks dictation/voice_note) | blocks non-meeting kinds | blocked while pending/uncertain/failed **and** while accepted kind is dictation/voice_note (decision 8); additionally requires decision 1's pre-pass having found the recording diarization-eligible | **changed** (stricter: adds the pending-block and the pre-pass condition) |
| `app.py:2544-2547` (voice-match: blocks dictation/voice_note) | blocks non-meeting kinds | same as rediarize above, minus the pre-pass condition (voice-match doesn't depend on diarization method) | **changed** |
| `app.py:2696` (voice-note chain rerun: requires kind == voice_note) | blocks non-voice-note | same check against accepted kind; blocked while pending | **preserved** |
| `services/llm_jobs.py:182` (`enqueue_auto_classify`: dictation-only) | no-ops for non-dictation | same check against accepted kind; naturally no-ops during pending (this IS `classify_intent`'s dictation-only enqueue guard, distinct from the new pipeline classifier of decision 2 — do not conflate) | **preserved** |
| `services/llm_jobs.py:203` (`enqueue_auto_voice_note`: voice_note-only) | no-ops for non-voice-note | same check against accepted kind; must be triggered retroactively once a pending transcript resolves to `voice_note` (paired with `app.py:1269-1277` above) | **changed** (retroactive trigger added) |
| `services/queue.py:565` (chunked finalization: voice_note branch) | same voice_note check as `app.py:1269-1277`, for the chunked-completion path | same retroactive-trigger requirement, chunked path — #267 item 3's "no duplicate enqueue across inline/chunked" applies here | **changed** |
| `services/transcription.py:187,221` (kind-specific summary/prompt selection) | branches on `transcript.kind` | branches on accepted kind; template selection logic itself unchanged | **preserved** |
| `services/reformatting.py:88` (`classify_intent`) | dictation-only reformat-tab hint, unrelated to routing | **untouched** — explicitly out of scope, do not rename or merge with decision 2's classifier | **preserved, unrelated** |
| `static/rack.js:1714,1727-1731,1811,4207,4246,4275,4379-4404` | mode/speaker controls, detail label, kind toggle | frontend mirror of the predicates above; owned by #269's implementation, not re-derived here | **changed** (#269's scope) |
| `app.py:2807` (`/api/diarize` standalone) | no guard — transcript-less, so `effective_kind` has nothing to read; diarization runs unconditionally | additionally requires decision 1's pre-pass (no kind check — no transcript to check; explicit request does not override, same reading as rediarize row 9) | **changed** (pre-pass added, #417) |

## Amendment 2026-08-15 (#416 / PR #418): row 2's kind veto is retained

Row 2 above originally read "decoupled from kind entirely", with
`diarize = prepass.eligible`. The implementation deliberately does not do that.
Recording why here, because the doc otherwise describes behavior the code does
not have.

The pre-pass may use only duration and silence/pause pattern (decision 1 rules
out transcript text and ML). Those signals cannot distinguish a dictation's
thinking-pauses from a meeting's conversational turn-taking — both are
speech-pause-speech, and silence detection cannot see whether the speaker
changed across a pause. So a pre-pass permissive enough not to reject real
meetings is also permissive enough to pass an ordinary dictation. Dropping the
kind veto would have made a dictation start diarizing, which it never did
before: a looser gate, not the tighter one this design intends.

What ships instead is both conditions ANDed, so the gate is strictly tighter
than the pre-#416 one and never looser. This is a narrower deviation than it
reads as: `kind == "auto"` is resolved to `"meeting"` before this guard, so the
kind veto can only fire on an **explicit** single-speaker choice, which decision
5 says to honor. Kind-as-classification is no longer a factor here, as row 2
intended; kind-as-explicit-user-intent still is.

`voice_dump` is included in the veto. It postdates this document by one day
(doc `92ea93a` 2026-08-01, `voice_dump` `f28e254` 2026-08-02) and appears
nowhere in the original text, so its treatment is stated rather than inherited.

Row 9 (rediarize) shipped as written: kind veto retained, pre-pass ANDed on
top, and its "additionally requires" wording read as admitting no override for
an explicit rediarize request. Row 10 (voice-match) is unchanged and stays
exempt from the pre-pass.

Revisit if a cheap speaker-change signal ever becomes available (a fast VAD, a
lightweight embedding pass). Row 2 as originally written becomes achievable
then, and the kind veto can come out.

`/api/diarize` was a third diarization entry point with no kind or eligibility
guard; #417 adds decision 1's pre-pass to it (no kind guard — transcript-less,
so not expressible) and adds its row to the table above.

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
