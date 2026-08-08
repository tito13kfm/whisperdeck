# Investigation: Issue #207 — STT Pricing Catalog + Cost Computation

**Target**: #207 (resolved from tracking issue #204, first child, no deps)
**Branch**: `issue-207-deepseek`
**Worktree**: `C:/Claude/whisperdesk-deepseek-207`
**Main repo**: `C:/Claude/whisperdesk`

## 1. Confirmed: billable transcript status set

From `services/queue.py:57` (`compute_audio_seconds_used`):

```python
Transcript.status.in_(["completed", "partial"])
```

This same set is used consistently across `services/queue.py`:
- Line 57: `Transcript.status.in_(["completed", "partial"])` (transcript total)
- Line 70: `Transcript.status.notin_(["completed", "partial"])` (complement for job rows)
- Line 106: `Transcript.status.in_(["completed", "partial"])` (`_oldest_contributing_timestamp`)
- Line 117: `Transcript.status.notin_(["completed", "partial"])` (same function, complement)
- Line 562: `if new_status in ("completed", "partial")` (finalize after all chunks done)

Also in `app.py` as read guards (not billability, but data-access gates):
- Line 2141: `if transcript.status not in ("completed", "partial")`
- Line 2355: `if t.status not in ("completed", "partial")`

Terminal statuses (broader, includes non-billable `failed`, `cancelled`):
- `queue.py:345`: `TERMINAL_TRANSCRIPT_STATUSES = ("completed", "failed", "partial", "cancelled")`

**Decision**: Use `["completed", "partial"]` as the billable status set for `provider_cost()`. This is the narrowest, most-correct set the codebase already uses for audio-seconds accounting.

## 2. Database models relevant to cost computation

### Transcript (`database/__init__.py:31`)
- `provider` (String(64)) — e.g. "groq", "openai"
- `model` (String(64)) — e.g. "whisper-large-v3-flash"
- `duration_seconds` (Float) — duration of the audio
- `status` (String(32)) — pending/processing/completed/failed/partial
- `correction_model` (String(128)) — e.g. "groq/llama-3.3-70b-versatile"
- `created_at` (DateTime)
- `summary` → relationship to Summary (one-to-one)
- `voice_note` → relationship to VoiceNote (one-to-one)

### LlmJob (`database/__init__.py:93`)
- `transcript_id` (Integer, FK)
- `kind` (String(32)) — "correction", "summary", "assistant", etc.
- `status` (String(32)) — pending, running, completed, failed, cancelled
- `provider` (String(64))
- `model` (String(128))
- `progress_done` / `progress_total` (Integer)

### Summary (`database/__init__.py:139`)
- `transcript_id` (Integer, FK)
- `model` (String(64))
- `provider` (String(64))

### VoiceNote (`database/__init__.py:155`)
- `transcript_id` (Integer, FK)
- `model` (String(128))
- `provider` (String(64))

## 3. Existing patterns to follow

### Provider catalog pattern (`services/model_catalog.py:14`)
`CORRECTION_MODELS` — a `dict[str, list[dict]]` keyed by provider, each entry has `id` and `label`. The new `STT_RATES` should follow the same shape.

### Rate limit pattern (`services/queue.py:19`)
`PROVIDER_LIMITS` — a flat dict keyed by provider with nested limits. Simpler than CORRECTION_MODELS since it's per-provider, not per-model.

### `_price_note()` reuse (`services/model_catalog.py:73`)
Takes a single dict with a `pricing` key (containing `prompt` and `completion` floats), returns a string like `"$0.14/M in · $0.28/M out"`. Can be imported directly in `cost.py` for LLM job cost display.

```python
from services.model_catalog import _price_note
```

This function only works with OpenRouter's pricing format — it would need a model_info dict to be fetched. For the LLM cost computation, we can use it if we have access to the OpenRouter live model catalog, but for the MVP in this issue, the spec says: "OpenRouter LLM jobs: use the existing live pricing." That means we'd need to fetch from the OpenRouter API. But since `_price_note` is synchronous and the catalog is async-fetched, the simplest approach for `transcript_cost()` is to note the LLM cost as a display string derived from `_price_note` if available, or just mark it with the rate_source.

