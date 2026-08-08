# Issue #129 — Investigation

## Resolution

- **Target issue:** #129 (standalone, not a tracking issue). Fetched via `gh issue view 129`.
- **Issue title:** Race condition: rapid transcript clicks can show wrong transcript
- **Reporter-claimed location:** `static/rack.js:2338 loadTranscriptDetail()`
- **Actual current location:** `static/rack.js:2372 loadTranscriptDetail()` — line number drifted since the issue was filed, code itself is the same.

## Symptom

When the user rapidly clicks transcript row A then transcript row B (tape library, dashboard, voice roster), the detail page can end up showing A even though B was clicked last. The slower of the two `/api/transcripts/{id}` responses wins, regardless of click order.

## Root cause (verified in source)

`loadTranscriptDetail` (lines 2372–2387):

```js
async function loadTranscriptDetail(id, opts = {}) {
  if (id == null) { navigate('transcripts'); return; }
  const prevId = detailData ? detailData.id : null;
  try {
    detailData = await api('/api/transcripts/' + id);
  } catch (e) { toast(e.message, 'error'); return; }
  if (prevId !== null && prevId !== detailData.id) resetSegAudio();
  if (!opts.preserveQuery) S.query = '';
  if (S.detailTab === 'format' && detailData.kind !== 'dictation') S.detailTab = 'transcript';
  renderDetail();
  scheduleDetailPoll();
}
```

- `prevId` captures what was shown BEFORE the await, but that's the wrong reference: it tells you "what was on screen" not "what does the user currently want."
- `detailData = await ...` assigns unconditionally.
- No `AbortController`, no generation counter, no `S.detailId` check after the await.
- A second click can call `loadTranscriptDetail(B)` before the first call's `await` resolves. If B's response arrives first, A's slower response overwrites B. UI shows A.

## Existing correct pattern (the codebase already knows how to do this)

`scheduleDetailPoll` (lines 2398–2415) does the right thing for the same `/api/transcripts/{id}` fetch:

```js
detailPollTimer = setTimeout(async () => {
  if (S.page !== 'detail' || !detailData || detailData.id !== id) return;   // guard BEFORE
  try {
    const fresh = await api('/api/transcripts/' + id);
    if (S.page !== 'detail' || !detailData || detailData.id !== id) return; // guard AFTER
    detailData = fresh;
    ...
  }
  ...
}, 2500);
```

`loadTranscriptDetail` is missing the equivalent guard after the await.

## Click entry points that race against each other

| Line | Path | Sets `S.detailId` before await? |
|------|------|---------------------------------|
| 899 | dashboard "open" button → `navigate('detail', id)` | yes (line 426) |
| 1425 | `openDone` after a long recording finishes → `navigate('detail', S.doneId)` | yes (line 426) |
| 2111 | tape library row "open" → `navigate('detail', id)` | yes (line 426) |
| 2306 | voice roster "open transcript" → `navigate('detail', tid)` | yes (line 426) |
| 437 | navigate loader → `loadTranscriptDetail(S.detailId)` | yes — caller is `navigate` which just set it |

All click-from-list entry points go through `navigate('detail', id)`, which writes `S.detailId = id` at line 426 before invoking the loader at line 437. `S.detailId` is therefore the source of truth for "what does the user want right now."

## Action-handler callers (refresh of the current detail — not race-prone but still pass the new check)

These are the other callers the sibling sweep surfaced. They refresh the current detail after an action (rename, cancel, resume, retry, summarize, format, correct, enroll, voice-match, context, re-transcribe, re-diarize, relabel-undo). For each, `t = detailData` so `S.detailId === t.id` is already true at call time, and the new guard passes:

