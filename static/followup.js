// Pure helpers for the Follow-up review UI on the Summary tab (see
// docs/plans/14-followup-session.md). Mirrors the arrangement of
// static/dump_review.js: no DOM, no globals, dependency-free, so
// `node --test` can load it directly and esbuild inlines it into the
// rack.js bundle at build time.

// Mirrors FOLLOWUP_ITEM_TYPES in services/followups.py, and deliberately
// matches plan 07's Entity.type vocabulary so a later #245 ingestion needs
// no mapping table. This exact literal equality is pinned by a test.
const FOLLOWUP_ITEM_TYPES = ['action_item', 'decision', 'reference', 'question_later'];

const FOLLOWUP_TYPE_LABELS = {
  action_item: 'Action item',
  decision: 'Decision',
  reference: 'Reference',
  question_later: 'Question for later',
};

// followupState(job) is a pure function of the job ALONE: no
// finalizedRows parameter, no second fetch. "finalized" is a marker on the
// job (result_json.finalized_at), never the existence of FollowupItem rows
// -- see the Architecture decisions section of plan 14 for why row
// existence cannot be the signal (repeated follow-ups per transcript are
// allowed, and an all-discarded finalize inserts zero rows but must still
// close the card). The route guards mirror this exact predicate so the UI
// chrome and the renderer cannot disagree.
function followupState(job) {
  if (!job || typeof job !== 'object') return 'none';
  const result = (job.result_json && typeof job.result_json === 'object') ? job.result_json : {};
  if (result.finalized_at) return 'finalized';
  const status = job.status;
  if (result.phase === 'apply') {
    if (status === 'completed') return 'review';
    if (status === 'failed' || status === 'cancelled') return 'apply_failed';
    return 'applying';
  }
  // phase === 'generate', or a job whose result_json has not been written
  // yet (freshly enqueued): treat as generate lineage.
  if (status === 'completed') return 'draft';
  if (status === 'failed' || status === 'cancelled') return 'generate_failed';
  return 'generating';
}

// summaryStale(summary, job) compares the live Summary row's created_at
// against the snapshot the follow-up job captured at start time -- NEVER
// a job timestamp. A fresh apply job is a new row with a new created_at,
// so comparing against job.created_at would make the notice vanish the
// moment Apply is clicked even though the carried-forward snapshot is
// still the stale one. See "Staleness compares the snapshot against the
// live Summary, never two job timestamps" in plan 14.
function summaryStale(summary, job) {
  if (!summary || typeof summary !== 'object') return false;
  if (!job || typeof job !== 'object') return false;
  const result = (job.result_json && typeof job.result_json === 'object') ? job.result_json : {};
  const snapshot = result.summary_snapshot;
  if (!snapshot || typeof snapshot !== 'object') return false;
  return summary.created_at !== snapshot.created_at;
}

// normalizeFollowupItems(items, draft) turns job.result_json.items (the
// generate-phase seeds, enriched by the model) plus an optional saved
// draft (job.result_json.draft) into the editable shape the review cards
// bind to. `questions` always comes from the generate item -- the draft
// carries `answers` only (aligned to that same question order) plus the
// user's edited type/owner/due/private. source_bucket/source_index are
// carried straight through untouched so nothing downstream ever needs to
// reverse-parse `key`.
function normalizeFollowupItems(items, draft) {
  if (!Array.isArray(items)) return [];
  const draftByKey = {};
  if (Array.isArray(draft)) {
    draft.forEach((d) => {
      if (d && typeof d === 'object' && typeof d.key === 'string') draftByKey[d.key] = d;
    });
  }
  return items.map((it, i) => {
    const src = (it && typeof it === 'object') ? it : {};
    const key = typeof src.key === 'string' ? src.key : String(i);
    const d = draftByKey[key] || {};
    // A generate item carries `questions` plus (via the draft) bare-string
    // answers. An apply-phase `input[]` entry carries neither: it has only
    // pair-form `answers: [{question, answer}]`. The apply_failed state
    // hands us exactly that list as the draft source, so read the questions
    // back out of the pairs there -- otherwise `questions` is empty, the
    // answers array below is empty too, and every answer the user typed is
    // dropped on the way back to Apply.
    const pairs = Array.isArray(src.answers)
      ? src.answers.filter((a) => a && typeof a === 'object' && typeof a.question === 'string')
      : [];
    const questions = Array.isArray(src.questions)
      ? src.questions.filter((q) => typeof q === 'string' && q.trim())
      : pairs.map((a) => a.question).filter((q) => q.trim());
    const draftAnswers = Array.isArray(d.answers) ? d.answers : [];
    const answers = questions.map((_, qi) => {
      if (typeof draftAnswers[qi] === 'string') return draftAnswers[qi];
      const carried = pairs[qi];
      return (carried && typeof carried.answer === 'string') ? carried.answer : '';
    });
    return {
      key,
      source_bucket: typeof src.source_bucket === 'string' ? src.source_bucket : '',
      source_index: Number.isInteger(src.source_index) ? src.source_index : i,
      source_text: typeof src.source_text === 'string' ? src.source_text : '',
      type: FOLLOWUP_ITEM_TYPES.includes(d.type)
        ? d.type
        : (FOLLOWUP_ITEM_TYPES.includes(src.type) ? src.type : 'reference'),
      owner: typeof d.owner === 'string' ? d.owner : (typeof src.owner === 'string' ? src.owner : ''),
      due: typeof d.due === 'string' ? d.due : (typeof src.due === 'string' ? src.due : ''),
      // Only the user can set `private`, and the two source shapes differ.
      // A generate item (always has `questions`) may carry a model-supplied
      // private:true that the server already forced false -- ignore it here
      // too, belt and braces. An apply-phase input[] entry (never has
      // `questions`) carries the flag the user themselves set, and it MUST
      // be honoured: dropping it on the apply_failed path would feed that
      // item's text into the apply prompt on the retry.
      private: typeof d.private === 'boolean' ? d.private
        : (!Array.isArray(src.questions) && typeof src.private === 'boolean'
          ? src.private : false),
      needs_clarification: !!src.needs_clarification,
      reason: typeof src.reason === 'string' ? src.reason : '',
      questions,
      answers,
      confidence: typeof src.confidence === 'number' ? src.confidence : null,
    };
  });
}

