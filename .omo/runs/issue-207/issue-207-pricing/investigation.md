# Issue #207 — STT pricing catalog + cost computation (Phase 1 investigation)

## 1. Target

Issue #207 asks for two new backend files (no API / frontend changes in this PR):

- `services/pricing.py` — per-provider, per-model STT rate catalog, plus an
  LLM-pricing rule that reuses `_price_note()` from `services/model_catalog.py`.
  Shape mirrors `PROVIDER_LIMITS` in `services/queue.py`.
- `services/cost.py` — three functions:
  - `transcript_cost(transcript)` → structured breakdown
    (stt, correction, summary, total; each with `rate_source`).
  - `provider_cost(provider, since)` → sum
    `duration_seconds × rate` for transcripts in billable status within window.
  - `estimate_cost(provider, model, duration_seconds)` →
    `{cost, rate_per_minute, rate_source}`.

Plus unit tests in `tests/test_pricing.py`.

This investigation is read-only Phase 1; the implementation lives in Phase 2
and is **not** in scope for this report.

## 2. Files read (worktree `C:/Claude/whisperdesk-issue-207-pricing/`)

| File | Lines | Purpose |
|------|-------|---------|
| `services/model_catalog.py` | 1–159 | `_price_note()` (line 73), `_openrouter_live_models()` (line 55), `get_correction_models()` (line 128) |
| `services/queue.py` | 1–80 | `PROVIDER_LIMITS` (line 19), `compute_audio_seconds_used` (line 25) |
| `database/__init__.py` | 31–180 | `Transcript`, `TranscriptionJob`, `LlmJob`, `Summary`, `VoiceNote` models |
| `backends/__init__.py` | 1–126 | `PROVIDER_REGISTRY`, `LOCAL_PROVIDERS`, `list_providers()` (pricing hints live in `description` strings only) |
| `backends/openrouter.py` | 100–149 | `list_models()` and `_default_models()` — confirms `deepgram/nova-3`, `openai/whisper-1`, etc. are valid OpenRouter STT model ids |
| `services/llm_jobs.py` | 270–549 | `run_llm_job()` branches — confirms `LlmJob.result_json` is an output snapshot, not token-usage |
| `app.py` | 303–412, 553–602 | `_serialize_transcript`, `_serialize_summary`, `_serialize_transcript_summary` — no cost field today |
| `tests/test_correction_routing.py` | 1–80 | Test conventions: `db_session` fixture, `_make_user_and_transcript` helper, `_FakeResponse` mock for `httpx` |
| `tests/conftest.py` | 1–80 | `db_session` fixture via `init_db()` per test, `WHISPERDECK_DATA_DIR` isolation |

Sibling-sweep greps (no edits):

- `grep -r "_price_note"` → 2 hits, both in `services/model_catalog.py`
  (definition at line 73; sole caller at line 155 inside `get_correction_models`).
- `grep -r "cost\|pricing\|rate_per_minute\|estimate_cost"` in
  `services/` → only the 6 hits in `services/model_catalog.py` (all
  about the OpenRouter `pricing` dict or human-readable price *labels*
  in the curated model list). No existing cost computation.
- `grep -r "cost\|price\|rate"` in `static/` → 34 hits, all unrelated
  to cost (queue `rate_limited` state, "rate-limited — waiting" UI
  text, audio bitrate, "curated cost-aware shortlist" panel copy).
  No existing cost display surface to conflict with.
- `grep -r "lookup_rate\|RATE_PER_MINUTE\|STT_RATES\|PRICING\|PRICE_BOOK"`
  → no hits. No naming collision.
- `tests/test_pricing*` and `tests/test_cost*` → not present.

## 3. Billable status set

Confirmed from `services/queue.py:25-78` (`compute_audio_seconds_used`)
and `services/queue.py:345` (`TERMINAL_TRANSCRIPT_STATUSES`):

```python
# services/queue.py:345
TERMINAL_TRANSCRIPT_STATUSES = ("completed", "failed", "partial", "cancelled")

# services/queue.py:57
Transcript.status.in_(["completed", "partial"])
```

