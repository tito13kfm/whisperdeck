# Investigation: Issue #169 — Voice-Note Board (minimax-m3-r2)

## What the issue asks for

A capture mode distinct from `meeting` / `dictation`:

1. Single-speaker audio (diarization forced off, same as `dictation`).
2. After transcription, an **LLM chain** that first classifies the kind of
   note the speaker is taking (todo / idea / reminder / journal / general),
   then branches on that kind to produce a **structured write-up** —
   not just reformatted prose.
3. The structured output has to live somewhere durable and be displayable
   in the UI.

Issue explicitly calls out reusing the `LlmJob` queue infrastructure and
the existing single-speaker transcription path rather than inventing a new
async mechanism.

## Design decisions (locked before any code)

### Kind name: `voice_note` (with underscore)

Column is `String(16)`. "voice_note" is 10 chars, fits. Compared to the
"one word" alternative `voicenote` (9 chars), I pick `voice_note` because:

- UI label "VOICE NOTE" reads better than "VOICENOTE" on the VFD display.
- API error messages ("Reformatting is only available for dictation
  transcripts") flow more naturally with two words.
- The existing schema has mixed conventions (camelCase-ish, no strict
  one-word rule) — `_` between natural word boundaries is the path of
  least friction.

### Chain shape: ONE `voice_note` LlmJob with TWO awaits, NOT two chained jobs

Tradeoff:

| Aspect | 1 job, 2 awaits | 2 separate jobs |
|---|---|---|
| Retry unit | one | per-step |
| UI queue rows | one | two |
| Progress visibility | `done=1/2 → 2/2` | two `done=1/1` |
| Cancel granularity | one cancel kills both | each can be cancelled |
| Re-run granularity | one rerun retries both | can re-run structure only |
| Schema surface area | one new kind | two new kinds |

I pick **1 job, 2 awaits** because:

- The user-perceived concept is "the voice note", not "the classification"
  and "the structuring" as independent things. The Queue screen should
  show one bar.
- `LlmJob.progress_done` / `progress_total` already supports 2-step
  progress within one job — the field isn't an integer-stamped one-step
  design.
- A re-run with a different provider makes more sense as a single unit;
  per-step re-runs are a power-user case the issue doesn't ask for.
- The issue literally says "a chain of LLM calls" — one job, two awaits
  *is* a chain. Two separate jobs would be a pipeline, not a chain.

### Note types: `todo | idea | reminder | journal | general`

Five classes, the four the issue names explicitly plus `general` as the
safe fallback when the LLM can't classify confidently. This set:

- Covers the four explicit categories the issue calls out (todo, idea,
  reminder, journal entry).
- Avoids multi-modal understanding (no images, no meeting minutes
  domain) — these are personal-note patterns any LLM with text input can
  distinguish reliably.
- `general` exists so the second call always has a non-erroring path
  even when the first call produces a non-JSON or out-of-vocabulary
  label. The structured payload for `general` is just `{title, body}`
  so a "raw" voice note still gets something useful.

### Storage: new `VoiceNote` table

- Mirrors the existing `Summary` model (one row per transcript, in-place
  on re-run).
- Columns: `id`, `user_id`, `transcript_id` (unique, one per
  transcript), `note_type`, `title`, `body`, `structured` (JSON, the
  per-type field bag), `model`, `provider`, `created_at`.
- The job's `result_json` carries the same payload for the run-history
  view, mirroring how `summary` does it.
- New `voice_note` relationship on `Transcript` (uselist=False, cascade
  delete-orphan) so the row dies with the transcript.

No schema migration is required for the table itself — `create_all`
picks up new tables on a fresh DB and `ensure_columns` is only used
for ALTERs to existing tables. The new table just appears on next
startup. A user with a pre-existing `transcripts` table already
has the `kind` column (the `ensure_columns` call at line 345 of
`database/__init__.py` adds it if missing), so the value `voice_note`
is a free-form string and no migration is needed for that either.

### Transcription pipeline: same as `dictation`

- `kind == "voice_note"` joins `kind == "dictation"` in the existing
  server-side `diarize = False` rule (one-line change at app.py:938).
- `voice_note` does **not** enqueue `auto_correction` — voice notes
  are short, the structured-write-up pass already does prose cleanup
  as part of `structure_voice_note`, and a separate correction pass
  would be redundant work the user didn't ask for.
- `voice_note` does **not** enqueue `classify_intent` — that's the
  format-tab hint for dictation→reformat UI, irrelevant here.
- `voice_note` **does** enqueue a new `voice_note` LlmJob (the chain).
- Both branches of `_run_transcription_pipeline` (inline + chunked)
  get the same change so the chunked-finalize path in
  `services/queue.py` doesn't strand voice-note transcripts without
  their chain.

### Routes that gate on `kind`

- `/format/{target}` (app.py:1932): reject `voice_note` (the chain
  does its own structuring; no reformat). Existing 400 message
  "Reformatting is only available for dictation transcripts" stays
  the dictation-flavored wording; the new check adds a per-kind note
  for clarity.
- `/rediarize` (app.py:1987): reject `voice_note` (single-speaker,
  rediarize doesn't apply).
- `/voice-match` (app.py:2018): reject `voice_note` (same reason).
- `/summarize`: handled inside `transcription_service.summarize()`
  (services/transcription.py:187). I add a `voice_note` branch that
  returns a "see the Notes tab" stub without an LLM call — running
  a meeting-style summary on a voice note would be duplicate work
  the chain already did, and the structured voice note IS the
  summary.

### New API endpoints

- `GET /api/transcripts/{id}/voice-note` — fetch the latest `VoiceNote`
  row for the transcript, returning the structured payload.
- `GET /api/voice-notes` — list all voice notes for the current user
  (powers the rail/board page).
- `DELETE /api/voice-notes/{id}` — remove a single voice note row.
  Removing the note does NOT remove the underlying transcript (the
  note is a derived artifact; the transcript is the user's source).

### Frontend

- `tog-mode` on the Transcribe page becomes a 3-way segmented control:
  `MEETING / DICTATION / VOICE NOTE`. Cycling: `meeting → dictation
  → voice_note → meeting`. Existing `S.mode` string is reused.
- The `tog-mode` paddle position, `vfd-mode` label, the
  `ctl-diarize` locked state, and the `kind=...` form field all read
  from `S.mode`, so the existing syncTranscribe() / startJob()
  plumbing picks up the new value with no per-site change once `S.mode`
  accepts the third value.
- Detail page: the `format` tab is dictation-only (rack.js:2427). I
  add a sibling `notes` tab for `voice_note` kind. The
  `classify_intent_job` / `format_*_job` columns become a single
  `voice_note_job` column on the serialized transcript.
- New page: `page-voicenotes` (a board of recent voice notes,
  grouped by note_type). New rail button. Mirror the
  "Tape library" styling so it slots in visually.
- Detail header "Mode" button: shows `VOICE NOTE` for `voice_note`,
  `DICTATION` for dictation, `MEETING` otherwise.
- Detail page action area: for `voice_note` kind, the
  `data-dact="toggle-kind"` button cycles between the three values
  same as on the Transcribe page.

## Real call sites in scope (the fix must touch every one)

Authoritative list, verified by reading current code (the issue did
not enumerate them — the prior variants' investigations covered most
but not all of these, I verified each line myself):

### `services/llm_jobs.py`
- `VALID_KINDS` line 20-22 — add `"voice_note"`.
- `AUTO_RETRY_KINDS` line 34 — add `"voice_note"` (network-bound
  LLM call, same as the others).
- `IO_KINDS` line 41 — add `"voice_note"` (provider API, not local
  compute). `CPU_KINDS` stays unchanged. The
  `test_io_cpu_pools_partition_valid_kinds` invariant at
  tests/test_llm_jobs.py:439-444 holds.
- New `enqueue_auto_voice_note(db, transcript, user_settings)`
  helper (after `enqueue_auto_classify` at line 175-191). Resolves
  the user's `format_provider` / `format_model` settings (the same
  provider the chain uses), pre-fails with "no API key" if needed.
- `run_llm_job` if/elif dispatch (line 254 onward) — new branch for
  `voice_note`, two-await implementation with progress updates
  between the awaits.
- `serialize_llm_job` (line 47-69) — no change needed; it already
  serializes any kind generically.

### `services/voice_notes.py` (new file)
- `classify_voice_note(text, ...)` — JSON-mode LLM call returning
  `{"type": "todo|idea|reminder|journal|general"}`. Mirrors
  `classify_intent` in `services/reformatting.py:87-112`. Never
  raises — falls back to `general` on any failure.
- `structure_voice_note(text, note_type, ...)` — JSON-mode LLM call
  returning the per-type structured payload. One of five prompt
  templates selected by `note_type`. The JSON shape is documented
  per-type in the prompt itself.
- `run_voice_note_chain(text, ...)` — orchestrates the two calls
  in order, returns `{type, title, body, structured}`. Raises on
  classification failure (caller is the LlmJob worker, errors land
  on the job). Falls back to a `general` body if structure parse
  fails so a bad second call doesn't block the user from seeing
  whatever the first call did produce.

### `database/__init__.py`
- New `VoiceNote` model (mirrors `Summary`):
  - `id`, `user_id`, `transcript_id` (FK, unique — one per
    transcript, in-place update), `note_type`, `title`, `body`,
    `structured` (JSON, per-type fields), `model`, `provider`,
    `created_at`.
- New relationship on `Transcript`: `voice_note = relationship(
  "VoiceNote", back_populates="transcript", uselist=False,
  cascade="all, delete-orphan")`.
- New backref on `VoiceNote`: `transcript`.
- Update the column comment on `Transcript.kind` (line 38) to
  mention `voice_note` so the next reader sees it from the
  schema.

### `app.py`
- `/api/transcribe` validation (line 1179) — allow
  `kind=voice_note` in the allowlist.
- `/api/transcripts/{id}` PATCH validation (line 1474) — allow
  `kind=voice_note` in the allowlist.
- `_run_transcription_pipeline` (line 938) — extend the
  `diarize = False` rule to cover `kind == "voice_note"` alongside
  `kind == "dictation"`.
- `_run_transcription_pipeline` post-pipeline enqueue (line 1145-1149):
  - Skip `enqueue_auto_correction` when `kind == "voice_note"`.
  - Skip `enqueue_auto_classify` when `kind == "voice_note"`.
  - ADD `enqueue_auto_voice_note` call when `kind == "voice_note"`.
- `services/queue.py` chunked-finalize path (line 565-571) — same
  conditional skip + enqueue for the chunked-finalize branch. The
  inline and chunked paths must stay aligned or long voice-note
  recordings (>LOCAL_CHUNK_SECONDS) silently skip the chain.
- `/format/{target}` route (line 1932) — reject `voice_note` with a
  per-kind message rather than the existing dictation-only wording.
- `/rediarize` (line 1987) — also reject `voice_note` (currently
  only blocks dictation, the conditional is
  `if t.kind == "dictation"` which lets `voice_note` through
  unintentionally).
- `/voice-match` (line 2018) — also reject `voice_note` (same
  issue as `/rediarize`).
- `/summarize` -> `transcription_service.summarize` (services/
  transcription.py:187-199) — add `kind == "voice_note"` branch
  that returns a stub Summary without an LLM call.
- New `GET /api/transcripts/{id}/voice-note` — fetch the latest
  `VoiceNote` row for the transcript, returning the structured
  payload.
- New `GET /api/voice-notes` — list the current user's voice
  notes, most recent first, with the related transcript's title and
  duration.
- New `DELETE /api/voice-notes/{id}` — remove a single voice note
  row (does NOT remove the underlying transcript).
- `_serialize_transcript` (line 299-348) — for `voice_note` kind,
  include `voice_note_job` in the dictation_job_fields-style
  return. For meeting/dictation, the field is null.
- `_SERIALIZED_JOB_KINDS` (line 266-269) — add `"voice_note"`.
- The `/api/transcripts/{id}/runs/{kind}` allowlist (line 2037) —
  add `"voice_note"` so the run-history picker can show voice-note
  runs.

### `services/transcription.py`
- `summarize()` (line 187) — add `kind == "voice_note"` branch
  early-returning a stub `Summary` (no LLM call).

### Frontend — `static/rack.js`
- `S.mode` allowed values: `meeting | dictation | voice_note` (line 41
  comment update + line 542 default + the cycle at line 1463).
- The `tog-mode` toggle (line 1356) — convert from binary to
  3-position. The existing `tog` CSS class is binary (on/off); I
  need either a 3-position CSS variant or three buttons in a
  segmented control. The 3-position toggle would need a small CSS
  change to support 33%/67% paddle positions. The 3-button
  segmented control is simpler and matches the existing chassis
  language.
- `setVfd('vfd-mode', ...)` (line 1649) — handle 3 cases.
- The `ctl-diarize` lock logic (line 1650, 1467) — extend to
  `S.mode === 'dictation' || S.mode === 'voice_note'`.
- Detail tabs `detailTabsHtml()` (line 2425-2436) — for
  `kind === 'voice_note'`, push `'notes'` instead of `'format'`.
  The format tab stays for dictation.
- Sticky `S.detailTab` guard (line 2392) — extend to also reset
  `'notes'` if a voice_note is replaced with a non-voice_note
  transcript.
- Detail header "Mode" button label (line 3311) — handle
  `kind === 'voice_note'`.
- `data-dact="toggle-kind"` handler (search for it, currently
  cycles 2-way; cycle 3-way).
- Detail body for the new `notes` tab — render the structured
  payload from `detailData.voice_note_job.result_json` (or fetch
  via the new `/voice-note` endpoint).
- New `loadVoiceNotes()` function — fetches `/api/voice-notes`,
  renders a card grid.
- New `voiceNotesHtml()` function — produces the card grid.
- Add `'voicenotes'` to `PAGES` array (line 432-441) + the loaders
  map.
- The `transcribe` form's `kind=...` (line 1727) — already uses
  `S.mode`, so no change needed once the toggle is 3-way.

### Frontend — `static/index.html`
- New `<button class="rail-btn" data-nav="voicenotes">` between
  the existing rail entries.
- New `<div class="page" id="page-voicenotes"></div>` inside the
  content area.
- The new page renders into the existing content area; no other
  HTML changes.

### Frontend — `static/rack.css`
- The new segmented control for the 3-way mode toggle, IF I go
  the 3-button route. The existing `.ctl` and `.tog` classes cover
  the binary case; the 3-button variant needs a small new class.
- A small `.voice-note-card` style for the board page.
- A small `.note-type-badge` style for the type chip (todo / idea
  / reminder / journal / general).

## Sibling-sweep (the rule: actively search for siblings the issue
never named)

I grep'd for every place a `kind` value is enumerated or compared:

- `app.py`: lines 355, 938, 1118, 1145-1149, 1179-1180, 1473-1475,
  1932, 1987, 2018, 2037. All enumerated above. The PATCH endpoint
  (1473-1475) and the upload endpoint (1179-1180) both have
  `kind` allowlists that MUST be updated in lockstep. The issue
  references neither; a one-sided fix here would let uploads
  succeed while PATCHing the kind fails (or vice versa).
- `services/llm_jobs.py`: lines 20, 34, 41, 42, 98, 158-191, 254-425.
  All enumerated above.
- `services/transcription.py:187` — `summarize()` branches on
  `kind == "dictation"`. The sibling branch for
  `kind == "voice_note"` is in scope.
- `services/queue.py:565-571` — the chunked-finalize path enqueues
  the same post-pipeline jobs. The voice-note skip + enqueue must
  be added here too, or chunked voice-note recordings (>3 minutes
  locally, >threshold for cloud) silently skip the chain.
- `database/__init__.py:38` — column comment for `kind` enum.
  Update the comment to include `voice_note`.
- `tests/test_io_cpu_pools_partition_valid_kinds` (tests/test_llm_jobs.py:439) —
  this test will pass unchanged after I add `voice_note` to
  IO_KINDS (the partition invariant holds for any well-formed
  addition).
- `tests/test_serialize_transcript_contract.py:26-37` — the
  EXPECTED_KEYS set will grow with `voice_note_job`. Both
  meeting and dictation transcripts get the new key (null for
  them), matching the existing pattern for the dictation-only
  fields. The "meeting and dictation have the same field names"
  test (line 52) will still pass.
- `tests/test_reformatting.py:248+` — the test that pins
  `/format/*` rejecting non-dictation kinds. The new
  `voice_note` rejection will be tested in a new test file.
- `tests/test_transcript_kind_patch.py:13-48` — the test that
  pins PATCH kind allowlist. I add a test that PATCHing kind to
  `voice_note` is accepted.
- `tests/test_llm_job_history_backfill.py` — no change needed;
  the backfill only handles correction/summary/rediarize.
- `static/rack.css` — no kind-related class names; the new
  segmented control may need a small CSS class but the existing
  chassis buttons should style it correctly out of the box.
- `static/rack.js` — `KIND_LABELS` at line 2248 — add
  `voice_note: 'VOICE NOTE'` for the Queue screen label.
- `static/rack.js` — `KIND_LABELS` is referenced for the queue
  page's job-kind VFD. The job's `kind` value is the LlmJob kind
  (`voice_note`), and that gets looked up in the labels map.

## What the issue's framing gets right and what it doesn't

Right:
- LlmJob queue is the right substrate.
- `dictation` kind is the right reference for single-speaker
  transcription behavior.
- Avoiding dictation's reformat-templates metaphor in favor of a
  structured payload.

Does NOT specify:
- The exact set of note types. I'm picking `todo | idea |
  reminder | journal | general` (5 classes).
- The on-disk shape of the structured output. I chose a
  `VoiceNote` table because (a) it's how `Summary` is already
  stored, (b) a queryable structured field beats a JSON blob
  for the board's card rendering.
- Whether the chain runs in 1, 2, or 3 LLM calls. I chose 1 job
  with 2 awaits (see design decisions above).

## Acceptance criteria (none explicit; the four "Scope" bullets
as criteria)

| Criterion | Where it lives | Status |
| --- | --- | --- |
| A place for these notes to live | `VoiceNote` table + `transcript.kind="voice_note"` | covered |
| A multi-step LLM chain (classify → branch → structure) | `services/voice_notes.py` `run_voice_note_chain` | covered (1 job, 2 awaits) |
| Somewhere to store and display the structured output | `VoiceNote` table + `/api/transcripts/{id}/voice-note` + Notes tab + board page | covered |
| A UI surface for it | 3-way mode toggle + Notes tab in detail + new board page | covered |
| Reuse `LlmJob` queue infrastructure | new `kind="voice_note"` in `VALID_KINDS` | covered |
| Single-speaker (diarization off) | server-side force in `_run_transcription_pipeline` (line 938) | covered |
