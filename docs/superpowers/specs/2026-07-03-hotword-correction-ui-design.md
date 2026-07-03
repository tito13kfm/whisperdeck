# Hotword Glossary + Correction Pass — Frontend UI Design

**Goal:** Wire the existing backend (hotword CRUD, context-doc extraction, correction pass — all shipped, see `docs/superpowers/plans/2026-07-02-hotword-glossary-and-correction-pass.md`) into `static/index.html`, the project's single vanilla-JS page. Currently none of it is reachable through the UI; users can only hit the API directly.

**Scope:** Frontend only. No backend changes. Single file: `static/index.html` (script + markup, no build step, no external JS framework).

## Components

### 1. Glossary management (Settings page)

New card in the Settings page, following the existing provider-key card pattern (see `loadSettings()` around line 1299).

- List of current hotwords: term, source badge (`manual` / `extracted`), delete (×) button per row.
- Populated via `GET /api/hotwords`, called from `loadSettings()`.
- Add-term input + button at the top of the card, calls `POST /api/hotwords` with `{term}`, then re-renders the list on success.
- Delete button calls `DELETE /api/hotwords/{id}`, then re-renders the list.
- Errors surface via the existing `toast(msg, 'error')` pattern.

### 2. Context doc field (Upload → Advanced)

- New `<textarea id="txContextDoc">` inside the existing collapsible `txAdvanced` block (toggled by `toggleAdv()`, near line 908), alongside diarize/num_speakers.
- In `startTx()` (line 912), append `form.append('context_doc', value)` only when the textarea is non-empty — matches the backend's optional-field handling and avoids triggering extraction on every upload.

### 3. Auto-correct toggle (Settings → Audio settings card)

- New checkbox in the existing Audio settings card (same card as bitrate/chunk-threshold, `loadAudioSettings()`/`saveAudioSettings()` around lines 1469–1519).
- `loadAudioSettings()` reads `auto_correct` off `GET /api/settings` and sets the checkbox state.
- `saveAudioSettings()` includes `auto_correct: <bool>` in the `PUT /api/settings` payload alongside the existing fields.

### 4. Corrected tab (Detail page)

- Third tab alongside Transcript/Summary (`data-tab="corrected"`), extending `switchDetailTab()` (line 1076) to show/hide a third panel `#detailCorrected`.
- Content sourced from the transcript object already fetched in `loadTranscriptDetail()` (line 1011) — no extra request:
  - If `t.corrected_text` is set: render as plain-text paragraph.
  - Else if `t.correction_error` is set: render in an error-styled block (reuse existing error color conventions, e.g. `var(--error)`).
  - Else: empty-state message ("Correction hasn't run for this transcript yet").

### 5. Manual re-run (Corrected tab)

- "Re-run correction" button on the Corrected tab panel.
- Clicking reveals inline provider + model `<select>` elements, reusing the existing provider-list/model-fetch pattern from the upload page (`fetchProviderModels()`, line 1384).
- Confirm calls `POST /api/transcripts/{id}/correct` with the chosen `provider`/`model` form fields, then re-renders the Corrected tab from the response body (which is a full serialized transcript).
- Errors surface via `toast(msg, 'error')`; button re-enables after the request settles (success or failure), matching the pattern in `summarizeTranscript()` (line 1104).

## Data flow

All five components consume endpoints that already exist and are already tested on the backend (`/api/hotwords`, `/api/settings`, `/api/transcribe` with `context_doc`, `/api/transcripts/{id}` fields `corrected_text`/`correction_error`/`correction_model`, `/api/transcripts/{id}/correct`). No new backend work, no schema changes.

## Error handling

Follows the file's existing convention throughout: `try/catch` around each `fetch`, failures reported via `toast(msg, 'error')`, no new error-handling paradigm introduced.

## Testing

The project has no JS test harness (no build step, no bundler, plain script tag in `index.html`). Verification is manual click-through against a running server, consistent with how the rest of `index.html`'s features were built and verified. The implementation plan should include an explicit manual test pass per component (add/delete hotword, upload with context doc, toggle auto-correct, view corrected tab in all three states, re-run correction with a different model).

## Out of scope

- Any change to backend routes, services, or database schema.
- Per-segment corrected-text rendering (backend only stores one corrected blob per transcript, not per-segment) — the Corrected tab shows a single block of text, not a diff or segment-aligned view.
- Automated frontend tests (no harness exists; not introducing one for this feature).
