// Regression tests for computeBatchAggregate (static/rack.js), the Queue
// batch-group status/count logic. Every case here is a bug that shipped in
// PR #256 with zero test coverage -- these exist so none of them can ship
// silently again.
const test = require('node:test');
const assert = require('node:assert/strict');
const { computeBatchAggregate } = require('../static/batch_aggregate.js');

// rack.js defines RED/GREEN as module-scoped consts, not exported (they're
// plain colors, not logic worth re-testing) -- pull the literal values in
// directly so this file has no hidden coupling to import order.
const RED_FOR_TEST = '#E0554A';
const GREEN_FOR_TEST = '#5FCB7A';

function job(status, overrides = {}) {
  return { status, title: status, ...overrides };
}

test('a running job counts as processing, not falling through unmatched', () => {
  const r = computeBatchAggregate([job('completed'), job('running')]);
  assert.equal(r.counts.processing, 1, 'running job must increment processing, not vanish');
  assert.equal(r.statusLine.includes('processing'), true);
});

test('cancelled jobs are not folded into "done"', () => {
  const r = computeBatchAggregate([job('completed'), job('cancelled')]);
  assert.equal(r.done, 1, 'done must count only completed, not completed+cancelled');
  assert.equal(r.counts.cancelled, 1);
  assert.equal(r.statusLine.includes('cancelled'), true);
});

test('an all-cancelled batch (0 completed) has done=0, not done=total', () => {
  const r = computeBatchAggregate([job('cancelled'), job('cancelled')]);
  assert.equal(r.done, 0);
  assert.equal(r.total, 2);
});

test('a fully-failed batch (no active jobs) shows FAILED, not green DONE', () => {
  const r = computeBatchAggregate([job('completed'), job('failed')]);
  assert.equal(r.activeInBatch, 0);
  assert.equal(r.failedInBatch, 1);
  assert.equal(r.badgeWord, 'FAILED');
  assert.equal(r.badgeClass, 'failed');
  assert.equal(r.batchColor, RED_FOR_TEST);
});

test('a batch with an active job shows ACTIVE even if it also has a failure', () => {
  const r = computeBatchAggregate([job('running'), job('failed')]);
  assert.equal(r.badgeWord, 'ACTIVE');
  assert.equal(r.badgeClass, 'running');
});

test('a fully-successful batch shows DONE with green color', () => {
  const r = computeBatchAggregate([job('completed'), job('completed')]);
  assert.equal(r.badgeWord, 'DONE');
  assert.equal(r.batchColor, GREEN_FOR_TEST);
});

test('queued and waiting both count as pending, not processing', () => {
  const r = computeBatchAggregate([job('queued'), job('waiting')]);
  assert.equal(r.counts.pending, 2);
  assert.equal(r.counts.processing, 0);
});

test('a single-job group still aggregates correctly (no minimum-size assumption)', () => {
  const r = computeBatchAggregate([job('completed')]);
  assert.equal(r.total, 1);
  assert.equal(r.done, 1);
  assert.equal(r.badgeWord, 'DONE');
});