// sortForReview orders cards by ascending confidence, so the items the
// model was least sure about surface first ("confidence only sorts the
// cards" -- plan 14). A missing/null confidence is treated as maximally
// uncertain (sorts first, same as confidence 0). Ties keep their original
// relative order (stable sort keyed on the source index).
function sortForReview(items) {
  if (!Array.isArray(items)) return [];
  return items
    .map((it, i) => [it, i])
    .sort((a, b) => {
      const ca = typeof a[0].confidence === 'number' ? a[0].confidence : -1;
      const cb = typeof b[0].confidence === 'number' ? b[0].confidence : -1;
      if (ca !== cb) return ca - cb;
      return a[1] - b[1];
    })
    .map((pair) => pair[0]);
}

// materializeApplyInput(items) turns the edited draft items (the shape
// normalizeFollowupItems returns) into the apply-phase `input[]` wire
// entries: {key, source_bucket, source_index, source_text, type, owner,
// due, private, answers: [{question, answer}]}. source_bucket and
// source_index ride straight through from the generate items so finalize
// never has to reverse-parse `key` for provenance. Unanswered questions
// are simply not carried into `answers` -- there is no separate
// "questions" field on the apply side of the wire.
function materializeApplyInput(items) {
  if (!Array.isArray(items)) return [];
  return items.map((it) => {
    const src = (it && typeof it === 'object') ? it : {};
    const questions = Array.isArray(src.questions) ? src.questions : [];
    const rawAnswers = Array.isArray(src.answers) ? src.answers : [];
    const answers = [];
    questions.forEach((q, i) => {
      const a = (rawAnswers[i] || '').trim();
      if (a) answers.push({ question: q, answer: a });
    });
    return {
      key: src.key,
      source_bucket: typeof src.source_bucket === 'string' ? src.source_bucket : '',
      source_index: Number.isInteger(src.source_index) ? src.source_index : 0,
      source_text: typeof src.source_text === 'string' ? src.source_text : '',
      type: FOLLOWUP_ITEM_TYPES.includes(src.type) ? src.type : 'reference',
      owner: src.owner || '',
      due: src.due || '',
      private: !!src.private,
      answers,
    };
  });
}

// normalizeProposals(proposals, draft) turns the apply-phase
// result_json.proposals plus an optional saved review-time draft into the
// editable review-card shape. A private item's proposal is a
// server-synthesized carry-through (changed: false, a fixed note) -- it is
// not special-cased here, it just flows through like any other proposal.
function normalizeProposals(proposals, draft) {
  if (!Array.isArray(proposals)) return [];
  const draftByKey = {};
  if (Array.isArray(draft)) {
    draft.forEach((d) => {
      if (d && typeof d === 'object' && typeof d.key === 'string') draftByKey[d.key] = d;
    });
  }
  return proposals.map((p, i) => {
    const src = (p && typeof p === 'object') ? p : {};
    const key = typeof src.key === 'string' ? src.key : String(i);
    const d = draftByKey[key] || {};
    return {
      key,
      source_bucket: typeof src.source_bucket === 'string' ? src.source_bucket : '',
      source_index: Number.isInteger(src.source_index) ? src.source_index : i,
      type: FOLLOWUP_ITEM_TYPES.includes(d.type)
        ? d.type
        : (FOLLOWUP_ITEM_TYPES.includes(src.type) ? src.type : 'reference'),
      text: typeof d.text === 'string' ? d.text : (typeof src.text === 'string' ? src.text : ''),
      owner: typeof d.owner === 'string' ? d.owner : (typeof src.owner === 'string' ? src.owner : ''),
      due: typeof d.due === 'string' ? d.due : (typeof src.due === 'string' ? src.due : ''),
      private: typeof d.private === 'boolean' ? d.private : !!src.private,
      discarded: !!d.discarded,
      confidence: typeof src.confidence === 'number' ? src.confidence : null,
      changed: !!src.changed,
      note: typeof src.note === 'string' ? src.note : '',
    };
  });
}

// materializeFinalizeItems(items) turns the reviewed items into the
// finalize wire payload: {key, type, text, owner, due, private,
// discarded}. Every item is sent, discarded ones included -- finalize
// filters server-side, same convention as voice dump's save-draft/finalize
// split. No client-side "empty text" fallback: finalize is strict there
// (400 on empty text, no silent fallback to source_text the way apply's
// normalize_apply_result is).
function materializeFinalizeItems(items) {
  if (!Array.isArray(items)) return [];
  return items.map((it) => {
    const src = (it && typeof it === 'object') ? it : {};
    return {
      key: src.key,
      type: FOLLOWUP_ITEM_TYPES.includes(src.type) ? src.type : 'reference',
      text: typeof src.text === 'string' ? src.text : '',
      owner: src.owner || '',
      due: src.due || '',
      private: !!src.private,
      discarded: !!src.discarded,
    };
  });
}

module.exports = {
  FOLLOWUP_ITEM_TYPES,
  FOLLOWUP_TYPE_LABELS,
  followupState,
  summaryStale,
  normalizeFollowupItems,
  sortForReview,
  materializeApplyInput,
  normalizeProposals,
  materializeFinalizeItems,
};
