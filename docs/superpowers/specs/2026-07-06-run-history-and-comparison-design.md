# Run history and comparison (issue #11)

## Problem

Transcriptions, corrections, and summaries (including reruns) don't record which
provider/model produced them anywhere visible or comparable. There's also no way
to compare runs of the same transcript across different providers/models — the
point is finding the cheapest combo that still holds up on quality.

## Scope

Covers correction, summary, transcription, and rediarize reruns. Excludes
voice_match (a labeling aid, not a generative output with comparable quality).

Phased — each phase ships independently and is fully usable on its own:

- **Phase 1**: export leading-line metadata + `Summary.provider` gap fix.
- **Phase 2**: correction run history + diff.
- **Phase 3**: transcription version history + diff (across `retranscribe` rows).
- **Phase 4**: summary run history + diff, rediarize run history + structural compare.

This spec covers the full design; only Phase 1 gets an implementation plan right
now. Later phases get their own plan when reached.

## Data model

- `LlmJob.result_json` (new, nullable JSON): output snapshot saved when a job
  completes, going forward only.
  - `correction`: `{"corrected_text": "..."}`
  - `summary`: `{"short_summary": "...", "key_points": [...], "action_items": [...], "decisions": [...]}`
  - `rediarize`: `{"segments": [...]}`
- `Transcript.source_transcript_id` (new, nullable FK to `transcripts.id`):
  `retranscribe_transcript` sets this on the newly created row. Always points at
  the root transcript — if `t.source_transcript_id` is already set on the
  transcript being retranscribed, copy that value forward rather than chaining,
  so every rerun of the same original audio points at one common root.
- `Summary.provider` (new column): `Summary` currently has `model` but not
  `provider` — gap noticed during this design, fixed alongside since it's the
  same "which provider/model made this" problem the issue is about.

## Source of truth (resolves ambiguity between two homes for "current output")

`Transcript.corrected_text` / `Summary`'s row remain canonical for "what the
Corrected tab / Summary tab display right now" — unchanged. `LlmJob.result_json`
is an append-only side channel purely for history/diff; nothing reads it as the
current value anywhere.

**Backfill**: a one-time data migration (run once, not per-request) sets
`result_json` on the single latest completed `LlmJob` per `(transcript_id, kind)`
from the transcript's current `corrected_text` / `Summary` fields, so the history
picker isn't empty for existing data on day one. Every *older*, already-superseded
completed job has no snapshot (it predates this feature) — the history picker
shows those as "no snapshot available (run predates history tracking)" instead of
enabling diff on them. New runs always get a snapshot going forward.

## Backend

- `GET /api/transcripts/{id}/runs/{kind}` (kind: correction | summary | rediarize)
  — lists all `LlmJob` rows for that transcript+kind, any dismissed state
  (dismiss only hides from the Queue screen, rows persist), with
  id/provider/model/created_at/status/result_json.
- `GET /api/transcripts/{id}/versions` — resolves the root
  (`source_transcript_id` or self) and returns every transcript sharing that
  root (including the root), for transcription-run comparison.
- `run_llm_job` (services/llm_jobs.py) populates `result_json` immediately
  before `_finish(db, job, "completed")` for correction/summary/rediarize.

## Frontend

- Detail page: a "History" control next to each of the Corrected / Summary /
  Diarization tabs opens a picker of past runs (provider, model, timestamp).
  Picking two enables the diff view for that tab.
- Tape library / detail page: "Compare versions" appears when
  `GET .../versions` returns more than one transcript — same diff view, applied
  to `full_text`.
- Diff rendering, per kind (no single view fits all four — see below):
  - **Correction, transcription**: word-level diff (hand-rolled LCS, plain JS —
    no bundler/npm in this repo, no external diff library dependency), rendered
    inline in one text stream with insertions/deletions highlighted.
  - **Summary**: `short_summary` gets the same word-level diff. `key_points` /
    `action_items` / `decisions` are each sorted alphabetically before a
    line-level diff (one bullet = one line) — sorting avoids pure-reorder churn;
    a reworded-and-reordered bullet can still show as remove+add, which is an
    accepted limitation, not a bug.
  - **Rediarize**: not a text diff — segments compare structurally. Render a
    summary like "37 segments relabeled, 112 unchanged" plus a list of the
    relabeled spans (timestamp + old speaker → new speaker).
- Export (`handleExportClick`, static/rack.js): prepends one metadata line to
  copied/downloaded text, e.g.
  `[groq/whisper-large-v3-turbo · corrected by openrouter/deepseek-v4-flash]`.
  Filename unchanged (kind suffix already present) — metadata goes in the
  leading line, not the filename.

## Known tradeoffs

- `LlmJob.result_json` duplicates full text/segment data across every rerun of
  a long transcript — no cap proposed; flag later if storage becomes a real
  concern.
- Summary/rediarize diffs are approximations (sorted-bullet diff, structural
  relabel count) rather than exact text diffs — accepted given the shape of
  that data.
