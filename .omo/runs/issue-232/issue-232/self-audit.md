# Self-Audit: Issue #232 — Batch Management API

## Investigation promises

[x] GET /api/batches route — delivered, confirmed at `app.py:1496-1560`
[x] GET /api/batches/{batch_id} route — delivered, confirmed at `app.py:1563-1603`
[x] POST /api/batches/{batch_id}/cancel route — delivered, confirmed at `app.py:1606-1649`
[x] All call sites in scope handled — no missed siblings per Phase 1 sweep
[x] Reuse _serialize_transcript() + _batch_latest_jobs() for detail — confirmed at `app.py:1597,1603`
[x] Reuse cancel_transcript_jobs() for cancel — confirmed at `app.py:1638`
[x] All #231 tests still pass — confirmed, 18/18 in test_bulk_import.py + all 622 tests green

## New tests — mutation checks

[x] test_list_batches — mutation check: fails with SQL query returning empty? yes (removing GROUP BY or batch_id filter → wrong counts)
[x] test_list_batches_empty — mutation check: null guard missing → null batches appear? yes (asserting empty list, removing isnot(None) → null batch appears)
[x] test_list_batches_limit_offset — mutation check: limit/offset not applied → all 5 returned? yes
[x] test_get_batch_detail — mutation check: serializer missing → empty fields? yes (asserting batch_id, title, duration_seconds)
[x] test_get_batch_not_found — mutation check: 404 guard missing → 200? yes
[x] test_get_batch_other_user_excluded — mutation check: user isolation missing → wrong user's batch visible? yes
[x] test_cancel_batch_all_pending — mutation check: cancel not called → status unchanged? yes (refreshing and asserting cancelled)
[x] test_cancel_batch_mixed_statuses — mutation check: terminal overwritten → status changed? yes (asserting completed/failed unchanged)
[x] test_cancel_batch_not_found — mutation check: 404 guard missing → 200? yes
[x] test_cancel_batch_idempotent — mutation check: double-counting → cancelled > 0 on second call? yes
[x] test_cancel_batch_no_active — mutation check: all-terminal counted as cancelled? yes (asserting cancelled=0)

## Acceptance criteria from issue #232

[x] GET /api/batches returns batches with aggregate stats — delivered, test_list_batches confirms counts, duration, first_title
[x] GET /api/batches/{batch_id} returns transcripts in batch — delivered, test_get_batch_detail confirms transcript list with full serialization
[x] POST /api/batches/{batch_id}/cancel cancels active transcripts — delivered, test_cancel_batch_all_pending and test_cancel_batch_mixed_statuses confirm
[x] Null batch_id transcripts excluded from batch listing — delivered, test_list_batches includes t5 with batch_id=None, test_list_batches_empty confirms
[x] All tests green including #231 tests — delivered, 621/621 pass

## Main repo checkout

[x] Main repo clean — `git -C C:/Claude/whisperdesk diff --stat` shows no output (only untracked .omo/runs/ files)

## Phase 1.5

N/A — no job/state completion path modified. cancel_transcript_jobs is called as-is with no changes to its internals.

## Not delivered

None — all investigation promises and issue criteria delivered.

## Oracle regression pass (Phase 3.75)

Verdict: NEEDS-DISCUSSION → resolved.

Two findings, both fixed:

1. **Cross-user title leak in list_batches** (BLOCK): `first_transcripts` subquery had no `user_id` filter. If two users collided on same batch_id, attacker could see victim's first_title. Fixed by adding `Transcript.user_id == current_user.id` to the subquery (the title join is transitively protected since `min_id` is now user-scoped). Also removed dead `if first_transcripts is not None:` guard (subquery() always returns truthy).

2. **cancel_batch session rollback** (NEEDS-DISCUSSION): `cancel_transcript_jobs` does `db.commit()` internally. If one fails, the session enters a failed state and subsequent iterations fail with `PendingRollbackError`. Fixed by adding `db.rollback()` in the except block.

Re-ran test_batch_api.py + test_bulk_import.py after fixes: 29/29 pass.

## /audit-pr (GLM 5.2 independent review)

Verdict: BLOCK → resolved.

Three findings, all fixed:

1. **Vacuous first_title assertion** (BLOCK): `test_list_batches` used `assert b_a["first_title"] in ("Test", None)`, which accepts `None` — a broken title lookup would silently pass. Fixed: changed to `assert b_a["first_title"] == "Test"`.

2. **Missing partial-failure test** (SHOULD FIX): Issue #232 requested `test_cancel_batch_partial` but the errors-array code path was untested. Fixed: added `test_cancel_batch_partial_failure` that monkeypatches the second call to raise, verifies cancelled=2, errors=1, and t1/t3 still cancelled while t2 stays pending (rollback).

3. **Non-deterministic list_batches order** (NIT): `order_by(func.min(created_at).desc())` with no tiebreaker — batches sharing a timestamp (common in bulk uploads) return in unstable order. Fixed: added `Transcript.batch_id` as secondary sort key.

Re-ran full suite after audit fixes: 12/12 test_batch_api.py, all 622 tests green.

### Audit-flagged inaccuracies in self-audit, corrected above:
- "39/39 in test_bulk_import.py" → 18/18 (the file has 18 tests, not 39; the 39 was a miscount)
- "user_id filter added to both the subquery and the title join" → filter is on the subquery only; title join protected transitively
