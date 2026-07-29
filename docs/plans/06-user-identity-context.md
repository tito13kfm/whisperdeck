# Owner identity: tying meeting content and voice notes to "me"

> One-line status: Draft plan. WhisperDeck-native feature; personalizes the Blinko-inspired assistant / daily-review / semantic-search plans.

## Motivation

WhisperDeck already has the pieces to know who a speaker is, but nowhere does it know which speaker is the *account holder*. `services/diarization.py` labels meeting speakers as `Speaker 1` / `SPEAKER_00` (or, for live captures, the mic-channel label `"You"`). `services/voice_id.py` can match a voice against an enrolled roster and rename a segment via `services/relabel.py`. `services/voice_notes.py` turns a single-speaker recording into a structured note. None of these treat any one profile as special. The result: there is no reliable way to ask "what did I say across all my meetings" or "what did I commit to", the assistant (`services/assistant.py`) and search (`services/search.py`) can find *content*, but not *content attributed to the user specifically*, and voice notes carry no attribution at all beyond the account they were uploaded under.

This plan wires existing primitives together around one new concept, a designated "owner" voice profile, rather than building new inference. It also makes each meeting/voice-note's connection to "me" explicit enough that the assistant, search, and the daily-review plan can build a genuine personal layer on top.

## How it relates to existing features (attribution where due)

This is a WhisperDeck-native feature, not adapted from Blinko. It personalizes three Blinko-inspired plans that live alongside it in `docs/plans/`:

- `docs/plans/01-semantic-rag-search.md`, semantic search gains an owner-scoped mode ("just what I said") once owner attribution exists.
- `docs/plans/03-daily-review-recall.md`, "what I committed to" resurfacing depends on knowing which content is the owner's.
- `services/assistant.py`'s natural-language search/summarize actions inherit first-person queries directly.

No Blinko feature or docs describe an owner/identity concept to attribute here, this plan is pure wiring of WhisperDeck's own `voice_id`/`diarization`/`relabel`/`voice_notes` services.

## Proposed approach

**1. Designate one owner profile.** Reuse the existing Voice roster (`VoiceProfile` via `services/voice_id.py`, rendered by `loadVoices()` in `static/rack.js`), the user enrolls themselves exactly like any other speaker, then flags that profile as "me." Store the designation as a per-user scalar, `owner_voice_profile_id`, in `User.settings` (the same JSON blob `services/settings.py` already uses for `hf_token`, `export_directory`, etc.) rather than a new column or table, there is exactly one owner per account, which is a settings-shaped fact, not a roster-shaped one. A "Mark as me" button on each roster card in `loadVoices()` writes this via the existing settings PATCH flow.

**2. Auto-identify the owner in diarized meetings.**
- **Live-stereo captures already solve this for free.** `diarization.py`'s `diarize_live_stereo` labels every mic-channel utterance `"You"` by construction (channel separation, not voice matching), for `diarization_method == "live_stereo"` transcripts, `speaker == "You"` *is* the owner's segments, no `voice_id` lookup needed or wanted.
- **Everything else needs voice matching.** Uploaded meeting audio diarized via pyannote or the pause-gap heuristic only produces generic labels (`SPEAKER_00`, `Speaker 1`). The existing `voice_match` `LlmJob` (`services/llm_jobs.py`, `VALID_KINDS`/`CPU_KINDS`) already does per-segment `voice_id_service.identify()` and relabels matching segments with a roster name, today it's a manual "Match against voice roster" button (`static/rack.js`, `btn-voicematch`). This plan adds an *automatic*, owner-scoped variant: after diarization finishes, if the user has an owner profile with clips and a real backend (not `"none"`), check whether the owner's voice appears, and relabel just those segments, without forcing the full-roster identification pass on every meeting.
- Gate this behind a new opt-in setting, `auto_identify_owner` (default off): embedding extraction is CPU-bound work sharing the same `CPU_KINDS` pool (capped at 1 concurrent job) as `rediarize`, so it shouldn't silently run on every meeting for every user.
- Record the change through `services/relabel.py`'s `record_relabel`, exactly like manual voice-match does, so `POST /api/transcripts/{id}/relabel-undo` also undoes an auto-identify pass.
- Degrade gracefully: backend `"none"` → no-op (same message the manual `voice_match` job already returns). Below-threshold match → leave the segment's existing label alone; never guess into a low-confidence label.