For #207, **"billable" = `status IN ("completed", "partial")`**. This
is the same set the rate-limit budget already uses, and it's the
right set for cost too: audio was actually sent to the provider in
both cases (`partial` means some chunks succeeded). `failed` and
`cancelled` count for the rate-limit budget (via the TranscriptionJob
side of the union in `compute_audio_seconds_used`), but NOT for cost —
the provider never billed a failed run, and a cancelled one might not
have either depending on timing. Provider_cost() should sum
`duration_seconds` for status `IN ("completed", "partial")` only and
use the cheaper `Transcript.created_at` or `updated_at` index for the
window predicate (`updated_at` matches the queue's pattern, but
`created_at` is more semantically correct for "what was billed in
this window"; this is a small follow-up for the implementer).

## 4. LLM cost feasibility — what the schema actually supports

This is the section the issue gets slightly wrong, and where the
implementer will save themselves time by checking the schema first.

### STT side: clean

`Transcript` has `provider` (String(64)) and `model` (String(64)) —
both stored on completion, both directly lookup-able against the
catalog. `duration_seconds` (Float) is set in `_finalize_if_done`
(`services/queue.py:554`). Cost is `duration_seconds / 60 *
rate_per_minute`. The transcript side is fully costable from a
local catalog lookup alone.

### LLM side: token counts are NOT stored

`LlmJob` columns relevant to cost (`database/__init__.py:93-112`):

```python
class LlmJob(Base):
    id, user_id, transcript_id, kind, status, attempts,
    progress_done, progress_total,
    provider, model, error, dismissed, result_json,
    created_at, updated_at
```

There is **no `usage` / `prompt_tokens` / `completion_tokens` column**
on `LlmJob`, and `result_json` is an output snapshot (e.g.
`{"corrected_text": "..."}` for correction, `{"tags": [...]}` for
tagging), not a usage record. I confirmed by reading
`services/llm_jobs.py:run_llm_job` lines 270–549 — none of the
branches (`correction`, `summary`, `format_*`, `classify_intent`,
`tagging`, `voice_note`, `rediarize`, `voice_match`) extracts
`response.usage` from the upstream LLM response.

The only places `prompt_tokens`/`completion_tokens` appear in the
codebase are:

- `scripts/llm_stub.py:87-88` — a stub for tests, not real.
- `scripts/test_correction_models.py:36` — a debug script.

So **the app currently throws away token usage at the response layer**.
This is a real gap, but it is **out of scope for #207** (the issue
says "no API or frontend changes in this PR" and "library code"), and
fixing it would require either:

- Modifying every LLM call site to capture `response.usage` and
  persist it on `LlmJob` (schema migration, behavior change), or
- Adding a generic usage-capturing wrapper around the OpenAI client.

Neither is in the issue's text or the issue's repository. The
correct read of the issue is: **LLM cost is best-effort and
mostly unknowable from the data we keep today.** The implementer
should not invent a token-capture pass as part of #207.

`Summary` (`database/__init__.py:139-152`) only stores `provider`
and `model` — same shape as LlmJob. No token counts. Same gap.

### What we CAN say about LLM cost

For `transcript_cost()`'s `correction` and `summary` fields:

- **Provider is "local" or "local_llm"** → cost $0.00, source `"free"`.
  This covers Lemonade/Ollama/Whisper.cpp local endpoints. The
  `LOCAL_PROVIDERS` tuple in `backends/__init__.py:37` is the
  authoritative set of transcription-side local providers; for
  LLM jobs we'd want an analogous set or just match
  `provider in ("local", "local_llm")`.
- **Provider is "openrouter"** → if the model is in the live
  catalog at compute time, we can format a `rate_source="live"`
  string (per-token), but **we cannot compute a numeric cost
  without token counts**. The cleanest honest output is
  `{cost: None, rate_source: "live-token-based",
   rate_per_minute: None, rate_note: "$0.14/M in · $0.28/M out"}`
  using `_price_note()`. Or, more conservatively, `rate_source:
  "unknown-token-based"` since we don't actually know the count.
