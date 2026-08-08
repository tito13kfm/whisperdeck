# Wrong Directions: Issue #207

## 1. `_price_note` is private (`model_catalog.py`)
The issue says "use the existing live pricing already resolved by `_price_note()` in `model_catalog.py` (line ~73)." The `_` prefix means it's a private function. Cross-module private imports are technically fine in Python but not ideal. **Recommendation**: For now, import it with the `_` prefix (it's already established as the pricing formatter in this codebase). If this grows, consider making it public.

## 2. `_price_note` takes OpenRouter API response dict, not model ID
The issue implies `_price_note()` can be called on any LLM job's model. It cannot. It takes a dict with a `pricing` key (OpenRouter API shape: `{"pricing": {"prompt": 0.00000014, "completion": 0.00000028}}`). For `transcript_cost()`, we need to:
1. Fetch the live OpenRouter model catalog (reusing `_openrouter_live_models()` cache)
2. Look up the specific model's info dict
3. Pass that info dict to `_price_note()` to get the display string

The issue text is ambiguous about this but the implementation handles it correctly.

## 3. No token counts stored anywhere
`LlmJob` has `progress_done`/`progress_total` (chunk counts) but no `input_tokens`/`output_tokens`. The spec says LLM costs are "best-effort" and tags non-OpenRouter as "cost unknown, token-based" — this is correct given the data available. No API change needed in this slice.

## 4. Provider cost: STT rate per provider
`provider_cost()` computes STT cost by provider. Different models within the same provider have different rates (e.g., Groq: flash=$0.004/min, turbo=$0.006/min). The implementation resolves this by using the rate of the first model matching the provider, with a note. This is a known limitation — a per-model aggregate would need a different query. Acceptable for this slice since the spec says "SUM(duration_seconds) x STT rate" (singular rate).

## 5. Oracle: `asyncio.run()` in sync context (must fix before slice 2)
`_resolve_openrouter_rate()` calls `asyncio.run(_openrouter_live_models())`. This works in tests (no event loop running) but will raise `RuntimeError` inside a FastAPI async handler because an event loop is already running. The `except Exception` catches it and returns "network error," so it won't crash, but OpenRouter rate display will always degrade in production. Must be converted to async (make `transcript_cost` async, or inject cached live models) before wiring into API endpoints in slice 2 (#208).

## 6. Oracle: `get_provider_stt_rate` first-match ambiguity for multi-rate providers
Groq has two STT models with different rates (flash $0.004/min, turbo $0.006/min). `get_provider_stt_rate("groq")` returns the first match by dict insertion order (flash at 0.004). If a user only uses turbo, `provider_cost` under-bills by 33%. Needs a per-model weighted sum or a defined policy (max rate? weighted average?) before slice 2.
