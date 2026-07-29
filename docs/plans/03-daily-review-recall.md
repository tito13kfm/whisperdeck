# Daily Review / Recall

> One-line status: Draft plan. Idea inspired by Blinko (github.com/blinkospace/blinko), concept only, no code copied.

## Motivation

WhisperDeck accumulates history fast: transcripts of every kind (`meeting`, `dictation`, `voice_note`) and, for voice notes, a structured `VoiceNote` row typed as `todo` / `idea` / `reminder` / `journal` / `general`. Once an entry scrolls off the first page of Tape library or the Voice notes board, the only way back to it is search or scrolling. That's fine for todos and reminders (they're meant to be actioned and closed), but journal entries and ideas are exactly the kind of content that's worth rereading later: a journal entry gains context with distance, and an idea half-formed two months ago might click today. Right now nothing proactively brings old content back in front of the user; recall is 100% opt-in via search.

## What Blinko does (attribution)

Blinko documents a "Daily Review" feature (blinko.mintlify.app/en/how-to-use/daily-review) framed as a review *ritual*: a "Morning Review" (revisit yesterday's captured notes) and an "Evening Review" (organize the day's thoughts before closing out), pitched as turning fleeting notes into lasting insights over time.

Caveat, stated plainly: Blinko's public docs describe the purpose and UX framing of Daily Review, not its selection algorithm. There's no published spec for how it picks which notes to resurface (no mention of spaced-repetition scheduling, random sampling, or date-bucketing) or the exact screen it lives on. We're borrowing the *idea*, resurface old content on a recurring cadence instead of leaving it to search, and designing our own simple selection mechanism from scratch, informed by generic spaced-repetition/"on this day" patterns common to journaling and note apps generally, not by reading Blinko's implementation.

## Proposed approach

Keep v1 mechanical, no LLM involved (this is a retrieval/scheduling problem, not a generation one):

1. **Candidate pool**: completed transcripts (`Transcript.status == "completed"`) belonging to the user, at least `MIN_AGE_DAYS` old (e.g. 3 days, so nothing from this week gets recalled), not permanently dismissed. Join `VoiceNote` where present to get `note_type`.
2. **Two selection buckets, merged**:
   - **On this day**: transcripts whose `created_at` month/day matches today's month/day in a prior year. Weak in the near term (the app is only months old) but free once the corpus has a birthday's worth of history, and it's the closest analog to a "memory" feature users intuitively expect.
   - **Recency + spacing**: everything else, ranked by "hasn't been shown recently." Ordered by `last_shown_at ASC` (NULLS FIRST, i.e. never-shown first) so review rotates evenly through the backlog rather than hammering the same handful of entries, tie-broken by `created_at ASC` so genuinely old material surfaces first. Skip anything shown within the last `SPACING_DAYS` (e.g. 7 days).
3. **Weighting**: `journal` and `idea` voice notes get priority within a bucket (they're the reflective/incubating kinds Blinko's framing targets); `todo`/`reminder` voice notes are excluded by default since they're action items, not recall material, and a stale todo is a nag, not an insight; plain `meeting`/`dictation` transcripts are eligible but rank behind journal/idea.
4. **Output**: a small fixed set per day (e.g. 3-5 cards), each tagged with a `reason` string ("On this day, 2026", "From 3 weeks ago", "An idea worth revisiting") so the UI can explain *why* this surfaced, mirroring Blinko's framing rather than presenting an unexplained pick.
5. **User actions**: "Not now" (snooze, pushes `last_shown_at` forward without penalty) and "Don't show again" (permanent dismiss). Opening the transcript counts as a natural "reviewed" signal and also updates `last_shown_at`.

This is deliberately simpler than a real spaced-repetition scheduler (see Research notes / Open questions), no recall-quality rating, no growing interval curve, just "don't repeat too soon" plus a deterministic rotation through the backlog.

## Code touchpoints (files + symbols, no line numbers)

