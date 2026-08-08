# Issue #246 Investigation

## Issue Summary
`_jobFingerprint` in `static/rack.js` (lines 3196-3201) builds a poll-comparison string from various job fields but **never included `tagging_job`** when tagging shipped. This means when only tagging is active, the fingerprint doesn't change when tagging progress updates, so `updateDetailJobStatus`/re-render never fires.

## Current Code Analysis

### `_jobFingerprint` function (static/rack.js:3196-3201)
```javascript
function _jobFingerprint(t) {
  const f = (j) => j ? j.status + ':' + (j.progress ? j.progress.done : 0) : '-';
  return f(t.correction_job) + '|' + f(t.summary_job) + '|' + f(t.voice_match_job) + '|' +
    f(t.format_markdown_job) + '|' + f(t.format_email_job) + '|' + f(t.format_coding_prompt_job) + '|' +
    f(t.classify_intent_job);
}
```

**Missing:** `f(t.tagging_job)` — this is the bug.

### `scheduleDetailPoll` function (static/rack.js:3205-3222)
```javascript
function scheduleDetailPoll() {
  clearTimeout(detailPollTimer);
  const t = detailData;
  if (!t || !(llmJobActive(t.correction_job) || llmJobActive(t.summary_job) || llmJobActive(t.voice_match_job) ||
    llmJobActive(t.format_markdown_job) || llmJobActive(t.format_email_job) || llmJobActive(t.format_coding_prompt_job) ||
    llmJobActive(t.classify_intent_job) || llmJobActive(t.tagging_job))) return;
  const fp = _jobFingerprint(t), id = t.id, prevActive = jobActiveSnapshot(t);
  // ... poll logic ...
  if (_jobFingerprint(fresh) !== fp) await updateDetailJobStatus(fresh, prevActive);
```

**Correct:** Line 3210 DOES check `llmJobActive(t.tagging_job)` — so polling continues while tagging runs.
**Problem:** But the fingerprint comparison on line 3218 won't detect tagging-only changes.

### `jobActiveSnapshot` function (static/rack.js:3655-3666)
```javascript
function jobActiveSnapshot(t) {
  return {
    correction: llmJobActive(t.correction_job),
    summary: llmJobActive(t.summary_job),
    voice_match: llmJobActive(t.voice_match_job),
    format_markdown: llmJobActive(t.format_markdown_job),
    format_email: llmJobActive(t.format_email_job),
    format_coding_prompt: llmJobActive(t.format_coding_prompt_job),
    classify_intent: llmJobActive(t.classify_intent_job),
    tagging: llmJobActive(t.tagging_job),
  };
}
```

**Correct:** Line 3664 DOES include `tagging: llmJobActive(t.tagging_job)`.

## Sibling Sweep

Searched for all job field references in `static/rack.js`:

1. **`llmJobActive` guard in `scheduleDetailPoll` (line 3208-3210):** ✅ Includes `tagging_job`
2. **`jobActiveSnapshot` (line 3655-3666):** ✅ Includes `tagging_job`
3. **`_jobFingerprint` (line 3196-3201):** ❌ MISSING `tagging_job`

**Conclusion:** The bug is isolated to `_jobFingerprint`. The guard and snapshot both correctly handle tagging, but the fingerprint string used for change detection does not.

## Other job fields in `_jobFingerprint`
- `correction_job` ✅
- `summary_job` ✅
- `voice_match_job` ✅
- `format_markdown_job` ✅
- `format_email_job` ✅
- `format_coding_prompt_job` ✅
- `classify_intent_job` ✅
- `tagging_job` ❌ **MISSING**

## Backend Serialization
Confirmed in `app.py` lines 368, 384, 392, 398: `tagging_job` is serialized and returned in the transcript API response for all transcript kinds (meeting, dictation, voice_note). The field exists and is populated.

## Existing Tests
- `tests/e2e/test_detail_poll_partial_update.py`: Tests detail polling for correction jobs, but does NOT test tagging_job fingerprint changes.
- `tests/test_tagging.py`: Tests tagging service logic, but NOT UI polling/fingerprint behavior.
- No existing test specifically covers `_jobFingerprint` change detection for tagging.

## Fix Required
Add `f(t.tagging_job)` to the `_jobFingerprint` concatenation in `static/rack.js` line 3198-3200.

## Acceptance Criteria (from issue)
- [ ] A detail-poll test asserting the fingerprint string changes when only `tagging_job.progress.done` changes

## Files to Modify
- `static/rack.js`: Add `f(t.tagging_job)` to `_jobFingerprint` function
- `tests/e2e/test_detail_poll_partial_update.py` or new test file: Add test for tagging_job fingerprint change

## Complement Rule Check
Searched for all similar patterns:
- All other job fields (`correction_job`, `summary_job`, `voice_match_job`, `format_*_job`, `classify_intent_job`) are already in `_jobFingerprint`
- `tagging_job` is the ONLY missing field
- No other job fields exist in the codebase that aren't already included

## Issue's Suggested Fix
The issue correctly identifies the fix: "Add `f(t.tagging_job)` to the `_jobFingerprint` concatenation, matching the other job fields already there."

This matches our investigation findings exactly.
