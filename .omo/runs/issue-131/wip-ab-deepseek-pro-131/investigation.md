# Investigation — issue #131: Polling timers not cleared on logout

## Scope Summary

Three polling timers in `static/rack.js` continue firing after logout/401 session expiry:
- `bgJobPollTimer` (L573): background LLM job watcher, 8s interval
- `bankPollTimer` (L2018): transcript list refresher, 4-5s interval
- `detailPollTimer` (L2332): transcript detail LLM job watcher, 2.5s interval

`resetDeckState()` (L512-551) calls `stopCorrectionPoll()` but not `stopBackgroundJobPoll()`, and does not clear `bankPollTimer` or `detailPollTimer`.

---

## Real file/function locations (current code, not issue's line numbers)

| What | Line | Description |
|------|------|-------------|
| `resetDeckState()` | 512-551 | Clears per-account state, calls `stopCorrectionPoll()` only |
| `showLogin()` | 553-561 | Calls `resetDeckState()`, hides app shell |
| `showApp()` | 562-567 | Calls `startBackgroundJobPoll()` to resume bg polling |
| `bgJobPollTimer` | 573 | Module-level var for bg job poll timer |
| `startBackgroundJobPoll()` | 609-613 | Clears + restarts bg job poll |
| `stopBackgroundJobPoll()` | 616-619 | Clears + nulls bg job poll timer |
| `stopCorrectionPoll()` | 1821+ | Clears correction poll (already called from `resetDeckState()`) |
| `bankPollTimer` | 2018 | Module-level var for transcript list poll |
| `bankPollTimer` creation | 2129-2132 | `loadTranscripts()` schedules self when active transcripts exist |
| `detailPollTimer` | 2332 | Module-level var for detail poll |
| `scheduleDetailPoll()` | 2389-2406 | Self-scheduling detail poll while LLM jobs are active |

**Issue line number accuracy**: Issue says L571, L2104, L2365. Actual: L573, L2018-2131, L2332-2396. Close enough that the right code was identified, but wrong enough that a commit referencing issue line numbers would be incorrect. Do NOT use the issue's line numbers in any code.

---

## Full list of call sites / entry points

### 1. `showLogin()` call sites (6 total)

| Line | Path |
|------|------|
| 230 | `api()` — direct 401 response |
| 241 | `api()` — CSRF retry 401 |
| 631 | `checkAuth()` — non-200 `/api/bootstrap` |
| 638 | `checkAuth()` — `body.user` missing |
| 640 | `checkAuth()` — catch block |
| 805 | `logout()` — explicit logout (already calls `stopBackgroundJobPoll()` first at L800) |

### 2. `bgJobPollTimer` callers (3)
- L606: `pollBackgroundJobs()` — `setTimeout` re-schedule
- L610: `startBackgroundJobPoll()` — `clearTimeout`
- L617: `stopBackgroundJobPoll()` — `clearTimeout`

### 3. `bankPollTimer` callers
- L2129: `clearTimeout` in `loadTranscripts()` re-entry
- L2131: `setTimeout` in `loadTranscripts()`

### 4. `detailPollTimer` callers
- L2390: `clearTimeout` in `scheduleDetailPoll()` re-entry
- L2396: `setTimeout` in `scheduleDetailPoll()`

### 5. `stopBackgroundJobPoll()` caller
- L800: `logout()` — correct, but the ONLY caller

---

## What the issue's suggested fix gets right / wrong

**Right:**
- Adds `stopBackgroundJobPoll()` call needed for bg poll timer
- Identifies `bankPollTimer` and `detailPollTimer` need clearing in `resetDeckState()`

**Wrong/incomplete:**
- Suggests adding `stopBackgroundJobPoll()` in `showLogin()` rather than `resetDeckState()`. Adding it to `resetDeckState()` is cleaner: `resetDeckState()` is the documented "one place to clear per-account state" (comment L509-511), already calls `stopCorrectionPoll()`, and is called by `showLogin()` (L554). Adding to `resetDeckState()` covers ALL 6 `showLogin()` paths with one change. Adding to `showLogin()` duplicates the "clear state" responsibility across two functions.
- Does not mention that `logout()` at L800 already calls `stopBackgroundJobPoll()`, making a `showLogin()`-only addition redundant for the explicit logout path but still necessary for auth-failure paths. Adding to `resetDeckState()` makes the L800 call redundant but harmless (`stopBackgroundJobPoll()` is idempotent). The redundant L800 call should be removed for cleanliness if adding to `resetDeckState()`.

---

## Fix Plan

### Change 1: `resetDeckState()` — add all missing timer clears

After the existing `stopCorrectionPoll()` call (L546), or at the end of the function before closing brace (L551), add:

```javascript
stopBackgroundJobPoll();
clearTimeout(bankPollTimer);
bankPollTimer = null;
clearTimeout(detailPollTimer);
detailPollTimer = null;
```

### Change 2 (cleanup): `logout()` — remove now-redundant call

Remove `stopBackgroundJobPoll()` at L800 since `resetDeckState()` (called via `showLogin()` at L805) now handles it. Or leave it — it's idempotent and harmless, removing it risks regression if `showLogin()` is refactored later. **Decision: leave it. Not worth the risk.**

---

## Code to modify

**File**: `static/rack.js`

**Location**: Inside `resetDeckState()`, after the existing `stopCorrectionPoll()` call at line 546.

**Lines to insert** (after line 546):
```
  stopBackgroundJobPoll();
  clearTimeout(bankPollTimer);
  bankPollTimer = null;
  clearTimeout(detailPollTimer);
  detailPollTimer = null;
```

**Why after `stopCorrectionPoll()`**: Groups all polling timer cleanup together, follows the existing pattern of `stopCorrectionPoll()` being called in this function.
