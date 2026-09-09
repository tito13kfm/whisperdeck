// Tests for the Follow-up review draft helpers (static/followup.js), see
// docs/plans/14-followup-session.md. Two signatures are load-bearing and
// easy to get backwards: followupState(job) takes the job ALONE (no
// finalizedRows -- finalized is job.result_json.finalized_at), and
// summaryStale(summary, job) compares summary.created_at against
// job.result_json.summary_snapshot.created_at, never a job timestamp.
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  FOLLOWUP_ITEM_TYPES,
  FOLLOWUP_TYPE_LABELS,
  followupState,
  summaryStale,
  normalizeFollowupItems,
  sortForReview,
  materializeApplyInput,
  normalizeProposals,
  materializeFinalizeItems,
} = require('../static/followup.js');

/* ── FOLLOWUP_ITEM_TYPES ── */

test('FOLLOWUP_ITEM_TYPES matches the backend enum literally', () => {
  assert.deepEqual(FOLLOWUP_ITEM_TYPES, ['action_item', 'decision', 'reference', 'question_later']);
});

test('FOLLOWUP_TYPE_LABELS has a label for every type and no extras', () => {
  assert.deepEqual(Object.keys(FOLLOWUP_TYPE_LABELS).sort(), [...FOLLOWUP_ITEM_TYPES].sort());
  FOLLOWUP_ITEM_TYPES.forEach((t) => {
    assert.equal(typeof FOLLOWUP_TYPE_LABELS[t], 'string');
    assert.ok(FOLLOWUP_TYPE_LABELS[t].length > 0);
  });
});

/* ── followupState: the eight-state matrix, driven by the job alone ── */

test('followupState is "none" when there is no job', () => {
  assert.equal(followupState(null), 'none');
  assert.equal(followupState(undefined), 'none');
});

test('followupState: generate phase running/pending is "generating"', () => {
  assert.equal(followupState({ status: 'running', result_json: { phase: 'generate' } }), 'generating');
  assert.equal(followupState({ status: 'pending', result_json: { phase: 'generate' } }), 'generating');
});

test('followupState: generate phase failed or cancelled is "generate_failed"', () => {
  assert.equal(followupState({ status: 'failed', result_json: { phase: 'generate' } }), 'generate_failed');
  assert.equal(followupState({ status: 'cancelled', result_json: { phase: 'generate' } }), 'generate_failed');
});

test('followupState: generate phase completed is "draft"', () => {
  assert.equal(followupState({ status: 'completed', result_json: { phase: 'generate', items: [] } }), 'draft');
});

test('followupState: apply phase running/pending is "applying"', () => {
  assert.equal(followupState({ status: 'running', result_json: { phase: 'apply' } }), 'applying');
  assert.equal(followupState({ status: 'pending', result_json: { phase: 'apply' } }), 'applying');
});

test('followupState: apply phase failed or cancelled is "apply_failed" (draft-editable, per plan 14)', () => {
  assert.equal(followupState({ status: 'failed', result_json: { phase: 'apply' } }), 'apply_failed');
  assert.equal(followupState({ status: 'cancelled', result_json: { phase: 'apply' } }), 'apply_failed');
});

test('followupState: apply phase completed with no finalized_at is "review"', () => {
  assert.equal(
    followupState({ status: 'completed', result_json: { phase: 'apply', proposals: [] } }),
    'review',
  );
});

test('followupState: finalized_at on the job is "finalized", regardless of finalized_count', () => {
  assert.equal(
    followupState({
      status: 'completed',
      result_json: { phase: 'apply', proposals: [], finalized_at: '2026-09-08T00:00:00', finalized_count: 3 },
    }),
    'finalized',
  );
  // An all-discarded finalize inserts zero rows but still closes the card:
  // finalized_count: 0 must not fall back to "review".
  assert.equal(
    followupState({
      status: 'completed',
      result_json: { phase: 'apply', proposals: [], finalized_at: '2026-09-08T00:00:00', finalized_count: 0 },
    }),
    'finalized',
  );
});

test('followupState does not take a finalizedRows parameter -- it is a function of the job alone', () => {
  // Regression guard for the exact mistake the design doc calls out: a
  // second argument must have no effect on the result.
  const job = { status: 'completed', result_json: { phase: 'apply', proposals: [], finalized_at: 'x' } };
  assert.equal(followupState(job, []), followupState(job, [{ id: 1 }, { id: 2 }]));
});