**Decision**: For the LLM part of `transcript_cost()`, we'll fetch the live model info for OpenRouter jobs (reusing the existing `_openrouter_live_models` cache), call `_price_note()` to get the display rate, and include that as `rate_source`. For non-OpenRouter LLM jobs, tag as `"cost unknown, token-based"` per the locked spec. For local LLM, `$0.00`.

### Test patterns
Tests use:
- `db_session` fixture (conftest.py:72) — fresh SQLite per test via `init_db()`
- Direct imports from `database` and `services.*`
- Helper functions to create test data (e.g., `_make_user_and_transcript`)
- `from unittest.mock import AsyncMock, patch` when needed

## 4. Sibling sweep

### Other billable status definitions
No other billable status set exists beyond `["completed", "partial"]` in `queue.py`. The `TERMINAL_TRANSCRIPT_STATUSES` tuple includes `failed` and `cancelled` but those are not billable (no audio was successfully transcribed).

### Other duration_seconds consumers
- `app.py:318`: serializer output
- `app.py:497`: admin stats query
- `app.py:1119`: setting duration on upload
- `app.py:1132`: local chunking threshold
- `app.py:1138`: chunk size calculation
- `app.py:1171`: saved on transcript
- `services/queue.py:489,554`: chunk finalization
- `services/transcription.py:124`: inline transcription
- Various test files: creating test transcripts

None of these compute a cost — the field is purely a duration until now.

### Other provider/model-keyed lookups
- `backends/__init__.py:24`: `PROVIDER_REGISTRY` (provider → class)
- `backends/__init__.py:37`: `LOCAL_PROVIDERS` tuple
- `services/model_catalog.py:14`: `CORRECTION_MODELS` (provider → model list)
- `services/queue.py:19`: `PROVIDER_LIMITS` (provider → rate limits)

## 5. What the issue's spec gets right/wrong

### Right
- Billable status set of `["completed", "partial"]` confirmed in source
- STT rates table is complete and matches what `list_providers()` already displays
- LLM pricing rules are correct: OpenRouter has live pricing via `_price_note`, others are unknown, local is free
- `_price_note` is importable and reusable

### Needs clarification
- **LLM cost computation**: The spec says "OpenRouter LLM jobs: use the existing live pricing already resolved by `_price_note()`." But `_price_note()` takes a `model_info` dict from the OpenRouter API — in `transcript_cost()`, we'd need to fetch the live model info for each LlmJob's model. We can't just call `_price_note` on a bare model ID. We should either:
  1. Fetch the live OpenRouter catalog (reusing `_openrouter_live_models()` cache) and look up each model's pricing, or
  2. Store just the `rate_source` string (`"OpenRouter live pricing"`) and let the consumer decide.
  
  **Decision**: For `transcript_cost()`, fetch the live OpenRouter catalog when processing OpenRouter LLM jobs, call `_price_note()` to get the display string, and include it as `rate_source`. This is cheap (cached ~1h) and provides the actual pricing to the consumer.

## 6. Implementation plan

### New file: `services/pricing.py`
- `STT_RATES`: dict keyed by `(provider, model)` → `{"rate_per_minute": float, "rate_source": str}`
- `get_stt_rate(provider, model)` → rate entry or `{"rate_per_minute": 0.0, "rate_source": "free"}` for local/unknown
- Local providers (builtin, moonshine) not stored; handled by `get_stt_rate` returning free sentinel

### New file: `services/cost.py`
- `transcript_cost(db, transcript)` → dict with `stt`, `correction`, `summary`, `total`, each with `cost` and `rate_source`
- `provider_cost(db, user_id, provider, since)` → dict with `total_seconds`, `total_cost`, `rate_per_minute`, `rate_source`
- `estimate_cost(provider, model, duration_seconds)` → dict with `cost`, `rate_per_minute`, `rate_source`

### New file: `tests/test_pricing.py`
- Test known paid provider (Groq whisper-large-v3-flash)
- Test local ($0) provider
- Test unknown pair (no raise)
- Test mixed transcript (STT + OpenRouter correction + local summary)
- Mutation checks on all new functions
