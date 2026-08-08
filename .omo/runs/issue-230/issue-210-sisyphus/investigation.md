# Investigation: PR #230 (closes #210)

## Phase 0: Target resolution

`/issue 230` was invoked. `gh issue view 230` returns a PR (GitHub shares
issue/PR numbering), not a standalone issue. PR #230 is an OPEN pull request
on branch `issue-210-sisyphus` that closes issue #210 ("Cost analytics 4/4:
Costs dashboard page + Queue budget gauge").

**Real target: issue #210**, already implemented by PR #230. This run audits
the existing PR rather than creating a new one.

## Worktree paths

- PR worktree: `C:/Claude/whisperdesk-issue-210-sisyphus` (branch `issue-210-sisyphus`)
- Main repo: `C:/Claude/whisperdesk` (branch `master`)

## Issue #210 acceptance criteria

1. New Costs page registered in all three places (PAGES, loaders, nav) and
   reachable; empty state renders without error.
2. Per-provider monthly spend and lifetime totals display from GET /api/costs.
3. Queue page shows the budget gauge alongside the existing rate-limit state;
   when not rate-limited, the gauge still shows usage-so-far, not a blank.
4. Grep the e2e/test dirs for Queue-page and nav selectors; update if a
   label/role changed. If any nav-count test asserts the number of pages/nav
   items, update it.
5. Drive in a real browser (Playwright MCP) against a running server.

## Files changed (against origin/master, excluding rack.min.js)

- `app.py` — import `get_rate_limit_gauge`; add gauge to `_build_jobs_payload`
  and `GET /api/costs`
- `services/queue.py` — new `get_rate_limit_gauge()` function
- `static/index.html` — Costs nav button + `#page-costs` page container
- `static/rack.js` — `PAGES` array, loaders map, `loadCostsPage()`, Queue
  gauge, window export
- `static/rack.min.js` — esbuild recompile (verified: contains all new strings)
- `tests/e2e/test_costs_ui_e2e.py` — new browser e2e test
- `tests/test_bootstrap.py` — updated jobs payload assertion
- `tests/test_cost_api.py` — new rate_limit_gauge tests

## Complement Rule sweep

### PAGES / loaders / nav (3 registration points)

All three updated:
- PAGES: `'costs'` added to array (rack.js:412)
- loaders: `costs: loadCostsPage` in navigate() (rack.js:448)
- nav: `<button class="rail-btn" data-nav="costs">` in index.html:77
- page container: `<div class="page" id="page-costs">` in index.html:115

The `navigate()` function toggles `active` class via `PAGES.forEach()` (rack.js:437)
and toggles rail button active state via `querySelectorAll('.rail-btn')` (rack.js:438-440).
Both cover the new 'costs' page automatically.

### Sibling sweep: other nav badges

`nav-badge-costs` span exists in index.html but is never populated by any JS
function. This is correct: there is no "costs count" concept. The span remains
empty, same as other badges before their data loads.

### Nav-count tests

Searched `tests/` for nav/page count assertions. `tests/e2e/test_browser_smoke.py`
waits for at least one `.rail-btn` but does not assert a specific count. Adding
the Costs nav item does not break it.

## Backend verification

### `get_rate_limit_gauge()` (services/queue.py:764-799)

- All referenced symbols exist: `PROVIDER_LIMITS`, `DEFAULT_LIMITS`,
  `compute_audio_seconds_used`, `_oldest_contributing_timestamp`, `utcnow_naive`
  (all in queue.py), `LOCAL_PROVIDERS` (backends/__init__.py:37),
  `get_provider_stt_rate` (services/pricing.py:41).
- No circular imports: `backends` and `services.pricing` do not import from
  `services.queue`. Deferred imports inside the function body are a safe pattern.
- Local provider handling: returns zeros with `is_local: True`. Correct.
- Groq math verified: `limit_seconds = PROVIDER_LIMITS["groq"]["asd"] = 28800`,
  `stt_rate = 0.004/min` (whisper-large-v3-flash). For 120s: `used_cost =
  round((120/60) * 0.004, 4) = 0.008`. For 28800s: `limit_cost = round((28800/60)
  * 0.004, 4) = 1.92`. Matches test assertions exactly.

### `GET /api/costs` (app.py:2830-2855)

- Enriches each provider's cost object with gauge fields (`used_today_seconds`,
  `limit_today_seconds`, `used_today_cost`, `limit_today_cost`, `resets_in_seconds`).
- Adds top-level `rate_limit_gauge` for groq (primary provider).
- The Costs page uses the top-level gauge; per-provider gauge fields are computed
  but not displayed (not a bug, just unused future data).

### `_build_jobs_payload` (app.py:671-676)

- Adds `rate_limit_gauge` to the jobs payload. Queue page reads it from the
  `/api/jobs` response.

## Frontend verification

### `loadCostsPage()` (rack.js:3049-3135)

- Fetches `GET /api/costs`, renders Monthly Spend, Lifetime Spend, Rate-Limit
  Budget, and per-provider breakdown.
- Empty state: `providerKeys.length === 0` shows "No transcription spend
  recorded this month".
- `escapeHtml` used on dynamic values. No XSS risk.

### Queue gauge (rack.js:2994-3004)

- Renders `if (gauge.limit_seconds)` — always truthy for groq (28800).
- When user has no transcripts: `used_seconds = 0`, gauge shows
  "0 / 28,800 audio-seconds used today". Satisfies acceptance criterion 3.

## Test results

- Unit/integration suite: **592 passed, 0 failed** (excluding e2e)
- New tests: `test_costs_endpoint_includes_rate_limit_gauge`,
  `test_jobs_endpoint_includes_rate_limit_gauge`, updated
  `test_bootstrap_authenticated_returns_full_payload`
- Mutation check: all new tests fail if `get_rate_limit_gauge` returns empty/None
  (KeyError on `gauge["provider"]`).
- Exact-value assertions: `gauge["used_cost"] == 0.008`, `gauge["limit_cost"]
  == 1.92`, `gauge["used_seconds"] == 120.0`, `gauge["limit_seconds"] == 28800`.

## E2e browser test

`tests/e2e/test_costs_ui_e2e.py` exists with correct assertions (checks
`#page-costs.active`, "Monthly Spend", "Lifetime Spend", "Rate-Limit Budget",
`.budget-gauge` on Queue page). Could NOT run: Playwright Python package not
installed in this environment (`Skipped: Playwright not installed`). Static
review of the test confirms assertions match the implementation. Could not
start a live server for Playwright MCP browser check either (no pre-start
server file, can't background a process).

## Acceptance criteria walk

1. [x] Costs page registered in PAGES, loaders, nav + page container. Empty state renders.
2. [x] Monthly spend + lifetime totals display from GET /api/costs.
3. [x] Queue gauge shows usage-so-far even when not rate-limited (limit_seconds always 28800 for groq).
4. [x] No nav-count test breakage. E2e selectors use data-nav, not count.
5. [ ] E2e browser test exists but could not run (Playwright Python not installed). Static check confirms assertions correct.
