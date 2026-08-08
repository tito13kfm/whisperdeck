# Investigation: Issue #234 — Queue + Tape Library batch grouping

**Target**: Issue #234 (resolved from tracking issue #100, child 4/4)
**Date**: 2026-07-30
**Worktree**: `C:/Claude/whisperdesk-sisyphus-234` (branch `issue-234-sisyphus`, from `origin/master` at `26c0633`)
**Main repo**: `C:/Claude/whisperdesk` (branch `master`)

## Dependency status

- #231 (backend batch infrastructure) — CLOSED, merged via PR #235
- #232 (batch management API) — CLOSED, merged via PR #240
- #233 (bulk import screen) — CLOSED, merged via PR #254
- #234 — OPEN, no merged PR → **TARGET**

## What exists

### Backend
- `Transcript.batch_id`: `String(64)`, nullable, indexed (database/__init__.py:60)
- `_serialize_transcript()` includes `"batch_id": t.batch_id or None` (app.py:317)
- `/api/batches` — lists batches with aggregate counts (app.py:1498)
- `/api/batches/{batch_id}` — detail with all transcripts (app.py:1573)
- `/api/batches/{batch_id}/cancel` — cancels pending/processing in batch (app.py:1612)
- `/api/transcripts?batch_id=X` — filter by batch (app.py:1675)
- `_transcription_queue_entry()` (app.py:2801) — builds queue entry for transcription jobs, **does NOT include `batch_id`** (9 fields: id, kind, transcript_id, title, status, progress, provider, model, error, created_at)
- `_build_jobs_payload()` (app.py:645) — mixes transcription entries + LLM jobs into one list

### Frontend
- `loadQueue()` (rack.js:3283) — renders Queue page from `getJobs()` → `/api/jobs`
- `loadTranscripts()` (rack.js:3013) — renders Tape Library from `/api/transcripts?limit=100`
- `renderBankRows()` (rack.js:3169) — renders individual transcript rows in Tape Library
- Bulk Import page exists (`page-bulk`, rack.js:2696-3011)
- `bankListCache` (rack.js:2571) — stores transcript list, already includes `batch_id`
- Queue polls every 3s when active (rack.js:3364), Tape Library polls every 4s (rack.js:3118)
- CSS classes: `.vfd`, `.unit`, `.status-badge`, `.btn`, `.key` exist; no `.batch-group` or `.batch-pill` yet
- PAGES array includes 'bulk' (rack.js:416)

## Scope

### 1. Backend: add `batch_id` to `_transcription_queue_entry()`

**File**: `app.py`, function `_transcription_queue_entry` (line 2801)

Add `"batch_id": t.batch_id` to the return dict. This lets the Queue page group transcription entries by batch. The function has one call site: `_build_jobs_payload` line 669.

### 2. Frontend: Queue page batch grouping

**File**: `static/rack.js`, function `loadQueue` (line 3283)

- After fetching jobs, group transcription entries by `batch_id`
- For each distinct `batch_id` with 2+ entries, insert a collapsible batch header
- Batch header shows: "BATCH" VFD label, first_title or count, aggregate LED bargraph, nixie with completed/total, status badge
- Individual entries inside the batch group render as before (using existing `jobStatusView`, `jobActions`, `bargraph`)
- Batch-level actions: Cancel all, Open batch detail
- Non-batch entries (no `batch_id` or LLM jobs) render unchanged
- State: add `S.batchSnapshots` for transition detection, `S.expandedBatches` for open state

### 3. Frontend: Batch completion toast

**File**: `static/rack.js`, in `loadQueue` poll behavior

- Store previous batch state per batch_id: `{active: N, failed: N}`
- On each poll, compare current vs previous
- If all transcripts in a batch transition to terminal, show toast with success/failure count

### 4. Frontend: Tape Library batch filter and indicator

**File**: `static/rack.js`, functions `loadTranscripts` and `renderBankRows`

- Add batch filter dropdown to Tape Library header (alongside Search and Sort)
- Options: "All transcripts", "In a batch", "Single uploads", plus specific batch IDs
- Filter `bankListCache` client-side on `batch_id`
- Add batch indicator pill to transcript rows with `batch_id`
- Clicking batch indicator opens detail page (no filter action needed for MVP)

### 5. CSS additions

**File**: `static/rack.css`

- `.batch-group`: collapsible batch header styling (uses existing `.unit` base)
- `.batch-pill`: small tag on tape library rows

## Complement Rule / sibling sweep

| Component | Call sites | All covered? |
|---|---|---|
| `_transcription_queue_entry()` | `_build_jobs_payload` line 669 (sole caller) | Yes |
| `loadQueue()` | `navigate()` line 474, self poll line 3365 | Both use the same rendering, grouping applies to both |
| `loadTranscripts()` | `navigate()`, poll loop line 3116, bank actions line 3100 | All three enter the same render path |
| `renderBankRows()` | `loadTranscripts` line 3042, direct lines 3056/3091 | All go through same filter logic |
| `bargraph()` / `nixie()` / `statusView()` | Various across queue/library | Unchanged, reusable |

**Sweep result**: No unlisted siblings. The batch grouping is additive to existing rendering:
- Queue: batch headers are inserted between page-head and individual entries, individual entries render as before
- Tape Library: filter dropdown added to header, batch pill added to rows, existing row rendering unchanged

## What the issue spec gets wrong or misses

1. **`batch_id` not in queue entries**: The issue assumes `batch_id` is available in jobs data. `_transcription_queue_entry()` (app.py:2801) doesn't include it. Must add it.

2. **Tape Library "Batch" filter val values**: The issue suggests dynamic `<option>` values like `batch_20260729_...`. Actual `batch_id` format is `YYYYMMDD_HHMMSS_xxxxxx`. Need to use the real IDs from `bankListCache`.

3. **"Open batch detail" action**: The issue mentions it but there's no dedicated batch detail page. For MVP, this can navigate to Tape Library filtered to that batch.

4. **"Retry all failed" button**: The issue's batch header HTML includes it, but there's no `POST /api/batches/{batch_id}/retry` endpoint. Only cancel exists. Skip this action for MVP, or rely on per-transcript retry buttons inside the expanded group.

5. **Queue mixing pattern**: The issue proposes a separate "Batch Jobs" section. Simpler to just insert batch group `<details>` elements in the existing job list, keeping entries grouped but within the same flat rendering. No separate section needed.

## UI verification plan

1. **Queue with batch**: Create a batch via API call (multi-file bulk-transcribe). Load Queue page. Verify batch header with correct counts, expand to see individual entries.
2. **Queue without batch**: Verify existing single-upload jobs render unchanged.
3. **Tape Library filter**: Load library, verify filter dropdown works, batch indicator pill shows on batch transcripts.
4. **Batch completion toast**: Create small batch (2 files), let them complete, verify toast appears.

## Design decisions

- Batch groups use `<details>` elements with summary, matching existing Queue and Tape Library pattern
- Batch header uses VFD component for "BATCH" label, existing `bargraph()` for LED display
- Expand state tracked in `S.expandedBatches` Set, preserved across poll ticks via `openIds` pattern (same as existing Queue and Library)
- Batch filter in Tape Library is client-side only (filter `bankListCache`), no new API call