# Phase 1 Investigation — Issue #210 (Cost analytics 4/4: Costs dashboard page + Queue budget gauge)

## Real Target Issue
Target issue: #210 ("Cost analytics 4/4: Costs dashboard page + Queue budget gauge")
Derived from tracking issue #204, where child issues #207, #208, and #209 are already CLOSED and MERGED. #210 is the final open child issue.

## Files and Functions Referenced
1. `app.py`:
   - `api_costs_overview` (line 2812): Needs to return `rate_limit_gauge` data along with per-provider monthly and lifetime totals.
   - `_build_jobs_payload` (line 640): Needs to include `rate_limit_gauge` data so `GET /api/jobs` supplies the budget gauge to the Queue page.
   - `get_rate_limit_gauge(db, user_id, provider)` (new helper in `services/queue.py` or `app.py`): Computes used audio-seconds today (`compute_audio_seconds_used(..., 86400)`), daily limit (`PROVIDER_LIMITS`), estimated cost, and reset time (`_oldest_contributing_timestamp`).
2. `services/queue.py`:
   - `compute_audio_seconds_used`, `_oldest_contributing_timestamp`, `PROVIDER_LIMITS`, `DEFAULT_LIMITS`: Existing helpers for audio usage calculation.
3. `static/rack.js`:
   - `PAGES` array (line 412): Add `'costs'` to register the new page.
   - `navigate()` `loaders` map (line 442): Add `costs: loadCostsPage`.
   - `loadCostsPage()` (new function): Fetches `GET /api/costs`, renders monthly spend, lifetime total, budget gauge, and per-provider breakdown table/cards.
   - `loadQueue()` (line 2951): Consumes `data.rate_limit_gauge` from `getJobs()` (`GET /api/jobs`) and renders the usage gauge in `.page-head`.
   - `window` export block (line 5444): Add `loadCostsPage` to exposed window globals.
4. `static/index.html`:
   - Rail nav element: Add `<button class="rail-btn" data-nav="costs"><span class="led"></span><span class="lbl">Costs</span><span class="badge" id="nav-badge-costs"></span></button>`.
   - Main content container: Add `<div class="page" id="page-costs"></div>`.
5. `package.json` & Frontend Build:
   - Must run `npm run build` (`esbuild static/rack.js --bundle --minify --outfile=static/rack.min.js`) so that `static/rack.min.js` and `static/rack.min.css` reflect changes in served single-page app.

## Complement Rule & Sibling Sweep
- **Registration Complement (PAGES + loaders + nav + HTML page container)**:
  - Checked `PAGES` in `static/rack.js`: currently 10 pages (`dashboard`, `transcribe`, `transcripts`, `voicenotes`, `queue`, `detail`, `voices`, `files`, `settings`, `assistant`). Adding `'costs'` makes 11 pages.
  - Checked `loaders` in `navigate()` in `static/rack.js`: 10 page loaders. Adding `costs: loadCostsPage`.
  - Checked `<nav class="rail">` in `static/index.html`: 9 rail buttons (`dashboard`, `transcribe`, `transcripts`, `voicenotes`, `queue`, `voices`, `files`, `assistant`, `settings`). Adding `costs` rail button.
  - Checked `<div class="content">` in `static/index.html`: 10 page divs (`#page-dashboard`, `#page-transcribe`, etc.). Adding `<div class="page" id="page-costs"></div>`.
  - Checked test suite for assertions on nav count, rail button selectors, or page counts (`tests/e2e/test_browser_smoke.py`, `tests/e2e/test_bundle_globals.py`).
- **Sibling Sweep Result**:
  - Swept all test files in `tests/` for hardcoded page counts, `PAGES` length assertions, or nav button selectors.
  - No test asserts exact page count; `tests/e2e/test_browser_smoke.py` checks for `.rail-btn` presence, which remains compatible.
  - Swept all endpoints in `app.py` returning costs or jobs payload. Both `GET /api/costs` and `GET /api/jobs` will carry structured `rate_limit_gauge` payloads.

## What Issue's Suggested Snippet Got Right / Missed
- The issue correctly noted the three-part registration requirement (PAGES, loaders, nav).
- The issue noted using `compute_audio_seconds_used()` for the budget gauge.
- Missed detail: `GET /api/costs` and `GET /api/jobs` needed `rate_limit_gauge` added on the backend so the frontend receives `used_seconds`, `limit_seconds`, `used_cost`, `limit_cost`, and `resets_in_seconds` without duplicate calculations or front-end assumptions.
- Missed detail: `static/rack.min.js` must be rebuilt via `npm run build` because `index.html` serves `rack.min.js`.

## Acceptance Criteria Checklist Walk
1. `[ ]` Costs page registered in PAGES, loaders, nav, and HTML container; empty state renders without error.
2. `[ ]` Per-provider monthly spend and lifetime totals display from `GET /api/costs`.
3. `[ ]` Queue page shows budget gauge alongside rate-limit state; when not rate-limited, shows usage-so-far, not blank.
4. `[ ]` Grep test dirs for Queue-page and nav selectors; update if label/role changed.
5. `[ ]` Drive in a real browser (Playwright MCP) against running server to verify Costs page and Queue page gauge.

## Implementation Plan
1. Backend (`services/queue.py` + `app.py`):
   - Add `get_rate_limit_gauge(db, user_id, provider)` in `services/queue.py`.
   - Update `_build_jobs_payload` in `app.py` to include `rate_limit_gauge`.
   - Update `api_costs_overview` in `app.py` to include `rate_limit_gauge` and rate limit metrics per provider.
2. Frontend (`static/rack.js` + `static/index.html`):
   - Add `'costs'` to `PAGES`.
   - Add `costs: loadCostsPage` to `loaders`.
   - Add `loadCostsPage()` function rendering monthly spend, lifetime spend, budget gauge, and provider table.
   - Add `loadCostsPage` to `window` exports.
   - Update `loadQueue()` to display `budget-gauge` in `.page-head`.
   - Add `<button class="rail-btn" data-nav="costs">` and `<div class="page" id="page-costs">` to `static/index.html`.
3. Build & Test:
   - Run `npm run build` in worktree to update `static/rack.min.js` and `static/rack.min.css`.
   - Write unit tests in `tests/test_cost_api.py` for `rate_limit_gauge` in `GET /api/costs` and `GET /api/jobs`.
   - Run pytest suite (`pytest`).
   - Run live server & Playwright browser test to verify Costs page and Queue page gauge.