- **Any other provider (groq, openai, replicate, openai-compatible
  local, …)** → cost unknown, source `"unknown-token-based"`.

The issue's "reuse `_price_note()`" line works only for the
**annotation**, not for the number. Implementer should expose
`_price_note()` (drop the leading underscore) or factor a small
wrapper that returns a structured `{prompt, completion, note}`
triple, so `cost.py` can pull the note without re-implementing the
formatting and the existing `get_correction_models()` call site
keeps working unchanged.

## 5. `_price_note` reuse analysis

```python
# services/model_catalog.py:73
def _price_note(model_info: dict) -> str:
    """'$0.14/M in · $0.28/M out' from OpenRouter's per-token pricing."""
    pricing = model_info.get("pricing") or {}
    try:
        prompt = float(pricing.get("prompt", 0)) * 1_000_000
        completion = float(pricing.get("completion", 0)) * 1_000_000
    except (TypeError, ValueError):
        return ""
    if prompt <= 0 and completion <= 0:
        return "free"
    return f"${prompt:.2f}/M in · ${completion:.2f}/M out"
```

What it needs:

- An OpenRouter **model info dict** with a `pricing` sub-dict
  containing `prompt` and `completion` per-token price strings
  (the OpenRouter `/api/v1/models` shape).
- That dict is fetched by `_openrouter_live_models()` (line 55) from
  the live OpenRouter catalog, cached 1 hour.

What we have at compute time:

- An `LlmJob` row with `provider` and `model` strings. The
  `provider` is something like `"openrouter"`, the `model` is
  something like `"deepseek/deepseek-v4-flash"`. **No pricing
  dict is stored.** The dict is only in the in-memory OpenRouter
  cache, which is process-local and not durable.

Two paths:

**(a) Issue's intent (best-effort live lookup)**: At
`transcript_cost()` time, call
`await _openrouter_live_models()` (or its sync read of the cache
if a sync helper is added) and look up `info = live.get(model)`.
If found, return the formatted note as `rate_source="live"`.
If not found (model delisted, network down, 1-hour-old cache
missed the id), return `rate_source="unknown-token-based"`.
This is what the issue's text says, and it's the right behavior
for #207.

**(b) As-is reuse of `_price_note` directly**: the function takes
a dict, so the implementer would need to fetch the dict first.
That fetch is async (it's an httpx call), and `transcript_cost`
should be sync (it's a pure function over a Transcript ORM row,
no DB writes, no I/O). The implementer should:

- Refactor `_price_note` into a small public helper
  (e.g. `format_price_note(model_info: dict) -> str`) that takes
  the dict — no behavior change.
- Add a sync `lookup_openrouter_pricing(model_id: str) -> dict |
  None` that reads from `_openrouter_cache["models"]` only
  (no network). If the cache is cold (None) or the id is absent,
  return None and let the caller tag `rate_source="unknown"`.
- `_openrouter_live_models()` stays async, called by the
  existing picker path and (optionally) by a future warmer.

**(a) and (b) are the same plan, just with the dependency
direction clarified.** The implementer should NOT try to make
`transcript_cost` itself async or do live HTTP fetches.

Critical caveat: `_openrouter_cache` is process-local and never
persisted. A historical job for a model that was in the catalog
last week but delisted this week will return `None` from the
cache after the next cache TTL or restart. That is the correct
behavior for "live" — anything else would silently serve stale
prices. Implementer should NOT add a fallback to a static
catalog of "all OpenRouter models ever" — that's a different
subsystem, and the issue's text doesn't ask for it.

## 6. Call sites / future consumers

### In-scope for #207 (library code only)

This is **library code**. The implementer should:

- Create `services/pricing.py` and `services/cost.py`.
- Create `tests/test_pricing.py`.
- **Do not** wire these into any route, serializer, or frontend
  component. That's #208's job.

### Out of scope, future consumers (do NOT touch in #207)

| Site | Future issue | What it will do |
|------|--------------|-----------------|
| `app.py:303` `_serialize_transcript` | #208 | Add `"cost": transcript_cost(t)` field to the per-transcript dict |
| `app.py:553` `_serialize_transcript_summary` | #208 | Add `"cost": transcript_cost(t)` field to the list-view dict |
| `app.py:398` `_serialize_summary` | #208 | Add a per-summary LLM cost field |
| New `app.py` route `/api/costs` | #208 | `provider_cost(provider, since)` per provider for a time window |
| New `app.py` route `/api/costs/estimate` | #208 | `estimate_cost(provider, model, duration_seconds)` for the pre-upload UI |
| `static/rack.js` | #208/#209 | Display the cost figures; pick models from the estimate response |

The implementer MUST keep this surface clean: `cost.py`'s three
public functions take simple Python types (Transcript, str, float),
no FastAPI imports, no DB-session-passing-API-objects. That mirrors
`services/queue.py`'s pattern (`compute_audio_seconds_used` takes
`db` and primitive args, not a request). Issue #208 will wrap these
in routes; the wrapper does the auth/serialization/JSON-shape work.