- `database/__init__.py`: new model, e.g. `ReviewState` (mirrors the simplicity of `HotwordEntry`/`RelabelHistory`, small, additive, no schema coupling to `Transcript` or `VoiceNote`). Picked up by the existing `Base.metadata.create_all()` call in `init_db()` like other plain tables; no need for the manual `CREATE TABLE IF NOT EXISTS` dance used for `transcript_tags`/`transcripts_fts` (those needed raw SQL for FTS5 virtual-table/trigger setup, not applicable here).
- `services/recall.py` (new): candidate pool query, on-this-day matching, spacing/weighting logic, dismiss/snooze state updates. Same shape as `services/search.py`, a query-only service module, no LLM client involved. Reuse `VoiceNote`/`Transcript` joins the way `list_voice_notes` in `app.py` already does.
- `app.py`: new endpoints `GET /api/recall`, `POST /api/recall/{transcript_id}/dismiss`, `POST /api/recall/{transcript_id}/snooze`. Follow the `_serialize_voice_note` pattern for voice-note-backed entries; plain transcripts serialize with the same title/kind/created_at/duration fields `list_voice_notes` already joins in. Consider folding a small recall preview into `/api/bootstrap` (the same cache that already carries `recent_transcripts` for `loadDashboard()`'s first paint) so the dashboard doesn't need an extra round trip on first load.
- `static/rack.js`: `loadDashboard()`, add a "Recall" panel near `dash-recents`, rendered with the same card idiom `loadVoiceNotes()` uses (type-color dot, title, preview text, action buttons). Add `dismissRecallItem()` / `snoozeRecallItem()` handlers mirroring `discardVoiceNote()`.
- `static/index.html`: no new nav page required for v1 if the panel lives inside `page-dashboard`. A dedicated `page-recall` (with its own `data-nav="recall"` rail button) is a fine phase-2 addition if the dashboard panel proves popular and needs more room (filters, history of past picks).

## Data model / schema changes

New table, additive only:

- `ReviewState`: `id`, `user_id` (FK `users.id`), `transcript_id` (FK `transcripts.id`, `ON DELETE CASCADE`, unique per user+transcript), `last_shown_at` (nullable, null means never shown), `shown_count` (default 0), `dismissed` (boolean, default false), `created_at`.

No changes to `Transcript` or `VoiceNote`. Keeping recall bookkeeping in its own table avoids growing those two models with a concern (review scheduling) unrelated to what they represent, and keeps the feature fully removable by dropping one table.

## Research notes

- Blinko's Daily Review: documented as a scheduled review ritual (morning/evening passes over recent notes), not a published algorithm. Source: blinko.mintlify.app/en/how-to-use/daily-review.
- Generic prior art we're informally drawing on (not Blinko-specific): "on this day" resurfacing (Facebook Memories, Day One journaling app) is pure date-bucketing, no scheduling algorithm needed. Full spaced-repetition (Anki's SM-2 family) assigns a growing review interval based on an explicit recall-quality rating from the user each time, that requires a feedback loop ("was this worth seeing again? yes/no/meh") that WhisperDeck doesn't have yet. Our v1 spacing (fixed `SPACING_DAYS` cooldown, no interval growth) is intentionally the simplest thing that avoids showing the same note every day, not an SRS implementation.

## Open questions

- Should permanently dismissed items ever resurface after a very long time (e.g. a year), or is `dismissed` truly forever? Leaning forever for v1, simplest, and "don't show again" should mean it.
- Should the candidate pool include `meeting`/`dictation` transcripts with no `VoiceNote` row at all, or start scoped to voice notes (`journal`/`idea`) only? Proposal above includes both but weights voice notes higher; could ship voice-notes-only first and widen later if it feels too sparse.
- `created_at` is stored as naive UTC. "On this day" bucketing by UTC month/day will occasionally be off by one calendar day in the user's local timezone near midnight. Worth fixing, or acceptable slop for v1?
- Fixed count per day (3-5) vs. scaling with library size, punt to fixed for v1, revisit once there's usage data.
- No push/notification channel exists in WhisperDeck today; v1 is pull-based (user has to visit the dashboard to see the panel). A daily digest/notification would need a scheduled background tick (the app already has `queue_worker_loop`/`llm_worker_loop` as a precedent for a long-running async loop), worth it only if the pull-based version shows low engagement.

## Rough phasing / checklist

**Phase 1: backend selection logic + schema**
- [ ] Add `ReviewState` model to `database/__init__.py`
- [ ] Write `services/recall.py`: candidate pool query, on-this-day matcher, spacing/weighting, dismiss/snooze state mutators
- [ ] Unit tests for `services/recall.py` (see Testing considerations)

**Phase 2: API surface**
- [ ] `GET /api/recall` in `app.py`, scoped to `current_user`, returns ranked candidates with `reason` strings
- [ ] `POST /api/recall/{transcript_id}/dismiss` and `POST /api/recall/{transcript_id}/snooze`
- [ ] Decide whether to fold a preview into `/api/bootstrap` for first-paint parity with `recent_transcripts`

**Phase 3: dashboard UI**
- [ ] Recall panel in `loadDashboard()` (`static/rack.js`), card styling reused from `loadVoiceNotes()`
- [ ] Wire dismiss/snooze buttons and "open" navigation to `detail`
- [ ] Empty state (no eligible candidates yet, new users, or everything dismissed)

**Phase 4 (future, not v1)**
- [ ] Dedicated `page-recall` history view (what's been shown, when)
- [ ] Timezone-aware "on this day" bucketing
- [ ] Push/digest delivery via a background tick, if pull-based engagement is low
- [ ] Revisit whether a real spaced-repetition curve (recall-quality rating -> growing interval) is worth the added complexity, once there's usage data on how often "not now" vs. "dismiss" gets clicked

## Testing considerations

- Unit tests for `services/recall.py`: age threshold cutoff, on-this-day matching across a year boundary (Dec 31 / Jan 1), spacing (an item shown yesterday must NOT reappear inside `SPACING_DAYS`), permanent dismiss excludes an item even after the spacing window would otherwise allow it, journal/idea weighting actually changes ordering versus a plain transcript of the same age.
- Per project convention: each test must fail if the function under test were replaced with `return`, construct the "already shown recently" state explicitly before asserting it's excluded, don't just assert on an empty pool.
- Endpoint tests: `/api/recall` scoped correctly to `current_user.id` (no cross-user leakage), dismiss/snooze endpoints actually persist `ReviewState` changes.
- This is a new, self-contained UI surface (no existing text/role/control is being changed), so it doesn't require updating existing e2e selectors. Per the project's testing tiers, a backend change of this shape needs the unit/integration tier every time; the dashboard panel is a UI-visible addition, so drive it once manually (or via a scoped Playwright check) rather than running the full `e2e-ux-audit` suite for this alone, save that for a pre-release pass.