test('followupState: a fresh job with no phase written yet falls back to generate lineage', () => {
  assert.equal(followupState({ status: 'pending', result_json: {} }), 'generating');
  assert.equal(followupState({ status: 'pending', result_json: null }), 'generating');
});

/* ── summaryStale: snapshot vs live Summary, never a job timestamp ── */

test('summaryStale is false when the Summary has not changed since the snapshot', () => {
  const job = { created_at: '2026-01-01T00:00:00', result_json: { summary_snapshot: { created_at: 'S1' } } };
  assert.equal(summaryStale({ created_at: 'S1' }, job), false);
});

test('summaryStale is true when the live Summary created_at differs from the snapshot', () => {
  const job = { created_at: '2026-01-01T00:00:00', result_json: { summary_snapshot: { created_at: 'S1' } } };
  assert.equal(summaryStale({ created_at: 'S2' }, job), true);
});

test('summaryStale still fires when the latest job is the apply job (snapshot carried forward)', () => {
  // The apply job is a brand-new row with its own created_at; comparing
  // against that instead of the snapshot would make the notice vanish the
  // moment Apply is clicked even though the snapshot is still stale.
  const applyJob = {
    id: 99,
    created_at: '2026-02-02T00:00:00', // deliberately close to nothing below
    result_json: { phase: 'apply', summary_snapshot: { created_at: 'S1' } },
  };
  assert.equal(summaryStale({ created_at: 'S2' }, applyJob), true);
  // And the job's own created_at must play no role at all: an equal
  // job.created_at/summary.created_at coincidence must not suppress it.
  const trickyJob = {
    created_at: 'S2',
    result_json: { phase: 'apply', summary_snapshot: { created_at: 'S1' } },
  };
  assert.equal(summaryStale({ created_at: 'S2' }, trickyJob), true);
});

test('summaryStale tolerates missing summary, job, result_json or snapshot without throwing', () => {
  assert.equal(summaryStale(null, { result_json: {} }), false);
  assert.equal(summaryStale({ created_at: 'S1' }, null), false);
  assert.equal(summaryStale({ created_at: 'S1' }, { result_json: null }), false);
  assert.equal(summaryStale({ created_at: 'S1' }, { result_json: {} }), false);
});

/* ── normalizeFollowupItems ── */

test('normalizeFollowupItems keeps generate-item keys and defaults answers to empty per question', () => {
  const items = [{
    key: 'a0', source_bucket: 'action_items', source_index: 0, source_text: 'Ship it',
    type: 'action_item', owner: '', due: '', private: false,
    needs_clarification: true, reason: 'no owner given', questions: ['Who owns this?'], confidence: 0.4,
  }];
  const out = normalizeFollowupItems(items, null);
  assert.equal(out.length, 1);
  assert.equal(out[0].key, 'a0');
  assert.equal(out[0].source_bucket, 'action_items');
  assert.equal(out[0].source_index, 0);
  assert.equal(out[0].source_text, 'Ship it');
  assert.equal(out[0].type, 'action_item');
  assert.equal(out[0].needs_clarification, true);
  assert.equal(out[0].reason, 'no owner given');
  assert.deepEqual(out[0].questions, ['Who owns this?']);
  assert.deepEqual(out[0].answers, ['']);
  assert.equal(out[0].confidence, 0.4);
  assert.equal(out[0].private, false);
});

test('normalizeFollowupItems overlays a saved draft by key, keeping answers aligned to the question order', () => {
  const items = [
    {
      key: 'a0', source_bucket: 'action_items', source_index: 0, source_text: 'Ship it',
      type: 'action_item', questions: ['Who owns this?', 'When is it due?'],
    },
    {
      key: 'd0', source_bucket: 'decisions', source_index: 0, source_text: 'Use SQLite',
      type: 'decision', questions: [],
    },
  ];
  const draft = [
    { key: 'a0', type: 'decision', owner: 'Alice', due: '2026-10-01', private: true, answers: ['Alice', ''] },
  ];
  const out = normalizeFollowupItems(items, draft);
  assert.equal(out[0].type, 'decision', 'the drafted type wins over the generate-time type');
  assert.equal(out[0].owner, 'Alice');
  assert.equal(out[0].due, '2026-10-01');
  assert.equal(out[0].private, true);
  assert.deepEqual(out[0].answers, ['Alice', '']);
  // d0 has no draft entry, so it falls back to the generate-item defaults.
  assert.equal(out[1].owner, '');
  assert.equal(out[1].private, false);
});