**3. Voice notes: assume the owner, optionally verify.** A `voice_note`-kind transcript is single-speaker by construction and already belongs to `Transcript.user_id`, that implicit tie is enough for a default "this is you" assumption, no schema change required for the default case. What's missing is a way to *check* that assumption: run `voice_id_service.identify()` once against the whole `transcript.audio_path` (a single call, not per-segment, cheap) if an owner profile exists, and record whether it actually matched. Never block note creation on the result, a failed/low-confidence match surfaces as a soft "not verified as your voice" badge on the Voice notes board, so a genuine misattribution (wrong device, someone else picked it up) is visible instead of silently trusted.

**Voice notes as self-reinforcing enrollment.** A completed `voice_note` transcript is single-speaker and owner-authored, which makes it a free, already-labeled positive sample of the owner's voice. Feed it back to strengthen the owner profile: on voice-note completion, enqueue the note's audio as a new `VoiceClip` on the owner `VoiceProfile` and let the existing averaging path recompute the profile embedding. This is the same flow the "Enroll from transcript lines" endpoint in `app.py` already uses, `voice_id_service.add_clip()` persists the clip and calls `_recompute_profile_embedding()` internally, so there's no new embedding code, just a new trigger point. The result is a closed loop: better owner print → better meeting auto-identify (the Phase 2 hook) → better "what I said" extraction, all from normal use, no manual re-enrollment.

The guards are the whole point here, an unguarded feedback loop is how a print gets poisoned:

