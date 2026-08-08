# Investigation: Issue #208 — Cost analytics 2/4: API endpoints + serializer cost fields

**Target**: #208 (child of tracking issue #204)
**Worktree**: `C:/Claude/whisperdesk-sisyphus-208` (branch `issue-208-sisyphus`, based on `origin/master` @ `f6d0f9d`)
**Main checkout**: `C:/Claude/whisperdesk` (`master` @ `ac87adf`)

## Prerequisites from #207 (merged, PR #211)

### `services/pricing.py` (54 lines)

- `STT_RATES`: `dict[tuple[str, str], dict]` — locked rate table, 5 entries:
  - `("groq", "whisper-large-v3-flash")`: $0.004/min
  - `("groq", "whisper-large-v3-turbo")`: $0.006/min
  - `("openai", "whisper-1")`: $0.006/min
  - `("assemblyai", "universal-3-pro")`: $0.0035/min
  - `("openrouter", "deepgram/nova-3")`: $0.0043/min
- `LOCAL_STT_PROVIDERS`: `set[str]` — `("builtin", "moonshine")`, returns $0.00
- `get_stt_rate(provider, model) -> dict`: returns `{rate_per_minute, rate_source}`
- `get_provider_stt_rate(provider) -> dict`: returns first matching model's rate for a provider

### `services/cost.py` (151 lines)

- `transcript_cost(db: Session, transcript: Transcript) -> dict`: Full breakdown (stt, correction, summary, total, rate_source each)
- `provider_cost(db: Session, user_id: int, provider: str, since: datetime) -> dict`: Aggregated STT cost per provider since cutoff
- `estimate_cost(provider: str, model: str, duration_seconds: float) -> dict`: Pre-submit estimate (no DB needed)
- Private helpers: `_llm_job_cost`, `_resolve_openrouter_rate`

## Serializer call sites analysis

### `_serialize_transcript` (lines 303-353) — detail serializer

7 call sites, ALL single-transcript context. No N+1 risk.

| Line | Route | Context |
|------|-------|---------|
| 1179 | `transcribe_audio` sync path | Returns single transcript as API response |
| 1243 | `transcribe_audio` async path | Returns single transcript as API response |
| 1349 | `GET /api/transcripts/{id}` | Returns single transcript as API response |
| 1601 | `PATCH /api/transcripts/{id}` | Returns single transcript as API response |
| 1788 | `rename_transcript_speaker` | Wraps in `{"renamed": ..., "transcript": ...}` |
| 1829 | `retag_transcript_segments` | Wraps in `{"retagged": ..., "transcript": ...}` |
| 1870 | `undo_last_relabel` | Wraps in `{"undone": ..., "transcript": ...}` |

### `_serialize_transcript_summary` (lines 553-584) — list/bank serializer

2 call sites, BOTH list comprehensions. N+1 risk surface.

| Line | Context | Details |
|------|---------|---------|
| 602 | `_build_recent_transcripts` search path | `[_serialize_transcript_summary(db, t, tags=...) for t in paged]` |
| 613 | `_build_recent_transcripts` default path | `[_serialize_transcript_summary(db, t, tags=...) for t in transcripts]` |

### N+1 risk assessment

- `_serialize_transcript`: Safe — only single-transcript calls. Adding `transcript_cost()` adds one extra DB query per detail page load.
- `_serialize_transcript_summary`: N+1 if `transcript_cost()` is called per row. However, STT cost can be computed purely from fields already loaded on the Transcript object (`duration_seconds`, `provider`, `model`) via `get_stt_rate()` lookup — zero additional DB queries. LLM job costs require querying `LlmJob` rows, which IS N+1.

**Decision**: `_serialize_transcript_summary` gets STT-only cost (computed inline, no DB). Detail gets full breakdown via `transcript_cost()`. This avoids N+1 on list views while still showing a meaningful cost number. The summary already has duration/provider/model; the STT rate lookup is a dict lookup, not a DB query.

## Existing cost/pricing references (none relevant)

- No `cost` or `pricing` column on Transcript model (database/__init__.py)
- No per-transcript cost display in frontend (static/rack.js)
- `model_catalog.py:_price_note()` is per-model OpenRouter token pricing (model picker tooltips), unrelated
- `app.py:2440-2441` mention of "cost-aware model shortlist" is model-picker UI, unrelated

