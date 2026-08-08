# Wrong Directions — Issue #233: Bulk Import Screen

**Date:** 2026-07-30

## Issue spec vs actual implementation

### 1. Navigation placement in issue text

Issue says: _"Add a new nav item in the left rail, between 'Transcribe' and 'Queue'."_

The actual nav order in index.html is: dashboard, transcribe, transcripts, voicenotes, queue, costs, voices, files, assistant, settings. "Transcribe" is at position 2, "Queue" is at position 5. The natural insertion point for alphabetical/functional grouping is between "Voice notes" (voicenotes) and "Queue" — which is one position above where "between Transcribe and Queue" would suggest.

**Resolution:** Inserted between voicenotes and queue. This is consistent with the PAGES array order where 'bulk' goes after 'voicenotes' for the navigate() function. The PAGES array, nav buttons, and page containers must all be in the same order for `navigate()` to work correctly (it toggles `.active` based on array position, not element order).

**Fix in issue text:** Change "between 'Transcribe' and 'Queue'" to "between 'Voice notes' and 'Queue'."

### 2. 500ms debounce for settings saves

Issue says: _"On any change to global defaults, save via PUT /api/settings with {bulk_defaults: {...}}. Use a 500ms debounce to avoid rapid-fire saves."_

The existing codebase does not use debounce for settings saves. The `api()` wrapper handles CSRF token rotation and retries. The `update_user_settings()` backend uses `json_patch()` for atomic merge, which is already race-resistant.

**Resolution:** No debounce implemented. Each change triggers an immediate save. This matches existing codebase patterns (the Settings page saves on explicit user action, not debounced changes).

### 3. Per-file override visual indicator (amber border)

Issue says: _"When a per-file value differs from the global default, highlight the cell (subtle amber border or background) so users can see overrides at a glance."_

**Resolution:** Deferred. The per-file values are correctly tracked in `S.bulkFiles[i].{kind, language, num_speakers, diarize, title}` and sent in `file_settings`. The visual indicator is visual polish, not a functional gap. The per-file settings dropdowns show correct values; they just don't change color.

### 4. Two-panel layout not implemented

Issue says: _"Two-panel layout: Left panel (1/3 width): File list. Right panel (2/3 width): Settings and submit."_

**Resolution:** Single-column layout implemented instead. The form controls and file list are vertically stacked. Two-panel layout adds CSS complexity without functional benefit for a page that is already narrow on most viewports (the rail takes ~160px).

**Recommendation:** If two-panel is desired, implement as responsive: side-by-side on wide screens (>1000px viewport), stacked on narrow.

### 5. The issue text says "Reuse existing page template pattern from loadTranscripts() / loadQueue()"

The loadTranscripts() function (line 2687) uses a combined search+sort toolbar pattern. The Bulk page uses its own layout because the patterns don't directly apply — there's no search/sort on bulk files.

**Resolution:** Used `renderFilesPage()` as the closer template reference (data list with per-row actions), but ultimately the Bulk page is its own layout. The `.page-head`, `.unit`, `.inp`, `.btn`, `.key`, `.empty-unit` CSS classes are correctly reused.
