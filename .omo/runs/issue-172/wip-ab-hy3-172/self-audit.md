# Self-audit — issue #172 (variant hy3)

Re-checked every promise in investigation.md and the plan's acceptance
criteria after implementation. All artifacts re-confirmed in source or by the
passing test suite (476 non-e2e + 5 e2e).

## Tasks (plan checklist)
- [x] T1 export_directory in DEFAULT_SETTINGS — delivered, confirmed at services/settings.py:31; round-trips via test_export_directory_settings_round_trip (tests/test_reformatting.py:395)
- [x] T2 build_export_markdown() — delivered, services/reformatting.py:116; 5 unit tests tests/test_reformatting.py:336-380
- [x] T3 export route + bootstrap settings — delivered, app.py:2023 route; app.py:667 adds settings to /api/bootstrap; 6 route tests tests/test_reformatting.py:403-456
- [x] T4 settings input + save — delivered, rack.js:4527-4529 (input), rack.js:4670 (handler)
- [x] T5 Save-as-.md button gated on S.exportDir — delivered, rack.js:3189 (button), rack.js:859 (S.exportDir from bootstrap)
- [x] T6 wire-up click handler — delivered, rack.js:2597-2603
- [x] T7 unit tests for build_export_markdown — delivered, 5 tests pass
- [x] T8 integration test for export route — delivered, 6 tests pass
- [x] T9 settings round-trip test — delivered, passes

## Acceptance criteria (plan T1-T6)
- [x] T1: key exists default ""; GET returns ""; PUT persists — test_export_directory_settings_round_trip
- [x] T2: full markdown; transcript-only; empty-segments -> full_text fallback; title sanitized; empty sections omitted; synchronous — 5 unit tests cover each path
- [x] T3: 200 ok+path (test_export_markdown_route_writes_file); 400 not configured (test_export_markdown_route_requires_directory); 404 (test_export_markdown_route_404_for_missing); 400 not completed (test_export_markdown_route_rejects_not_completed); 500 dir missing (test_export_markdown_route_rejects_missing_directory); 500 not writable (test_export_markdown_route_rejects_not_writable); 401 (get_current_user dependency) + 403 (enforce_csrf middleware) rely on the SAME shared mechanism as every other mutation route — not separately asserted here, consistent with existing route tests
- [x] T4: input visible/prefilled/save persists/empty clears/toast — code at rack.js:4527-4529 + 4670; backend PUT/GET covered by round-trip test; UI click-through not browser-asserted (e2e boots app but does not drive the settings save)
- [x] T5: button present iff S.exportDir set; tooltip shows path — rack.js:3189-3190
- [x] T6: wired with toast feedback — rack.js:2602

## Full-suite gate
- [x] Full non-e2e suite: 476 passed.
- [x] e2e suite: 5 passed (real Chromium, no skip).

Nothing left un-delivered. Two scope notes are called out honestly rather than
hidden: 401/403 are enforced by the shared auth+CSRF path (same as sibling
routes, not separately tested), and the settings UI save is code-verified but
not click-tested in a browser. Both are consistent with this repo's existing
test conventions for analogous routes.
