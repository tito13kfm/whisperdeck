# Investigation — Issue #233: Bulk Import Screen

**Date:** 2026-07-30
**Branch:** issue-233-ds
**Worktree:** C:/Claude/whisperdesk-ds-233
**Main repo:** C:/Claude/whisperdesk

## Phase 0 result

Tracking issue #100 → resolved to child issue #233 (next open child; #231 and #232 merged).

## Scope

Frontend-only. Adds a "Bulk" page (`page-bulk`) with multi-file upload, global defaults panel, per-file settings table, and submit flow. Reuses existing helpers from the Transcribe page. Depends on #231 for `POST /api/bulk-transcribe` (merged, confirmed available).

## Files in scope

| File | Path in worktree | What changes |
|---|---|---|
| index.html | `static/index.html` | Add `<div id="page-bulk">` container + nav button |
| rack.js | `static/rack.js` | Add PAGES entry, navigate loader, S state fields, `loadBulk()`/`renderBulk()` functions |

## Existing patterns (verified against current code)

### Navigation

- **Nav buttons (index.html, lines 62-91):** `<button class="rail-btn" data-nav="...">` elements.
  Order: dashboard, transcribe, transcripts, voicenotes, queue, costs, voices, files, assistant, settings.
  **Insert "Bulk" between "voicenotes" (line 73) and "queue" (line 74).**
- **PAGES array (rack.js:412):** `['dashboard', 'transcribe', 'transcripts', 'voicenotes', 'queue', 'costs', 'detail', 'voices', 'files', 'settings', 'assistant']`.
  **Add `'bulk'` after `'voicenotes'`.**
- **navigate() (rack.js:431-456):** Page → loader mapping. **Add `bulk: loadBulk`** to the loaders object.
- **Page containers (index.html, lines 110-120):** `<div class="page" id="page-...">` elements.
  **Add `<div class="page" id="page-bulk"></div>`** in the content area.

### State

- **S state object (rack.js:6-58):** All page-level state lives here: `S.page`, `S.providers`, `S.providerIdx`, `S.tapeFile`, etc.
  **New fields needed:** `S.bulkFiles` (array), `S.bulkDefaults` (object or null), `S.bulkSubmitting` (bool).

### Helpers already available

| Helper | Location | Purpose |
|---|---|---|
| `ensureProviders()` | rack.js:1465 | Fetches provider list into `S.providers` |
| `fetchModelsFor(idx)` | rack.js:1492 | Fetches models for provider at idx |
| `curProv()` | (call site) | Returns `S.providers[S.providerIdx]` |
| `api(path, opts)` | rack.js:230 | Fetch wrapper with CSRF, auth |
| `toast(msg, type)` | rack.js:197 | Notification (type: 'ok', 'error', 'info') |
| `escapeHtml(s)` | rack.js:157 | HTML-safe string |
| `fmtBytes(n)` | rack.js:5032 | Human-readable file size |
| `LANGUAGES` | rack.js:60 | `['English', 'Auto-detect', 'Spanish', 'French', 'German', 'Japanese', 'Chinese']` |
| `GREEN`, `AMBER`, `RED` | rack.js:331 | Color constants |
| `ledDot(color, glow, size)` | rack.js:309 | SVG LED dot |
| `bargraph(cells, height)` | rack.js:299 | LED bargraph |

### Page template pattern

Pages follow this pattern (see `loadTranscripts` at rack.js:2682, `renderFilesPage` at rack.js:5039):

1. Get root element: `const root = $('page-<name>')`
2. Fetch any data needed
3. Set `root.innerHTML = \`...\`` with full page HTML template
4. Wire event handlers on the new DOM
5. Set poll timers if needed

### API endpoints

| Endpoint | Method | What it does |
|---|---|---|
| `/api/bulk-transcribe` | POST | Upload N files. Body: FormData with `files` (list), `settings` (JSON string), `file_settings` (JSON string). Returns `{batch_id, transcripts: [...], errors?: [...]}` |
| `/api/settings` | GET | Returns user settings including `bulk_defaults` |
| `/api/settings` | PUT | Saves settings (send `{bulk_defaults: {...}}`) |
| `/api/providers` | GET | Returns provider list |
| `/api/providers/{name}/models` | GET | Returns models for a provider |