- **Drift protection.** Reuse the whole-file owner-verification pass proposed for voice notes above *before* reinforcing. Only add the clip if the note clears the owner threshold. If it fails, do NOT auto-add, flag it for user confirmation ("Add this to your voiceprint?") instead. Without this, a note recorded on the user's account by someone else (shared device, someone grabbed the mic) silently drags the owner print toward the wrong voice.
- **Bootstrap.** The first voice notes exist before any owner print exists to verify against, so the drift check has nothing to compare to. Chosen approach: require a one-time explicit initial enrollment (the user marks a roster profile as "me," Phase 1) before auto-reinforcement does anything, auto-add stays inert until an owner print exists. The rejected alternative (accept the first N notes as seed clips behind an "Is this you?" confirm) front-loads friction onto every new user and still needs a confirm UI; requiring one deliberate enrollment is simpler and makes the "who is me" moment explicit. Tradeoff: no reinforcement value from notes recorded before the user gets around to enrolling.
- **Quality and cap.** Skip clips that are too short or low-SNR to be a useful embedding (a two-second "remind me to call Bob" is a weak sample). Cap stored clips per owner profile as a rolling window (drop the oldest auto-added clip past the cap via the existing `remove_clip` path) so the print tracks the user's recent voice and storage stays bounded rather than growing with every note forever.
- **User control.** A setting to disable auto-reinforcement entirely (`auto_reinforce_owner`, default on once an owner exists, it's low-cost and self-correcting under the drift guard), and a way to see and remove auto-added clips. The roster clip list in `loadVoices()` (`static/rack.js`) already renders and removes clips; auto-added clips just need a visual marker (e.g. "auto" tag) so the user can tell them from manually enrolled ones.

**4. The personal layer.** Everything above exists to answer "what's mine" without re-deriving speaker matching in three different places. A new `services/identity.py` centralizes it:
- `get_owner_profile(db, user_id)`, reads `owner_voice_profile_id` off `User.settings`.
- `my_utterances(db, user_id, ...)`, segments across meeting transcripts where `speaker` equals the owner profile's `name`, or (`diarization_method == "live_stereo"` and `speaker == "You"`).
- `my_voice_notes(db, user_id, ...)`, the user's `VoiceNote` rows, annotated with `owner_match_confidence` where available.
- `my_activity(db, user_id, ...)`, the two merged into one feed, for "what I said this week" style queries.

That single source of truth then powers:
- **Assistant** (`services/assistant.py`): teach `_INTERPRETER_SYSTEM` to recognize first-person phrasing ("what did I say," "my action items") and add an `owner_only` param to the existing `search` action, routed through `services/identity.py` instead of the whole-corpus `search_transcripts`.
- **Search** (`services/search.py`): an `owner_only` filter on `search_transcripts`/`search_transcripts_snippets`, reusing the same segment-matching rule, so both keyword search and the semantic-rag plan can offer a "just mine" toggle without a second implementation.
- **Daily review** (`docs/plans/03-daily-review-recall.md`, once built): a "mine" filter on the recall panel, and a starting point for "what I committed to", see Open questions for why per-item attribution isn't fully solved by this plan alone.

## Code touchpoints (files + symbols, no line numbers)

- `services/settings.py`: `DEFAULT_SETTINGS` gains `owner_voice_profile_id` and `auto_identify_owner`.
- `services/voice_id.py`: `VoiceIdentificationService` gains an owner-scoped identify helper (reusing `_extract_embedding`/`_cosine_similarity`, stricter threshold, see Research notes) and a whole-file verification helper for voice notes. `delete_profile` needs a guard/clear for the case where the profile being deleted is the current owner.
- `services/llm_jobs.py`: the `voice_match` branch is the model to follow for the new owner-scoped auto-identify path. Per the project's Complement Rule, this must be wired into **all** diarization-completion sites, not just one: the direct-upload finalize path in `app.py` ("Run diarization if requested" block), `services/queue.py`'s `_finalize_if_done` (the chunked-upload finalize path), and the `rediarize` job kind's handler. Factor the "maybe auto-identify owner" step into one shared function all three call.
- `services/relabel.py`: `record_relabel` used for the new auto-identify kind, so relabel-undo covers it.
- `services/voice_notes.py`: add the optional whole-file owner-verification call alongside `run_voice_note_chain`, storing the result on the `VoiceNote` row.
- `services/assistant.py`: `_INTERPRETER_SYSTEM`, `_SUPPORTED_ACTIONS`, and `execute_plan`'s `search` branch.
- `services/search.py`: `search_transcripts`, `search_transcripts_snippets`.
- `services/identity.py` (new): `get_owner_profile`, `my_utterances`, `my_voice_notes`, `my_activity`.
- `app.py`: `GET /api/voices` payload gains an owner flag; existing settings PATCH endpoint accepts the two new keys; new `GET /api/me/activity` (or similar) backing the personal layer.
- `static/rack.js`: `loadVoices()` gets a "Mark as me" control per roster card; the Voice notes board gets the "not verified" badge; the assistant page and search UI get a "mine" filter toggle.

## Data model / schema changes

- `User.settings` (JSON, no migration needed): `owner_voice_profile_id` (nullable int), `auto_identify_owner` (bool, default `False`).
- `VoiceNote`: two additive nullable columns via `ensure_columns()` in `database/__init__.py` (the same pattern used for `Transcript.corrected_text`, `queue_dismissed`, etc.): `owner_verified` (Boolean), `owner_match_confidence` (Float).
- No changes to `VoiceProfile`, `Transcript`, or the `segments` JSON shape. Owner attribution for meeting segments is derived at query time by matching `segment["speaker"]` against the owner profile's `name` (or the literal `"You"` for `live_stereo`), not stored as a new per-segment field. This keeps the feature fully additive: no rediarization or backfill needed for transcripts that already exist.

## Research notes

- **Threshold asymmetry.** `voice_id_service.identify()` and the `voice_match` job both use a flat `threshold=0.65` cosine similarity today. For ordinary roster naming, a false accept just mislabels a meeting participant, annoying, cheaply fixed via relabel/undo. For owner identification feeding a personal "what I said" index, a false accept means someone else's words get attributed to the account holder in assistant answers and searches, a worse failure mode. This plan recommends a stricter bar for owner auto-identify specifically (e.g. requiring both a higher cosine similarity, roughly 0.75-0.8, and the existing coverage/margin confidence `combine_with_transcript` already computes per segment) rather than reusing `voice_match`'s default verbatim. There's no universally-published FAR/FRR-optimal cutoff for ECAPA-TDNN/wespeaker embeddings on arbitrary field microphones, treat this as a starting point to tune from real false-accept reports, not a settled number.
- **Favor false-reject over false-accept for the owner specifically.** A missed "you" segment just falls back to "unidentified" (low cost, user can confirm manually); a wrongly-accepted one is baked into a personal index. This is the opposite bias from typical diarization tuning, which favors coverage.
- **Storage of the owner voiceprint.** `services/security.py` only encrypts provider API keys (`encrypt_api_key`/`decrypt_api_key`) today; `VoiceProfile.embedding`/`VoiceClip.embedding` are plain JSON floats in SQLite, and enrolled clip audio sits as plain files under `data/voices/`. That exposure already exists for any enrolled speaker, but the owner profile is the highest-value target in that store, it's the profile most likely to accumulate the most clips, and the one this feature turns into the key that unlocks a cross-meeting "what I said" index. Adding encryption-at-rest is out of scope for this wiring plan, but the stakes of the existing gap go up once this ships, and it's worth flagging plainly rather than adding quietly.
- **Consent for recording other participants.** WhisperDeck already records and diarizes meeting participants without managing their consent at the product level, an existing assumption, not something this plan changes. This feature only strengthens attribution of *the owner's own* speech; it doesn't add new voice-fingerprinting of other participants beyond what the existing `voice_match` feature already opts a transcript into. A UI-level reminder that recording others may need their consent (jurisdiction-dependent) is worth having, but that's a product-wide gap, not specific to this plan.

## Open questions

- Single owner per account is baked into the design (a `User.settings` scalar, not a list). A shared/family account with multiple "me"s is out of scope; flag if it ever comes up.
- Should `auto_identify_owner` default to on once an owner profile exists, or stay opt-in? Proposal above defaults off, given the CPU cost (`CPU_KINDS` caps concurrent extraction jobs at 1) and the false-accept stakes; revisit with real usage data.
- Per-action-item speaker attribution isn't available today, `Summary.action_items` is LLM-derived from the whole transcript with no speaker tag per item. "What I committed to across meetings" needs either a summary-prompt change (pass speaker-labeled segments explicitly, ask the model to tag each item's speaker) or a coarser v1 cut ("meetings where I have attributed utterances," not "items I personally said"). This plan doesn't resolve that; it only makes the coarser cut possible.
- What happens when the user deletes the currently-designated owner profile (`DELETE /api/voices/{id}` → `voice_id_service.delete_profile`)? `owner_voice_profile_id` would dangle. Needs an explicit decision: block deletion of the current owner, or clear the setting when it's deleted.
- Should live-stereo transcripts ever run voice-id-based owner identification as a cross-check, given `"You"` labeling is already free and reliable there? Proposal above skips it entirely (no benefit, only cost); confirm before implementing.

