# Investigation — Issue #150: Detail page partial DOM updates during job polling

**Date**: 2026-07-26
**Branch**: `wip/ab-deepseek-pro-150`
**Worktree**: `../whisperdesk-wip-ab-deepseek-pro-150`

## 1. The real code (not the issue's line numbers)

### Polling trigger

`scheduleDetailPoll()` — `static/rack.js` lines 2389-2406:

```js
function scheduleDetailPoll() {
  clearTimeout(detailPollTimer);
  const t = detailData;
  if (!t || !(llmJobActive(t.correction_job) || llmJobActive(t.summary_job) || llmJobActive(t.voice_match_job) ||
    llmJobActive(t.format_markdown_job) || llmJobActive(t.format_email_job) || llmJobActive(t.format_coding_prompt_job) ||
    llmJobActive(t.classify_intent_job))) return;
  const fp = _jobFingerprint(t), id = t.id;
  detailPollTimer = setTimeout(async () => {
    if (S.page !== 'detail' || !detailData || detailData.id !== id) return;
    try {
      const fresh = await api('/api/transcripts/' + id);
      if (S.page !== 'detail' || !detailData || detailData.id !== id) return;
      detailData = fresh;
      if (_jobFingerprint(fresh) !== fp) renderDetail();  // ← THE PROBLEM
      scheduleDetailPoll();
    } catch { }
  }, 2500);
}
```

**Finding**: Line 2402 calls `renderDetail()` on every fingerprint change. This is the only poll-triggered call — all other `renderDetail()` callers (tab switch, video detach/reattach, select-mode toggle, initial load) are legitimate full re-renders.

### Fingerprint function

`_jobFingerprint(t)` — lines 2380-2385. Compares `status + progress.done` for all 7 LLM job types.

### Full render

`renderDetail()` — lines 3156-3270. Assigns `root.innerHTML = ` a massive template string (lines 3193-3241) that rebuilds:
- Page head with action bar (14+ buttons, many with `disabled` attrs based on `llmJobActive()`)
- Metadata unit (duration, provider, status badge, speakers, segments, mode)
- Video element (if applicable)
- Tab bar with search input
- Then calls `renderDetailBody()` which does `body.innerHTML = ...` for the content tab

**Every poll tick that detects a job status change rebuilds ALL of this from scratch.**

### Helper functions

- `llmJobActive(job)` — line 2811: `return job && (job.status === 'pending' || job.status === 'running');`
- `jobRunningUnit(job, label)` — lines 2815-2827: Returns HTML for a running job indicator
- `statusView(t)` — line 335: Returns `{color, lit, nix, word}` for the status badge

## 2. All `renderDetail()` call sites (Complement Rule)

| Line | Caller | Should use targeted update? |
|------|--------|----------------------------|
| 2376 | `loadTranscriptDetail` (initial fetch) | NO — full render needed |
| 2402 | `scheduleDetailPoll` (poll tick) | **YES — FIX THIS** |
| 2594 | `detachVideo` (floating video) | NO — changes video DOM |
| 2623 | `reattachVideo` (reattach video) | NO — changes video DOM |
| 3247 | Tab click handler | NO — content changes |
| 3251 | Search input (tab switch to transcript) | NO — content changes |
| 3260 | Select mode toggle | NO — UI changes |

**Only line 2402 is in scope.** The fix is a surgical change to the poll path.

## 3. What the issue's proposed snippet gets right vs. wrong

**Right**: Splitting into a chrome renderer + status updater. Calling the status updater from `scheduleDetailPoll`.

**Wrong / incomplete**:
- The snippet references `$('detail-status')` which doesn't exist in the codebase
- The snippet only updates a single status element, but the actual UI that changes during polling includes:
  1. **Action bar button disabled states** (lines 3207, 3211, 3213): "Match against voice roster", "Summarize", "Re-run correction" disable/enable based on `llmJobActive`
  2. **Content body** (line 3241 `detail-body`): When a correction/summary job completes, the transcript/corrected/summary tab needs new content
  3. **Status badge** (line 3227): Shows transcript status word

## 4. Fix approach

Create `updateDetailJobStatus(fresh)` that:
1. Updates `detailData` to `fresh`
2. Updates action bar button `disabled` attributes
3. Updates the status badge
4. Re-renders `detail-body` via `renderDetailBody()` — this handles corrected text appearing, summary appearing, job-running indicators
5. Does NOT rebuild the video element, page head structure, or metadata grid

The key insight: during polling, the only things that change are job statuses and content that depends on them. The page chrome (title, metadata, video, tab buttons) never changes during an active LLM job. Calling `renderDetailBody()` handles the content area cleanly since it already dispatches to the right tab renderer (`segmentsHtml`, `correctedHtml`, `summaryHtml`, `formatHtml`).

## 5. Regression risk

- **Segment interactions (play, rename, retag)**: `renderDetailBody()` rebuilds the segment list HTML via `innerHTML`, which will destroy event listeners. However, these are handled by `detailBodyClick` (line 3266) — a delegated handler on `detail-body` that uses data attributes. Event delegation means destroying and recreating child elements does NOT break interactions. Verified: line 3266 sets up the delegation once in `renderDetail()`, and `renderDetailBody()` doesn't touch it.
- **Search state**: `S.query` is preserved since we're only updating job status, not clearing it. The search input's value survives because we don't rebuild it.
- **Tab state**: `S.detailTab` is preserved.
- **Video**: Not rebuilt — safe.
