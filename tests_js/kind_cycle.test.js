const test = require('node:test');
const assert = require('node:assert');
const { KIND_CYCLE, nextTranscriptKind } = require('../static/kind_cycle.js');

test('the rotation visits every kind and wraps back to the start', () => {
  assert.strictEqual(nextTranscriptKind('meeting'), 'dictation');
  assert.strictEqual(nextTranscriptKind('dictation'), 'voice_note');
  assert.strictEqual(nextTranscriptKind('voice_note'), 'voice_dump');
  assert.strictEqual(nextTranscriptKind('voice_dump'), 'meeting');
});

test('voice_dump is reachable from inside the cycle (#299)', () => {
  // This is the assertion that pins the fix. Pre-#299 the chain was
  //   meeting -> dictation -> voice_note -> meeting
  // so voice_dump was not a member: it could never be reached by toggling,
  // and a transcript that was already voice_dump fell through to the
  // `: 'meeting'` arm. Membership is what makes it reachable again.
  assert.ok(KIND_CYCLE.includes('voice_dump'));
  assert.strictEqual(nextTranscriptKind('voice_note'), 'voice_dump',
    'voice_note must lead into voice_dump, not skip back to meeting');
});

test('leaving voice_dump still lands on meeting, by wrap not by fallback', () => {
  // Worth stating explicitly so nobody "fixes" this later: voice_dump ->
  // meeting is the correct end of the rotation and matches the pre-#299
  // output. The data-detachment guard is the confirm prompt in rack.js, not
  // this return value. Changing this to avoid meeting would break the cycle.
  assert.strictEqual(nextTranscriptKind('voice_dump'), 'meeting');
});

test('an unrecognised kind declines to cycle instead of defaulting', () => {
  // null means "leave the kind alone". Returning a default here is what
  // caused the original data-detachment bug, so a future kind added to the
  // schema but not to KIND_CYCLE must not be silently rewritten.
  assert.strictEqual(nextTranscriptKind('some_future_kind'), null);
  assert.strictEqual(nextTranscriptKind(''), null);
  assert.strictEqual(nextTranscriptKind(undefined), null);
  assert.strictEqual(nextTranscriptKind(null), null);
});

test('cycling four times from any member returns to that member', () => {
  for (const start of KIND_CYCLE) {
    let k = start;
    for (let i = 0; i < KIND_CYCLE.length; i++) k = nextTranscriptKind(k);
    assert.strictEqual(k, start, `cycle from ${start} did not close`);
  }
});