### Adjacent code (not integration, but worth noting)

- `backends/__init__.py:48-117` `list_providers()` has human-readable
  pricing hints embedded in the `description` strings (e.g.
  `"whisper-1 · high accuracy, $0.006/min"`). The new catalog in
  `services/pricing.py` is the machine-readable source of truth;
  the `description` strings are display copy. They will drift; the
  new catalog does not need to regenerate them in #207 (out of
  scope), but #208 or later should consider moving them to derive
  from the catalog so they don't drift.
- `services/model_catalog.py:14-31` `CORRECTION_MODELS` has model
  ids in labels like `"Llama 3.3 70B — strong default"`. The new
  catalog does NOT need to back-fill pricing into these labels in
  #207 — `_price_note()` is already annotating them at picker-fill
  time for OpenRouter.

## 7. Sibling sweep results

| Adjacency | Found? | Action in #207 |
|-----------|--------|----------------|
| Existing cost computation | None | n/a |
| Existing pricing display in `static/rack.js` | None (only queue rate-limit UI) | No frontend changes per issue scope |
| `_price_note` callers beyond `get_correction_models` | None (only one definition, one caller) | Refactor `_price_note` to public if needed; do not break the existing caller |
| `lookup_rate` / `RATE_PER_MINUTE` / `PRICING` symbols | None | Free to use these names |
| `services/pricing.py` / `services/cost.py` | Do not exist | New files, no conflict |
| `tests/test_pricing.py` / `tests/test_cost.py` | Do not exist | New file, no conflict |
| `LlmJob` token-usage tracking | None — `result_json` is output-only | Document as a known gap; do NOT fix in #207 (out of scope) |
| `list_providers()` pricing description strings | Yes (`backends/__init__.py:80, 104`) | Adjacency, out of scope; may drift; not a #207 concern |
| `CORRECTION_MODELS` curated model labels | Yes | Adjacency, already priced via `_price_note` for OpenRouter |
| `LOCAL_PROVIDERS` in `backends/__init__.py:37` | Yes (STT side) | Mirror as a local-provider set on the LLM side in `pricing.py` for the $0.00 rule |
| README pricing table | `$0.006/min` for OpenAI in README and `backends/__init__.py` description | Matches the new catalog entry; no doc update needed in #207 |

## 8. Design decisions (recommended)

### 8.1 `services/pricing.py`

Suggested shape (illustrative, not the implementation):

```python
# Per-(provider, model) STT rate, USD per minute. Sourced from each
# provider's published page; see issue #207 for the audit trail.
STT_RATES: dict[tuple[str, str], dict] = {
    ("groq", "whisper-large-v3-flash"):  {"per_minute": 0.004,  "source": "catalog"},
    ("groq", "whisper-large-v3-turbo"):  {"per_minute": 0.006,  "source": "catalog"},
    ("openai", "whisper-1"):             {"per_minute": 0.006,  "source": "catalog"},
    ("assemblyai", "universal-3-pro"):   {"per_minute": 0.0035, "source": "catalog"},
    ("openrouter", "deepgram/nova-3"):   {"per_minute": 0.0043, "source": "catalog"},
    # ...etc.
}

# Local STT providers — any model, $0.00.
LOCAL_STT_PROVIDERS = ("builtin", "moonshine")
```

