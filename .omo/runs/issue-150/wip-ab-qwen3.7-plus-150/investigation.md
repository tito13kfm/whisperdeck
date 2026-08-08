# Issue #150 Investigation: Partial DOM updates during job polling

## Target
Issue #150: Detail page re-renders entire transcript view on every poll tick (2.5s) when LLM job active. For 500+ segments, this rebuilds hundreds of DOM elements causing flicker and layout thrashing.

## Current Behavior (verified in current code)

### Poll flow (rack.js:2389-2406)
```javascript
function scheduleDetailPoll() {
  // ... checks if any LLM job active
  detailPollTimer = setTimeout(async () => {
    const fresh = await api('/api/transcripts/' + id);
    detailData = fresh;
    if (_jobFingerprint(fresh) !== fp) renderDetail();  // <-- FULL RE-RENDER
    scheduleDetailPoll();
  }, 2500);
}
```

### renderDetail() (rack.js:3156-3270)
Rebuilds entire detail page via `innerHTML`:
- Page head with all action buttons (lines 3194-3218)
- Metadata grid (duration, provider, status, speakers, segments, mode) (lines 3223-3232)
- Video element (lines 3173-3191)
- Tab buttons (line 3235)
- Search input (line 3239)
- Calls `renderDetailBody()` which rebuilds segments (line 3243)

### What changes during a job poll tick
Only job-related state changes:
1. **Status badge** (line 3227): `<span class="status-badge status-badge--${sv.word}">${sv.word}</span>`
2. **Button disabled states** (lines 3207, 3211, 3213):
   - Voice match button: `llmJobActive(t.voice_match_job)`
   - Summarize button: `llmJobActive(t.summary_job)`
   - Re-run correction button: `llmJobActive(t.correction_job)`
3. **Job running indicators** in body (renderDetailBody):
   - Voice match: line 3276 `jobRunningUnit(t.voice_match_job, 'Voice match')`
   - Correction: line 3034 `jobRunningUnit(t.correction_job, 'Correction')`
   - Summary: line 3128 `jobRunningUnit(t.summary_job, 'Summary')`
   - Format jobs: line 3097-3098 `jobRunningUnit(job, target.label)`

## Issue's Suggested Fix vs Reality

### Issue's Option A snippet:
```javascript
function updateDetailJobStatus(fresh) {
  const statusEl = $('detail-status');
  if (statusEl) statusEl.innerHTML = statusBadgeHtml(fresh);
  // ... update correction_job, summary_job indicators inline
}
```

### Problems with issue's snippet:
1. **No `detail-status` element exists** - status badge is at line 3227 with class `status-badge`, no ID
2. **No `statusBadgeHtml()` function exists** - status badge is inline HTML in renderDetail()
3. **Missing button state updates** - disabled states on 3 buttons depend on job status
4. **Missing body updates** - jobRunningUnit() calls in renderDetailBody() need updating
5. **Missing format job updates** - format_markdown_job, format_email_job, format_coding_prompt_job also have running indicators

## Call Sites In Scope (Complement Rule)

### renderDetail() callers (rack.js):
- Line 2376: initial load (loadTranscriptDetail)
- Line 2402: **poll tick (THE PROBLEM)**
- Line 2594: detachVideo
- Line 2623: reattachVideo
- Line 3247: tab switch
- Line 3251: search input (when switching to transcript tab)
- Line 3260: select mode toggle

**Only line 2402 (poll tick) should use partial update.** All other callers need full rebuild.

### renderDetailBody() callers (rack.js):
- Line 3243: inside renderDetail()
- Line 3252: search input (when already on transcript tab)
- Line 3289+: tab content rendering

**Line 3252 could also benefit from partial update** if only job status changed, but search input changes don't affect job state, so this is fine as-is.

## Implementation Plan

### Option A (minimal, recommended): Targeted DOM updates

