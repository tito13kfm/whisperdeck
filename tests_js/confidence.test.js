// Tests for isLowConfidence (static/confidence.js), the single predicate
// behind the per-line "?" marker and the detail header's "N uncertain"
// count. Issue #305: the -1 user-assigned sentinel (stamped by the manual
// retag endpoint) must never read as "uncertain" — a human override is not
// the diarizer being unsure.
const test = require('node:test');
const assert = require('node:assert/strict');
const { isLowConfidence, LOW_CONFIDENCE_THRESHOLD, USER_ASSIGNED_CONFIDENCE } = require('../static/confidence.js');

test('diarizer confidence below the threshold is low', () => {
  assert.equal(isLowConfidence({ speaker_confidence: 0.3 }), true);
  assert.equal(isLowConfidence({ speaker_confidence: 0 }), true);
});

test('diarizer confidence at or above the threshold is not low', () => {
  assert.equal(isLowConfidence({ speaker_confidence: LOW_CONFIDENCE_THRESHOLD }), false);
  assert.equal(isLowConfidence({ speaker_confidence: 0.9 }), false);
});

test('the user-assigned sentinel is never uncertain (issue #305)', () => {
  assert.equal(isLowConfidence({ speaker_confidence: USER_ASSIGNED_CONFIDENCE }), false);
});

test('never-diarized segments (null or absent) are not uncertain', () => {
  assert.equal(isLowConfidence({ speaker_confidence: null }), false);
  assert.equal(isLowConfidence({}), false);
});