test('normalizeFollowupItems ignores a model-supplied private:true at generate time (no draft yet)', () => {
  // Per plan 14: private is always false at generate; only a saved draft
  // (the user) can set it. This guards against a future normalizer
  // regression that trusts the raw item's own `private` field.
  const out = normalizeFollowupItems([{ key: 'a0', private: true, questions: [] }], null);
  assert.equal(out[0].private, false);
});

test('normalizeFollowupItems clamps an unknown type to the bucket default "reference"', () => {
  const out = normalizeFollowupItems([{ key: 'k0', type: 'not_a_real_type', questions: [] }], null);
  assert.equal(out[0].type, 'reference');
});

test('normalizeFollowupItems falls back to array position when key is missing, and tolerates malformed input', () => {
  assert.deepEqual(normalizeFollowupItems(null, null), []);
  assert.deepEqual(normalizeFollowupItems(undefined, null), []);
  assert.deepEqual(normalizeFollowupItems('nope', null), []);
  const out = normalizeFollowupItems([{ questions: ['Q?'] }, null], null);
  assert.equal(out[0].key, '0');
  assert.equal(out[1].key, '1');
  assert.deepEqual(out[1].questions, []);
});

test('normalizeFollowupItems drops blank questions and pairs answers only to real ones', () => {
  const out = normalizeFollowupItems([{ key: 'a0', questions: ['Real?', '', '   ', 42] }], null);
  assert.deepEqual(out[0].questions, ['Real?']);
  assert.deepEqual(out[0].answers, ['']);
});

/* ── sortForReview: confidence only sorts the cards ── */

test('sortForReview orders ascending by confidence, least-confident first', () => {
  const out = sortForReview([
    { key: 'a', confidence: 0.9 },
    { key: 'b', confidence: 0.1 },
    { key: 'c', confidence: 0.5 },
  ]);
  assert.deepEqual(out.map((it) => it.key), ['b', 'c', 'a']);
});

test('sortForReview treats a missing confidence as maximally uncertain, so it sorts first', () => {
  const out = sortForReview([
    { key: 'a', confidence: 0.2 },
    { key: 'b', confidence: null },
    { key: 'c' },
  ]);
  assert.deepEqual(out.map((it) => it.key), ['b', 'c', 'a']);
});

test('sortForReview is stable across equal confidence and does not mutate the input array', () => {
  const input = [{ key: 'a', confidence: 0.5 }, { key: 'b', confidence: 0.5 }];
  const out = sortForReview(input);
  assert.deepEqual(out.map((it) => it.key), ['a', 'b']);
  assert.equal(input[0].key, 'a', 'the original array order must be untouched');
});

test('sortForReview tolerates a non-array input', () => {
  assert.deepEqual(sortForReview(null), []);
  assert.deepEqual(sortForReview('nope'), []);
});

/* ── materializeApplyInput ── */

test('materializeApplyInput emits the apply input[] wire shape, carrying source_bucket/source_index through', () => {
  const items = normalizeFollowupItems([{
    key: 'a0', source_bucket: 'action_items', source_index: 2, source_text: 'Ship it',
    type: 'action_item', owner: 'Bob', due: '2026-09-10', questions: ['Who owns this?'],
  }], [{ key: 'a0', private: true, answers: ['Bob confirmed'] }]);
  const [out] = materializeApplyInput(items);
  assert.deepEqual(Object.keys(out).sort(), [
    'answers', 'due', 'key', 'owner', 'private', 'source_bucket', 'source_index', 'source_text', 'type',
  ]);
  assert.equal(out.key, 'a0');
  assert.equal(out.source_bucket, 'action_items');
  assert.equal(out.source_index, 2);
  assert.equal(out.source_text, 'Ship it');
  assert.equal(out.private, true);
  assert.deepEqual(out.answers, [{ question: 'Who owns this?', answer: 'Bob confirmed' }]);
});

