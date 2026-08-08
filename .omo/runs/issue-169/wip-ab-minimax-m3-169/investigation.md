# Investigation — Issue #169: Voice-note board

## What the issue asks for (verbatim intent, restated for the design log)

A capture mode distinct from the existing `meeting` / `dictation` flows:
1. Single-speaker audio (diarization forced off, same as `dictation`).
2. After transcription, an **LLM chain** that first classifies the kind of note
   the speaker is taking (todo, idea, reminder, journal entry, general), then
   branches on that kind to produce a **structured write-up**, not just
   reformatted prose.
3. The structured output has to live somewhere durable and be displayable in
   the UI — not buried in a reformat result.

The issue explicitly calls out reusing the `LlmJob` queue infrastructure and
the existing single-speaker transcription path rather than inventing a new
async mechanism.

## Decisions (locked before any code)

- **`kind="voice_note"`** is a new value on `Transcript.kind` (column is
  `String(16)`; "voice_note" is 10 chars, fits with headroom). No schema
  migration needed.
- **Storage of the structured output**: a new `VoiceNote` table linked by
  `transcript_id` (one per transcript; replaces in place on re-run), modeled
  on the existing `Summary` table. The job's own `result_json` keeps a copy
  of the same payload for the `/runs/{kind}` history endpoint, so the runs
  view stays uniform with the other LlmJob kinds.
- **LLM chain shape**: **two calls**, both JSON-mode, both inside one
  `LlmJob(kind="voice_note")`. Step 1 classifies (one of
  `todo | idea | reminder | journal | general`). Step 2 picks a per-type
  prompt template and produces a structured payload
  (`title`, `body`, plus per-type `structured` fields like
  `todos: []` / `remind_at: ...` / `tags: []`). `progress_total = 2` and the
  worker increments `progress_done` between the two calls so the queue
  screen shows real movement.
- **Transcription side**: `voice_note` gets the same single-speaker treatment
  as `dictation` (`diarize=False` forced server-side in
  `_run_transcription_pipeline`). It does NOT enqueue the existing
  `auto_correction` pass — voice notes are short, and a second LLM pass for
  prose cleanup before the structured-write-up pass would just be redundant
  noise on the user's note. It does NOT enqueue the existing
  `classify_intent` job — that one's a format-tab hint for the
  dictation→reformat UI, not relevant here. It DOES enqueue the new
  `voice_note` job at the end of the pipeline, so capture-to-board is
  one-button.