1. **Add IDs to job-sensitive elements** in renderDetail():
   - Status badge: add `id="detail-status-badge"` to line 3227
   - Voice match button: add `id="btn-voicematch"` to line 3207
   - Summarize button: add `id="btn-summarize"` to line 3211
   - Re-run correction button: add `id="btn-rerun"` to line 3213

2. **Add IDs to job running containers** in renderDetailBody():
   - Voice match unit: wrap line 3276 in `<div id="job-voice-match">...</div>`
   - Correction unit: wrap line 3034 in `<div id="job-correction">...</div>`
   - Summary unit: wrap line 3128 in `<div id="job-summary">...</div>`
   - Format job units: wrap line 3098 in `<div id="job-format-${target.key}">...</div>`

3. **Create updateDetailJobStatus() function**:
   ```javascript
   function updateDetailJobStatus(t) {
     // Update status badge
     const sv = statusView(t);
     const badge = $('detail-status-badge');
     if (badge) {
       badge.className = 'status-badge status-badge--' + sv.word;
       badge.dataset.word = sv.word;
       badge.textContent = sv.word;
     }
     
     // Update button disabled states
     const vmBtn = $('btn-voicematch');
     if (vmBtn) {
       const active = llmJobActive(t.voice_match_job);
       vmBtn.disabled = active;
       vmBtn.title = active ? 'Voice match job already queued' : '';
     }
     const sumBtn = $('btn-summarize');
     if (sumBtn) {
       const active = llmJobActive(t.summary_job);
       sumBtn.disabled = active;
       sumBtn.title = active ? 'Summary job already queued' : '';
     }
     const rerunBtn = $('btn-rerun');
     if (rerunBtn) {
       const active = llmJobActive(t.correction_job);
       rerunBtn.disabled = active;
       rerunBtn.title = active ? 'Correction job already queued' : '';
     }
     
     // Update job running indicators in body
     updateJobRunningUnit('job-voice-match', t.voice_match_job, 'Voice match');
     updateJobRunningUnit('job-correction', t.correction_job, 'Correction');
     updateJobRunningUnit('job-summary', t.summary_job, 'Summary');
     // Format jobs
     ['markdown', 'email', 'coding_prompt'].forEach(key => {
       const job = t['format_' + key + '_job'];
       updateJobRunningUnit('job-format-' + key, job, key.replace('_', ' '));
     });
   }
   
   function updateJobRunningUnit(containerId, job, label) {
     const container = $(containerId);
     if (!container) return;
     if (llmJobActive(job)) {
       container.innerHTML = jobRunningUnit(job, label);
     } else {
       container.innerHTML = '';
     }
   }
   ```

4. **Update scheduleDetailPoll()** (line 2402):
   ```javascript
   if (_jobFingerprint(fresh) !== fp) {
     updateDetailJobStatus(fresh);  // instead of renderDetail()
   }
   ```

### Acceptance Criteria Verification
- [ ] Detail page does not rebuild segment list during job polling (only updates job indicators)
- [ ] Stage indicators update correctly during active jobs (status badge, button states, running units)
- [ ] No visible flicker or layout shift during poll ticks (segments DOM untouched)
- [ ] Segment interactions (play, rename, retag) still work after targeted updates (event listeners on detail-body survive)

## Testing Strategy

### Static check (Phase 3)
1. Verify all IDs added to job-sensitive elements
2. Verify updateDetailJobStatus() updates all job-related state
3. Verify scheduleDetailPoll() calls updateDetailJobStatus() instead of renderDetail()
4. Verify no other renderDetail() callers were changed

### Live check (if browser tool available)
1. Open transcript detail page with active LLM job
2. Observe no flicker during poll ticks
3. Verify status badge updates
4. Verify button states update
5. Verify segment interactions still work

## Files to Modify
- `static/rack.js`: add IDs, create updateDetailJobStatus(), update scheduleDetailPoll()

## Risks
- Missing a job-sensitive element → incomplete update, user sees stale state
- Breaking event listeners → segment interactions fail (but detail-body listener is delegated, should survive)
- Video element → already has special handling to avoid reload, not affected by this change