test('materializeApplyInput drops unanswered questions from answers[] entirely', () => {
  const items = normalizeFollowupItems([{
    key: 'a0', source_bucket: 'action_items', source_index: 0, questions: ['Q1?', 'Q2?'],
  }], [{ key: 'a0', answers: ['A1', ''] }]);
  const [out] = materializeApplyInput(items);
  assert.deepEqual(out.answers, [{ question: 'Q1?', answer: 'A1' }]);
});

test('materializeApplyInput is deterministic: calling it twice on the same normalized items matches exactly', () => {
  const items = normalizeFollowupItems([
    { key: 'a0', source_bucket: 'action_items', source_index: 0, source_text: 'x', questions: ['Q?'] },
    { key: 'd0', source_bucket: 'decisions', source_index: 1, source_text: 'y', questions: [] },
  ], [{ key: 'a0', answers: ['A'] }]);
  assert.deepEqual(materializeApplyInput(items), materializeApplyInput(items));
});

test('materializeApplyInput tolerates malformed input', () => {
  assert.deepEqual(materializeApplyInput(null), []);
  assert.deepEqual(materializeApplyInput('nope'), []);
});

/* ── normalizeProposals ── */

test('normalizeProposals keeps proposal fields and overlays a review-time draft by key', () => {
  const proposals = [
    { key: 'a0', source_bucket: 'action_items', source_index: 0, type: 'action_item', text: 'Ship it by Friday.', owner: 'Bob', due: '2026-09-12', confidence: 0.8, changed: true, note: '' },
    { key: 'k0', source_bucket: 'key_points', source_index: 0, type: 'reference', text: 'Use SQLite.', owner: '', due: '', confidence: null, changed: false, note: 'kept private - not rewritten', private: true },
  ];
  const out = normalizeProposals(proposals, [{ key: 'a0', text: 'Ship it by Monday instead.', discarded: true }]);
  assert.equal(out[0].text, 'Ship it by Monday instead.', 'the reviewer\'s edit overrides the model text');
  assert.equal(out[0].discarded, true);
  assert.equal(out[0].source_bucket, 'action_items');
  assert.equal(out[0].source_index, 0);
  assert.equal(out[1].private, true, 'the synthesized carry-through proposal keeps its private flag with no draft entry');
  assert.equal(out[1].changed, false);
  assert.equal(out[1].note, 'kept private - not rewritten');
});

test('normalizeProposals defaults discarded to false and clamps an unknown type', () => {
  const out = normalizeProposals([{ key: 'a0', type: 'nonsense', text: 't' }], null);
  assert.equal(out[0].discarded, false);
  assert.equal(out[0].type, 'reference');
});

test('normalizeProposals tolerates malformed input', () => {
  assert.deepEqual(normalizeProposals(null, null), []);
  assert.deepEqual(normalizeProposals('nope', []), []);
});

/* ── materializeFinalizeItems ── */

test('materializeFinalizeItems emits exactly the finalize wire keys, discarded items included', () => {
  const reviewed = normalizeProposals([
    { key: 'a0', type: 'action_item', text: 'Ship it.', owner: 'Bob', due: '2026-09-12' },
    { key: 'k0', type: 'reference', text: 'Use SQLite.' },
  ], [{ key: 'k0', discarded: true }]);
  const out = materializeFinalizeItems(reviewed);
  assert.equal(out.length, 2, 'both items are sent; finalize filters discarded server-side');
  assert.deepEqual(Object.keys(out[0]).sort(), ['discarded', 'due', 'key', 'owner', 'private', 'text', 'type']);
  assert.equal(out[0].discarded, false);
  assert.equal(out[1].discarded, true);
});

test('materializeFinalizeItems does not fall back an empty text to source_text -- finalize is strict there', () => {
  const out = materializeFinalizeItems([{ key: 'a0', type: 'action_item', text: '' }]);
  assert.equal(out[0].text, '', 'no silent fallback; the server 400s on empty text');
});

test('materializeFinalizeItems tolerates malformed input', () => {
  assert.deepEqual(materializeFinalizeItems(null), []);
  assert.deepEqual(materializeFinalizeItems('nope'), []);
});