Lookup helper `lookup_rate(provider: str, model: str) -> dict`:

- If `provider in LOCAL_STT_PROVIDERS` → `{per_minute: 0.0, source: "free"}`.
- If `(provider, model) in STT_RATES` → return the entry.
- Otherwise → `{per_minute: None, source: "unknown"}`. **Never raises.**

A "(provider, any)" wildcard entry for local STT is cleaner than a
separate `LOCAL_STT_PROVIDERS` set, and matches the issue's
"builtin any: $0.00 / moonshine any: $0.00" wording. Implementer
should pick one of the two representations and stay consistent.

### 8.2 LLM pricing rule

`pricing.py` should expose a sync function for LLM cost lookup:

```python
def llm_cost(provider: str, model: str) -> dict:
    """{per_minute, source, note} for an LLM job. LLM pricing is
    per-token, not per-minute, so per_minute is always None; source
    distinguishes free (local), live (OpenRouter catalog hit),
    and unknown (everything else)."""
    if provider in {"local", "local_llm"}:
        return {"per_minute": None, "source": "free", "note": "free (local)"}
    if provider == "openrouter":
        info = _openrouter_cache_synchronous_read(model)  # see §5
        if info is not None:
            note = format_price_note(info)
            return {"per_minute": None, "source": "live", "note": note}
    return {"per_minute": None, "source": "unknown", "note": None}
```

The sync cache read needs a small refactor of
`_openrouter_live_models` so the cache is readable without an async
fetch (read `_openrouter_cache["models"]` if `at` is fresh; else
return None).

### 8.3 `services/cost.py`

```python
def transcript_cost(transcript: Transcript) -> dict:
    """{stt: {...}, correction: {...}, summary: {...}, total: ...} —
    each component has {cost, rate_per_minute, rate_source}."""
    stt = _stt_cost(transcript)
    correction = _llm_cost_for_kind(transcript, "correction")
    summary = _llm_cost_for_kind(transcript, "summary")
    total = sum(c["cost"] for c in (stt, correction, summary) if c["cost"] is not None)
    return {
        "stt": stt,
        "correction": correction,
        "summary": summary,
        "total": total,
    }

def provider_cost(provider: str, since: datetime) -> float:
    """Sum duration_seconds × rate for completed/partial Transcripts
    for `provider` since `since`. Local providers return 0.0."""
    if provider in LOCAL_STT_PROVIDERS:
        return 0.0
    rate = lookup_rate(provider, "<needed>")  # see note below
    # NB: provider_cost only makes sense if all models for a provider
    # share a rate. The issue's locked rates do, but the implementer
    # should pick: per-model sum (most accurate), or assume a
    # single rate per provider (cheaper query). Recommend per-model
    # sum; the `since` window is already an indexed scan.

def estimate_cost(provider: str, model: str, duration_seconds: float) -> dict:
    """{cost, rate_per_minute, rate_source} for a not-yet-run job."""
    rate = lookup_rate(provider, model)
    if rate["per_minute"] is None:
        return {"cost": None, "rate_per_minute": None, "rate_source": rate["source"]}
    return {
        "cost": duration_seconds / 60.0 * rate["per_minute"],
        "rate_per_minute": rate["per_minute"],
        "rate_source": rate["source"],
    }
```

Notes for the implementer:

- `provider_cost(provider, since)` as written takes a single
  `provider`, but transcripts on that provider can use different
  models (e.g. Groq with `whisper-large-v3-flash` at $0.004 and
  `whisper-large-v3-turbo` at $0.006). Recommend: per-(provider,
  model) sub-sum then add. Issue text isn't explicit; this is the
  implementer's call but should be explicit in the docstring and
  covered by a test.
