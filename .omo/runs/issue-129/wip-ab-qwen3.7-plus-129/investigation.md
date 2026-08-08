# Investigation: Issue #129 - Race condition in loadTranscriptDetail()

## Target
Issue #129 (standalone): rapid transcript clicks can show wrong transcript.

## Root cause (verified against current code)
`loadTranscriptDetail()` at `static/rack.js:2372-2387` fetches transcript data via `await api('/api/transcripts/' + id)` and unconditionally assigns to the global `detailData`. No abort controller, no generation counter, no id-comparison guard after the await. If call A's response arrives after call B's, `detailData` holds A's data while the user expects B.

## Real function source (lines 2372-2387)
```javascript
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

## All call sites (12 total)
| Line | Caller | Context |
|------|--------|---------|
| 437 | `navigate()` | `detail: () => loadTranscriptDetail(S.detailId)` |
| 2716 | `renameSpeaker()` | after speaker rename |
| 2815 | `renameSpeaker()` | after speaker rename (second path) |
| 3403 | `detailAction()` | after retry |
| 3409 | `detailAction()` | after resume |
| 3415 | `detailAction()` | after summarize |
| 3428 | `detailAction()` | after format-* actions |
| 3500 | `detailAction()` | after relabel-undo |
| 3647 | `rerunCorrection()` | after rerun correction |
| 3693 | `toggleRetranscribePicker()` | after retranscribe |
| 3722 | `toggleRediarizePicker()` | after rediarize |
| 3733 | `runVoiceMatch()` | after voice-match |

All callers pass the id they want loaded. The race happens when two calls are in flight simultaneously with different ids.

## Shared state touched
- `detailData` (global) - the main race target
- `S.query` (global) - cleared unless preserveQuery
- `S.detailTab` (global) - fallback guard for format tab
- `segAudio`, `segAudioTid`, `segPlayingBtn`, `seedClips` (module-level) - reset via resetSegAudio() on transcript switch
- `videoFloatingTid`, `selectMode`, `selectedSegments` (module-level) - touched indirectly via renderDetail()

## Sibling sweep (same race-condition shape)
Searched all async functions in rack.js that fetch data and assign to shared/global state without a guard.

**Siblings found with identical shape:**
1. `ensureProviders()` (line 1169) - assigns to `S.providers`, `S.providerIdx`, `S.langIdx` after `await api('/api/providers')`. Called from page navigation and provider/model knob clicks. Same race risk.

**Functions with similar shape but lower risk:**
- `loadTranscripts()` (line 2060) - assigns to `list`, `bankListCache`. Called from poll timer and click handlers.
- `loadQueue()` (line 2251) - assigns to local `data`. Called from poll timers and click handlers.
- `refreshQueueBadge()` (line 2331) - updates badge text. Lower impact.
- `fetchModelsFor()` (line 1194) - assigns to `p.models`, `p.modelsFetched`. Called from renderTranscribe() and knob clicks.

**Other async functions** (23 total found) mostly use local state or render UI directly, not persistent shared state. Lower race risk.

## Issue's suggested fix assessment
Issue suggests: "Use a generation counter or compare S.detailId after the await before assigning detailData."

**Assessment**: Correct approach. A generation counter is cleaner than comparing ids because it handles the case where the user clicks A, then B, then A again (id comparison would pass but generation would show it's stale). Will implement generation counter.

## What the issue misses
1. `ensureProviders()` has the identical race shape and should get the same fix.
2. The fix should also cancel/ignore the stale response's side effects (scheduleDetailPoll, renderDetail), not just the data assignment.

## Acceptance criteria from issue
- [ ] Rapid clicks on transcript A then B show B, not A
- [ ] No abort controller needed (generation counter is sufficient)
- [ ] Fix should not break existing callers (all 12 call sites)

## Fix plan
1. Add a module-level `detailLoadGen` counter at the top of rack.js (near other module state).
2. In `loadTranscriptDetail()`: increment counter before await, capture current value, compare after await, bail if stale.
3. Apply same pattern to `ensureProviders()` with its own `providersLoadGen` counter.
4. Verify all 12 call sites still work (they all just call the function, no changes needed at call sites).
