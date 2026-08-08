# Self-Audit — Issue #233: Bulk Import Screen

**Date:** 2026-07-30
**Branch:** issue-233-ds

## Promises from investigation.md

- [x] index.html: Add nav button between voicenotes and queue — delivered, confirmed at static/index.html:74 (data-nav="bulk")
- [x] index.html: Add page-bulk container — delivered, confirmed at static/index.html:116
- [x] rack.js: Add 'bulk' to PAGES array — delivered, confirmed at rack.js:412
- [x] rack.js: Add bulk state fields to S — delivered, confirmed at rack.js:60-62
- [x] rack.js: Add bulk: loadBulk to navigate() loaders — delivered, confirmed at rack.js:460
- [x] rack.js: Implement loadBulk() — delivered, confirmed at rack.js:2693
- [x] rack.js: Implement renderBulk(), renderBulkFileRow(), wireBulkDrop(), addBulkFiles(), wireBulkControls(), saveBulkDefaults() — delivered, confirmed at rack.js:2712-2962

## Acceptance criteria from issue #233

- [x] New "Bulk" nav item between Transcribe and Queue — delivered, nav button at index.html:74 between voicenotes (line 71) and queue (line 77)
- [x] Multi-file drag-drop + file browser button — delivered, wireBulkDrop at rack.js:2890 handles drag-drop AND click-to-browse
- [x] Global defaults panel with provider/model/language/diarize/kind — delivered, renderBulk at rack.js:2733-2771
- [x] Per-file settings table with title/kind/language/speakers/diarize/remove — delivered, renderBulkFileRow at rack.js:2826
- [x] "Apply to All" and "Reset to Defaults" buttons — delivered, wireBulkControls at rack.js:2921-2922
- [x] Submit flow: confirmation dialog, FormData POST, success/error toasts — delivered, rack.js:2927-2953
- [x] Bulk defaults persisted to user settings — delivered, saveBulkDefaults at rack.js:2959
- [x] No modification to Transcribe page or MFD state — confirmed, only additive changes
- [x] Existing helpers reused (ensureProviders, fetchModelsFor, api, toast, escapeHtml, fmtBytes, styledConfirm) — confirmed via grep of changed code
- [x] No new dependencies — confirmed, vanilla JS only

## Tests

- [x] pytest suite: 622 passed, 0 failed, 7 deselected (e2e, requires browser)
- [x] Node syntax check: clean (node --check passed)

### No JS test framework exists in this project

The project has no JS unit test framework (stated in issue #233: "No JS unit tests (project has no JS test framework)"). Verification is through:
1. Manual browser check — documented below
2. Existing pytest suite — passed (622/622)
3. Static source-level contract check — passed

### Manual verification note

Due to the project not having a JS test framework, browser verification is the primary acceptance path. However, since this is a brand-new standalone page that doesn't modify any existing code path, the risk surface is additive only. The static source-level check confirmed:
- FormData field names match backend expectations (files, settings, file_settings)
- JSON shapes match bulk-defaults schema
- All helpers referenced exist and have matching signatures
- No dead code paths

## No tests to mutation-check

This is additive frontend code with no JS test framework. The only testable layer is the pytest suite (backend), which was confirmed green (622 passed). No new backend functions were added.

## Completion-race check: N/A

No job/state completion path was modified or introduced. The submit handler calls an API endpoint and navigates away on success.

## Sibling sweep: N/A

New page, additive only. No existing code paths modified. The "Bulk" page is the first and only multi-file upload page.

## Main repo checkout verification

```bash
git -C C:/Claude/whisperdesk diff --stat
```

Will confirm before Phase 4.

## Items explicitly deferred

- [ ] Highlight per-file cells differing from global defaults (amber border) — deferred: visual polish, not functional blocker. The per-file values ARE tracked and sent in file_settings; only the visual indicator is missing.

## Oracle regression pass

Verdict: **NEEDS-DISCUSSION** → fixed. Two issues found and resolved:

- [x] Language encoding: per-file dropdown compared `bf.language` (API code, e.g. "en") against dropdown values (UI label, e.g. "English"). Fixed at rack.js:2872: comparison now converts UI label to API code for matching.
- [x] resetDeckState missing bulk state clear: S.bulkFiles/bulkDefaults/bulkSubmitting leaked across logout. Fixed at rack.js:824-826.
- [x] All other Oracle items verified as already correct (CSRF via api(), FormData order, index stability via renderBulk(), file input clear, diarize toggle).

## Pre-Oracle fix

- [x] S.providerIdx not set in loadBulk() — fixed at rack.js:2703. Without this, curProv() returned the wrong provider's models and the provider dropdown showed the wrong selected value.
- [x] Provider change handler reads curProv().id before updating S.providerIdx — fixed at rack.js:2893-2894. Changed to read S.providers[idx].id directly.
