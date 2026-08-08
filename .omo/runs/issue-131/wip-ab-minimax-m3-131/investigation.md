# Issue #131 — Polling timers continue firing after logout

## Target resolution (Phase 0)

Issue #131 is **standalone**, not a tracking issue. Targeting it directly.

## What the issue claims

Three polling timers in `static/rack.js` continue firing after logout (or a
mid-session 401) and hammer the server with requests that come back 401:

- `bgJobPollTimer` (issue says line 571, 8s)
- `bankPollTimer` (issue says line 2104, 5s)
- `detailPollTimer` (issue says line 2365, 2.5s)

`resetDeckState()` (issue says line 511) clears `correctionPollTimer` but
misses the other three. Suggested fix: add `stopBackgroundJobPoll()` in
`showLogin()`, and clear `bankPollTimer`/`detailPollTimer` in `resetDeckState()`.

## Reality vs. issue (current code on master @ 2437344)

Issue line numbers are off by a few (this file is 4473 lines and has drifted
since the issue was filed), but the bug is real. Actual locations:

| Symbol | File | Line | Issue says | Notes |
|---|---|---|---|---|
| `resetDeckState` | static/rack.js | 512 | 511 | only calls `stopCorrectionPoll()` (line 546) |
| `showLogin` | static/rack.js | 553 | — | calls `resetDeckState()` (line 554), no other cleanup |
| `bgJobPollTimer` decl | static/rack.js | 573 | 571 | |
| `pollBackgroundJobs` reschedule | static/rack.js | 606 | — | unconditional `setTimeout(..., 8000)` even after the catch swallows the 401 |
| `stopBackgroundJobPoll` | static/rack.js | 616–619 | — | already exists, only called from `logout()` (line 800) |
| `logout` | static/rack.js | 799–806 | — | calls `stopBackgroundJobPoll()` then `showLogin()` |
| `bankPollTimer` decl | static/rack.js | 2018 | 2104 | |
| `bankPollTimer` set/clear | static/rack.js | 2129–2131 | — | 4000 ms (issue says 5 s) — only re-armed after a successful `loadTranscripts()` |
| `detailPollTimer` decl | static/rack.js | 2332 | 2365 | |
| `detailPollTimer` set/clear | static/rack.js | 2390–2396 | — | 2500 ms — only re-armed inside the success path of the tick callback |
| `api()` 401 → `showLogin()` | static/rack.js | 230, 241 | — | the single chokepoint that fires `showLogin()` on 401 |

## The infinite-loop pattern (where the issue's bug description is right)

Only `bgJobPollTimer` truly self-loops. `pollBackgroundJobs` (line 577)
unconditionally reschedules itself at line 606 even when `api()` threw on a
401 caught at line 605:

```js
async function pollBackgroundJobs() {
  try {
    const data = await api('/api/jobs?limit=50');  // 401 → showLogin() + throw
    ...
  } catch { /* transient fetch failure — just retry next tick */ }
  bgJobPollTimer = setTimeout(pollBackgroundJobs, 8000);  // ← reschedules no matter what
}
```

So after logout, every 8 s: `api('/api/jobs')` → 401 → `showLogin()` (no-op,
already shown) → throw → catch swallows → reschedule. Forever, until the page
is reloaded.

## The "single 401, then dies" pattern (where the issue's bug description is slightly off)

`bankPollTimer` and `detailPollTimer` are NOT in an infinite 401 loop, but
they do leave stale timer handles that the issue wants cleared for hygiene.

- `bankPollTimer` (line 2131): timer callback fires `loadTranscripts()` only
  if `S.page === 'transcripts'`. `loadTranscripts()` returns at line 2056 if
  `api()` throws, so it never reaches the reschedule at line 2131. After one
  401 the timer is functionally dead, but the variable still points at the
  completed handle.
- `detailPollTimer` (line 2396): the catch at line 2404 explicitly says
  "transient — poll dies, next action revives it". Same story as bankPoll.