## Rough phasing / checklist

**Phase 1: owner designation (no auto-identify yet)**
- [ ] `services/settings.py`: add `owner_voice_profile_id`, `auto_identify_owner` to `DEFAULT_SETTINGS`
- [ ] `app.py`: surface an owner flag on `GET /api/voices`; accept `owner_voice_profile_id` via the existing settings PATCH endpoint
- [ ] `services/voice_id.py` `delete_profile`: guard or clear the setting when the deleted profile is the designated owner
- [ ] `static/rack.js` `loadVoices()`: "Mark as me" control per roster card

**Phase 2: meeting auto-identify**
- [ ] `services/voice_id.py`: owner-scoped identify helper with a stricter threshold than `voice_match`'s default
- [ ] Shared "maybe auto-identify owner" hook, called from all three diarize-completion sites: `app.py` upload finalize, `services/queue.py` `_finalize_if_done`, `services/llm_jobs.py` `rediarize` handler
- [ ] Wire through `services/relabel.py` `record_relabel` so auto-identify is undoable via the existing relabel-undo endpoint
- [ ] Confirm `live_stereo` transcripts skip voice-id-based owner identify (`speaker == "You"` already sufficient)
- [ ] Unit tests: owner profile with clips + matching segments → relabeled; no owner profile → no-op; backend `"none"` → no-op; below-threshold match → segment left alone