- `transcript_cost` needs to know whether a transcript has had
  correction and/or summary run. Look at `Transcript.corrected_text`
  (line 55) for correction and `Transcript.summary` (relationship
  on line 63) for summary. LLM job rows are an alternative signal
  but the artifact fields are simpler and already reflect what
  the user can see.
- `rate_source` enum (consistent across the three functions):
  `"catalog" | "live" | "free" | "unknown"`. Document this in the
  module docstring; the frontend will switch on it in #208.

### 8.4 Don't change existing files

`services/model_catalog.py:73` `_price_note` and
`services/model_catalog.py:155` (its only call) are NOT in #207's
blast radius. The implementer should leave them alone. If a small
refactor is needed to expose the format function (rename to
`format_price_note`, drop underscore), the implementer may do that,
but they MUST keep the existing call site's behavior identical
(test `tests/test_correction_routing.py` covers this path).

## 9. Test plan (for `tests/test_pricing.py`)

The mutation check: every test must fail if
`transcript_cost`/`estimate_cost`/`provider_cost` bodies are
replaced with `return`. Concretely, that means:

- Assert numeric `cost` values, not just "not None".
- Assert `rate_source` is exactly the expected enum value, not
  just "is a string".
- For `provider_cost`, seed a Transcript with a known
  `duration_seconds` and assert the returned number is the
  product, not just > 0.

Test cases (each one is a separate `def test_*`):

`lookup_rate` (services/pricing.py):

1. Catalog hit: `lookup_rate("groq", "whisper-large-v3-flash")`
   returns `per_minute=0.004, source="catalog"`.
2. Local provider wildcard: `lookup_rate("moonshine", "any-size")`
   and `lookup_rate("builtin", "tiny")` both return
   `per_minute=0.0, source="free"`.
3. Unknown provider/model: `lookup_rate("replicate", "whisper-large-v3-turbo")`
   returns `per_minute=None, source="unknown"`. **No exception.**
4. Empty-string / `None` model handled gracefully (probably
   `source="unknown"`).
5. All 7 locked rates from the issue are present in `STT_RATES`
   (table-driven, one assertion per row).

`estimate_cost` (services/cost.py):

6. Zero duration → `$0.00` cost (with rate_source="catalog" for
   known, "free" for local).
7. 60s on Groq whisper-large-v3-flash → `cost == 0.004`.
8. 60s on Moonshine (any model) → `cost == 0.0`, `source="free"`.
9. 90s on OpenAI whisper-1 → `cost == 0.009`.
10. Unknown provider/model → `cost is None`, `source="unknown"`.

`transcript_cost` (services/cost.py):

11. Build a Transcript (use `_make_user_and_transcript` pattern
    from `tests/test_correction_routing.py:13`), set
    `duration_seconds=120`, `provider="groq"`,
    `model="whisper-large-v3-flash"`, status="completed".
    Assert `cost["stt"]["cost"] == 0.008`, `source="catalog"`,
    `total == 0.008`.
12. Same as 11 but with `model="whisper-large-v3-turbo"` →
    `cost == 0.012`.
13. Local Moonshine transcript → `cost["stt"]["cost"] == 0.0`,
    `source="free"`.
14. Partial status (status="partial") still billable →
    same cost as 11.
15. Failed status → `cost["stt"]["cost"] == 0.0` (not billable).

`provider_cost` (services/cost.py):

16. Seed 3 transcripts on Groq (60s, 120s, 180s) within the
    window, plus 1 on Moonshine (300s) and 1 failed on Groq
    (60s). `provider_cost("groq", since)` returns
    `0.004 * (60+120+180)/60 == 0.024`. **Failed is excluded.**
17. `provider_cost("moonshine", since)` returns `0.0` (local
    short-circuit).
18. Empty DB → returns `0.0`.
19. Old transcripts (before `since`) excluded.

`llm_cost` / LLM components of `transcript_cost` (services/pricing.py,
services/cost.py):