- `dashPollTimer` (line 988, scheduleDashPoll): the early-return at line
  990 (`if (S.page !== 'dashboard') return;`) prevents reschedule when the
  user navigates away; on a 401 inside `loadDashboardJobs` the catch at
  line 920 returns and `scheduleDashPoll()` is never called. Already
  self-terminates. Not in the issue's list and not part of the fix.

So strictly only `bgJobPollTimer` is the infinite-loop offender. The other
two are hygiene/leak-prevention cleanups.

## Entry points that need to clear timers (Complement Rule)

Any code path that lands on the login screen should clear these timers.
`resetDeckState()` is the one place to clear per-account state (per its own
comment at lines 506–511, "Every path back to the login screen ... routes
through here"). All 401 → login paths flow through `api()` (lines 230, 241) →
`showLogin()` (line 553) → `resetDeckState()` (line 554). The explicit
`logout()` button also calls `showLogin()` (line 805).

So the fix is to clear all three timers on the showLogin/resetDeckState
boundary, not to instrument each call site. That matches the issue's
suggestion and the one-stop-clearing principle the file already follows.

## Diff plan (matches the issue's suggested fix exactly)

1. In `resetDeckState()` (line 512), add right next to the existing
   `stopCorrectionPoll();` call (line 546):
   ```js
   clearTimeout(bankPollTimer);
   bankPollTimer = null;
   clearTimeout(detailPollTimer);
   detailPollTimer = null;
   ```
   (Bare `clearTimeout` + null, matching the pattern `stopCorrectionPoll`
   already uses at lines 1821–1824 — there's no exported helper, so inline
   is consistent with the file's own style.)

2. In `showLogin()` (line 553), add right after the existing
   `resetDeckState();` call (line 554):
   ```js
   stopBackgroundJobPoll();
   ```
   (`stopBackgroundJobPoll` already exists at line 616; reusing it keeps
   the `clearTimeout + null` pattern in one place.)

3. The `stopBackgroundJobPoll()` call inside `logout()` (line 800) becomes
   redundant once `showLogin()` (called from `logout()` at line 805) does
   it. Leaving it in place is harmless and arguably clearer (it documents
   that logout stops the bg poll before the API call hits the server).
   Keeping it — the file's `logout()` reads "I'm going to stop the bg poll,
   then tell the server, then show the login screen" as a deliberate
   sequence, and the redundancy protects against a future refactor that
   changes `showLogin()`'s contract.

## What the issue's own suggested approach gets right and wrong

- Right: scope the cleanup at the showLogin/resetDeckState boundary, not at
  each individual call site.
- Right: reuse the existing `stopBackgroundJobPoll()` helper rather than
  open-coding `clearTimeout(bgJobPollTimer); bgJobPollTimer = null;` at the
  call site.
- Minor: the issue says bankPollTimer is 5 s and detailPollTimer is 2.5 s,
  but the actual values are 4000 ms and 2500 ms. Doesn't affect the fix.
- Minor: the issue overstates the bank/detail bug as "infinite loop", but
  the suggested fix (clear them in `resetDeckState`) is the right action
  regardless — stale timer handles are a real concern even if they don't
  self-reschedule, because (a) any future `clearTimeout(bankPollTimer)`
  or `clearTimeout(detailPollTimer)` operates on a completed handle
  (no-op today, but a footgun if the variable is ever reused), and
  (b) the principle that all per-account state lives in `resetDeckState`
  is the contract the file already promises.

## Test plan

- Static check: re-read the changed function and the three timer
  declarations to confirm the variable references resolve, no shadowing,
  no syntax surprises.
- JS sanity: `node --check static/rack.js` (the file is served as a
  static asset, not bundled, so a parse check is the minimum bar).
- No browser tool confirmed available in this session, so the live
  e2e-regression-http tier is skipped. Logging that explicitly in
  wrong-directions.md per the workflow's "no silent skip" rule.
- No new helper functions introduced (just inline `clearTimeout` +
  null, matching the existing `stopCorrectionPoll` pattern), so no new
  unit test is required — the change is mechanical, the contract is
  "on showLogin(), the three timers are dead", and the existing test
  suite for logout/auth still exercises the entry point.