- **Reformat / rediarize / voice-match / summary routes**: all gated on
  `t.kind == "dictation"` or `t.kind != "dictation"` — `voice_note` must be
  treated as a separate lane. The simplest correct rule is
  `voice_note` is its own lane everywhere: those routes must reject it with
  a clear message rather than silently re-using the meeting/dictation
  branches. The four existing gates I need to check:
  - `/format/{target}` (line 1932): must reject `voice_note`.
  - `/rediarize` (line 1987): `if t.kind == "dictation"` — also reject
    `voice_note` (single-speaker, no speaker diarization makes sense).
  - `/voice-match` (line 2018): same.
  - `/summarize` — actually not gated by kind; uses `t.kind` only to
    choose prompt. The `summarize()` function already branches on kind
    (`"dictation"` gets a single-speaker prompt); I add a `"voice_note"`
    branch that just returns an explanatory string ("voice notes have their
    own structured view — see the Notes tab") rather than running a
    second LLM call on top of the already-structured one.
- **Voice-roster enrollment**: the issue notes that "these recordings are
  also naturally clean single-speaker audio, which could be useful later as
  enrollment material." Not required for this issue — leaving the
  enrollment flow alone.

## Real call sites in scope (what the fix must touch)

Authoritative list, verified by reading current code (the issue didn't enumerate them):

### Backend — `services/llm_jobs.py`
- `VALID_KINDS` (line 20) — add `"voice_note"`. Required: `enqueue_llm_job`
  raises `ValueError` on unknown kind (line 98-99).
- `AUTO_RETRY_KINDS` (line 34) — add `"voice_note"`. The voice-note chain
  is a network-bound LLM call, same as the other retry-eligible kinds.
- `IO_KINDS` (line 41) — add `"voice_note"`. It belongs in the I/O pool
  (provider API), not the CPU pool. The `test_io_cpu_pools_partition_valid_kinds`
  invariant is hard: IO_KINDS ∪ CPU_KINDS == VALID_KINDS and the two are
  disjoint. Adding to IO_KINDS preserves that.
- `run_llm_job` if/elif dispatch (line 254 onward) — new branch for
  `voice_note`, two-await implementation with progress updates.

### Backend — `services/voice_notes.py` (new)
- `classify_voice_note(text, ...)` — JSON-mode LLM call returning
  `{"type": "todo|idea|reminder|journal|general"}`. Mirrors `classify_intent`
  in `services/reformatting.py`.
- `structure_voice_note(text, note_type, ...)` — JSON-mode LLM call
  returning the per-type structured payload. One of five prompt templates
  selected by `note_type`.
- `run_voice_note_chain(...)` — orchestrates the two calls in order, raises
  on classification failure (chain aborts), swallows structural parse errors
  to a fallback `general` body so a bad LLM parse never blocks the
  transcript from being usable.

### Backend — `database/__init__.py`
- New `VoiceNote` model (mirrors `Summary`):
  - `id`, `transcript_id` (FK, unique — one per transcript, in-place update),
    `note_type`, `title`, `body`, `structured` (JSON, per-type fields),
    `model`, `provider`, `created_at`.
- New relationship on `Transcript`: `voice_note = relationship("VoiceNote", back_populates="transcript", uselist=False, cascade="all, delete-orphan")`.
- `migrate_schema` (`create_all` + ALTER for older DBs) — SQLAlchemy's
  `create_all` only creates missing tables, so a new model is
  forward-compatible without an explicit migration.

### Backend — `app.py`
- `/api/transcribe` validation (line 1179): allow `kind=voice_note`.
- `/api/transcripts/{id}` PATCH (line 1474): allow `kind=voice_note`.
- `_run_transcription_pipeline` (line 938): force `diarize=False` for
  `kind=="voice_note"` (same one-line rule as dictation).
- `_run_transcription_pipeline` post-pipeline enqueue (line 1148-1149):
  - Skip `enqueue_auto_correction` when `kind == "voice_note"`.
  - Skip `enqueue_auto_classify` when `kind == "voice_note"`.
  - ADD `enqueue_auto_voice_note` call for `kind == "voice_note"`.
- `/format/{target}` route (line 1932): reject `voice_note` with a clear
  "voice notes have their own structured view" message.
- `/rediarize` (line 1987): also reject `voice_note`.
- `/voice-match` (line 2018): also reject `voice_note`.
- `/summarize` (`transcription_service.summarize`): add a `voice_note`
  prompt branch that returns a "see the Notes tab" short summary without
  an LLM call (it'd be a duplicate of work the voice_note job already did).
- New `GET /api/transcripts/{id}/voice-note` — fetch the latest
  `VoiceNote` row for the transcript, returning the structured payload.
- `enqueue_auto_voice_note` helper (in `services/llm_jobs.py`): the
  voice-note sibling of `enqueue_auto_correction` / `enqueue_auto_classify`.
  Resolves the user's format_provider/format_model setting, fails the job
  with a clear message if no API key.

### Frontend — `static/rack.js`
- `S.mode = "meeting"` (line 542) — initialization; the toggle already
  flips between `meeting` and `dictation` (line 1463). Extend to cycle
  `meeting → dictation → voice_note → meeting` (or add a 3-way segmented
  control — whichever fits the existing chassis; 3-way segmented is
  probably cleaner than a 3-state toggle).
- The current `tog-mode` is rendered dynamically (no static markup in
  `index.html`). I will swap it for a small 3-button segmented control.
- The mode label VFD (`vfd-mode`): `MEETING` / `DICTATION` / `VOICE NOTE`.
- The detail header "Mode" button (line 3311) — text + click target
  updated for the 3-way choice.
- `loadTranscriptDetail` (line 2392): the existing `if (S.detailTab === 'format' && detailData.kind !== 'dictation') S.detailTab = 'transcript';`
  guard must NOT clobber the new "notes" tab when kind is `voice_note`.
- Detail tabs (line 2427): if `detailData.kind === 'voice_note'`, add a
  `notes` tab.
- New "Voice-note board" page (in the rail, or as a new tile in the
  Monitor dashboard). Simplest first cut: a "Notes" rail entry that
  lists recent voice-note transcripts and surfaces their structured
  output in cards.
- The form `kind=...` (line 1727) automatically passes `S.mode`, so once
  `S.mode` accepts `voice_note` the upload carries it.

### Tests
- `tests/test_voice_note_chain.py` (new) — covers
  `classify_voice_note` and `structure_voice_note` happy paths, the
  unknown-type fallback, the "JSON parse error → general" recovery.
- `tests/test_voice_note_route.py` (new) — covers the full flow:
  upload with `kind=voice_note`, verify diarize-forced-off, verify the
  `voice_note` LlmJob is enqueued with `kind="voice_note"`, mock the LLM
  and drive `run_llm_job` to completion, verify the `VoiceNote` row is
  written and `result_json` matches.
- Existing test that pins the partition invariant
  (`test_io_cpu_pools_partition_valid_kinds`) — must still pass; verify
  the new kind keeps IO_KINDS ∪ CPU_KINDS == VALID_KINDS.

## Sibling-sweep (rule: "actively search for siblings the issue never named")

I grep'd for every place a `kind` value is enumerated or compared:

- `app.py`: lines 355, 938, 1179, 1180, 1474, 1475, 1932, 1987, 2018 —
  all enumerated above. The PATCH endpoint has the second allowlist and
  must be updated in lockstep with the upload endpoint, the issue
  references only the upload one.
- `services/llm_jobs.py`: lines 20, 34, 41, 42, 98, 181, 254-425 (the
  full `run_llm_job` dispatch chain). All enumerated above.
- `services/transcription.py:187` — `summarize()` branches on
  `kind == "dictation"`. A sibling branch for `kind == "voice_note"` is
  in scope (it'd be a regression to call the meeting prompt on a
  voice-note transcript).
- `database/__init__.py:38` — column comment documents the kind enum.
  I update the comment to include `voice_note` so the next reader sees
  it from the schema.

Tests:
- `tests/test_reformatting.py` — `_make_user_and_dictation` and `_upload`
  helpers pin the dictation kind. The test enforces that `/format/*`
  rejects non-dictation, including voice_note; the new test
  `test_voice_note_route.py` covers voice_note's own path.
- `tests/test_transcript_kind_patch.py` — pins PATCH kind validation. I
  add a test that voice_note is accepted by PATCH.
- `tests/test_serialize_transcript_contract.py` — pins the kind field's
  presence in the serialized transcript. No change needed; voice_note
  flows through unchanged.

Docs:
- `README.md` API table mentions `/summarize` and `/correct` but not
  `/format/*` explicitly, so no API table edit needed. I will add the
  new `/voice-note` endpoint to the table for discoverability.

Frontend:
- `static/rack.js` — the kind switch points I enumerated above; no
  static HTML markup references "kind" by string.
- `static/rack.css` — no kind-related class names; a new segment for the
  3-way toggle may need a small CSS class but the existing chassis
  buttons should style it correctly out of the box.

No place pinned the literal count of `kind` values in a way that breaks
on adding one.

## What the issue's own framing gets right and what it doesn't

The issue correctly identifies the LlmJob queue as the right substrate and
the `dictation` kind as the right reference for single-speaker
behavior. It correctly avoids dictation's reformat-templates metaphor in
favor of a structured payload.

It does NOT specify:
- The exact set of note types. I'm picking `todo | idea | reminder |
  journal | general` (5 classes, all common personal-note patterns,
  none of which need a multi-modal understanding). I will document this
  list in the new service module.
- The on-disk shape of the structured output. I chose a `VoiceNote`
  table because (a) it's how `Summary` is already stored and (b) a
  queryable structured field beats a JSON blob for the board's card
  rendering. The job's own `result_json` keeps the same payload for
  the runs/history view, mirroring how `summary` does it.
- Whether the chain runs in 1, 2, or 3 LLM calls. I chose 2 (classify
  + branch) for the following tradeoff: 1 call loses the chain's
  declared value (the issue says "classify → branch → structure", the
  branch IS the value). 3 calls adds a 50% latency tax for marginal
  quality — the second call already includes the per-type prompt
  template, so it's doing the branch + structure in one step. 2 calls
  is the minimum that preserves the "branch" shape the issue calls out.

## Walk-the-acceptance-criteria

The issue lists no explicit acceptance criteria. The closest thing to a
checklist is the four bullets in the "Scope" section. I treat each as a
criterion:

| Criterion | Where it lives | Status |
| --- | --- | --- |
| A place for these notes to live | `VoiceNote` table + `transcript.kind="voice_note"` | covered |
| A multi-step LLM chain (classify → branch → structure) | `services/voice_notes.py` `run_voice_note_chain` | covered (2 calls, branch in step 2) |
| Somewhere to store and display the structured output | `VoiceNote` table + `/api/transcripts/{id}/voice-note` route + Notes tab in detail | covered |
| A UI surface for it | 3-way mode toggle, voice-note board page, detail Notes tab | covered |
| Reuse `LlmJob` queue infrastructure | new `kind="voice_note"` in `VALID_KINDS` | covered |
| Single-speaker (diarization off) | server-side force in `_run_transcription_pipeline` | covered |

## Risks (called out so the implementation has to address them)

- The `classify_voice_note` call must not raise on a non-JSON LLM
  response. Mirror the `classify_intent` pattern: catch, fall back to
  `general`, the second call proceeds with the safe type.
- The worker tick's progress visibility: between the two awaits, the
  Queue screen should show `done=1, total=2` so the user sees
  mid-progress. Update `progress_done` in `run_llm_job` between the
  awaits.
- The existing `summarize()` function should not double-process a
  voice-note transcript. Branch on `kind == "voice_note"` early-return
  a short stub.
- The existing `/format/{target}` route handler is the only route that
  is dictation-only. Its current 400 message
  (`Reformatting is only available for dictation transcripts`) is fine
  for meeting transcripts but misleading for voice_note. Add a
  per-kind note.
