# Investigation: Issue #129 — Race condition in loadTranscriptDetail()

**Target**: Issue #129 (standalone)
**Branch**: `wip/ab-deepseek-pro-129`
**Date**: 2026-07-26

## Issue Summary

When a user rapidly clicks multiple transcript rows, `loadTranscriptDetail()` async API calls can resolve out of order. The last call to resolve wins, but if an earlier call's response arrives after a later call's response, the UI shows the wrong transcript.

## Root Cause Analysis

### The Bug (static/rack.js:2372-2387)

```javascript
async function loadTranscriptDetail(id, opts = {}) {
  if (id == null) { navigate('transcripts'); return; }
  const prevId = detailData ? detailData.id : null;
  try {
    detailData = await api('/api/transcripts/' + id);  // LINE 2376 — unconditional overwrite
  } catch (e) { toast(e.message, 'error'); return; }
  if (prevId !== null && prevId !== detailData.id) resetSegAudio();
  if (!opts.preserveQuery) S.query = '';
  if (S.detailTab === 'format' && detailData.kind !== 'dictation') S.detailTab = 'transcript';
  renderDetail();
  scheduleDetailPoll();
}
```

The function:
1. Takes `id` parameter — the transcript to load
2. Calls `api('/api/transcripts/' + id)` — async, may resolve out of order
3. Unconditionally overwrites `detailData` with whatever resolves (line 2376)
4. Does NOT verify that `id` still matches user intent after the `await`

### Race Scenario

1. User clicks transcript A: `navigate('detail', A)` — sets `S.detailId = A`, calls `loadTranscriptDetail(A)`
2. A's API call is in flight (slow)
3. User clicks transcript B: `navigate('detail', B)` — sets `S.detailId = B`, calls `loadTranscriptDetail(B)`
4. B's API call resolves first: `detailData = B_response`, `renderDetail()` shows B ✓
5. A's API call resolves later: `detailData = A_response`, `renderDetail()` shows A ✗

**Result**: UI shows transcript A even though user last clicked B.

### Existing Correct Pattern (scheduleDetailPoll, line 2398-2414)

```javascript
function scheduleDetailPoll() {
  clearTimeout(detailPollTimer);
  const t = detailData;
  // ...guard checks...
  const fp = _jobFingerprint(t), id = t.id, prevActive = jobActiveSnapshot(t);
  detailPollTimer = setTimeout(async () => {
    if (S.page !== 'detail' || !detailData || detailData.id !== id) return;  // PRE-AWAIT GUARD
    try {
      const fresh = await api('/api/transcripts/' + id);
      if (S.page !== 'detail' || !detailData || detailData.id !== id) return;  // POST-AWAIT GUARD
      detailData = fresh;
      // ...
    } catch { /* transient */ }
  }, 2500);
}
```

`scheduleDetailPoll()` already has the correct pattern: it captures `id` before the `await` and verifies it hasn't changed after the `await`. `loadTranscriptDetail()` should follow the same pattern.

### Existing Generation Counter Pattern (pollCorrectionStatus, line 1828-1853)

```javascript
let correctionPollGen = 0;

async function pollCorrectionStatus(id) {
  const gen = ++correctionPollGen;  // capture generation
  // ...
  if (gen !== correctionPollGen) return;  // guard before AND after await
}
```

This is the other established pattern for preventing stale async results. However, for `loadTranscriptDetail()`, the simpler approach of comparing against `S.detailId` is sufficient since `S.detailId` is the canonical "what the user last navigated to."

## Call Sites (14 total)

| Line | Calling Context | Trigger | Await? |
|------|----------------|---------|--------|
| 437 | `navigate()` via `loaders.detail` | Navigation to detail page | No |
| 2716 | `renameSpeaker()` | After rename API call | Yes |
| 2815 | `selectSegmentsRetag()` | After retag API call | Yes |
| 3403 | `detailAction('toggle-kind')` | Switch meeting↔dictation | Yes |
| 3409 | `detailAction('retry')` | Retry failed chunks | No |
| 3415 | `detailAction('resume')` | Resume paused job | No |
| 3428 | `detailAction('summarize')` | Queue summary job | Yes |
| 3442 | `detailAction('format-*')` | Queue format job | Yes |
| 3500 | `detailAction('relabel-undo')` | Undo voice match | Yes |
| 3647 | `queueCorrection()` | Queue correction job | Yes |
| 3693 | `toggleRetranscribePicker()` | Start re-transcription | No |
| 3722 | `toggleRediarizePicker()` | Start re-diarization | No |
| 3733 | `runVoiceMatch()` | Start voice match (×2, with preserveQuery) | No |

**Navigation path (line 437) is the primary race vector** — this is the path triggered by clicking transcript rows. The other 13 call sites are button actions on the current detail page; they're less likely to race but still vulnerable if the user rapidly switches transcripts during an action.

## Sibling Sweep

Searched for all `= await api(` assignments to shared variables in rack.js. Result:

- **`detailData`** (3 writers): `loadTranscriptDetail` (2376), `scheduleDetailPoll` (2410), `loadTape` (indirect via transcript list, not shared `detailData`)
- **`S.correctionStatus`** (1 writer): `pollCorrectionStatus` — already protected with generation counter
- **All other `= await api(`** assignments: local variables only, no race risk

No other shared-state async writes vulnerable to rapid-navigation races.

## Fix Approach

Add a post-await guard to `loadTranscriptDetail()` comparing `S.detailId` against the `id` parameter:

```javascript
async function loadTranscriptDetail(id, opts = {}) {
  if (id == null) { navigate('transcripts'); return; }
  const prevId = detailData ? detailData.id : null;
  try {
    const fresh = await api('/api/transcripts/' + id);
    if (S.detailId !== id) return;  // ← NEW: discard stale response
    detailData = fresh;
  } catch (e) { toast(e.message, 'error'); return; }
  // ... rest unchanged
}
```

**Why `S.detailId`**: It's the canonical "what the user last navigated to" — set at line 426 by `navigate('detail', id)` before `loadTranscriptDetail` is called at line 437. For non-navigation call sites, `S.detailId` matches `detailData.id` (the user is on that transcript's page), so the check still passes for legit re-loads.

**Why not a generation counter**: The `S.detailId` pattern is simpler, matches the existing idiom in `scheduleDetailPoll()`, and handles the race correctly for all 14 call sites. A generation counter would require adding a module-level variable and incrementing it on every call, which is more invasive without benefit for this case.

## Issue's Suggested Fix vs Reality

The issue suggests "compare S.detailId after the await." This is correct and matches what our investigation found. The issue's description is accurate for current code (line numbers are close: issue says ~2338, actual line is 2376 — a 38-line drift, common in this codebase).

## Acceptance Criteria (from issue)

| Criterion | Status |
|-----------|--------|
| Rapidly clicking transcript A then B always shows B | Will be met by fix |
| No request sequencing needed | Not using sequencing, using guard |
| No abort controller needed | Not using AbortController, using guard |
| Guard comparing loaded id to expected id | `S.detailId !== id` is the guard |

## Testing Plan

1. **Static check**: Verify the guard is correctly placed after the `await` and before `detailData` assignment
2. **Existing test suite**: Run `test.bat` to ensure no regressions
3. **Manual reasoning**: Walk through the race scenario step by step with the guard in place — confirmed correct
4. **e2e-regression-http**: Requires a live Playwright browser; check availability before running
