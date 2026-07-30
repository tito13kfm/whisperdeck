# Owner identity: brainstorm and pressure-test

> Companion to `docs/plans/06-user-identity-context.md` (the draft plan). This document widens the
> solution space on each major decision, argues tradeoffs honestly, and recommends one option per
> decision. It does not replace the plan; treat disagreements below as things to resolve before
> Phase 1 starts, not as a rewrite.

## 1. Owner designation: where does "this is me" live

The plan stores `owner_voice_profile_id` as a scalar in `User.settings` (same JSON blob as `hf_token`,
`export_directory`). Three real alternatives:

| Option | Where it lives | Pro | Con |
|---|---|---|---|
| A. Settings scalar (plan's choice) | `User.settings["owner_voice_profile_id"]` | No migration, reuses the existing settings PATCH flow, matches how `hf_token` and friends already work | No referential integrity: deleting the referenced `VoiceProfile` leaves a dangling id. The plan's own Open Questions section already flags this as unresolved. |
| B. `is_owner` boolean on `VoiceProfile` | New column via `ensure_columns()` (same pattern as `queue_dismissed`, `corrected_text`) | Colocated with the roster row it describes. Deleting the profile deletes the flag with it, so dangling references are structurally impossible, not just guarded against. A partial unique index (`WHERE is_owner = 1`) enforces "at most one owner" at the schema level. | Needs a migration. Setting a new owner requires clearing the old row's flag first (two writes in one transaction, not hard). |
| C. Dedicated `Owner` model | New table, one row per user, FK to `VoiceProfile` | None over B for this app's shape | Pure overhead: `User` is already one row per account and `VoiceProfile.user_id` is already scoped per account, so a separate table buys nothing a boolean column doesn't already give. |

**Recommendation: B, `is_owner` on `VoiceProfile`.** The plan's Phase 1 checklist already has to write
delete-guard logic for option A ("guard or clear the setting when the deleted profile is the current
owner"). Option B removes that requirement and the open question entirely, by construction, because
there's nothing left to dangle once the row is gone. The migration cost is small and this project
already does this exact kind of additive-column migration routinely (`database/__init__.py`'s
`ensure_columns()`). If the settings-scalar approach is kept for speed, at minimum resolve the open
question before Phase 1 ships rather than leaving it for later.

**Multi-user / multi-install:** one owner per account is correct, not one owner per install. `User` rows
are already independent accounts (own password, own `VoiceProfile.user_id` scope), and WhisperDeck has no
concept of an "install" shared across multiple logged-in users, so "per install" doesn't apply here. A
literal shared-family-account use case (two people logging into the same `User` row and each wanting to
be "me") is out of scope and should stay out of scope; flag it only if it comes up in practice.

## 2. Auto-identify in meetings

### The two paths

- **Live-stereo:** free. `diarization.py`'s `diarize_live_stereo` labels every mic-channel utterance
  `"You"` by channel separation, not voice matching. Zero marginal CPU cost, zero false-accept risk
  (it's a hardware fact, not an inference).
- **Voice-matching (uploaded / pyannote meetings):** the hard half. Needs an embedding extraction per
  candidate segment, run through `voice_id_service.identify()` against the owner profile.

### Threshold and the false-accept/false-reject asymmetry

The plan proposes a stricter combined bar for owner auto-identify (higher cosine similarity, ~0.75-0.8,
plus the existing coverage/margin confidence from `combine_with_transcript`) than the manual
`voice_match` job's flat `threshold=0.65`. Agreed, for the reason the plan gives: a false accept here
doesn't just mislabel a meeting participant (cheap, fixed by relabel/undo), it attributes someone else's
words to the account holder in a personal index. Favor false-reject.

One thing to add beyond a stricter number: **gate on profile maturity, not just per-match confidence.**
A one-clip owner profile produces a noisy mean embedding; the risk of a false accept is highest exactly
when the profile is newest. Recommend requiring a minimum `sample_count` (2-3 clips) on the owner
profile before auto-identify runs at all, in addition to the stricter similarity bar. Below that count,
skip auto-identify silently (same "no-op" shape the plan already uses for backend `"none"`).

### Cost shape: per-segment vs per-speaker-label

The existing `voice_match` job (`services/llm_jobs.py`) runs `identify()` once per *segment*: extract a
clip, embed it, compare, repeat, for every line in the transcript. That's the right design for
full-roster matching (a speaker's label can genuinely interleave across diarization turns), but for an
**owner-only** pass it's needless work: there are only as many distinct diarization speaker labels in a
meeting as there are speakers (typically 2-6), not as many as there are segments (can be hundreds).

Recommend the owner-scoped variant concatenate all segments sharing a diarization label into one clip
and run `identify()` once per distinct label, not once per segment. This cuts the CPU-bound extraction
count by roughly the average segments-per-speaker factor, directly easing pressure on the
`CPU_KINDS` pool (capped at 1 concurrent job, shared with `rediarize`) that the plan is already worried
about. It also produces a longer, less noisy clip per comparison, which helps the false-accept problem
at the same time as the CPU problem.

### When to run it

| Option | Behavior | Pro | Con |
|---|---|---|---|
| Automatic, every meeting (opt-out) | Runs after every diarization completion | Best coverage, matches the "auto-identify across meetings" pitch | Worst CPU and false-accept exposure; also increases how often *other* participants' audio gets embedded and compared, even transiently, without an explicit user action triggering it each time |
| Opt-in setting, default off (plan's choice) | User flips `auto_identify_owner` on once | Lowest risk, user has made a conscious choice | Discovery problem: most users won't find the toggle; a good feature nobody enables provides zero value. Needs to be surfaced well at the "Mark as me" moment, not buried in a settings panel. |
| On-demand only, per meeting | Reuses the existing manual "Match against voice roster" button, no scheduling change | No new CPU-pool contention at all | Defeats the point: the whole ask is "automatic across meetings," not "one more manual click" |
| Lazy, triggered by an owner-scoped query | Only run when a "what did I say" query actually touches an unmatched meeting | Skips cost for meetings that are never queried | Adds latency to the first personal-layer query on a cold meeting; adds a second call site for "maybe auto-identify" outside diarization completion, which cuts against the Complement Rule's point about not missing sibling entry points |

**Recommendation: opt-in, default off**, matching the plan, but for two reasons instead of one: CPU cost
(the plan's stated reason) *and* consent/exposure (an automatic pass touches every participant's audio
more often than a manual one would, even though it persists nothing new about non-owner speakers).
Revisit the default only once there's real data on false-accept rate and CPU backlog.

## 3. Voice-note reinforcement loop: the guards are the whole point

The plan proposes: verify each completed voice note against the current owner print before adding it as
a clip; auto-add only if it passes; otherwise ask the user. This is broadly right, but the deepest risk
in this design is one the plan doesn't quite name: **the guard checks against the same rolling average
it's about to update.**

If the current owner embedding has already drifted a small amount (say, one borderline clip snuck in
under the threshold weeks ago), the "verify against the current print" check is validated against a
target that has already moved. A verification step built this way can catch a gross mismatch (a
completely different voice) but cannot detect gradual drift, because gradual drift is, by definition,
a series of small moves that each look fine relative to the last position. This is the actual failure
mode to design against, not just "one bad clip gets in."

**Recommendation: verify new candidate clips against a frozen seed embedding, not the rolling average.**
Concretely: the clips created by the explicit Phase 1 "mark as me" enrollment (and only those) are
"seed" clips, permanently exempt from the rolling cap and never displaced. Reinforcement candidates are
checked against the mean of the seed clips specifically (a small, fixed, human-confirmed reference),
not against `VoiceProfile.embedding` (which is the mean of everything, seed plus every auto-added clip
so far). The rolling average used for meeting matching can still be the full mean (seed + reinforced),
but the *gate* that decides "is this candidate safe to add" should not be defined in terms of the thing
it is updating. This requires distinguishing seed clips from auto-added clips in the schema (a boolean
or a `source` field on `VoiceClip`), which the plan already needs anyway to mark auto-added clips in the
UI ("auto" tag) and to know which ones the rolling cap is allowed to drop.

Other guard refinements worth adding to the plan's list:

- **Probation period.** The first few auto-add candidates after enrollment are exactly when the seed
  embedding is noisiest (built from one manual clip). Require explicit confirm for the first N
  (say, 3) candidate clips even if they pass the threshold, then switch to automatic. This is a cheap
  addition on top of the plan's existing bootstrap logic, which correctly refuses to reinforce until an
  owner exists at all, but doesn't slow down once it starts.
- **Velocity limit, not just a per-clip gate.** Cap auto-added clips per day/week regardless of
  individual pass rate. A run of borderline-but-passing notes (a cold, a bad mic day, a new headset)
  shouldn't be able to push several clips through before a user notices something is off; a simple
  circuit breaker (e.g. no more than 2 auto-added clips per day) bounds the damage of any single bad
  stretch even if every individual check passes.
- **Duration floor, named explicitly.** The plan gestures at skipping "a two-second remind-me-to-call-Bob"
  clip but doesn't set a number. Recommend a concrete minimum (roughly 3-5 seconds of actual speech,
  distinct from the existing 30-second upper cap already applied in `_embed_speechbrain` /
  `_embed_pyannote`). Below that, skip the reinforcement step silently; still create the voice note
  normally.

**Automatic-with-guards vs confirm-each, restated:** given the frozen-seed verification and the
probation period above, automatic-with-guards is defensible. Without the frozen-seed fix, automatic
is genuinely risky in a way confirm-each is not, because confirm-each puts a human in the loop at
exactly the point a self-referential guard would fail silently. If the frozen-seed change is dropped
from scope for time reasons, fall back to confirm-each rather than shipping automatic-with-guards
against a rolling-average check.

## 4. The personal layer: how "what's mine" gets computed

| Option | Mechanism | Pro | Con |
|---|---|---|---|
| A. Derive-on-read (plan's choice) | `services/identity.py` filters `segment["speaker"]` against the owner's name / `"You"` at query time | Fully additive and fully retroactive: works on every existing transcript the moment an owner is designated, no backfill, and it can never go stale because there's nothing stored to go stale. Re-designating who "the owner" is takes effect on the next query with no cleanup. | Python-side scan over segments JSON at query time, same cost shape `search_transcripts` already has for `matching_segments`. Not a new anti-pattern, but a ceiling exists for very large personal libraries. |
| B. Stored owner flag on segments or a normalized utterance table | Write `speaker_is_owner: true` into segments JSON at diarization/relabel time, or a separate indexed table (mirroring how `transcripts_fts` already denormalizes `segment_text`, per the recent FTS perf work) | O(1) filtered queries, indexable | Needs active invalidation on every relabel, re-diarize, and owner re-designation, on top of the invalidation `record_relabel`/`clear_relabel_history` already do. A stale stored flag silently claims old content is "mine" after the user changes who the owner is. Also either needs a backfill of every past transcript (undoing the "fully additive" property) or has to coexist with derive-on-read for old rows anyway, which is two mechanisms instead of one. |

**Recommendation: A now, revisit B only if it gets slow in practice.** This mirrors the project's own
history with FTS population (ship the correct, additive version first; add the indexed, denormalized
version later once real perf data justifies it, as happened with `populate_fts`'s anti-join
optimization). Don't pre-optimize a query pattern that doesn't have a measured problem yet, especially
when the "correct without invalidation bugs" property is worth more here than raw speed.

**Assistant and search should call `services/identity.py`, not reimplement its predicate.** The plan's
approach (an `owner_only` param threaded through `search_transcripts` / `search_transcripts_snippets`
and the assistant's `search` action, both routed through `identity.py`) is right. The alternative
(assistant and search each independently deciding "is this segment the owner's") would duplicate the
"You" / owner-name matching logic in two places that would inevitably drift apart. One centralizing
module is the correct shape.

One gap worth closing: `diarization.py`'s `combine_with_transcript` already computes a per-segment
`speaker_confidence` (coverage times margin). The plan's `my_utterances` spec doesn't mention forwarding
it. A personal index built on top of imperfect diarization should carry that uncertainty through, so the
assistant can say "probably you, but this line had a low-confidence speaker label" instead of treating
every owner-labeled segment as equally certain. Cheap to add (the field already exists on the segment),
easy to forget since it's not called out anywhere in the plan's touchpoints.

## 5. Privacy and security

`services/security.py` only encrypts provider API keys today (`encrypt_api_key`/`decrypt_api_key`).
`VoiceProfile.embedding` and `VoiceClip.embedding` are plain JSON floats in SQLite; enrolled clip audio
is plain files under `data/voices/`. This plan doesn't create that gap, but it does raise the stakes:
it's what turns the owner's voiceprint into, in the plan's own words, "the key that unlocks a
cross-meeting what-I-said index."

**Out of scope for this plan, correctly flagged, not fixed here:** general at-rest encryption of every
enrolled speaker's embeddings and audio. That's a cross-cutting change (affects the whole roster, not
just the owner), needs its own key-management story beyond the per-session-derived Fernet key used for
API keys today, and needs a re-encryption path for existing rows. Bundling it into this feature would
blow the scope of a wiring plan.

**Cheaply in scope, worth doing as part of this plan:**
- The new fields this plan adds (`owner_verified`, `owner_match_confidence` on `VoiceNote`, and the
  relabel history entries an auto-identify pass writes) are themselves a new sensitive signal: a log of
  how much the system trusts a given recording is genuinely the account holder. Confirm the new
  `GET /api/me/activity` endpoint (and the settings PATCH for `owner_voice_profile_id`) are scoped to
  `current_user.id` with the same rigor as every other endpoint in this codebase (the plan's own testing
  considerations already call this out, good).
- No new gap is introduced by the `save_markdown` assistant action writing an owner-scoped digest to
  `export_directory` as plaintext; that's the same plaintext-on-disk exposure every export already has
  today. Worth a one-line note in user-facing docs that a "just mine" export carries the same exposure
  as any other export, not a new one.

## 6. Consent: recording and voiceprinting other meeting participants

The plan's stance: this feature only strengthens attribution of the owner's own speech; it doesn't
fingerprint other participants beyond what the existing manual `voice_match` feature already does. That
holds up on inspection: an owner-scoped auto-identify pass still has to embed and compare *every*
speaker's audio in order to determine who is *not* the owner, so it touches the same data the existing
manual voice-match button already touches. No new category of exposure is created.

What does change: automatic (vs manual, deliberately clicked) execution changes the *frequency* of that
touch, not its kind. If auto-identify defaults to on for every meeting, other participants' audio gets
embedded and compared far more often than it would from a user occasionally clicking "match against
roster." That's a second, independent reason (beyond CPU cost) to keep `auto_identify_owner` opt-in
rather than opt-out (see section 2).

**Recommendation:** don't build a consent-management system (per-participant opt-in tracking,
redaction workflows), clearly disproportionate for a self-hosted personal tool. Do add a one-line,
one-time in-product reminder ("recording others may require their consent depending on your
jurisdiction") at the natural moment this plan already creates: the "Mark as me" onboarding flow in
Phase 1. This is a product-wide gap that predates this plan, but this plan is what makes the stakes
of recording other people newly visible to the user (via the new "not verified" and owner-scoped UI),
so it's a good, cheap place to finally say something about it, without inventing a whole new system.

## User-intent framing

**Real jobs this is meant to unlock:** "what did I say this week," "what did I commit to across my
meetings," "find that voice note where I said X," "summarize just my side of a meeting." All of these
are first-person, cross-transcript questions that today require the user to manually reread every
transcript, because search and the assistant can find content but not content attributed to a
specific person.

**Assumptions the plan makes silently, worth naming out loud:**

- **The account holder is always the device operator.** Live-stereo `"You"` labeling and the
  voice-note "assume the owner" default both rely on this: one person, one laptop, one mic. It holds
  for WhisperDeck's actual usage pattern (a personal, local-first tool) but would break if someone else
  ever borrowed the device to record their own meeting or note under the account holder's login. The
  plan's voice-note "not verified" badge is a partial check against exactly this case; there's no
  equivalent check for meetings recorded by someone else on the owner's device (a live-stereo capture
  by a borrower would still get labeled `"You"` even though it isn't the account holder).
- **Diarization confidence isn't carried into the personal-index answer** (see section 4's gap on
  `speaker_confidence`). The plan implicitly treats every owner-labeled segment as equally trustworthy,
  when the underlying diarization already knows some labels are shakier than others.
- **Per-item action-item attribution isn't solved, and the plan says so.** `Summary.action_items` is
  LLM-derived from the whole transcript with no speaker tag per item, so "what I personally committed
  to" can only be answered at the coarser "meetings where I have attributed utterances" level until a
  separate change teaches the summary prompt to tag each item's speaker. Agreed with the plan's own
  framing here; nothing to add beyond confirming the coarser cut is an acceptable v1.

## Risks and failure modes, and how to detect them

| Risk | Detection signal |
|---|---|
| Owner auto-identify false-accepts a participant | Track how often an auto-identify relabel gets manually undone via the existing relabel-undo endpoint; a spike means the threshold is too loose |
| Owner print drift from reinforcement | Periodically compare the current rolling-average embedding against the frozen seed's; alert if cosine distance crosses a threshold ("your voiceprint has changed a lot recently, review recent samples") |
| CPU starvation | Auto-identify shares the `CPU_KINDS` pool (cap 1) with `rediarize`; a backlog of either can silently delay the other for hours. Surface queue depth / age of the oldest pending CPU-bound job somewhere visible, not just in job-by-job status. |
| Bootstrap gap: notes/meetings recorded before the owner enrolled | Meetings self-heal (derive-on-read means a later manual or auto voice-match pass retroactively attributes them). Voice notes do not self-heal automatically; confirm that re-running the `voice_note` LLM job also re-runs the owner-verification check, so an old note can be retroactively verified by clicking rerun. This isn't explicit in the plan's touchpoints and is worth a specific test. |
| Silent no-ops going unnoticed | Backend `"none"`, no owner profile, below-threshold match: all correctly degrade to no-op per the plan. Worth confirming the UI actually surfaces *why* nothing happened (e.g. on the roster page) rather than the feature just quietly doing nothing forever. |

## Recommended MVP slice vs later phases

Live-stereo owner attribution is nearly free: no threshold tuning, no false-accept risk, no CPU cost,
works today off existing `diarize_live_stereo` output. Voice-matching for uploaded/pyannote meetings is
the harder, riskier half: threshold tuning, CPU contention, false-accept exposure, and a reinforcement
loop that can poison itself if not guarded carefully.

**MVP (smallest slice that proves the value):**
1. Owner designation (Phase 1 as written, ideally with the `is_owner` flag from section 1).
2. A personal layer limited to what's free and safe today: `my_utterances` covering only
   `live_stereo` `"You"` segments, plus voice notes under the existing "assume the owner" default (no
   reinforcement loop yet). No voice-matching involved at all.
3. Assistant/search `owner_only` support wired to that limited slice.

This proves "what did I say this week" end-to-end for anyone using live capture, at zero marginal risk,
before any money is spent on the harder half. It's a reordering relative to the plan's own phase
sequence (which builds the full personal layer last, in Phase 4, after the riskier voice-match
auto-identify in Phase 2): pulling a thin slice of Phase 4 forward, right after Phase 1, lets real usage
validate the concept before committing to threshold tuning and the reinforcement loop's guard design.

**Later phases**, in roughly the plan's existing order, with the refinements from this document folded
in:
- Phase 2: voice-match auto-identify for uploaded/pyannote meetings, per-speaker-label rather than
  per-segment matching, gated on owner-profile maturity (`sample_count` minimum) as well as the
  stricter similarity threshold.
- Phase 3 / 3b: voice-note verification plus the reinforcement loop, with frozen-seed verification and
  a probation period on the first few auto-add candidates.
- Phase 4 (remainder): extend the personal layer to include voice-matched meetings once thresholds are
  validated against real false-accept reports.

## Decisions needed from the human

1. `is_owner` flag on `VoiceProfile` (recommended here) vs the plan's settings-scalar
   `owner_voice_profile_id`? Bigger diff, but resolves the dangling-reference open question by
   construction instead of by a guard.
2. Ship the thin live-stereo-only personal layer right after Phase 1, ahead of voice-match
   auto-identify, or keep the plan's original phase order (full personal layer last)?
3. Confirm the owner auto-identify threshold starting point (~0.75-0.8 combined with coverage/margin):
   this can only be tuned against real false-accept reports, not decided from code alone.
4. Reinforcement guard design: automatic-with-guards using a frozen-seed verification and a probation
   period (recommended here), or confirm-each for every candidate clip if the frozen-seed change doesn't
   make the cut?
5. Confirm single owner per account is sufficient; no shared/family-account "multiple me" use case is
   anticipated.
6. Bundle a one-line consent reminder into the Phase 1 "Mark as me" UI moment, or defer entirely to a
   separate, product-wide pass?
7. Encryption-at-rest for voice data: confirmed out of scope for this plan, or is a narrower first step
   (e.g. encrypting only the owner profile's embedding, given it's the highest-value target) wanted now?
