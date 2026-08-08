# Issue #209 investigation

**Target:** #209 (standalone) — Cost display UI (detail line + Transcribe estimate)
**Worktree:** `C:/Claude/whisperdesk-issue-209-sisyphus` (branch `issue-209-sisyphus`, from `origin/master` b9c0b44)
**Main repo:** `C:/Claude/whisperdesk`

## Dependency check

- #208 (API endpoints + serializer cost fields): CLOSED, PR #212 merged, on master at 650ba6d.
- Cost API is live on current master.

## API data available

### Detail serializer (`_serialize_transcript`, app.py:305)
Includes `"cost": transcript_cost(db, t)` at line 347. Shape:

```json
{
  "stt": {
    "cost": 0.0,
    "rate_per_minute": 0.004,
    "rate_source": "Groq fixed rate",
    "duration_seconds": 2700
  },
  "correction": {
    "cost": 0.0,
    "rate_per_minute": 0.0,
    "rate_source": "OpenRouter $0.14/M in · $0.28/M out"
  },
  "summary": {
    "cost": 0.0,
    "rate_per_minute": 0.0,
    "rate_source": "no completed job"
  },
  "total": 0.0
}
```

`rate_source` values for `_llm_job_cost` (services/cost.py:48):
- `"no completed job"` — no correction/summary job exists
- `"OpenRouter $0.14/M in · $0.28/M out"` — live pricing resolved
- `"cost unknown, token-based"` — Groq/OpenAI (non-OpenRouter cloud)
- `"Local LLM (free)"` — local/local_llm
- `"unknown"` — fallback

### Estimate endpoint (`POST /api/costs/estimate`, app.py:2858)
Accepts `{provider, model, duration_seconds}`. Returns `{cost, rate_per_minute, rate_source}`.

### Duration available client-side
- **Live capture:** `(Date.now() - S.captureStartedAt) / 1000` — computed from timer
- **File upload:** NO client-side duration extraction — `S.tapeFile` only has `.size`, not `.duration`
- The issue asks: "For files with unknown duration, show the per-minute rate instead"

## Code surfaces to touch

All changes in `static/rack.js` (single file, vanilla JS).

### A. Detail page cost line — `renderDetail()` lines 3898-3907

Current metadata grid (5 columns + Mode button):

```javascript
<div class="unit" style="border-radius:3px;margin-bottom:14px;padding:14px 22px 14px 34px">
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:14px">
    <div>Duration: formatDur(t.duration_seconds)</div>
    <div>Provider: t.provider · t.model</div>
    <div>Status: statusBadge</div>
    <div>Speakers: count + diarization method</div>
    <div>Segments: count</div>
    <div>Mode: toggle-kind button</div>
  </div>
</div>
```

Add a new full-width cost row below the grid, reading `t.cost`:

Format (from issue):
```
STT: $0.12  ·  Groq whisper-large-v3-flash  ·  45 min
Correction: $0.01  ·  Groq Llama 3.3 70B
Summary: free  ·  local
```

Logic:
- STT always present (every transcript has a provider/model/duration)
- Correction line: show if `t.correction_model` is set (job ran), or if `cost.correction.rate_source !== "no completed job"`
- Summary line: show if `t.has_summary` is true, or if `cost.summary.rate_source !== "no completed job"`
- Format cost: `$0.00` if zero, `free` if local, `$X.XX` otherwise
- For token-based (unknown cost), show the rate_source label instead of a dollar amount
- Edge cases: `t.cost` is always present (serializer includes it), but defend against null/missing

### B. Transcribe page estimate — `renderTranscribe()` and `syncTranscribe()`

**Where to add the UI:**
The "signal path" unit (lines 1623-1658) shows Provider/Model/Language knobs. Add an estimate line below the knobs.

**When to update:**
`syncTranscribe()` (line 1803) is called whenever provider/model changes (line 1730, 1735, 1744, etc.). Add estimate update logic there.

**Logic:**
- If a provider/model is selected AND duration is known (capturing or job running with elapsed): call `POST /api/costs/estimate` with `{provider: curProv().id, model: curModel(), duration_seconds: N}`
- If duration unknown: show per-minute rate via the estimate endpoint with `duration_seconds: 60` (yields rate_per_minute directly)
- If no provider selected or not transcribe page: hide estimate
- Format: `"Est. cost: ~$0.12 (Groq · $0.004/min · ~30 min)"` or `"Groq rates: $0.004/min (flash), $0.006/min (turbo)"`

## Sibling sweep

Checked for other locations where cost should be displayed but isn't:

1. **Bank rows / tape library (`renderBankRows`, line 2601):** renders from `_serialize_transcript_summary` which includes `cost` (STT-only, float). Not in scope for this issue (#210 handles Costs page + Queue gauge).
2. **Monitor recents:** same serializer. Not in scope.
3. **No other cost rendering exists anywhere in frontend** — zero references to `t.cost` or `.cost` in rack.js.

No siblings found outside the two deliverables.

## Issue's proposed fix vs reality

The issue's format examples are reasonable but lack detail on:
- Which HTML structure to use (grid row vs nested divs)
- Token-based/correction/summary cost handling (only the format string is specified)
- API call for estimate (it's in the issue but no fetch code is suggested)
- Duration from live capture (timer-based, no file metadata extraction)

Will implement based on actual state available, not the issue's inline snippets.

## E2E selector check

No e2e tests target the detail metadata block or Transcribe provider picker by selector. The three e2e test files (test_logout_polling_cleanup, test_detail_rapid_clicks, test_detail_poll_partial_update) test polling behavior, not UI content. No selector updates needed.

## Acceptance criteria

- [ ] Detail page shows cost breakdown line; unknown/free/paid cases all render sensibly
- [ ] Transcribe page shows live estimate when duration known, rates line when not; updates on provider/model change
- [ ] No e2e selectors broken (confirmed: none target affected areas)
- [ ] Browser drive: load completed transcript, confirm cost line; open Transcribe, pick provider, confirm estimate