**Phase 3: voice notes**
- [ ] `database/__init__.py`: add `owner_verified`, `owner_match_confidence` nullable columns to `VoiceNote` via `ensure_columns`
- [ ] Whole-file verification call in the voice-note chain (`services/voice_notes.py` / its `LlmJob` handler)
- [ ] `static/rack.js` Voice notes board: "not verified as your voice" badge when `owner_verified` is `False`

**Phase 3b: voice notes as self-reinforcing enrollment**
- [ ] On voice-note completion, if an owner profile exists and the note clears the drift check, add its audio as a `VoiceClip` on the owner profile via `voice_id_service.add_clip()` (embedding recompute is automatic)
- [ ] `services/settings.py`: add `auto_reinforce_owner` (default on once an owner exists)
- [ ] Quality gate (skip short / low-SNR clips) + rolling per-profile cap (drop oldest auto-added clip via `remove_clip`)
- [ ] Mark auto-added vs. manually-enrolled clips so the roster clip list in `loadVoices()` can label and remove them; surface the "add this to your voiceprint?" confirm for notes that fail the drift check

**Phase 4: personal layer**
- [ ] `services/identity.py` (new): `get_owner_profile`, `my_utterances`, `my_voice_notes`, `my_activity`
- [ ] `services/search.py`: `owner_only` param on `search_transcripts` / `search_transcripts_snippets`
- [ ] `services/assistant.py`: interpreter + `execute_plan` support for owner-scoped search ("what did I say" / "my action items")
- [ ] `app.py`: `GET /api/me/activity` (or fold into an existing bootstrap-style endpoint)
- [ ] Cross-link with `docs/plans/03-daily-review-recall.md` once built: a "mine" filter option on the recall panel

## Testing considerations

- Unit tests for `services/identity.py`: `my_utterances` correctly includes `live_stereo` `"You"` segments and voice-match-labeled segments matching the owner's name, and correctly excludes segments labeled with a *different* roster name (construct a transcript with both an owner segment and a non-owner segment, assert only the owner's is returned, per project convention, a test that would still pass if the filter were deleted proves nothing).
- Unit tests for the owner-scoped `voice_id.py` identify helper: threshold behavior (a similarity just above vs. just below the stricter owner cutoff), no owner profile enrolled, no clips on the owner profile, backend `"none"`.
- Self-reinforcement guard test: a voice note whose voice does NOT match the current owner print must NOT add a clip to the owner profile (construct a below-threshold note, assert the owner's `sample_count`/clip list is unchanged after completion, a test that passes with the drift check deleted proves nothing). Also cover: no owner profile yet → auto-add inert (bootstrap); `auto_reinforce_owner` off → no clip added; rolling cap reached → oldest auto-added clip dropped.
- Assistant interpreter tests: a first-person query routes to the `owner_only` path; a non-first-person query does not.
- Endpoint tests: `GET /api/me/activity` and the owner-designation PATCH are scoped to `current_user.id` (no cross-user leakage, recurring theme across this codebase's existing endpoint tests).
- This introduces new controls ("Mark as me" button, "not verified" badge, "mine" filter toggle) rather than renaming existing ones, so no existing e2e selectors need updating. Per the project's testing tiers, drive the new UI once manually (or via a scoped Playwright check) rather than running the full `e2e-ux-audit`/`e2e-regression-http` suite for this alone.
