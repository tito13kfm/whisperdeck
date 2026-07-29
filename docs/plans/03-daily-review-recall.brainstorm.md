# Daily Review / Recall: brainstorm

> Companion to `docs/plans/03-daily-review-recall.md`. That plan already picks a direction (dashboard panel, new `ReviewState` table, on-this-day plus recency/spacing, weighted to journal/idea). This document lays out the alternatives that plan chose between, and a few it didn't consider, so the choices are visible before anyone commits code. Nothing here is resolved. Where a recommendation is stated, it's a lean, not a decision.

## User-intent framing

The underlying bet: a user who records meetings, dictations, and voice notes accumulates content faster than they revisit it, and some of that content (a half-formed idea, a journal entry, an old planning conversation) gets more useful with distance and rereading, not less. Recall exists to counteract "out of sight, out of mind" for the reflective slice of that content, not to chase the user into rereading everything.

Assumptions worth naming, because the whole feature rests on them:

- The user wants to revisit old material at all. Some users are pure archivists: record, transcribe, search when needed, never browse. For that user, recall is at best inert, at worst clutter they have to dismiss repeatedly.
- Old content on average was not junk. If a large share of the corpus is failed transcriptions, three-second test recordings, or low-effort dictation, recall needs a quality filter or it teaches the user to ignore the panel.
- Being reminded is more often welcome than annoying. This is the difference between a feature and nagware, and it depends entirely on frequency, prominence, and how easy dismissal is (see Surface and State sections).
- The value is retrospective insight ("that idea still holds up," "I forgot I said that"), not task management. Todos and reminders are already served by whatever mechanism tracks open items; recall's job is the stuff that has no other reason to resurface.

If any of these are false for WhisperDeck's actual user base, the feature's value proposition shrinks to "occasionally cute," which changes how much engineering it deserves. Worth confirming before Phase 1, not after.

## 1. Selection algorithm