20. `transcript` with `Transcript.corrected_text` non-null AND
    `Transcript.correction_model="groq/llama-3.3-70b-versatile"`
    → `cost["correction"]["cost"] is None`,
    `source="unknown-token-based"`.
21. Same but `correction_model="local/llama3.1"` →
    `source="free"`, `cost == 0.0`.
22. Same but `correction_model="openrouter/deepseek/deepseek-v4-flash"`
    and `_openrouter_cache` is pre-populated (test fixture seeds
    the module-level cache) → `source="live"`, `note` contains
    `"$/M"`.
23. With a `Summary` row → `cost["summary"]` populated similarly.

Test infrastructure:

- Use `db_session` from `tests/conftest.py:71-80` for any test
  that needs a Transcript or Summary.
- Pre-populate `_openrouter_cache` and `_openrouter_cache["at"]`
  directly for the "live" tests rather than mocking httpx — the
  cache is module-level, and writing the dict is more honest
  than patching.
- Pre-populate `_openrouter_cache["at"]` to `time.monotonic()` so
  the cache is fresh within TTL.

## 10. Acceptance criteria walk

Issue #207's acceptance criteria (paraphrased from the issue text):

| # | Criterion | Achievable? | Notes |
|---|-----------|-------------|-------|
| 1 | `services/pricing.py` exists with `STT_RATES` dict mirroring `PROVIDER_LIMITS` shape | Yes | 7 entries per the issue; structure locked |
| 2 | Lookup helper `(provider, model) -> rate` never raises | Yes | Returns `source="unknown"` on miss; tested by test #3, #4 |
| 3 | `services/cost.py` has `transcript_cost(transcript) -> dict` with stt/correction/summary/total | Yes | LLM components are best-effort; see §4 |
| 4 | `services/cost.py` has `provider_cost(provider, since) -> float` summing billable duration × rate | Yes, with caveat | Per-(provider, model) sub-sum recommended; issue doesn't specify |
| 5 | `services/cost.py` has `estimate_cost(provider, model, duration_seconds) -> {cost, rate_per_minute, rate_source}` | Yes | Direct lookup + duration/60 × rate |
| 6 | Reuses `_price_note()` from `services/model_catalog.py:73` for OpenRouter LLM jobs | Partially | Reuses the *formatting* via `format_price_note()`; can't compute a number from the cached info alone (no token counts) |
| 7 | Non-OpenRouter LLM jobs tag `"cost unknown, token-based"` | Yes | All non-OpenRouter, non-local LLM providers |
| 8 | Local LLM = $0.00 | Yes | Match `provider in {"local", "local_llm"}`; or pull from a small set like `LOCAL_STT_PROVIDERS` |
| 9 | Unit tests in `tests/test_pricing.py` | Yes | 23 cases in §9 — implementer may consolidate but the mutation check applies |
| 10 | No API or frontend changes | Yes (in scope to enforce) | The three new functions are library code only; do NOT touch `app.py` or `static/rack.js` |

Net: 9 of 10 criteria are achievable as written. #6 is achievable
in spirit (we surface the OpenRouter live pricing as a `note`) but
not as a numeric cost — that's a data-model gap, not a code gap.
Document this in the PR description.

## 11. Open follow-ups (out of scope, for #208+)

- Wire `transcript_cost` into `_serialize_transcript` and
  `_serialize_transcript_summary` (#208).
- Add `/api/costs` and `/api/costs/estimate` routes (#208).
- Add `LlmJob.usage` column + capture `response.usage` at every
  LLM call site; this unlocks real LLM cost computation (#209 or
  later — separate issue, separate migration).
- Move `list_providers()` description strings to derive from
  `services/pricing.py` so they don't drift (later).

## 12. Out-of-scope (per issue, do not touch)

- No changes to `app.py` (routes, serializers, lifespan).
- No changes to `static/rack.js` or `static/rack.min.js`.
- No changes to `services/model_catalog.py` beyond the optional
  `_price_note` → `format_price_note` rename + sync cache reader.
- No new DB columns or migrations.
- No capture of `response.usage` from upstream LLM APIs.
