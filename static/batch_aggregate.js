// Pure function: turn one batch's job list into the counts/colors/badge/text
// the Queue batch-group header renders. Kept in its own file, free of any
// DOM/global dependency, so it can be unit-tested directly in Node (see
// tests_js/batch_aggregate.test.js) without loading rack.js's browser-only
// code. Every case here is a bug that shipped in PR #256 with zero test
// coverage: cancelled counted as done, "processing" never incrementing
// (status string mismatch), and a fully-failed batch rendering green/DONE.
function computeBatchAggregate(group) {
  const counts = { completed: 0, failed: 0, partial: 0, pending: 0, processing: 0, cancelled: 0 };
  let activeInBatch = 0, failedInBatch = 0;
  for (const j of group) {
    if (j.status === 'running' || j.status === 'queued' || j.status === 'waiting') activeInBatch++;
    if (j.status === 'failed' || j.status === 'partial') failedInBatch++;
    if (j.status === 'running') counts.processing++;
    else if (j.status === 'queued' || j.status === 'waiting') counts.pending++;
    else if (counts[j.status] != null) counts[j.status] = (counts[j.status] || 0) + 1;
  }
  const total = group.length;
  const done = counts.completed;
  const lit = total ? Math.max(1, Math.round(done / total * 11)) : 0;
  const RED = '#E0554A', GREEN = '#5FCB7A';
  const batchColor = failedInBatch > 0 && activeInBatch === 0 ? RED : GREEN;
  const badgeWord = activeInBatch ? 'ACTIVE' : (failedInBatch > 0 ? 'FAILED' : 'DONE');
  const badgeClass = activeInBatch ? 'running' : (failedInBatch > 0 ? 'failed' : 'done');
  const statusLine = [counts.completed ? counts.completed + ' done' : '',
    counts.processing ? counts.processing + ' processing' : '',
    counts.pending ? counts.pending + ' pending' : '',
    counts.failed ? counts.failed + ' failed' : '',
    counts.cancelled ? counts.cancelled + ' cancelled' : ''].filter(Boolean).join(' · ');
  return { counts, activeInBatch, failedInBatch, total, done, lit, batchColor, badgeWord, badgeClass, statusLine };
}

module.exports = { computeBatchAggregate };