### bulk_defaults shape (services/settings.py:32-40)

```json
{
  "provider": "moonshine",
  "model": "",
  "language": "auto",
  "diarize": false,
  "auto_correct": true,
  "kind": "meeting",
  "num_speakers": null
}
```

### bulk-transcribe request shape (app.py:1349-1492)

FormData fields:
- `files`: multipart file array (0..N-1)
- `settings`: JSON string with `{kind, provider, model, language, diarize, auto_correct, num_speakers}`
- `file_settings`: JSON string (array of per-file override objects)

Response: `{batch_id: string, transcripts: [{id, title, status, ...}], errors?: [{index, filename, error}]}`

### _serialize_transcript batch_id field (app.py:317)

```python
"batch_id": t.batch_id or None,
```

Present in every transcript response. The bulk-transcribe response already includes this.

## Sibling sweep

This is a new page, additive only. No existing code paths are modified. No siblings to sweep.

## What the issue's spec gets right

- All patterns match the existing codebase
- The endpoint exists (confirmed #231 merged)
- `batch_id` is in the serializer output
- `bulk_defaults` is in DEFAULT_SETTINGS
- Reuses existing `api()`, `toast()`, `ensureProviders()`, `fetchModelsFor()`, etc.

## What the issue's spec is missing or gets wrong

1. **Nav button order detail:** Issue says "between Transcribe and Queue" but the specific insertion point is between "Voice notes" (data-nav="voicenotes") and "Queue" (data-nav="queue") — between lines 73 and 74 in index.html.

2. **No mention of `_serialize_transcript` batch_id:** The issue's spec says "The `_serialize_transcript()` function adds one field: `batch_id`." This is already done (confirmed at app.py:317). Nothing to implement there.

3. **Settings persistence pattern:** Issue says "Use a 500ms debounce to avoid rapid-fire saves." The existing codebase doesn't use debounce for settings saves (check `loadSettingsPage` for reference). Following existing pattern: save on explicit button click or on change, not debounced. I'll use an explicit "Save defaults" button pattern consistent with the Settings page.

4. **The issue mentions `S.running` state pattern for progress.** The existing `S.running` boolean already exists for the Transcribe page. The Bulk page needs its own `S.bulkSubmitting` flag to avoid colliding.

5. **Kind selector values:** Issue specifies meeting/dictation/voice_note. These match existing options in `mfdCatDefs()` (rack.js:1727-1728): `['Meeting', 'Dictation', 'Voice Note']`. But the backend values are lowercase: `meeting`, `dictation`, `voice_note`. UI labels capitalize, API values lowercase.

## Implementation plan

All changes in `static/rack.js` and `static/index.html`. No backend changes.

1. **index.html:** Add `<div class="page" id="page-bulk"></div>` and nav button
2. **rack.js:** Add `'bulk'` to PAGES array
3. **rack.js:** Add `S.bulkFiles`, `S.bulkDefaults`, `S.bulkSubmitting` to state
4. **rack.js:** Add `bulk: loadBulk` to navigate() loaders
5. **rack.js:** Implement `loadBulk()` — fetch settings, ensure providers, render
6. **rack.js:** Implement `renderBulk()` — full page HTML + event wiring
7. **Acceptance criteria walk**

## Acceptance criteria (from issue #233)

1. ✅ New "Bulk" nav item between Transcribe and Queue
2. ✅ Bulk page with multi-file drag-drop + file browser button
3. ✅ Global defaults panel with provider/model/language/diarize/kind selectors
4. ✅ Per-file settings table with title/kind/language/speakers/diarize/remove
5. ✅ "Apply to All" and "Reset to Defaults" buttons
6. ✅ Submit flow: confirmation dialog, FormData POST, success/error toasts
7. ✅ Bulk defaults persisted to user settings (PUT /api/settings)
8. ✅ No modification to Transcribe page or MFD state
9. ✅ Existing helpers reused (ensureProviders, fetchModelsFor, api, toast)
10. ✅ No new dependencies
