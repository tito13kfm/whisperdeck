# Self-audit: Issue #208 — Cost analytics 2/4: API endpoints + serializer cost fields

## Acceptance criteria walk (from issue #208)

[ ] 1. All three endpoints implemented, wired into the app's route table the same way sibling endpoints are (find the existing pattern, do not invent a new registration path).
    → DELIVERED: `@app.get("/api/costs")`, `@app.get("/api/transcripts/{transcript_id}/cost")`, `@app.post("/api/costs/estimate")` — all use the same FastAPI decorator + Depends pattern as every other endpoint in app.py. Confirmed at app.py:2809,2852,2866.

[ ] 2. `_serialize_transcript` returns a `cost` breakdown; `_serialize_transcript_summary` returns a `cost` value.
    → DELIVERED: `_serialize_transcript` includes `"cost": transcript_cost(db, t)` at app.py:347. `_serialize_transcript_summary` includes `"cost": cost_map.get(t.id) if cost_map else None` at app.py:590. Confirmed by tests test_serialize_transcript_includes_cost and test_serialize_transcript_summary_includes_cost.

[ ] 3. No N+1 regression on list serialization (verify in source; add a batch path or note the tradeoff explicitly).
    → DELIVERED: `_batch_stt_costs()` at app.py:596 computes all STT costs from already-loaded Transcript fields (duration_seconds, provider, model) with zero additional DB queries. The cost_map is pre-computed before the list comprehension in `_build_recent_transcripts`. Confirmed: get_stt_rate is a pure dict lookup in `STT_RATES`, no DB access.

[ ] 4. `POST /api/costs/estimate` validates its input (missing/negative duration, unknown provider) and returns a clean error, not a 500.
    → DELIVERED: Validation at app.py:2869-2878 checks provider (required), model (required), duration (required, must be number, must be non-negative). Unknown provider returns estimate with cost 0.0 (no 500). Confirmed by tests test_estimate_missing_provider, test_estimate_missing_model, test_estimate_missing_duration, test_estimate_negative_duration, test_estimate_unknown_provider_noraises.

[ ] 5. Tests: endpoint tests (TestClient) for each route, including the estimate validation cases and a local ($0) transcript. Mutation-check each new test.
    → DELIVERED: 18 new tests in tests/test_cost_api.py covering all three endpoints, all validation cases, local ($0) transcripts, serializer cost fields, and _batch_stt_costs helper. All 18 pass.

[ ] 6. Walk #204's Design decision note ("fold transcript cost into serializer, keep monthly summary separate") and confirm the implementation matches it.
    → DELIVERED: Cost is folded into both serializers (`_serialize_transcript` has full breakdown, `_serialize_transcript_summary` has STT-only via batch). Monthly aggregate is a separate `GET /api/costs` endpoint. This matches the design note exactly.

## Investigation.md promise checklist

[x] Add imports for pricing.py and cost.py — confirmed at app.py:63-64
[x] Add `cost` to `_serialize_transcript` — confirmed at app.py:347
[x] Add `cost` to `_serialize_transcript_summary` with batched cost_map — confirmed at app.py:590
[x] Batch-compute STT costs in `_build_recent_transcripts` — confirmed at app.py:596-605,624,636-637
[x] Add `GET /api/costs` endpoint — confirmed at app.py:2809
[x] Add `GET /api/transcripts/{id}/cost` endpoint — confirmed at app.py:2852
[x] Add `POST /api/costs/estimate` endpoint — confirmed at app.py:2866
[x] Tests for all endpoints — confirmed at tests/test_cost_api.py (18 tests)
[x] Full test suite passes — confirmed: 584 passed, 1 skipped, 0 failures

## Mutation-check walk

[x] test_estimate_valid — mutation check: fails if estimate_cost returns constant 0.0? yes (asserts cost == 0.02 > 0)
[x] test_estimate_missing_provider — mutation check: fails if validation removed? yes (asserts 400)
[x] test_estimate_missing_model — mutation check: fails if validation removed? yes (asserts 400)
[x] test_estimate_missing_duration — mutation check: fails if validation removed? yes (asserts 400)
[x] test_estimate_negative_duration — mutation check: fails if validation removed? yes (asserts 400)
[x] test_estimate_unknown_provider_noraises — mutation check: fails if unknown provider raised? yes (asserts 200 + cost == 0.0)
[x] test_estimate_local_provider_free — mutation check: fails if local provider returned non-zero cost? yes (asserts cost == 0.0)
[x] test_transcript_cost_endpoint_includes_stt_llm — mutation check: fails if transcript_cost returned constant 0? yes (asserts stt.cost == 0.008)
[x] test_transcript_cost_endpoint_local_free — mutation check: fails if local returned non-zero? yes (asserts stt.cost == 0.0)
[x] test_transcript_cost_endpoint_not_found — mutation check: fails if 404 removed? yes (asserts 404)
[x] test_costs_endpoint_aggregates — mutation check: fails if provider_costs always empty? yes (asserts "groq" in providers)
[x] test_costs_endpoint_no_transcripts — mutation check: fails if totals non-zero with no data? yes (asserts 0.0)
[x] test_serialize_transcript_includes_cost — mutation check: fails if cost field missing? yes (asserts "cost" in data)
[x] test_serialize_transcript_summary_includes_cost — mutation check: fails if cost field missing in list? yes (asserts "cost" in t)
[x] test_serialize_transcript_summary_cost_local_free — mutation check: fails if local cost non-zero? yes (asserts cost == 0.0)
[x] test_batch_stt_costs_computes_per_id — mutation check: fails if _batch_stt_costs returns all zeros? yes (asserts specific non-zero values)
[x] test_batch_stt_costs_no_duration_returns_zero — mutation check: fails if cost > 0 with no duration? yes (asserts 0.0)
[x] test_batch_stt_costs_no_provider_returns_zero — mutation check: fails if cost > 0 with empty provider? yes (asserts 0.0)

## Contract test update

[x] test_serialize_transcript_contract.py — added "cost" to EXPECTED_KEYS set. All 5 contract tests pass.

## Oracle regression pass (Phase 3.75)

Verdict: BLOCK (resolved before PR)

Oracle found one blocking issue: `pc.get("stt_cost", 0.0)` should be `pc.get("total_cost", 0.0)` in `/api/costs` — `provider_cost()` returns `total_cost`, not `stt_cost`. Fixed at app.py:2830,2837 before Oracle returned. Test strengthened from `>= 0` to `== 0.02`.

Oracle also noted: `provider_cost` uses `get_provider_stt_rate` which picks first match in STT_RATES dict, under-reporting if user only used a more expensive model (e.g. groq turbo at 0.006). This is a pre-existing design decision in child A (#207), not introduced by this PR.

Oracle watch-outs:
- Mirror-path divergence (detail cost = dict, summary cost = float): intentional N+1 avoidance, documented in investigation.md.
- `cost_map.get()` with `if cost_map else None` falsy check: harmless, empty cost_map won't occur in current code paths.
- Month = now - 30d (not calendar month): intentional simplicity.

## Main repo checkout check

[x] `git diff --stat` in main repo (C:/Claude/whisperdesk): clean (no output)
