// Pure helpers for the voice-dump Dump Review tab: normalizing the draft
// items the LLM chain wrote onto the job, and turning the user's edited
// draft back into the wire payload /voice-dump/save-draft and
// /voice-dump/finalize accept. Kept dependency-free (no DOM, no globals)
// so it can be unit-tested directly in Node without loading rack.js's
// browser-only code -- same arrangement as batch_aggregate.js. esbuild
// inlines it into the bundle at build time, so the served file is still
// one self-contained script; nothing changes at runtime.

// Mirrors NOTE_TYPES in services/voice_notes.py. The finalize route does
// item.get("type", "general") with no enum check, so this list is the only
// thing keeping the type dropdown honest -- see the unknown-value handling
// in dumpReviewHtml, which round-trips a value outside this list rather
// than silently rewriting it.
const DUMP_NOTE_TYPES = ['todo', 'idea', 'reminder', 'journal', 'general', 'bug'];

// Draft items come off job.result_json.items with the keys the job runner
// wrote (services/llm_jobs.py): index, type, title, body, structured,
// clarifying_questions. `discarded` is added by this UI and read back by
// the finalize route; `answers` is client-only and never sent -- it is
// folded into `body` by materializeDumpItems.
function normalizeDumpItems(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map((it, i) => {
    const src = (it && typeof it === 'object') ? it : {};
    return {
      index: Number.isInteger(src.index) ? src.index : i,
      type: (typeof src.type === 'string' && src.type) ? src.type : 'general',
      title: typeof src.title === 'string' ? src.title : '',
      body: typeof src.body === 'string' ? src.body : '',
      structured: (src.structured && typeof src.structured === 'object') ? src.structured : {},
      clarifying_questions: Array.isArray(src.clarifying_questions)
        ? src.clarifying_questions.filter(q => typeof q === 'string' && q.trim())
        : [],
      discarded: !!src.discarded,
      answers: [],
      model: typeof src.model === 'string' ? src.model : '',
      provider: typeof src.provider === 'string' ? src.provider : '',
    };
  });
}

// Fold answered clarifying questions into the body and drop them from the
// question list, so re-saving the same draft cannot append an answer
// twice: the result is what gets stored, and re-normalizing the stored
// form yields no answered questions to fold. Unanswered questions survive
// so the user can come back to them after a reload.
//
// `discarded` is kept on the wire (finalize filters on it, and save-draft
// stores it so a reload remembers the checkbox). model/provider are
// stamped from the job because the finalize route reads them off each item
// and does not fall back to the job's own values.
function materializeDumpItems(items, job) {
  const jobModel = (job && job.model) || '';
  const jobProvider = (job && job.provider) || '';
  return (Array.isArray(items) ? items : []).map(it => {
    const questions = Array.isArray(it.clarifying_questions) ? it.clarifying_questions : [];
    const answers = Array.isArray(it.answers) ? it.answers : [];
    const answered = [], unanswered = [];
    questions.forEach((q, qi) => {
      const a = (answers[qi] || '').trim();
      if (a) answered.push(q + '\n' + a);
      else unanswered.push(q);
    });
    const parts = [(it.body || '').trim()].concat(answered).filter(Boolean);
    return {
      index: it.index,
      type: it.type || 'general',
      title: it.title || '',
      body: parts.join('\n\n'),
      structured: it.structured || {},
      clarifying_questions: unanswered,
      discarded: !!it.discarded,
      model: it.model || jobModel,
      provider: it.provider || jobProvider,
    };
  });
}

module.exports = { DUMP_NOTE_TYPES, normalizeDumpItems, materializeDumpItems };
