# Issue #269 — Self-Audit

## Acceptance criteria from issue

[x] user can record or import without selecting a mode first — delivered: `S.mode` defaults to `'auto'` at static/rack.js:810, VFD wheel shows Auto at index 0 (static/rack.js:1736), bulk import kind defaults to `'auto'` (static/rack.js:2693)
[x] explicit override remains available where specified — delivered: VFD mode wheel still offers Meeting/Dictation/Voice Note after Auto (static/rack.js:1736), detail page `toggle-kind` 3-state cycle preserved (static/rack.js:4861)
[x] pending/failed classification is not presented as a confident result — delivered: `classStatusText` shows "Classifying..." for pending, "Failed" for failed, "XX% — uncertain" for uncertain (static/rack.js:4687-4690)
[x] UI controls and server-side state agree; no stale mode survives transcript changes — delivered: `S.mode` reset to `'auto'` on session init (static/rack.js:810), `renderDetail()` reads `t.kind` and `t.classification_status` fresh from server each poll tick (static/rack.js:4675-4690)

## Decisions disclosed

[decision] PATCH now accepts `"auto"` kind to allow reverting from manual override back to auto-classification — not specified by the issue, because the design's "Auto" picker implies a way back, and without it a user who accidentally picks a kind is stuck
[decision] When PATCH receives `"auto"`, the stored kind becomes `"meeting"` (placeholder) with `classification_status="pending"` — same pattern as `_run_transcription_pipeline` for fresh auto recordings. This means the `kind` field in the JSON response will say "meeting" not "auto" — the `classification_status="pending"` is the real signal

## Changes verified

[x] `S.mode = 'auto'` — static/rack.js:810, confirmed
[x] VFD mode wheel: 4 options (Auto, Meeting, Dictation, Voice Note) — static/rack.js:1736, confirmed
[x] VFD change handler: array includes 'auto' at index 0 — static/rack.js:1819, confirmed
[x] `mfdSingleSpeaker()` unchanged (auto not single-speaker) — static/rack.js:1722, confirmed
[x] `DEFAULT_BULK_DEFAULTS.kind = 'auto'` — static/rack.js:2693, confirmed
[x] Bulk global kind selector: Auto option added — static/rack.js:2760, confirmed
[x] Bulk per-file kind selector: Auto option added — static/rack.js:2821, confirmed
[x] `kindLabel` + `classStatusText` for classification status display — static/rack.js:4684-4691, confirmed
[x] Mode cell shows provenance text + new title — static/rack.js:4759, confirmed
[x] PATCH accepts `"auto"` and resets to pending — app.py:2000,2007-2014, confirmed
[x] `rack.min.js` rebuilt — 205.2kb, contains "Classifying", "Manual override", "auto"

## Test mutation checks

[x] `test_patch_auto_sets_pending_placeholder` — mutation check: if function body replaced with `return`, kind would not change to "meeting" and status would not become "pending" → test fails? yes (asserts on exact row values)
[x] `test_patch_auto_accepted_by_serializer` — mutation check: if serialization dropped classification_status, test would fail → yes
[x] `test_bulk_transcribe_auto_kind_accepted` — mutation check: if kind validation rejected "auto", test would fail → yes (asserts 200 + pipeline receives "auto")
[x] `test_auto_kind_pending_serialization` — mutation check: if effective_kind leaked into serialize for pending, classify_intent_job would not be None → fails? yes (asserts None on dictation/voice-note job fields)

## Test suite

- `tests/test_transcript_kind_patch.py`: 8 tests (4 existing + 3 new) — PASS
- `tests/test_bulk_import.py`: 21 tests (20 existing + 1 new) — PASS
- `tests/test_serialize_transcript_contract.py`: 9 tests (8 existing + 1 new) — PASS
- Full suite: 723 passed, 0 failed

## Verify self-audit mechanical checker

Running `python scripts/verify_self_audit.py .omo/runs/issue-269/issue-269-sisyphus/self-audit.md`

## Oracle regression pass (Phase 3.75)

Oracle verdict: BLOCK (1 real issue, 2 false positives)

- **BLOCK: PATCH auto not enqueuing classification job** — FIXED. Added `enqueue_pipeline_classify(db, t, user_settings)` after the auto branch sets pending. Without this, a completed transcript PATCHed back to auto would stay pending forever (correction already finished, so correction-completion trigger never fires).
- False: "Upload path still 400s on auto" — Backend already accepts "auto" for upload (app.py:1373) and bulk (app.py:1450,1466). These were delivered by #266-268 (merged). Oracle was looking at an older base.
- False: "Stale minified bundle" — Bundle was rebuilt and verified. Contains "Classifying", "Manual override", "Auto" in minified output.