- 2716: renderSpeakerPicker rename
- 2815: re-tag modal confirm
- 3403: kind switch (meeting ↔ dictation)
- 3409: retry-failed-chunks
- 3415: resume
- 3428: summarize
- 3442: format (markdown/email/coding)
- 3500: relabel-undo
- 3647: correct
- 3693: re-transcribe (note: `nt.id` — a new transcript id; `S.detailId` is NOT updated here, the guard would FAIL — this is a pre-existing latent issue but unrelated to #129's race, and re-transcribe creates a brand-new transcript that should now be shown; the current code at 3693 calls `loadTranscriptDetail(nt.id)` without updating `S.detailId` first, so it relies on the next call to `navigate` to set S.detailId. This is out of scope for #129 but flagged.)
- 3722: re-diarize
- 3733: voice-match

**Sweep note:** I enumerated every `await api(...)` and every `= await api(...)` assignment in `static/rack.js`. The only function matching the "fetch a single transcript by id and assign to module-global `detailData`" shape is `loadTranscriptDetail`. Other loaders (`loadDashboard`, `loadTranscripts`, `loadQueue`, `loadVoices`, `loadSettingsPage`, `loadDashboardJobs`) fetch lists, not single entities, and assign to local variables — no race shape match. `scheduleDetailPoll` already guards correctly. **No other sibling needs the fix.**

## What the issue's suggested fix gets right / wrong

The issue suggests "use a generation counter or compare S.detailId after the await before assigning detailData." The `S.detailId` comparison is the cleaner approach and matches the existing pattern in `scheduleDetailPoll` (which uses `detailData.id !== id`, equivalent in the poller context but `S.detailId` is the more authoritative "current user intent" since `detailData` is the variable being overwritten).

A generation counter would also work but adds state that has to be incremented on every navigation; `S.detailId` is already that counter, just unlabeled.

## Fix (Phase 2 target)

Add a closure-captured `myId` and a post-await check against `S.detailId`:

```js
async function loadTranscriptDetail(id, opts = {}) {
  if (id == null) { navigate('transcripts'); return; }
  const myId = id;                                  // capture intent for closure
  const prevId = detailData ? detailData.id : null;
  try {
    const fresh = await api('/api/transcripts/' + id);
    if (S.detailId !== myId) return;                // user navigated elsewhere; abandon
    detailData = fresh;
  } catch (e) { toast(e.message, 'error'); return; }
  if (prevId !== null && prevId !== detailData.id) resetSegAudio();
  if (!opts.preserveQuery) S.query = '';
  if (S.detailTab === 'format' && detailData.kind !== 'dictation') S.detailTab = 'transcript';
  renderDetail();
  scheduleDetailPoll();
}
```

`S.detailId` is the user-intent source of truth (set at navigate:426, the loader at 437 reads it back). `myId` is what *this* call asked for. They diverge exactly when a faster second click already started a new load. When they diverge, this call's response is stale and should be discarded.

## Acceptance criteria (from the issue)

| Criterion | Met? |
|-----------|------|
| Rapid clicks A then B always show B | Yes (with fix) |
| Out-of-order response from A no longer overwrites B | Yes (with fix) |
| Other existing behavior preserved (query preservation, tab fallback, seg-audio reset) | Yes (only adds a guard, no other logic changed) |
| Poller (scheduleDetailPoll) still works | Yes (untouched) |
| Action-handler refreshes (rename, cancel, etc.) still work | Yes (S.detailId === t.id at call time for these, guard passes) |

## Regression test plan (Phase 3)

1. **Static source-level check first:** read the patched function and all 12 callers, reason that the new guard is harmless for action-handler refreshes (S.detailId already matches at call time) and necessary for click-from-list races (S.detailId is set to the most recent click by navigate:426).
2. **Existing pytest suite:** `pytest tests/ -x` must still pass — covers all action-handler paths that call `loadTranscriptDetail`.
3. **New e2e regression test (Playwright, route interception):** mock `/api/transcripts/A` to delay 800ms, leave `/api/transcripts/B` at default speed. In a browser, navigate to the tape library, click A, then within 100ms click B. Assert the detail page shows B's title, not A's. Confirms the race is fixed end-to-end.
4. If no live browser tool is available for the e2e tier, fall back to the static check + the unit suite and say so explicitly in the self-report.

## Out of scope (flagged, not fixed)

- Line 3693 (`loadTranscriptDetail(nt.id)` after re-transcribe) calls with a new id but doesn't update `S.detailId` first. Pre-existing latent issue, unrelated to #129's reported race. Leave for a separate issue.