| Approach | How it works | Strength | Weakness |
|---|---|---|---|
| On-this-day date buckets | Match `created_at` month/day against today, across prior years | Zero-config, matches the mental model users already have from photo apps and journaling apps, cheap to compute | Useless until the corpus has a year of history, and even then only fires on the anniversary date, not a rotation the user can rely on daily |
| Recency and spacing rotation | Rank by `last_shown_at` ascending (never-shown first), cooldown window before repeat | Works from day one regardless of corpus age, guarantees eventual coverage of the whole backlog | Feels arbitrary to the user ("why this note, why now"), no built-in signal for what's actually worth seeing again |
| Tag or topic clustering | Group by `TranscriptTag` (issue #171's auto-tagging) and resurface "you have five notes tagged X, revisit them together" | Gives recall a narrative the other two lack, could double as a light knowledge-graph entry point | Depends entirely on tagging quality and coverage, adds a second dependency (tags must exist and be decent) before recall can ship, harder to explain in one line of UI copy than "on this day" |
| Real spaced-repetition (SM-2 style) | User rates each shown item's usefulness, interval grows or shrinks based on that rating | The only approach with an actual quality signal driving future selection, closest to "recall that adapts to you" | Needs a feedback loop that doesn't exist yet (a rating UI, per-item state beyond shown/dismissed), meaningless without weeks of ratings to train on, overbuilt for a v1 |

The existing plan combines the first two (on-this-day plus recency/spacing) and defers the fourth to a later phase, which reads as reasonable: SM-2 needs a rating signal WhisperDeck doesn't collect anywhere yet, building it just for this feature is a lot of speculative infrastructure. Tag clustering is the one alternative worth a harder look, since issue #171 tagging already exists and could turn recall into a "themes worth revisiting" surface rather than "random old thing," but it's fair to treat as a phase-2 enrichment rather than a v1 blocker.

**Avoiding junk resurfacing.** None of the four approaches above filters for quality on their own; that has to be a separate gate applied to the candidate pool regardless of which selection method wins. Concretely: `Transcript.status == "completed"` (already proposed), plus a minimum content bar, something like a `full_text`/`body` length floor and excluding transcripts with no successful segments. A three-second "test test" recording or a failed-then-abandoned transcript should never occupy one of a fixed 3-5 daily slots. The existing plan's `MIN_AGE_DAYS` filter (nothing from this week) is a good first pass but doesn't address short/empty content; worth adding a length or word-count threshold to the candidate query explicitly, not just age and status.

## 2. Content scope

**Which VoiceNote kinds are eligible.**

| Kind | Recall-worthy? | Reasoning |
|---|---|---|
| `journal` | Yes | Exactly the reflective content that gains value with distance; core use case |
| `idea` | Yes | Half-formed thoughts that might click later; core use case |
| `todo` | Probably not by default | Action items have their own lifecycle (open, done); resurfacing a stale todo in a "look back" panel reads as a nag, not an insight, and risks the user conflating recall with a task list |
| `reminder` | Probably not by default | Same shape as todo: time-bound and actionable, not reflective. A reminder that already fired or expired is arguably worse than a stale todo to resurface |
| `general` | Ambiguous | Catch-all bucket for notes that didn't classify cleanly; behavior depends on what actually ends up here in practice, worth checking real data before deciding |

The existing plan's default (weight journal/idea higher, exclude todo/reminder) matches this table. The open question is whether "exclude" should be a hard filter or just a heavy down-weight, since a user might genuinely want an old todo to resurface if it never got marked done ("did I ever call Bob back?"). That's arguably a different feature (a stale-open-items list) rather than recall, but the line is blurry enough to flag rather than assume.

**Do meeting/dictation transcripts belong in recall at all?**

| Option | Argument for | Argument against |
|---|---|---|
| Voice notes only | Recall stays a clean, single-purpose feature: "revisit your reflective notes." Simpler candidate query, simpler UI story | Narrows the feature to only users who record voice notes; a user who only does meeting transcription gets an empty panel forever |
| Include meetings and dictations, ranked lower | Gives every user something in the panel, not just voice-note users; a months-old meeting can be genuinely useful to reread (what did we decide) | Meetings are long and multi-speaker; a snippet-based card doesn't summarize well without a `Summary` row, and reviewing a 45-minute meeting transcript is a much bigger ask than rereading a 30-second journal note, "revisit this" means something different for each |

The existing plan includes both, weighted toward voice notes. That's a reasonable default but worth stress-testing against actual card design: a meeting card probably needs to surface its `Summary.short_summary` (if one exists) rather than a raw text snippet, or it will look broken next to a voice-note card. If summaries aren't reliably present (they're a separate LLM job that may not have run), a meeting card with no snippet source is a real gap, not just a design nit.

## 3. Surface

| Option | Prominence | Cost | Risk |
|---|---|---|---|
| Dashboard panel (existing plan) | Medium; seen on every Monitor visit, one section among stats and recents | Low; reuses `loadDashboard()`'s existing render pass and `/api/bootstrap` cache | Competes for attention with `dash-stats` and `dash-recents`; if it's always populated with the same weak candidates, it becomes wallpaper the user learns to skip |
| Dedicated page (`page-recall`) | Low by default (user has to navigate to it); high once there, room for filters and history | Medium; new nav rail entry, own load function, more surface to test | Opt-in by construction, which may undercut the "resurface without being asked" premise, a page nobody navigates to resurfaces nothing |
| Daily push or OS notification | Highest; reaches the user without them opening the app at all | Highest; no notification channel exists in WhisperDeck today (browser push, email, or an OS-level mechanism would all be new infrastructure), plus a background scheduler tick | Real nagware risk if cadence or relevance is off; notifications are the fastest way to make a feature feel like an intrusion instead of a gift, and once disabled by a user they rarely get re-enabled |

The existing plan's dashboard-panel choice is the sensible middle ground: visible without requiring a new nav destination or new delivery infrastructure, contained within a page the user already visits to check status. The main design risk to flag is panel fatigue, if the panel always shows something (even weak candidates, to avoid an empty look), users will stop reading it within a couple of weeks. Worth deciding up front whether an honestly-empty panel ("nothing to review today") is acceptable, versus always padding to a fixed count.

## 4. State: ReviewState table vs. derive-on-read

| Approach | What it needs | Tradeoff |
|---|---|---|
| New `ReviewState` table (existing plan) | One row per user+transcript: `last_shown_at`, `shown_count`, `dismissed`, `created_at` | Explicit, queryable, supports "don't show again" as a real flag and "shown count" as a real counter. Extra table to maintain, migrate, and keep in sync (a row must exist before it can be read, so first-touch semantics need an upsert) |
| Derive purely from timestamps already on `Transcript`/`VoiceNote` | No new table; infer "not recently shown" from `created_at` age alone | Zero schema footprint | Cannot represent "the user explicitly dismissed this" at all, `created_at` age tells you nothing about whether the user already saw and rejected an item. This only works if recall never needs a dismiss/snooze action, which contradicts the plan's own proposed UI (Not now / Don't show again) |

Derive-on-read is really only viable if recall drops per-item state entirely (always show whatever the algorithm currently ranks highest, no memory of past shows). That's a valid, much simpler MVP shape worth considering on its own merits, not just as the table's poor cousin: it removes an entire table, an entire set of endpoints, and the "no cross-user leakage" test surface that comes with it. The cost is that the same item can resurface the next day if nothing else outranks it, which is exactly the repetition the spacing logic exists to prevent. A new table earns its cost specifically because dismiss/snooze are stated goals; if those turned out to be lower priority than shipping something, derive-on-read is the fallback worth remembering, not just a rejected alternative.

**Dismiss vs. snooze semantics**, assuming the table ships:

- Snooze ("Not now"): pushes `last_shown_at` forward so the spacing cooldown reapplies, item can come back later. Low commitment, matches "not today" without judging the content.
- Dismiss ("Don't show again"): sets `dismissed = true`, permanent exclusion (per the existing plan's lean). Two things worth separating that the current plan collapses into one flag: dismissing because "I've seen this enough" (positive, already internalized) versus dismissing because "this was junk, never surface it" (negative, a data-quality signal). A single boolean can't distinguish those, which matters if dismiss data is ever used to improve the candidate filter (a bulk-dismissed source could tell you your quality gate is too loose). Not a blocker for v1, but worth a comment in the schema if the field is ever reused for that.
- Opening a transcript as an implicit "reviewed" signal (existing plan) is a reasonable default, but conflates "I clicked to check" with "I actually reread and reflected on this," the same ambiguity search-result clicks have everywhere. Fine to accept for v1; not something to build detection for.

## 5. Relationship to owner identity (docs/plans/06)

`docs/plans/06-user-identity-context.md` already lists a "mine" filter on the recall panel as a Phase 4 cross-link once an owner profile exists. Three concrete integration points worth considering before that lands, so recall's schema doesn't need rework later:

- **Voice notes are already owner-scoped for free.** A `voice_note`-kind transcript is single-speaker and tied to `Transcript.user_id`; 06's Phase 3 adds `owner_verified`/`owner_match_confidence` columns to `VoiceNote`. Recall could surface that verification state on a card ("not verified as your voice") the same way the Voice notes board does, at no extra cost, once those columns exist. This is a pure UI addition, not a selection-algorithm change.
- **Meeting transcripts need the personal layer to mean anything for "what I said."** Without 06's `services/identity.py` (`my_utterances`), a meeting card in recall is "a meeting you were in," not "something you specifically said." If recall's pitch is personal reflection, a meeting resurfaced today reads as "here's a meeting," full stop, the "what did I contribute" framing only becomes available once identity lands. Worth deciding whether v1 recall's meeting cards should even claim personal relevance, or just present as "an old transcript," until identity exists.
- **Sequencing.** Recall can ship fully before 06 lands (it already doesn't depend on owner identity for anything in the v1 slice), the two plans just intersect at "add an owner filter" later. No reason to block either on the other; flagging the intersection here so whoever builds the owner filter later knows to touch `services/recall.py`'s candidate query, not just the UI.

## Risks and failure modes

| Risk | Detection | Mitigation direction |
|---|---|---|
| Empty state for new users (no completed transcripts past `MIN_AGE_DAYS`, or a fresh account) | Trivial to detect: candidate pool query returns nothing | Explicit empty-state copy, not a padded-out panel with weak filler; don't lower the age/quality bar just to fill three slots |
| Staleness (the panel always shows the same handful of items, or clearly stops rotating) | Harder to detect automatically; would need `shown_count` distribution monitoring, which nobody's proposed building for v1 | Spacing/rotation logic exists specifically for this; worth a manual spot-check after Phase 1 ships (query `ReviewState` for outliers with a high `shown_count`) rather than instrumenting it up front |
| Creepiness (resurfacing something the user finds unwelcome to be reminded of, an old journal entry from a bad week, a private idea they'd rather forget) | Not detectable programmatically at all; this is a judgment call about tone and control | Dismiss must be fast and final (one click, no confirm dialog friction), and should feel like the user is in control of what comes back, not the app deciding for them. Worth keeping the "reason" string (existing plan's idea) honest and low-key rather than falsely cheerful copy ("Remember this?") that reads as tone-deaf against sensitive content |
| Nagware perception (frequency, prominence, or repetition feels like being hassled) | Qualitative; watch for high dismiss-without-open rates once there's usage data | Keep it pull-based (visit the dashboard) rather than push for v1, per the existing plan; this is the single biggest lever against nagware and is already the proposed default |
| Junk resurfacing (short, empty, or failed content taking one of the daily slots) | Directly detectable in the candidate query (length/status filters either present or absent) | Add an explicit content-quality floor to the candidate pool, not just age and status, see Selection algorithm section above |

## Recommended MVP slice

Given the above, a defensible v1 cut:

- Candidate pool: voice notes only (`journal`/`idea`, `todo`/`reminder` excluded), `status == "completed"`, `MIN_AGE_DAYS` floor, plus a minimum body-length floor to keep junk out. Meeting/dictation transcripts deferred to a later phase rather than included-but-ranked-low, so the panel's story stays simple ("your reflective notes") and doesn't need a `Summary`-snippet fallback path designed on day one.
- Selection: recency/spacing rotation only for v1 (on-this-day added once the corpus has enough history to matter, likely a low-cost follow-up once the base rotation ships, not a bigger lift).
- State: the `ReviewState` table as proposed, since dismiss/snooze are core to the value proposition (user control against nagware), not a nice-to-have.
- Surface: dashboard panel, honestly empty when there's nothing good to show, no padding with weak candidates just to avoid a blank section.
- Owner scoping: skip for v1, revisit once `docs/plans/06` lands.

This trims the existing plan's scope by deferring meeting/dictation inclusion and on-this-day bucketing, both of which add real complexity (snippet source for meetings, calendar-boundary handling for on-this-day) for a payoff that's speculative until there's a working rotation to compare against.

## Later phases (not v1)

- Add meeting/dictation transcripts to the candidate pool once there's a clear card design for them (likely needs `Summary.short_summary` as the preview source, with a fallback for transcripts never summarized).
- Add on-this-day bucketing once the corpus has enough history for it to fire regularly; handle the timezone slop the existing plan flags (naive UTC `created_at` vs. local calendar day).
- Tag/topic clustering as a second selection lane, once issue #171 tagging coverage and quality are known to be good enough to build a UI narrative on top of.
- Owner-scoped "mine" filter, once `docs/plans/06` ships its personal layer.
- Dedicated `page-recall` history view, if the dashboard panel proves popular enough to want more room (filters, past picks, undo a dismiss).
- Push/digest delivery, only if pull-based engagement turns out low, and only after weighing the nagware risk explicitly, this is the last thing to build, not an early phase.
- Real spaced-repetition (recall-quality rating, growing interval), only once there's usage data on snooze-vs-dismiss ratios to justify the added complexity.

## Decisions needed from the human

- Is voice-notes-only an acceptable v1 scope, or is meeting/dictation inclusion a must-have from day one even without a settled card design?
- Hard exclude for `todo`/`reminder`, or a heavy down-weight that still allows a stale item through occasionally?
- Should the dashboard panel ever be empty, or should there always be something shown (with a looser quality bar) to avoid a blank section?
- Dismiss semantics: is "don't show again" truly forever, or should there be a very-long-term re-surfacing (a year-plus) for permanently dismissed items?
- Is the `ReviewState` table worth building for v1, or should dismiss/snooze be deferred and v1 ship as read-only derive-on-read (simpler, but no user control over repetition)?
- Priority of on-this-day bucketing relative to recency/spacing: build both in Phase 1 as the existing plan proposes, or ship recency/spacing alone first and add on-this-day once there's enough corpus history for it to matter?
