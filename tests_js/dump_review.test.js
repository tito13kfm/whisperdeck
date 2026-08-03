// Tests for the voice-dump Dump Review draft helpers (static/dump_review.js),
// added with the Dump Review tab in #287. The wire contract these enforce is
// easy to get wrong and invisible until runtime: the save-draft and finalize
// routes read `discarded` (not "discard"), `type` (not "note_type"), and do
// not stamp model/provider from the job themselves, and clarifying-question
// answers are appended to the body client-side, so a non-idempotent
// materialize would duplicate a user's answer on every save.
const test = require('node:test');
const assert = require('node:assert/strict');
const { DUMP_NOTE_TYPES, normalizeDumpItems, materializeDumpItems } = require('../static/dump_review.js');

// The five values services/voice_notes.py NOTE_TYPES defines. finalize does
// item.get("type", "general") with no enum check, so this list is the only
// guard on the dropdown.
test('DUMP_NOTE_TYPES matches the backend NOTE_TYPES vocabulary exactly', () => {
  assert.deepEqual(DUMP_NOTE_TYPES, ['todo', 'idea', 'reminder', 'journal', 'general']);
});

/* ── normalizeDumpItems ── */

test('normalizeDumpItems keeps the job-runner keys and adds the client-only ones', () => {
  const raw = [{
    index: 0, type: 'todo', title: 'Fix the poller', body: 'It never refreshes.',
    structured: { items: [{ text: 'x' }] }, clarifying_questions: ['Which poller?'],
  }];
  const out = normalizeDumpItems(raw);
  assert.equal(out.length, 1);
  assert.equal(out[0].index, 0);
  assert.equal(out[0].type, 'todo');
  assert.equal(out[0].title, 'Fix the poller');
  assert.equal(out[0].body, 'It never refreshes.');
  assert.deepEqual(out[0].structured, { items: [{ text: 'x' }] });
  assert.deepEqual(out[0].clarifying_questions, ['Which poller?']);
  assert.equal(out[0].discarded, false, 'a fresh draft item is never pre-discarded');
  assert.deepEqual(out[0].answers, [], 'answers start empty and are client-only');
});

test('normalizeDumpItems preserves a stored discarded flag so a reload remembers the checkbox', () => {
  const out = normalizeDumpItems([
    { index: 0, type: 'idea', title: 'a', body: 'a', discarded: true },
    { index: 1, type: 'idea', title: 'b', body: 'b', discarded: false },
  ]);
  assert.equal(out[0].discarded, true);
  assert.equal(out[1].discarded, false);
});

test('normalizeDumpItems falls back to array position when index is missing', () => {
  const out = normalizeDumpItems([{ title: 'a' }, { title: 'b' }]);
  assert.equal(out[0].index, 0);
  assert.equal(out[1].index, 1);
  assert.equal(out[0].type, 'general', 'a missing type defaults to general, matching the finalize route');
});

test('normalizeDumpItems tolerates a malformed result_json without throwing', () => {
  assert.deepEqual(normalizeDumpItems(null), []);
  assert.deepEqual(normalizeDumpItems(undefined), []);
  assert.deepEqual(normalizeDumpItems('not a list'), []);
  const out = normalizeDumpItems([null, { clarifying_questions: 'nope', structured: 'nope' }]);
  assert.equal(out.length, 2);
  assert.deepEqual(out[0].clarifying_questions, []);
  assert.deepEqual(out[1].clarifying_questions, [], 'a non-array question list must not leak through');
  assert.deepEqual(out[1].structured, {}, 'a non-object structured must not leak through');
});

test('normalizeDumpItems drops blank clarifying questions', () => {
  const out = normalizeDumpItems([{ clarifying_questions: ['Real question?', '', '   ', 42] }]);
  assert.deepEqual(out[0].clarifying_questions, ['Real question?']);
});

/* ── materializeDumpItems ── */

const JOB = { id: 7, provider: 'groq', model: 'llama-3.3-70b-versatile' };

function draft(overrides = {}) {
  return Object.assign({
    index: 0, type: 'todo', title: 'Title', body: 'Body text',
    structured: {}, clarifying_questions: [], discarded: false, answers: [],
    model: '', provider: '',
  }, overrides);
}

test('materializeDumpItems emits the exact keys the finalize route reads', () => {
  const [out] = materializeDumpItems([draft()], JOB);
  assert.deepEqual(Object.keys(out).sort(), [
    'body', 'clarifying_questions', 'discarded', 'index', 'model', 'provider', 'structured', 'title', 'type',
  ]);
  assert.equal(out.type, 'todo', 'the wire key is "type"; the DB column note_type is mapped server-side');
  assert.equal(out.discarded, false, 'the wire key is "discarded", not "discard"');
  assert.equal(Object.prototype.hasOwnProperty.call(out, 'answers'), false, 'answers is client-only and never sent');
});

