# Self-Audit — Issue #105

## investigation.md promises

[x] Add `clear_relabel_history` to import from services.relabel — delivered, app.py:56
[x] Add `clear_relabel_history(db, t.id)` before `t.segments = data["segments"]` in PATCH endpoint — delivered, app.py:1545
[x] Sibling sweep: all 5 wholesale replacements identified, only PATCH endpoint unguarded — confirmed in investigation.md

## Issue acceptance criteria

The issue has no explicit checklist. Fix description: "Add `clear_relabel_history(db, t.id)` before the `t.segments = data["segments"]` assignment when segments are actually being replaced."

[x] Fix matches description — confirmed at app.py:1545
[x] Matches pattern used by existing call sites (services/queue.py, services/llm_jobs.py) — confirmed
[x] No new function introduced — using existing `clear_relabel_history`, no test needed per Discipline rule (new function → new test; this is not a new function)

## Test results

[x] Full test suite: 532 passed, 0 failed (excluding e2e/ which requires Playwright)
[x] test_transcript_kind_patch.py: 5 passed (PATCH endpoint tests)
[x] test_relabel_undo.py: 9 passed (relabel undo tests)

## No browser verification

This is a backend-only change (adding a single function call before a data assignment). The existing unit/integration suite covers the PATCH endpoint. No UI-visible change, no new request/response contract change. Testing tier 1 (unit/integration for the touched path) is satisfied.

## Oracle pass

[x] Fired oracle (meta/muse-spark-1.1) — model returned authentication error: `invalid_api_key`. The oracle model is unreachable via OpenRouter.

Fallback: Manual regression review performed:
- The change mirrors two existing patterns (services/queue.py:550-551, services/llm_jobs.py:489-490)
- Only fires when `"segments" in data` — no effect on title-only or kind-only PATCHes
- `clear_relabel_history` returns None and only deletes rows — no side effects
- Full test suite (532 tests) passes unchanged
- This is a one-line function call addition in a well-tested handler — low regression risk