## Route registration pattern

All routes use FastAPI decorator syntax:
```python
@app.get("/api/...")
async def handler_name(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ...
    return {...}
```

POST endpoints accept JSON body via `data: dict = Body(...)`:
```python
@app.post("/api/...")
async def handler_name(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ...
    return {...}
```

Service imports are at top of file (lines 30-62). Current imports do NOT include `services/pricing` or `services/cost` — need to add them.

## Implementation plan

### 1. Add imports to app.py
```python
from services.pricing import get_stt_rate
from services.cost import transcript_cost, provider_cost, estimate_cost
```

### 2. Add three endpoints

**`GET /api/costs`** — per-provider monthly + lifetime totals
- Call `provider_cost(db, current_user.id, provider, since)` for each provider
- Aggregate into response dict
- Use `datetime.now()` minus 30 days for "current month"

**`GET /api/transcripts/{id}/cost`** — detail breakdown for one transcript
- Fetch transcript, call `transcript_cost(db, transcript)`, return the dict

**`POST /api/costs/estimate`** — pre-submit estimate
- Accept `{provider, model, duration_seconds}`
- Validate: missing fields, negative duration, unknown provider
- Call `estimate_cost(provider, model, duration_seconds)`

### 3. Add `cost` to `_serialize_transcript` (detail)
- Add `"cost": transcript_cost(db, t)` to the returned dict
- One extra DB query per detail page load (acceptable)

### 4. Add `cost` to `_serialize_transcript_summary` (list)
- Add a `cost_map: dict[int, dict] | None = None` parameter (defaults to None = not computed)
- If `cost_map` is provided, add `"cost": cost_map.get(t.id)` to returned dict
- In `_build_recent_transcripts`, batch-compute STT costs BEFORE the list comprehension:
  - Iterate transcripts, compute STT cost from `duration_seconds`, `provider`, `model` using `get_stt_rate()`
  - Build a `cost_map: dict[int, dict]`
  - Pass to `_serialize_transcript_summary`
- STT cost computation is a dict lookup + arithmetic, zero additional DB queries

### 5. Test plan
- Endpoint tests (TestClient): `/api/costs`, `/api/transcripts/{id}/cost`, `/api/costs/estimate`
- Estimate validation: missing fields, negative duration, unknown provider -> 400/422
- Local ($0) transcript: verify cost is 0
- Serializer: verify `_serialize_transcript` returns `cost` field
- Serializer: verify `_serialize_transcript_summary` returns `cost` when `cost_map` provided
- Mutation check all new tests

### 6. Acceptance criteria walk
- [ ] Three endpoints implemented, wired via `@app.get`/`@app.post` decorator pattern (same as sibling endpoints)
- [ ] `_serialize_transcript` returns `cost` breakdown
- [ ] `_serialize_transcript_summary` returns STT `cost` value (batch-computed, no N+1)
- [ ] No N+1 regression on list serialization — confirmed: STT cost computed from already-loaded fields, zero additional DB queries per row
- [ ] `POST /api/costs/estimate` validates input (missing/negative/unknown -> clean error, not 500)
- [ ] Tests: TestClient for each route, estimate validation cases, local ($0) transcript
- [ ] #204 design decision: "fold transcript cost into serializer, keep monthly summary separate" — matches: cost on both serializers, `/api/costs` is separate endpoint

## Sibling sweep

### Complement Rule check
- Both serializers (`_serialize_transcript` and `_serialize_transcript_summary`) get `cost` — confirmed in plan
- All 7 `_serialize_transcript` call sites get `cost` automatically (it's in the dict)
- Both 2 `_serialize_transcript_summary` call sites get `cost` via the new `cost_map` parameter

### Code shape siblings
- No other serializers exist that return transcript data (checked: `_serialize_summary` at line 398 serializes a Summary object, not a transcript — unrelated)
- No other cost/estimate functions exist to mirror
- No other providers/models beyond what's in `STT_RATES` (pricing.py is the single source)

## Phase 1.5: completion-race check

**Not applicable.** This issue adds read-only endpoints and serializer fields. No job/state completion paths are modified. No side effects triggered on completion.
