# Issue #131 Investigation

## Target
Clear polling timers on logout/401 to stop unnecessary API calls after session expires.

## Current State (static/rack.js, current master)

### Polling timers found

| Timer | Line | Interval | Started by | Stopped by | Cleared in resetDeckState()? |
|-------|------|----------|------------|------------|------------------------------|
| `bgJobPollTimer` | 573 | 8s | `startBackgroundJobPoll()` (609), called from `showApp()` (566) | `stopBackgroundJobPoll()` (616) | NO |
| `bankPollTimer` | 2018 | 4s (when active transcripts) | `loadTranscripts()` (2131), self-rescheduling | `clearTimeout` in `loadTranscripts()` (2129) before reschedule | NO |
| `detailPollTimer` | 2332 | 2.5s (when LLM jobs active) | `scheduleDetailPoll()` (2396), self-rescheduling | `clearTimeout` in `scheduleDetailPoll()` (2390) before reschedule | NO |
| `dashPollTimer` | 2017 | 3s (on dashboard) | `scheduleDashPoll()` (989), self-rescheduling | `clearTimeout` in `scheduleDashPoll()` (988) before reschedule | NO |
| `correctionPollTimer` | 1818 | varies | `scheduleCorrectionPoll()` | `stopCorrectionPoll()` | YES (line 546) |

### Logout flow

1. **Explicit logout** (`logout()` at line 799):
   - Calls `stopBackgroundJobPoll()` at line 800
   - Calls `showLogin()` at line 805
   - `showLogin()` calls `resetDeckState()` at line 554
   - `resetDeckState()` calls `stopCorrectionPoll()` at line 546
   - **Result**: bgJobPoll stopped, correctionPoll stopped, but bankPoll/detailPoll/dashPoll NOT stopped

2. **401 from expired session** (`api()` at line 222):
   - On 401 (line 230 or 241), calls `showLogin()` directly
   - `showLogin()` calls `resetDeckState()`
   - **Result**: correctionPoll stopped, but bgJobPoll/bankPoll/detailPoll/dashPoll NOT stopped
   - Each timer fires, calls `api()`, gets 401, calls `showLogin()` again, throws, catch swallows, timer resets itself

### Issue's suggested fix vs reality

Issue says:
- Add `stopBackgroundJobPoll()` in `showLogin()`
- Clear bankPollTimer/detailPollTimer in `resetDeckState()`

**What's missing from the issue**: `dashPollTimer` is also not cleared. Same problem as bank/detail polls.

## Fix Plan

Put ALL timer stops in `resetDeckState()` since it's the "clear per-account state" function. Both `logout()` and `showLogin()` call it, so this covers both paths.

Changes to `resetDeckState()` (after line 546 `stopCorrectionPoll()`):
```javascript
stopBackgroundJobPoll();
clearTimeout(bankPollTimer); bankPollTimer = null;
clearTimeout(detailPollTimer); detailPollTimer = null;
clearTimeout(dashPollTimer); dashPollTimer = null;
```

The existing `stopBackgroundJobPoll()` call in `logout()` (line 800) becomes redundant but harmless (idempotent). Leave it for clarity.

## Call sites in scope

- `resetDeckState()` at line 512: add timer clears
- No other files affected (all timers are module-local to static/rack.js)

## Testing approach

Static source check: verify all 5 poll timers have a clear path in `resetDeckState()`.
No new functions introduced, so no new unit test needed (existing timer logic unchanged, just adding clears to an existing teardown function).