test('materializeDumpItems stamps model and provider from the job', () => {
  // finalize does item.get("model", "") with no fallback to the job's own
  // values, so attribution is lost unless the client fills these in.
  const [out] = materializeDumpItems([draft()], JOB);
  assert.equal(out.model, 'llama-3.3-70b-versatile');
  assert.equal(out.provider, 'groq');
});

test('materializeDumpItems keeps a per-item model that already differs from the job', () => {
  const [out] = materializeDumpItems([draft({ model: 'other-model', provider: 'lemonade' })], JOB);
  assert.equal(out.model, 'other-model');
  assert.equal(out.provider, 'lemonade');
});

test('materializeDumpItems survives a null job without inventing attribution', () => {
  const [out] = materializeDumpItems([draft()], null);
  assert.equal(out.model, '');
  assert.equal(out.provider, '');
});

test('an answered clarifying question is appended to the body and removed from the question list', () => {
  const [out] = materializeDumpItems([draft({
    body: 'Ship the review tab.',
    clarifying_questions: ['Which endpoint saves it?', 'When is it finalized?'],
    answers: ['The save-draft route.', ''],
  })], JOB);
  assert.equal(out.body, 'Ship the review tab.\n\nWhich endpoint saves it?\nThe save-draft route.');
  assert.deepEqual(out.clarifying_questions, ['When is it finalized?'],
    'an unanswered question must survive so the user can answer it after a reload');
});

test('re-materializing a saved draft does not append the same answer twice', () => {
  // The idempotency guarantee: save-draft echoes back what it stored, the UI
  // re-seeds from that, and a second Save must be a no-op on the body. A
  // materialize that appended answers without pruning the question would
  // duplicate the answer on every save.
  const first = materializeDumpItems([draft({
    body: 'Original.',
    clarifying_questions: ['Q1?'],
    answers: ['A1.'],
  })], JOB);
  assert.equal(first[0].body, 'Original.\n\nQ1?\nA1.');
  const second = materializeDumpItems(normalizeDumpItems(first), JOB);
  assert.equal(second[0].body, 'Original.\n\nQ1?\nA1.', 'the answer must not be appended a second time');
  assert.deepEqual(second[0].clarifying_questions, []);
  const third = materializeDumpItems(normalizeDumpItems(second), JOB);
  assert.equal(third[0].body, 'Original.\n\nQ1?\nA1.');
});

test('a whitespace-only answer is treated as unanswered', () => {
  const [out] = materializeDumpItems([draft({
    body: 'Body.', clarifying_questions: ['Q?'], answers: ['   '],
  })], JOB);
  assert.equal(out.body, 'Body.', 'no empty question/answer block appended');
  assert.deepEqual(out.clarifying_questions, ['Q?']);
});

test('an answer on an item with an empty body becomes the body', () => {
  const [out] = materializeDumpItems([draft({
    body: '', clarifying_questions: ['Q?'], answers: ['A.'],
  })], JOB);
  assert.equal(out.body, 'Q?\nA.', 'no leading blank lines from an empty body');
});

test('materializeDumpItems carries the discard flag through for finalize to filter on', () => {
  const out = materializeDumpItems([
    draft({ index: 0, title: 'keep' }),
    draft({ index: 1, title: 'drop', discarded: true }),
  ], JOB);
  assert.equal(out.length, 2, 'both items are sent; finalize does the filtering server-side');
  assert.equal(out[0].discarded, false);
  assert.equal(out[1].discarded, true);
  assert.deepEqual(out.filter(it => !it.discarded).map(it => it.title), ['keep']);
});

test('materializeDumpItems preserves an edited type, including one outside NOTE_TYPES', () => {
  const out = materializeDumpItems([
    draft({ type: 'reminder' }),
    draft({ type: 'bug' }),
  ], JOB);
  assert.equal(out[0].type, 'reminder', 'a reclassify via the dropdown must reach the wire');
  assert.equal(out[1].type, 'bug', 'an unknown stored type round-trips instead of being silently rewritten');
});

test('materializeDumpItems tolerates missing arrays and a non-array input', () => {
  const [out] = materializeDumpItems([{ index: 0, title: 'a', body: 'b' }], JOB);
  assert.deepEqual(out.clarifying_questions, []);
  assert.equal(out.body, 'b');
  assert.deepEqual(materializeDumpItems(null, JOB), []);
  assert.deepEqual(materializeDumpItems('nope', JOB), []);
});
