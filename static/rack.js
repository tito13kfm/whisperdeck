/* WhisperDeck — Signal Rack frontend
   Vanilla JS, no build step. Values trace to design_handoff_whisperdesk_signal_rack/. */
'use strict';

/* ══════════════════ state ══════════════════ */
const S = {
  page: 'dashboard',
  user: null,
  isAdmin: false,
  authMode: 'login',          // login | register
  registrationMode: 'open',   // open | invite | closed — latched from /api/bootstrap (issue #395); server enforces, this only drives chrome
  passwordMinLength: 8,       // overridden from <meta name="wd-password-min-length"> at DOMContentLoaded
  detailId: null,
  detailTab: 'transcript',
  query: '',
  bankQuery: '',
  bankSort: 'date-desc',
  // transcribe
  tapeLoaded: false,
  tapeName: '',
  tapeFile: null,
  tapeIsLiveStereo: false,
  running: false,
  runningId: null,
  pct: 0,
  stage: null,                // upload (=Initialize) | transcribe | diarize | finalize
  jobStartedAt: null,
  indeterminate: false,       // running with no chunk data — show elapsed, not %
  jobDone: false,
  doneId: null,
  doneDuration: null,
  // settings state (persisted server-side)
  providers: [],
  providerIdx: 0,
  models: [],
  modelIdx: 0,
  langIdx: 0,
  diarize: true,
  autoCorrect: false,
  correctionPending: false,   // true while a non-blocking post-completion auto-correct poll is watching this run
  correctionStatus: null,     // pending | running | completed | failed — mirrors the run's correction_job.status
  mode: 'meeting',            // auto | meeting | dictation | voice_note | voice_dump — the last three are single-speaker: they skip diarization and unlock their own post-pipeline sets
  advSpeakerCount: null,      // null = auto-detect, else integer 1-12
  advTitle: '',
  advTemperature: 0,          // 0-10 wheel steps; sent to backend as /10 (0.0-1.0)
  advContext: '',
  exportDir: '',              // populated from /api/bootstrap.settings.export_directory; non-empty enables the "Save as .md" button
  // live capture
  capturing: false,
  stereoLive: false,
  permPending: false,          // true while browser mic-permission prompt is open
  // prefs (localStorage)
  theme: 'charcoal',
  phosphor: '#5CFFAC',
  motion: true,
  assistantHistory: [],
  bankSearchResults: null,           // FTS5 search results (issue #108)
  bankSearchController: null,        // AbortController for in-flight search
  // bulk import
  bulkFiles: [],
  bulkDefaults: null,                // cached from /api/settings
  bulkSubmitting: false,
};

const LANGUAGES = ['English', 'Auto-detect', 'Spanish', 'French', 'German', 'Japanese', 'Chinese'];

/* ══════════════════ themes (verbatim from canonical prototype) ══════════════════ */
const THEMES = {
  'charcoal': {},
  'silverface': {
    '--rack': '#E7E2D5', '--rail': '#DCD6C6', '--rail-edge': '#C2BCAA',
    '--panel-hi': '#F6F4EE', '--panel': '#E5E2D8', '--panel-lo': '#CDC9BC', '--edge': '#ACA795',
    '--label': '#2C2A24', '--label-dim': '#6E695C', '--label-faint': '#979181', '--body': '#3D3A31',
    '--hover': '#DBD7CB', '--hover-rail': '#D6D0C0', '--nav-active': '#D3CDBB', '--nav-border': '#B4AE9C',
    '--svc-hi': '#EAE7DE', '--svc': '#DEDACE', '--svc-lo': '#C6C2B4', '--svc-edge': '#AAA593',
    '--inset-edge': '#B0AB99', '--input': '#F2F0E8', '--input-edge': '#B0AB99',
    '--dash': '#A29D8C', '--dash-hover': '#837E6E', '--seg-edge': '#C8C3B4', '--dash2': '#B7B2A1',
  },
  'champagne': {
    '--rack': '#EFE8D8', '--rail': '#E4D9BE', '--rail-edge': '#C8BB98',
    '--panel-hi': '#F4EBD3', '--panel': '#E9DDBE', '--panel-lo': '#D2C29C', '--edge': '#AE9D74',
    '--label': '#3E3524', '--label-dim': '#7C7054', '--label-faint': '#A29572', '--body': '#4A4030',
    '--hover': '#E0D4B4', '--hover-rail': '#DCD0AE', '--nav-active': '#DCCEA8', '--nav-border': '#BBAB82',
    '--svc-hi': '#EDE3C9', '--svc': '#E2D5B4', '--svc-lo': '#CBBB92', '--svc-edge': '#AC9B70',
    '--inset-edge': '#B5A47B', '--input': '#F5EEDC', '--input-edge': '#B5A47B',
    '--dash': '#A6976E', '--dash-hover': '#8A7B54', '--seg-edge': '#CDBF9C', '--dash2': '#BCAD86',
  },
  'blue-glass': {
    '--rack': '#07090E', '--rail': '#0B0D13', '--rail-edge': '#1B1F2B',
    '--panel-hi': '#292E3A', '--panel': '#212630', '--panel-lo': '#161A22', '--edge': '#141823',
    '--label': '#D9DDEA', '--label-dim': '#8A90A4', '--label-faint': '#5C6274', '--body': '#C2C7D7',
    '--hover': '#2B303D', '--hover-rail': '#151923', '--nav-active': '#171B26', '--nav-border': '#2B3140',
    '--svc-hi': '#1D212B', '--svc': '#161A23', '--svc-lo': '#0F1218', '--svc-edge': '#10131B',
    '--inset-edge': '#10131B', '--input': '#10131B', '--input-edge': '#0B0E15',
    '--dash': '#343B4D', '--dash-hover': '#4A5268', '--seg-edge': '#272D3B', '--dash2': '#222834',
  },
};
const THEME_ORDER = ['charcoal', 'silverface', 'champagne', 'blue-glass'];
const PHOSPHORS = [
  { name: 'Green', value: '#5CFFAC' },
  { name: 'Cyan', value: '#4DE8D8' },
  { name: 'Amber', value: '#FFB84D' },
];

function applyTheme(name) {
  const root = document.documentElement;
  // clear previous overrides, then set the chosen theme's
  for (const t of Object.values(THEMES)) {
    for (const k of Object.keys(t)) root.style.removeProperty(k);
  }
  const map = THEMES[name] || {};
  for (const [k, v] of Object.entries(map)) root.style.setProperty(k, v);
  S.theme = THEMES[name] ? name : 'charcoal';
  localStorage.setItem('rack-theme', S.theme);
}

function applyMotion(on) {
  S.motion = !!on;
  document.body.classList.toggle('no-motion', !S.motion);
  localStorage.setItem('rack-motion', S.motion ? '1' : '0');
}

function applyPhosphor(value) {
  S.phosphor = value;
  localStorage.setItem('rack-phosphor', value);
}

function loadPrefs() {
  const t = localStorage.getItem('rack-theme');
  if (t && THEMES[t]) applyTheme(t);
  const m = localStorage.getItem('rack-motion');
  if (m !== null) applyMotion(m === '1');
  const p = localStorage.getItem('rack-phosphor');
  if (p && PHOSPHORS.some(x => x.value === p)) S.phosphor = p;
}

function motionAllowed() {
  return S.motion && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/* ══════════════════ tiny helpers ══════════════════ */
const $ = (id) => document.getElementById(id);

let csrfToken = null;
let bootData = null;  // cached /api/bootstrap response, used by checkAuth + loadDashboard

async function refreshCsrfToken() {
  try {
    const r = await api('/api/csrf-token');
    csrfToken = r && r.token ? r.token : null;
  } catch (e) {
    csrfToken = null;
    console.warn('CSRF token refresh failed:', e.message);
  }
  return csrfToken;
}

function csrfHeader() {
  return csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function timeAgo(iso) {
  if (!iso) return '';
  const s = Math.floor((Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

function formatTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

function formatDur(sec) {
  if (sec == null) return '—';
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return String(m).padStart(2, '0') + 'm' + String(s).padStart(2, '0') + 's';
}

// One predicate for both the per-line "?" marker and the detail header's
// "N uncertain" count — a threshold tweak in one place must move both.
// Lives in ./confidence.js (dependency-free, unit-tested in Node) and is
// inlined into the bundle at build time, same as batch_aggregate.js below.
// It excludes the -1 "user assigned this label by hand" sentinel that the
// retag endpoint stamps (issue #305).
const { isLowConfidence } = require('./confidence.js');

function hashColor(name) {
  let h = 0;
  for (let i = 0; i < (name || '').length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const hues = [145, 175, 30, 200, 90, 260, 330, 55];
  return 'hsl(' + hues[h % hues.length] + ',55%,62%)';
}

function toast(msg, type = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast' + (type === 'error' ? ' error' : type === 'info' ? ' info' : '');
  el.textContent = msg;
  $('toast-wrap').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

// A voice clip can be stored successfully yet on a degraded embedding model
// (the MFCC fallback), which leaves it unmatchable by voice match. The voice
// routes report that as a `warning` field on an otherwise-successful response
// — surface it everywhere we store a clip, or the degradation stays invisible
// (issue #109).
function toastVoiceWarning(r) {
  if (r && r.warning) toast(r.warning, 'info');
}

// Provider/parsing failures land in job.error as raw exception text (a
// json.JSONDecodeError message glued to a repr'd content snippet, or a raw
// HTTP error body) — see services/transcription.py and services/llm_client.py.
// Translate the known shapes into something a user can act on; anything
// unrecognized falls back to the raw message rather than hiding it.
function humanizeJobError(raw) {
  if (!raw) return 'unknown error';
  if (/did not return valid JSON/i.test(raw)) {
    return 'The AI model returned an unreadable response. Try rerunning — if it keeps happening, try a different model.';
  }
  const apiErr = raw.match(/API error \((\d+)\)/);
  if (apiErr) {
    return 'The AI provider returned an error (HTTP ' + apiErr[1] + '). Check the provider/model configuration and try again.';
  }
  return raw;
}

let inFlightCount = 0;

function setInFlight(n) {
  inFlightCount += n;
  const led = document.getElementById('net-led');
  if (led) led.classList.toggle('on', inFlightCount > 0);
}

async function api(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  const isMutation = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);
  const headers = { ...(opts.headers || {}) };
  if (isMutation && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  setInFlight(1);
  try {
    let res = await fetch(path, { credentials: 'same-origin', ...opts, headers });
    if (res.status === 401) { showLogin(); throw new Error('Not signed in'); }
    // One-shot CSRF retry: if another tab rotated the token (login, register),
    // refresh and retry once instead of failing silently (issue #51).
    if (res.status === 403 && isMutation) {
      let probe = null;
      try { probe = await res.clone().json(); } catch { /* non-JSON */ }
      // Must match the literal in app.py enforce_csrf -- keep in sync.
      if (probe && probe.detail === 'Invalid or missing CSRF token') {
        await refreshCsrfToken();
        if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
        res = await fetch(path, { credentials: 'same-origin', ...opts, headers });
        if (res.status === 401) { showLogin(); throw new Error('Not signed in'); }
      }
    }
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) {
      const detail = data && (data.detail || data.error) ? (data.detail || data.error) : ('HTTP ' + res.status);
      const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      err.status = res.status;
      throw err;
    }
    return data;
  } finally {
    setInFlight(-1);
  }
}

// A 409 from a job action means the row was mid-write when we asked (issue
// #391); the action did not happen and is safe to retry once the worker's
// transaction commits; a second 409 falls through to the caller's normal
// error toast, whose message (the backend detail) already tells the user
// to retry.
async function apiRetry409(path, opts = {}) {
  try { return await api(path, opts); }
  catch (err) {
    if (err.status !== 409) throw err;
    await new Promise(r => setTimeout(r, 600));
    return api(path, opts);
  }
}

// Disables `el` synchronously (re-entrancy guard) for the duration of `fn`.
// `el` must be the element resolved at click time (e.currentTarget / e.target.closest(...)),
// not a reference captured at bind time, so this works under event delegation too.
async function withBusy(el, fn, opts = {}) {
  // No element to guard (a caller forgot to pass one) — still run the action
  // rather than silently no-op; just skip the busy-state chrome.
  if (!el) return fn();
  if (el.disabled) return;
  const prevText = opts.busyText ? el.textContent : null;
  el.disabled = true;
  if (opts.busyText) el.textContent = opts.busyText;
  if (opts.spinner) el.classList.add('is-busy');
  try {
    return await fn();
  } finally {
    el.disabled = false;
    el.classList.remove('is-busy');
    if (opts.busyText) el.textContent = prevText;
  }
}

/* ══════════════════ component render helpers ══════════════════ */

// nixie readout: str rendered per-glyph with ghost-8 behind. variant: '', 'dim', 'fault'
// color overrides the tube glow (e.g. green "ML" diarization stat).
function nixie(str, variant = '', color = null) {
  const style = color ? ' style="color:' + color + ';text-shadow:0 0 3px ' + color + ',0 0 9px rgba(255,138,61,0.5)"' : '';
  const glyphs = String(str).split('').map(ch =>
    '<i aria-hidden="true"><b' + style + '>' + escapeHtml(ch) + '</b></i>').join('');
  const label = escapeHtml(String(str));
  return '<span class="nixie ' + variant + '" aria-label="' + label + '">' + glyphs + '</span>';
}

// 11-cell LED bargraph. cells: array of {on, color}. Color is set via the
// --on-color custom property so the cell class controls all the visual state.
function bargraph(cells, height = 14) {
  const inner = cells.map(c => c.on
    ? '<span class="on" style="--on-color:' + c.color + '"></span>'
    : '<span></span>').join('');
  return '<span class="bargraph" style="height:' + height + 'px">' + inner + '</span>';
}

// LED dot. Off-state = bare led-dot (default dim). On-state = led-dot--on with
// --led-color custom property. Size still needs an inline style since each
// caller may use a different size.
function ledDot(color, glow = true, size = 8) {
  if (!color) return '<span class="led-dot" style="width:' + size + 'px;height:' + size + 'px"></span>';
  return '<span class="led-dot led-dot--on" style="width:' + size + 'px;height:' + size + 'px;--led-color:' + color +
    ';box-shadow:0 0 5px ' + color + '"></span>';
}

// VFD value window, fixed width; marquee class added post-insert when text overflows
function vfd(text, id) {
  return '<span class="vfd"' + (id ? ' id="' + id + '"' : '') + '><span>' + escapeHtml(text) + '</span></span>';
}

// After inserting VFD windows, call to enable marquee only where text overflows.
function armVfdMarquees(rootEl) {
  (rootEl || document).querySelectorAll('.vfd').forEach(w => {
    const inner = w.firstElementChild;
    if (!inner) return;
    inner.classList.toggle('scroll', motionAllowed() && inner.scrollWidth > w.clientWidth + 1);
  });
}

/* ── the ONE status→presentation mapping used by every transcript view ──
   (Monitor recents, Tape library rows, detail meta must always agree.) */
const GREEN = '#5FCB7A', AMBER = '#E0A83E', RED = '#E0554A';
// Percent is derived, not stored: chunked jobs report chunks_done/chunks_total
// (queue_status while processing, job_progress otherwise); single-shot jobs
// have neither and sit at 0 until terminal.
function transcriptPct(t) {
  const qs = t.queue_status;
  if (qs && qs.chunks_total) return Math.round(qs.chunks_done / qs.chunks_total * 100);
  const jp = t.job_progress;
  if (jp && jp.total) return Math.round(jp.completed / jp.total * 100);
  if (t.status === 'completed') return 100;
  return 0;
}
function statusView(t) {
  const status = t.status || 'queued';
  const pct = Math.max(0, Math.min(100, transcriptPct(t)));
  const qs = t.queue_status || {};
  let color, lit, nix, nixVariant = '', word = status;
  switch (status) {
    case 'completed':
      color = GREEN; lit = 11; nix = '100%'; word = 'done'; break;
    case 'failed':
      color = RED; lit = 3; nix = 'ERR'; nixVariant = 'fault'; word = 'failed'; break;
    case 'partial':
      color = AMBER; lit = Math.max(1, Math.round(pct / 100 * 11)); nix = pct + '%'; word = 'partial'; break;
    case 'cancelled':
      color = AMBER; lit = 0; nix = pct + '%'; nixVariant = 'dim'; word = 'cancelled'; break;
    case 'processing':
      if (!qs.state && !(t.job_progress && t.job_progress.total)) {
        // single-shot run: no chunk jobs exist, so there is no percent —
        // show elapsed time instead of a frozen 0%
        color = AMBER; lit = 1;
        nix = t.created_at ? formatTime((Date.now() - new Date(t.created_at + 'Z').getTime()) / 1000) : '--:--';
        word = 'working';
      } else if (qs.state === 'queued') {
        color = null; lit = 0;
        nix = t.duration_seconds != null ? formatTime(t.duration_seconds) : '--:--';
        nixVariant = 'dim'; word = 'queued';
      } else {
        color = AMBER; lit = Math.round(pct / 100 * 11); nix = pct + '%';
        word = qs.state === 'rate_limited' ? 'waiting' : 'running';
      }
      break;
    default:
      color = null; lit = 0; nix = '--:--'; nixVariant = 'dim'; word = status;
  }
  const cells = [];
  for (let i = 0; i < 11; i++) cells.push({ on: color !== null && i < lit, color });
  // Per-stage segments (issue #70): only for chunked runs, where chunks_done/
  // chunks_total give a real boundary to derive stages from — mirrors
  // pollTranscript's own S.stage inference (rack.js pollTranscript) so the
  // Transcribe deck's stageLeds() and this bank-row view can't disagree.
  // Single-shot runs have no chunk data to derive stage boundaries from, so
  // they keep the 11-cell percent bar above instead (segments stays null).
  const segments = stageSegmentsFromQueueStatus(word, qs, t.diarize_requested);
  return { cells, nix, nixVariant, word, color: color || 'var(--label-dim)', pct, status, segments };
}

function stageSegmentsFromQueueStatus(word, qs, diarizeRequested) {
  if (!qs || !qs.chunks_total) return null;
  const total = qs.chunks_total, done = qs.chunks_done || 0;
  let st;
  if (word === 'done') st = 'done';
  else if (word === 'queued') st = 'upload';
  else if (done >= total) st = diarizeRequested ? 'diarize' : 'finalize';
  else if (qs.state === 'transcribing' || done > 0) st = 'transcribe';
  else st = 'upload';
  return [
    { label: 'Initialize', done: st !== 'upload', on: st === 'upload' },
    { label: 'Transcribe', done: st === 'diarize' || st === 'finalize' || st === 'done', on: st === 'transcribe' },
    { label: 'Diarize', done: st === 'finalize' || st === 'done', on: st === 'diarize' },
    { label: 'Finalize', done: st === 'done', on: st === 'finalize' },
  ];
}

// 4-cell stage-segment bar, reusing .bargraph/.bargraph>span.on as-is.
function stageSegmentBar(segments, height = 16) {
  const cells = segments.map(d => ({ on: d.done || d.on, color: d.done ? GREEN : d.on ? AMBER : null }));
  return bargraph(cells, height);
}

/* ══════════════════ navigation ══════════════════ */
const PAGES = ['dashboard', 'transcribe', 'transcripts', 'voicenotes', 'dumpnotes', 'bulk', 'queue', 'costs', 'detail', 'voices', 'files', 'settings', 'assistant'];

// Rail chrome (Tape-library/Voice-roster nav badges, storage meter) is
// otherwise only refreshed as a side effect of loadDashboard() — visiting
// any other page, or a job finishing while parked on one, left it showing
// whatever was true at the last Monitor visit. Best-effort like
// refreshQueueBadge(): a failed fetch (e.g. a 401 right after logout)
// shouldn't surface as a toast for a background chrome refresh.
async function refreshRailChrome() {
  try {
    const st = await api('/api/status');
    $('nav-badge-transcripts').textContent = String(st.total_transcripts ?? 0).padStart(2, '0');
    $('nav-badge-voices').textContent = String(st.voice_profiles ?? 0).padStart(2, '0');
    const vnBadge = $('nav-badge-voicenotes');
    if (vnBadge) vnBadge.textContent = String(st.voice_notes ?? 0).padStart(2, '0');
    const vdBadge = $('nav-badge-dumpnotes');
    if (vdBadge) vdBadge.textContent = String(st.voice_dump_unseen ?? 0).padStart(2, '0');
    updateRailStorage(st.total_minutes ?? 0);
  } catch { /* chrome refresh is best-effort */ }
}

function navigate(page, data) {
  if (!PAGES.includes(page)) page = 'dashboard';
  S.page = page;
  if (page !== 'detail' && videoFloating) reattachVideo();
  refreshRailChrome();
  if (page === 'detail' && data != null) S.detailId = data;
  PAGES.forEach(p => $('page-' + p).classList.toggle('active', p === page));
  document.querySelectorAll('.rail-btn').forEach(b => {
    const target = b.dataset.nav;
    b.classList.toggle('active', target === page || (target === 'transcripts' && page === 'detail'));
  });
  const loaders = {
    dashboard: loadDashboard,
    transcribe: renderTranscribe,
    transcripts: loadTranscripts,
    voicenotes: loadVoiceNotes,
    dumpnotes: loadVoiceDumpItems,
    bulk: loadBulk,
    queue: () => loadQueue({force: true}),
    costs: loadCostsPage,
    detail: () => loadTranscriptDetail(S.detailId),
    voices: loadVoices,
    files: renderFilesPage,
    settings: loadSettingsPage,
    assistant: loadAssistant,
  };
  (loaders[page] || (() => {}))();
}

/* ══════════════════ assistant page ══════════════════ */
async function loadAssistant() {
  if (!S.user) { showLogin(); return; }
  renderAssistant();
}

function renderAssistant() {
  const el = $('page-assistant');
  if (!el) return;

  try {
    const raw = sessionStorage.getItem('wd_assistant_history');
    const parsed = raw ? JSON.parse(raw) : null;
    S.assistantHistory = Array.isArray(parsed) ? parsed : [];
  } catch { S.assistantHistory = []; }

  el.innerHTML = `
    <div class="unit" style="padding:24px 32px;margin-bottom:16px">
      <h2 class="t-cap" style="font-size:14px;margin-bottom:16px">Assistant</h2>
      <p style="font-size:13px;color:var(--label-dim);margin-bottom:16px">Ask anything about your transcripts — search, summarize, export.</p>
      <div style="display:flex;gap:8px;align-items:flex-start">
        <textarea id="assistant-input" class="inp" placeholder="Ask anything about your transcripts..." maxlength="2000" rows="2" style="flex:1;min-width:0;resize:vertical;font-family:var(--f-mono);font-size:12px"></textarea>
        <button id="assistant-send" class="key" style="height:52px;padding:0 20px;font-size:12px">Send</button>
      </div>
      <div style="display:flex;justify-content:space-between;margin-top:6px">
        <span id="assistant-char-count" style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim)">0 / 2000</span>
        <span id="assistant-status" style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim)"></span>
      </div>
      <div id="assistant-progress" style="display:none;margin-top:16px">
        <div class="t-cap" style="font-size:11px;margin-bottom:6px;color:var(--label-dim)" id="assistant-progress-label">Processing…</div>
        <div class="bargraph slim" id="assistant-bargraph"></div>
      </div>
      <div id="assistant-result" style="display:none;margin-top:16px"></div>
      <div id="assistant-error" style="display:none;margin-top:16px"></div>
    </div>
    <div class="unit" style="padding:20px 32px">
      <h3 class="t-cap" style="font-size:12px;margin-bottom:12px;color:var(--label-dim)">Recent requests</h3>
      <div id="assistant-history-list" style="display:flex;flex-direction:column;gap:8px"></div>
    </div>
  `;

  const input = $('assistant-input');
  const sendBtn = $('assistant-send');
  const charCount = $('assistant-char-count');

  input.addEventListener('input', () => {
    charCount.textContent = input.value.length + ' / 2000';
    charCount.style.color = input.value.length > 1900 ? 'var(--amber)' : 'var(--label-dim)';
  });
  sendBtn.addEventListener('click', () => submitAssistantRequest());
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitAssistantRequest(); }
  });

  renderAssistantHistory();

  if (S._assistantPrefill) {
    input.value = S._assistantPrefill;
    charCount.textContent = input.value.length + ' / 2000';
    charCount.style.color = input.value.length > 1900 ? 'var(--amber)' : 'var(--label-dim)';
    delete S._assistantPrefill;
  }
}

function renderAssistantHistory() {
  const list = $('assistant-history-list');
  if (!list) return;
  if (S.assistantHistory.length === 0) {
    list.innerHTML = '<div style="font-size:12px;color:var(--label-faint);padding:8px 0">No requests yet</div>';
    return;
  }
  list.innerHTML = S.assistantHistory.slice(-5).reverse().map((entry, i) => {
    const statusIcon = entry.status === 'completed'
      ? '<span class="led-dot" style="background:var(--green)"></span>'
      : entry.status === 'failed'
        ? '<span class="led-dot" style="background:var(--red)"></span>'
        : '<span class="led-dot" style="background:var(--amber)"></span>';
    const truncated = entry.request.length > 60 ? entry.request.slice(0, 60) + '…' : entry.request;
    const resultPreview = entry.result
      ? (entry.result.summary ? entry.result.summary.slice(0, 100) : '')
      : (entry.error || '');
    const realIdx = S.assistantHistory.length - 1 - i;
    return '<div style="display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--edge);font-size:12px;cursor:pointer" onclick="expandAssistantHistory(' + realIdx + ')">' +
      '<span>' + statusIcon + '</span>' +
      '<div style="flex:1;min-width:0">' +
        '<div style="color:var(--label);font-family:var(--f-mono);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escapeHtml(truncated) + '</div>' +
        '<div style="color:var(--label-dim);font-size:11px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escapeHtml(resultPreview) + '</div>' +
      '</div>' +
    '</div>';
  }).join('');
}

async function submitAssistantRequest() {
  const input = $('assistant-input');
  const sendBtn = $('assistant-send');
  if (!input || !sendBtn) return;
  const request = input.value.trim();
  if (!request) return;

  input.disabled = true;
  sendBtn.disabled = true;
  const statusEl = $('assistant-status');
  if (statusEl) statusEl.textContent = 'Submitting…';

  $('assistant-result').style.display = 'none';
  $('assistant-error').style.display = 'none';
  $('assistant-progress').style.display = 'block';
  $('assistant-progress-label').textContent = 'Enqueuing job…';

  try {
    const formData = new URLSearchParams();
    formData.append('request', request);
    const resp = await api('/api/assistant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData.toString(),
    });
    const jobId = resp && resp.job && resp.job.id;
    if (!jobId) throw new Error('No job id returned');
    if (statusEl) statusEl.textContent = 'Job ' + jobId;
    pollAssistantJob(jobId, request);
  } catch (e) {
    showAssistantError(request, e.message);
  }
}

function pollAssistantJob(jobId, request) {
  const labelEl = $('assistant-progress-label');
  const bar = $('assistant-bargraph');
  if (labelEl) labelEl.textContent = 'Processing…';

  const poll = async () => {
    try {
      const data = await api('/api/assistant/result/' + jobId);

      if (data.status === 'pending' || data.status === 'running') {
        if (data.progress && data.progress.total > 0) {
          const pct = Math.round((data.progress.done / data.progress.total) * 100);
          if (labelEl) labelEl.textContent = 'Processing… ' + pct + '%';
          if (bar) {
            if (typeof bargraph === 'function') {
              bar.innerHTML = bargraph([{ on: true, color: 'var(--amber)' }], 6);
            } else {
              bar.innerHTML = '<div style="height:6px;background:var(--panel-lo);border-radius:3px"><div style="width:' + pct + '%;height:100%;background:var(--amber);border-radius:3px"></div></div>';
            }
          }
        } else {
          if (labelEl) labelEl.textContent = 'Processing…';
        }
        setTimeout(poll, 1500);
      } else if (data.status === 'completed') {
        $('assistant-progress').style.display = 'none';
        const statusEl = $('assistant-status');
        if (statusEl) statusEl.textContent = 'Completed';
        showAssistantResult(request, data.result);
      } else if (data.status === 'failed') {
        $('assistant-progress').style.display = 'none';
        const statusEl = $('assistant-status');
        if (statusEl) statusEl.textContent = 'Failed';
        showAssistantError(request, (data && data.error) || 'Job failed');
      }
    } catch (e) {
      setTimeout(poll, 3000);
    }
  };

  poll();
}

function showAssistantResult(request, result) {
  const el = $('assistant-result');
  if (!el) return;
  el.style.display = 'block';

  const inner = (result && result.result) || {};
  const summary = inner.summary || '';
  const filePath = inner.file_path || null;
  const preview = inner.preview || summary;

  let html = '<div class="t-cap" style="font-size:11px;margin-bottom:8px;color:var(--green)">Complete</div>';
  if (summary) {
    html += '<div class="unit" style="padding:16px;margin-bottom:10px;font-size:13px;line-height:1.6;color:var(--body);white-space:pre-wrap">' + escapeHtml(summary) + '</div>';
  }
  html += '<div style="display:flex;gap:8px;margin-top:10px">';
  html += '<button id="assistant-copy" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Copy</button>';
  html += '<button id="assistant-download" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Download .txt</button>';
  if (filePath) {
    html += '<button id="assistant-copy-path" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)" title="' + escapeHtml(filePath) + '">Copy path</button>';
  }
  html += '</div>';
  el.innerHTML = html;

  const textToCopy = preview || '';
  $('assistant-copy')?.addEventListener('click', () => copyToClipboard(textToCopy));
  $('assistant-download')?.addEventListener('click', () => downloadTextFile('assistant-result.txt', textToCopy));
  $('assistant-copy-path')?.addEventListener('click', () => copyToClipboard(filePath));

  saveAssistantHistory(request, 'completed', result);
}

function showAssistantError(request, error) {
  const el = $('assistant-error');
  if (!el) return;
  el.style.display = 'block';
  el.innerHTML =
    '<div class="t-cap" style="font-size:11px;margin-bottom:8px;color:var(--red)">Error</div>' +
    '<div style="font-size:12px;color:var(--body);margin-bottom:10px">' + escapeHtml(error || 'Unknown error') + '</div>' +
    '<button id="assistant-retry" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Retry</button>';
  $('assistant-retry')?.addEventListener('click', () => submitAssistantRequest());

  const input = $('assistant-input');
  const sendBtn = $('assistant-send');
  if (input) input.disabled = false;
  if (sendBtn) sendBtn.disabled = false;

  saveAssistantHistory(request, 'failed', null, error);
}

function saveAssistantHistory(request, status, result, error) {
  const entry = { request, status, result: result || null, error: error || null, time: Date.now() };
  S.assistantHistory.push(entry);
  if (S.assistantHistory.length > 5) S.assistantHistory = S.assistantHistory.slice(-5);

  try {
    sessionStorage.setItem('wd_assistant_history', JSON.stringify(S.assistantHistory));
  } catch { /* swallow */ }

  if (S.page === 'assistant') renderAssistantHistory();
}

function expandAssistantHistory(idx) {
  const entry = S.assistantHistory[idx];
  if (!entry) return;
  const summary = entry.result && entry.result.summary ? entry.result.summary : '';
  const errorText = entry.error || '';
  if (summary) {
    openModal(
      '<h2 class="modal-title">Assistant result</h2>' +
      '<div style="font-size:12px;color:var(--label-dim);margin-bottom:12px;font-family:var(--f-mono)">' + escapeHtml(entry.request) + '</div>' +
      '<div style="font-size:13px;line-height:1.6;color:var(--body);white-space:pre-wrap;max-height:60vh;overflow:auto">' + escapeHtml(summary) + '</div>' +
      '<div class="modal-actions"><button class="btn btn--ghost btn--sm" id="expand-close">Close</button></div>'
    );
    $('expand-close')?.addEventListener('click', closeModal);
  } else if (errorText) {
    toast(errorText, 'error');
  } else {
    toast('No result available', 'info');
  }
}

/* ══════════════════ modal primitive ══════════════════ */
function openModal(html) {
  $('modal-box').innerHTML = html;
  $('modal-overlay').classList.add('open');
}
function closeModal() {
  // Any dismissal path that closes the modal without an explicit button-click
  // resolver (Escape key, clicking the overlay backdrop) must still settle the
  // pending styledConfirm/styledPrompt Promise, or the awaiting coroutine hangs
  // forever. Button-click handlers clear pendingStyledModal to null themselves
  // (and resolve with their own value) before calling closeModal(), so this is
  // a no-op in that case.
  if (pendingStyledModal) {
    const { resolve, cancelValue } = pendingStyledModal;
    pendingStyledModal = null;
    resolve(cancelValue);
  }
  $('modal-overlay').classList.remove('open');
  $('modal-box').innerHTML = '';
}

// Tracks the pending styledConfirm/styledPrompt resolver (and its cancel value
// used for non-button dismissals) so closeModal() can settle the Promise
// instead of leaving the awaiting coroutine suspended forever. Cleared
// whenever the modal resolves via a button click (before closeModal() runs).
let pendingStyledModal = null;

function styledConfirm(message) {
  return new Promise(resolve => {
    openModal(`
      <h2 class="modal-title">${escapeHtml(message)}</h2>
      <div class="modal-actions">
        <button id="styled-confirm-cancel" class="btn btn--ghost btn--sm">Cancel</button>
        <button class="btn btn--red" id="styled-confirm-ok" style="font-size:12px">Confirm</button>
      </div>`);
    pendingStyledModal = { resolve, cancelValue: false };
    $('styled-confirm-cancel').addEventListener('click', () => { pendingStyledModal = null; closeModal(); resolve(false); });
    $('styled-confirm-ok').addEventListener('click', () => { pendingStyledModal = null; closeModal(); resolve(true); });
  });
}

function styledPrompt(message, defaultValue) {
  return new Promise(resolve => {
    openModal(`
      <h2 class="modal-title">${escapeHtml(message)}</h2>
      <input class="inp" id="styled-prompt-input" type="text" value="${escapeHtml(defaultValue || '')}" style="font-size:13px;padding:8px 10px;width:100%;margin-bottom:16px">
      <div class="modal-actions">
        <button id="styled-prompt-cancel" class="btn btn--ghost btn--sm">Cancel</button>
        <button id="styled-prompt-ok" class="btn btn--amber btn--sm">OK</button>
      </div>`);
    const input = $('styled-prompt-input');
    input.focus();
    input.select();
    const submit = () => { const v = input.value; pendingStyledModal = null; closeModal(); resolve(v); };
    pendingStyledModal = { resolve, cancelValue: null };
    $('styled-prompt-cancel').addEventListener('click', () => { pendingStyledModal = null; closeModal(); resolve(null); });
    $('styled-prompt-ok').addEventListener('click', submit);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  });
}

/* ══════════════════ auth ══════════════════ */
// Every path back to the login screen (explicit logout, a 401 mid-session,
// or checkAuth() finding no session on page load) routes through here, so
// this is the one place to clear per-account state — otherwise the next
// account to sign in inherits the previous account's deck status, detail
// view, and caches (issue #54).
function resetDeckState() {
  bootData = null;
  S.user = null;
  S.isAdmin = false;
  S.detailId = null;
  S.detailTab = 'transcript';
  S.query = '';
  S.bankQuery = '';
  S.tapeLoaded = false;
  S.tapeName = '';
  S.tapeFile = null;
  S.tapeIsLiveStereo = false;
  S.running = false;
  S.runningId = null;
  S.pct = 0;
  S.stage = null;
  S.jobStartedAt = null;
  S.indeterminate = false;
  S.jobDone = false;
  S.doneId = null;
  S.doneDuration = null;
  // providerIdx is intentionally left alone: ensureProviders() owns picking
  // firstReady and early-returns once S.providers is populated, so forcing
  // this to 0 here would only match a fresh page load by coincidence.
  S.modelIdx = 0;
  S.langIdx = 0;
  S.diarize = true;
  S.autoCorrect = false;
  S.correctionPending = false;
  S.correctionStatus = null;
  S.mode = 'auto';
  S.capturing = false;
  S.stereoLive = false;
  S.permPending = false;
  stopCorrectionPoll();
  stopBackgroundJobPoll();
  clearTimeout(bankPollTimer);
  bankPollTimer = null;
  clearTimeout(detailPollTimer);
  detailPollTimer = null;
  clearTimeout(dashPollTimer);
  dashPollTimer = null;
  clearTimeout(queuePollTimer);
  queuePollTimer = null;
  S.bulkFiles = [];
  S.bulkDefaults = null;
  S.bulkSubmitting = false;
  detailData = null;
  // Holds user-authored, not-yet-saved dump-review edits — per-account
  // client state, cleared here for the same reason detailData is (#54).
  dumpReview = null;
  bankListCache = [];
  seedClips = {};
  expandedVoice = null;
  _jobCache = { data: null, ts: 0, pending: null };
}

function showLogin() {
  resetDeckState();
  // #video-dock lives outside #app-shell (so it survives renderDetail()),
  // which also means hiding app-shell alone leaves it floating over the
  // login screen — close it explicitly.
  if (videoFloating) closeVideoDock();
  $('page-login').style.display = 'flex';
  $('app-shell').style.display = 'none';
  // Re-sync on every show: the latched mode can change between paints
  // (e.g. 'open' at first boot, 'invite' after the first registration).
  syncRegistrationChrome();
}

/* Registration-mode chrome (issue #395): the server enforces the gate; this
   mirrors it so a closed instance never offers a dead register form and an
   invite instance always shows the invite field in register mode. Called
   from showLogin (first paint, logout re-show) and toggleAuthMode. */
function syncRegistrationChrome() {
  const closed = S.registrationMode === 'closed';
  $('auth-toggle').style.display = closed ? 'none' : '';
  if (closed && S.authMode === 'register') { toggleAuthMode(); return; } // snap back; toggle re-syncs
  $('auth-invite-wrap').style.display = (S.authMode === 'register' && S.registrationMode === 'invite') ? '' : 'none';
}
function showApp() {
  $('page-login').style.display = 'none';
  $('app-shell').style.display = 'flex';
  navigate('dashboard');
  startBackgroundJobPoll();
}

/* ══════════════════ shared job cache ══════════════════
   Every /api/jobs consumer reads from this single cache instead of
   independently fetching. The always-on background poll is the only
   periodic writer; pages that need fresher data after a user action
   (queue job, cancel, etc.) call getJobs({force:true}) for a one-shot
   refresh. */
let _jobCache = { data: null, ts: 0, pending: null };
const _JOB_CACHE_TTL = 15000;  // 15s: longer than background poll's 8s interval with margin for jitter

async function getJobs(opts = {}) {
  const now = Date.now();
  if (!opts.force && _jobCache.data && (now - _jobCache.ts) < _JOB_CACHE_TTL) {
    return _jobCache.data;
  }
  if (_jobCache.pending) return _jobCache.pending;
  _jobCache.pending = api('/api/jobs?limit=50').then(data => {
    _jobCache.data = data;
    _jobCache.ts = Date.now();
    _jobCache.pending = null;
    return data;
  }).catch(e => {
    _jobCache.pending = null;
    throw e;
  });
  return _jobCache.pending;
}

// Watches every LLM job regardless of which page is open, so a summary/
// correction/etc. job that finishes failing while the user has navigated
// away still gets a toast (the page-scoped pollers in loadQueue/
// scheduleDetailPoll only run while their own page is showing).
let bgJobPollTimer = null;
let bgJobPollFirstTick = true;
const bgJobStatusSeen = new Map();

async function pollBackgroundJobs() {
  try {
    const data = await getJobs();
    for (const j of (data.jobs || [])) {
      // will_retry is only set on LLM job entries (services/llm_jobs.py); it's
      // undefined on transcription queue entries, so they never match here —
      // transcription failures are surfaced elsewhere, out of scope for #58.
      // rediarize/voice_match are LLM jobs but not in AUTO_RETRY_KINDS, so
      // will_retry is always false for them — a single failed attempt is
      // already terminal, and toasting it is correct (no other passive
      // surface exists for those two kinds).
      const terminalFailure = j.status === 'failed' && j.will_retry === false;
      const wasTerminalFailure = bgJobStatusSeen.get(j.id) === 'terminalFailed';
      // The very first tick after login/registration just establishes a
      // silent baseline, so pre-existing failures don't all toast at once.
      // A job created after that baseline (not just one already open on the
      // Queue/Detail page) can still fail-and-exhaust-retries between two
      // ticks, so this can't be "was this job seen before" per job id — a
      // job whose whole pending->failed->retry->terminal lifecycle finishes
      // inside one poll gap must still toast on the tick that first sees it
      // terminal. A rerun that fails again re-toasts because its status left
      // 'terminalFailed' in between.
      if (!bgJobPollFirstTick && terminalFailure && !wasTerminalFailure) {
        toast(humanizeJobError(j.error), 'error');
      }
      bgJobStatusSeen.set(j.id, terminalFailure ? 'terminalFailed' : j.status);
    }
    bgJobPollFirstTick = false;
  } catch { /* transient fetch failure — just retry next tick */ }
  bgJobPollTimer = setTimeout(pollBackgroundJobs, 8000);
}

function startBackgroundJobPoll() {
  clearTimeout(bgJobPollTimer);
  bgJobStatusSeen.clear();
  bgJobPollFirstTick = true;
  pollBackgroundJobs();
}

function stopBackgroundJobPoll() {
  clearTimeout(bgJobPollTimer);
  bgJobPollTimer = null;
}

// BACKGROUND: checkAuth is the first call in the boot sequence. It used to
// chain refreshCsrfToken + GET /api/me, but /api/bootstrap returns csrf_token,
// user, status, recent_transcripts, and jobs in ONE round-trip (issue #143).
// The response is cached as `bootData` so loadDashboard can consume it without
// a second fetch.  Raw fetch() is deliberate: bootstrap is a public GET that
// must not go through the api() helper (api() adds CSRF headers and retries on
// 403 — irrelevant for a GET, and it triggers setInFlight).
async function checkAuth() {
  try {
    const res = await fetch('/api/bootstrap', { credentials: 'same-origin' });
    if (!res.ok) { await res.text(); showLogin(); return; }
    const body = await res.json();
    bootData = body;
    csrfToken = body.csrf_token || null;
    S.user = body.user ? (body.user.username || null) : null;
    S.isAdmin = !!(body.user && body.user.is_admin);
    S.registrationMode = body.registration_mode || 'open';
    S.exportDir = (body.settings && body.settings.export_directory) || '';
    if (S.user) $('rail-operator').textContent = 'Operator: ' + S.user + (S.isAdmin ? ' (admin)' : '');
    if (body.user) { showApp(); } else { showLogin(); }
  } catch {
    showLogin();
  }
}


/* ── client-side password mirror ──
   Reads S.passwordMinLength from the <meta name="wd-password-min-length">
   tag injected by the server at render time, so the client pre-check and
   hint text always match the server's PASSWORD_MIN_LENGTH env var. */
function passwordHintText() {
  return 'Min ' + S.passwordMinLength + ' chars, at least one letter and one number.';
}

function clientValidatePassword(pw) {
  if (pw.length < S.passwordMinLength) { return { ok: false, reason: 'Password must be at least ' + S.passwordMinLength + ' characters' }; }
  if (!/[a-zA-Z]/.test(pw)) { return { ok: false, reason: 'Password must contain at least one letter' }; }
  if (!/[0-9]/.test(pw)) { return { ok: false, reason: 'Password must contain at least one digit' }; }
  return { ok: true, reason: '' };
}

function toggleAuthMode() {
  S.authMode = S.authMode === 'login' ? 'register' : 'login';
  $('auth-title').textContent = S.authMode === 'login' ? 'Operator sign-in' : 'Register operator';
  $('auth-submit').textContent = S.authMode === 'login' ? 'Power on' : 'Register';
  $('auth-toggle').textContent = S.authMode === 'login' ? 'No account? Register' : 'Have an account? Sign in';
  // "Find username" only makes sense on the login side; reset code flows work either way.
  $('auth-forgot-username').style.display = S.authMode === 'login' ? '' : 'none';
  $('auth-pass-confirm-wrap').style.display = S.authMode === 'register' ? '' : 'none';
  $('auth-req-hint').style.display = S.authMode === 'register' ? '' : 'none';
  $('auth-req-hint').textContent = S.authMode === 'register' ? passwordHintText() : '';
  $('auth-pass').autocomplete = S.authMode === 'register' ? 'new-password' : 'current-password';
  if (S.authMode === 'login') { $('auth-pass-confirm').value = ''; $('auth-invite').value = ''; }
  syncRegistrationChrome();
}


/* ── account recovery flows ── */

async function showForgotUsername() {
  try {
    const data = await api('/api/forgot-username', { method: 'POST' });
    const usernames = data.usernames || [];
    if (!usernames.length) {
      openModal(`
        <h2 class="modal-title">Registered operators</h2>
        <div style="font-size:13px;color:var(--label-dim);margin-bottom:16px">No operators are registered yet.</div>
        <div style="display:flex;justify-content:flex-end">
          <button id="fu-close" class="btn btn--ghost btn--sm">Close</button>
        </div>`);
      $('fu-close').addEventListener('click', closeModal);
      return;
    }
    openModal(`
      <h2 class="modal-title">Registered operators</h2>
      <div style="font-size:12.5px;color:var(--label-dim);margin-bottom:14px">${usernames.length} operator${usernames.length !== 1 ? 's' : ''} on this system:</div>
      <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:18px">
        ${usernames.map(u => '<div style="font-family:var(--f-mono);font-size:14px;background:var(--input);border:1px solid var(--input-edge);padding:10px 14px;border-radius:2px">' + escapeHtml(u) + '</div>').join('')}
      </div>
      <div style="display:flex;justify-content:flex-end">
        <button id="fu-close" class="btn btn--ghost btn--sm">Close</button>
      </div>`);
    $('fu-close').addEventListener('click', closeModal);
  } catch (e) { toast(e.message, 'error'); }
}

/* ── admin tool: generate password reset token (Service Panel action) ── */
async function showGenerateResetCode() {
  openModal(`
    <h2 class="modal-title">Generate reset code</h2>
    <div style="font-size:13px;color:var(--label-dim);margin-bottom:14px">Enter the username that needs a new password.</div>
    <input class="inp" id="fp-username" type="text" placeholder="username" style="font-size:13px;padding:8px 10px;width:100%;margin-bottom:16px">
    <div id="fp-token-result" style="display:none;margin-bottom:14px;padding:12px;border:1px solid var(--nixie);border-radius:2px;background:var(--nixie-bg)">
      <div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;color:var(--label-dim);margin-bottom:6px">Reset code (valid 1 hour)</div>
      <div id="fp-token-text" style="font-family:var(--f-mono);font-size:12px;color:var(--nixie);word-break:break-all;text-shadow:0 0 4px rgba(255,138,61,0.4)"></div>
      <div style="font-size:10px;color:var(--label-dim);margin-top:8px">Give this code to the operator — they enter it on the login screen.</div>
    </div>
    <div class="modal-actions">
      <button id="fp-close" class="btn btn--ghost btn--sm">Close</button>
      <button id="fp-generate" class="btn btn--amber btn--sm">Generate</button>
    </div>`);
  $('fp-close').addEventListener('click', closeModal);
  const doGenerate = async () => {
    const username = $('fp-username').value.trim();
    if (!username) { toast('Enter a username', 'error'); return; }
    try {
      const r = await api('/api/forgot-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username }) });
      $('fp-token-result').style.display = 'block';
      $('fp-token-text').textContent = r.reset_token;
    } catch (e) { toast(e.message, 'error'); }
  };
  const guardedGenerate = () => withBusy($('fp-generate'), doGenerate);
  $('fp-generate').addEventListener('click', guardedGenerate);
  $('fp-username').addEventListener('keydown', (e) => { if (e.key === 'Enter') guardedGenerate(); });
}

/* ── admin tool: mint a registration invite (Service Panel action, issue #395) ── */
async function showGenerateInviteCode() {
  openModal(`
    <h2 class="modal-title">Generate invite code</h2>
    <div style="font-size:13px;color:var(--label-dim);margin-bottom:14px">Mints a single-use registration invite. Share it out-of-band with the person you're inviting.</div>
    <div id="iv-token-result" style="display:none;margin-bottom:14px;padding:12px;border:1px solid var(--nixie);border-radius:2px;background:var(--nixie-bg)">
      <div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;color:var(--label-dim);margin-bottom:6px">Invite code (valid 72 hours, single use)</div>
      <div id="iv-token-text" style="font-family:var(--f-mono);font-size:12px;color:var(--nixie);word-break:break-all;text-shadow:0 0 4px rgba(255,138,61,0.4)"></div>
      <div style="font-size:10px;color:var(--label-dim);margin-top:8px">They enter it in the "Invite code" field when registering.</div>
    </div>
    <div class="modal-actions">
      <button id="iv-close" class="btn btn--ghost btn--sm">Close</button>
      <button id="iv-generate" class="btn btn--amber btn--sm">Generate</button>
    </div>`);
  $('iv-close').addEventListener('click', closeModal);
  const doGenerate = async () => {
    try {
      const r = await api('/api/admin/invites', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      $('iv-token-result').style.display = 'block';
      $('iv-token-text').textContent = r.invite_token;
    } catch (e) { toast(e.message, 'error'); }
  };
  $('iv-generate').addEventListener('click', () => withBusy($('iv-generate'), doGenerate));
}

async function showResetCode() {
  openModal(`
    <h2 class="modal-title">Reset your password</h2>
    <div style="font-size:13px;color:var(--label-dim);margin-bottom:14px">Enter the reset code and your new password.</div>
    <div class="field" style="gap:6px;margin-bottom:12px">
      <label class="t-label" style="font-size:12px">Reset code</label>
      <input class="inp" id="rc-token" type="text" placeholder="Paste the code here" style="font-size:13px;padding:8px 10px;width:100%;font-family:var(--f-mono)">
    </div>
    <div class="field" style="gap:6px;margin-bottom:18px">
      <label class="t-label" style="font-size:12px">New password</label>
      <input class="inp" id="rc-password" type="password" placeholder="Choose a new password" style="font-size:13px;padding:8px 10px;width:100%">
    </div>
    <div style="font-size:11px;color:var(--label-dim);margin-bottom:10px">${passwordHintText()}</div>
    <div class="modal-actions">
      <button id="rc-close" class="btn btn--ghost btn--sm">Cancel</button>
      <button id="rc-submit" class="btn btn--amber btn--sm">Reset password</button>
    </div>`);
  $('rc-close').addEventListener('click', closeModal);
  const doReset = async () => {
    const token = $('rc-token').value.trim();
    const password = $('rc-password').value;
    if (!token || !password) { toast('Both fields are required', 'error'); return; }
    const cv = clientValidatePassword(password);
    if (!cv.ok) { toast(cv.reason, 'error'); return; }
    try {
      const r = await api('/api/reset-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, new_password: password }) });
      toast('Password reset — signed in as ' + r.username);
      closeModal();
      await refreshCsrfToken();
      await checkAuth();
    } catch (e) { toast(e.message, 'error'); }
  };
  const guardedReset = () => withBusy($('rc-submit'), doReset);
  $('rc-submit').addEventListener('click', guardedReset);
  $('rc-password').addEventListener('keydown', (e) => { if (e.key === 'Enter') guardedReset(); });
  $('rc-token').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('rc-password').focus(); });
}

async function submitAuth(ev) {
  ev.preventDefault();
  const username = $('auth-user').value.trim();
  const password = $('auth-pass').value;
  if (!username || !password) { toast('Operator and password required', 'error'); return; }
  const payload = { username, password };
  if (S.authMode === 'register') {
    const confirm = $('auth-pass-confirm').value;
    if (password !== confirm) { toast('Passwords do not match', 'error'); return; }
    const cv = clientValidatePassword(password);
    if (!cv.ok) { toast(cv.reason, 'error'); return; }
    if (S.registrationMode === 'invite') {
      const invite = $('auth-invite').value.trim();
      if (!invite) { toast('An invite code is required to register', 'error'); return; }
      payload.invite_token = invite;
    }
  }
  return withBusy($('auth-submit'), async () => {
    try {
      await api('/api/' + S.authMode /* api-paths: /api/login /api/register */, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      $('auth-led').classList.add('ok');
      await refreshCsrfToken();
      await checkAuth();
    } catch (e) {
      toast(e.message, 'error');
    }
  }, { spinner: true });
}

async function logout() {
  stopBackgroundJobPoll();
  try { await api('/api/logout', { method: 'POST' }); } catch { /* session may be gone */ }
  // Logout clears the whole session server-side, invalidating csrfToken too —
  // refresh it so a subsequent login in this page session isn't rejected.
  await refreshCsrfToken();
  showLogin();
}

/* ══════════════════ dashboard (Monitor) ══════════════════ */
function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

function transcriptMeta(t) {
  const sv = statusView(t);
  const parts = [];
  parts.push((t.provider || '—') + (t.model ? ' · ' + t.model : ''));
  if (t.duration_seconds != null) parts.push(formatDur(t.duration_seconds));
  if (sv.word === 'done' && t.speaker_count) parts.push(t.speaker_count + ' speakers');
  if (sv.word === 'running') parts.push('transcribing…');
  if (sv.word === 'working') parts.push('transcribing a ' + (t.duration_seconds ? Math.round(t.duration_seconds / 60) + '-min ' : '') + 'recording — running as one block');
  if (sv.word === 'waiting') parts.push('rate-limited — waiting');
  if (sv.word === 'queued') parts.push('awaiting turn');
  if (sv.word === 'failed' && t.error) parts.push(t.error);
  if (sv.word === 'done' || sv.word === 'failed') parts.push(timeAgo(t.created_at));
  return parts.join(' · ');
}

function updateRailStorage(totalMinutes) {
  const cap = 500;
  $('rail-storage-text').textContent = Math.round(totalMinutes) + ' / ' + cap + ' min';
  const lit = Math.min(11, Math.round(totalMinutes / cap * 11));
  $('rail-storage-leds').innerHTML = [...Array(11)].map((_, i) => i < lit
    ? '<span style="background:' + GREEN + ';box-shadow:0 0 3px ' + GREEN + '"></span>'
    : '<span></span>').join('');
}

// BACKGROUND: on the FIRST call after boot, status + recent_transcripts come
// from bootData (cached by checkAuth from /api/bootstrap) so first paint
// skips the extra round trip. bootData is consumed (nulled) right after, so
// every later visit to Monitor re-fetches live — otherwise stats/recents
// would stay frozen at boot-time values for the rest of the session.
async function loadDashboard() {
  const boot = bootData;
  bootData = null;
  const root = $('page-dashboard');
  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Monitor</h1>
      <div class="page-status page-status--ok">${ledDot(GREEN, true, 9)}${escapeHtml(greeting())}</div>
    </div>
    <div class="unit" id="dash-stats" style="display:grid;grid-template-columns:repeat(4,1fr);padding:8px 28px"></div>
    <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:20px 0 8px 36px">Recent signals</div>
    <div id="dash-recents"></div>`;
  try {
    const [st, recents] = boot
      ? [boot.status || {}, boot.recent_transcripts || []]
      : await Promise.all([api('/api/status'), api('/api/transcripts?limit=5')]);
    const stats = [
      { nix: String(Math.round(st.total_minutes ?? 0)), label: 'Minutes transcribed', glow: 'var(--nixie)' },
      { nix: String(st.total_transcripts ?? 0).padStart(2, '0'), label: 'Transcripts', glow: 'var(--nixie)' },
      { nix: String(st.voice_profiles ?? 0).padStart(2, '0'), label: 'Voice profiles', glow: 'var(--nixie)' },
      st.diarization_available
        ? { nix: 'ML', label: 'Diarization ready', glow: GREEN }
        : { nix: '--', label: 'Diarization basic', glow: 'var(--nixie)' },
    ];
    $('dash-stats').innerHTML = stats.map(s => `
      <div style="padding:12px 8px;display:flex;flex-direction:column;align-items:center;gap:9px;border-right:1px solid rgba(0,0,0,0.22)">
        ${nixie(s.nix, '', s.glow === 'var(--nixie)' ? null : s.glow)}
        <div class="t-cap" style="text-align:center">${escapeHtml(s.label)}</div>
      </div>`).join('');
    updateRailStorage(st.total_minutes ?? 0);
    $('nav-badge-transcripts').textContent = String(st.total_transcripts ?? 0).padStart(2, '0');
    $('nav-badge-voices').textContent = String(st.voice_profiles ?? 0).padStart(2, '0');
    const vnBadge2 = $('nav-badge-voicenotes');
    if (vnBadge2) vnBadge2.textContent = String(st.voice_notes ?? 0).padStart(2, '0');
    refreshQueueBadge();

    $('dash-recents').innerHTML = (recents && recents.length) ? recents.map(t => {
      const sv = statusView(t);
      return `
      <button class="unit" data-open="${t.id}" style="display:grid;grid-template-columns:1fr 170px 100px;align-items:center;gap:16px;padding:11px 30px">
        <span style="min-width:0">
          <span style="display:block;font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(t.title || t.filename || 'Untitled')}</span>
          <span style="display:block;font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-top:2px">${escapeHtml(transcriptMeta(t))}</span>
        </span>
        ${bargraph(sv.cells)}
        <span class="status-badge status-badge--${escapeHtml(sv.word)}" data-word="${escapeHtml(sv.word)}">${escapeHtml(sv.word)}</span>
      </button>`;
    }).join('') : '<div class="empty-unit">No signals yet — load a tape on the Transcribe deck</div>';
    $('dash-recents').querySelectorAll('[data-open]').forEach(b =>
      b.addEventListener('click', () => navigate('detail', Number(b.dataset.open))));
  } catch (e) {
    toast(e.message, 'error');
  }
  loadDashboardJobs(boot && boot.jobs);
  scheduleDashPoll();
}

// Real job `kind` values (services/llm_jobs.py VALID_KINDS + the transcription
// queue entry's own 'transcription') mapped to the dashboard's pipeline lights.
// There is no distinct 'diarize' kind \u2014 initial diarization runs inline inside
// the transcription entry, so it has no light of its own here (only the rare,
// user-triggered 'rediarize' is separate, and isn't shown on this strip).
const DASH_STAGE_KINDS = [
  { light: 'transcribe', kind: 'transcription' },
  { light: 'correct', kind: 'correction' },
  { light: 'summarize', kind: 'summary' },
  { light: 'voicematch', kind: 'voice_match' },
];

// BACKGROUND: on first call (from loadDashboard), initialJobs comes from
// bootData.jobs (cached /api/bootstrap) — no fetch needed.  On subsequent
// poll ticks (scheduleDashPoll), initialJobs is undefined, so the existing
// /api/jobs fetch runs normally.
async function loadDashboardJobs(initialJobs) {
  var data;
  var jobs;
  if (initialJobs) {
    jobs = initialJobs.jobs || [];
  } else {
    try { data = await getJobs(); } catch { return; }
    jobs = data && data.jobs || [];
  }
  var active = jobs.filter(function(j) { return j.status === 'running' || j.status === 'pending'; });
  INST.dashActive = active.length > 0;
  var wrap = $('dash-activity');
  if (!wrap) {
    var stats = $('dash-stats');
    if (!stats) return;
    wrap = document.createElement('div');
    wrap.id = 'dash-activity';
    wrap.className = 'dash-activity unit';
    wrap.innerHTML =
      '<div class="dash-activity-head"></div>'
      + '<div class="dash-activity-body">'
      + '<div class="dash-ticker-wrap"><span class="dash-ticker-text"></span></div>'
      + '<div id="dash-vu" class="dash-vu-wrap"></div>'
      + '</div>'
      + '<div class="dash-stage-lights">'
      + '<span class="dash-stage-label">Pipeline:</span>'
      + DASH_STAGE_KINDS.map(function(s) {
          return '<span class="dash-stage-light" data-stage="' + s.light + '"></span>'
            + '<span class="dash-stage-label">' + s.light + '</span>';
        }).join('')
      + '</div>';
    stats.insertAdjacentElement('afterend', wrap);
  }
  wrap.classList.toggle('dash-activity--active', INST.dashActive);
  var head = wrap.querySelector('.dash-activity-head');
  head.innerHTML = INST.dashActive
    ? '<span class="led-dot led-dot--on" style="--led-color:var(--amber);box-shadow:0 0 5px var(--amber)"></span>Live'
    : '<span class="led-dot"></span>Standby';
  var ticker = wrap.querySelector('.dash-ticker-text');
  if (INST.dashActive) {
    var title = active[0].title || 'Untitled';
    ticker.textContent = (escapeHtml(title) + '  \u00B7  ').repeat(4).trimEnd();
    ticker.classList.toggle('scroll', motionAllowed());
  } else {
    ticker.textContent = 'No active jobs';
    ticker.classList.remove('scroll');
  }
  var activeKinds = {};
  active.forEach(function(j) { activeKinds[j.kind] = true; });
  wrap.querySelectorAll('.dash-stage-light').forEach(function(lt) {
    var entry = DASH_STAGE_KINDS.find(function(s) { return s.light === lt.dataset.stage; });
    lt.classList.toggle('active', !!(entry && activeKinds[entry.kind]));
  });
  if (!INST.scopeInit) dashInitVu();
}

function dashInitVu() {
  if (INST.scopeInit) return;
  INST.scopeInit = true;
  INST.driveMic = 0.6;
  var canvas = document.createElement('canvas');
  canvas.width = 96;
  canvas.height = 48;
  var wrap = $('dash-vu');
  if (wrap) wrap.appendChild(canvas);
  function frame(ts) {
    if (S.page !== 'dashboard') { INST.dashRaf = null; INST.scopeInit = false; return; }
    INST.dt = ts / 1000;
    drawVU(canvas, 'dash');
    INST.dashRaf = requestAnimationFrame(frame);
  }
  INST.dashRaf = requestAnimationFrame(frame);
}

function scheduleDashPoll() {
  clearTimeout(dashPollTimer);
  dashPollTimer = setTimeout(async function() {
    if (S.page !== 'dashboard') return;
    await loadDashboardJobs();
    scheduleDashPoll();
  }, 3000);
}
/* ══════════════════ transcribe: instruments (verbatim from prototype logic) ══════════════════ */
const INST = { dt: 0, raf: null, dashRaf: null, vuMeters: {}, scopeInit: false, driveMic: null, driveSys: null, dashActive: false };

function instrumentsActive() { return S.running || S.capturing; }

// 'dash' has no real signal (background jobs carry no audio) — it's driven by
// whether any job is active per the last /api/jobs poll, purely decorative.
function vuActive(key) { return key === 'dash' ? INST.dashActive : instrumentsActive(); }

function drawVU(canvas, key) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const m = INST.vuMeters[key] || (INST.vuMeters[key] = { v: 0, target: 0, next: 0 });
  const active = vuActive(key);
  if (key === 'dash' && !motionAllowed()) {
    // decorative jitter is motion; the active/idle level itself is state, so it
    // still shows, just as a still needle rather than a wandering one.
    m.target = m.v = active ? 0.5 : 0.03;
  } else {
    // during live capture the drive is the real analyser level for this channel
    const override = key === 'mic' ? INST.driveMic : key === 'sys' ? INST.driveSys : null;
    const drive = active ? (override ?? 0.75) : 0.03;
    if (INST.dt > m.next) {
      m.target = Math.min(1, drive * (0.3 + Math.random() * 0.7) + (Math.random() < 0.1 ? 0.2 * drive : 0));
      m.next = INST.dt + 0.12 + Math.random() * 0.32;
    }
    m.v += (m.target - m.v) * (m.target > m.v ? 0.3 : 0.06);
  }

  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, '#F3E9C9');
  g.addColorStop(1, '#E0D2A8');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  const lamp = ctx.createRadialGradient(w / 2, h * 0.15, 8, w / 2, h * 0.15, w * 0.7);
  lamp.addColorStop(0, active ? 'rgba(255,196,110,0.26)' : 'rgba(255,196,110,0.10)');
  lamp.addColorStop(1, 'rgba(255,196,110,0)');
  ctx.fillStyle = lamp;
  ctx.fillRect(0, 0, w, h);

  const cx = w / 2, cy = h * 1.28;
  const rA = Math.min(h * 0.98, (w / 2 - 8 - h * 0.1) / 0.75 - h * 0.135);
  const start = -Math.PI * 0.27, end = Math.PI * 0.27;

  ctx.strokeStyle = '#2A241A';
  ctx.lineWidth = Math.max(1, h * 0.012);
  ctx.beginPath();
  ctx.arc(cx, cy, rA, start - Math.PI / 2, end - Math.PI / 2);
  ctx.stroke();

  const redStart = start + (end - start) * 0.76;
  ctx.strokeStyle = '#C03A2E';
  ctx.lineWidth = Math.max(2, h * 0.028);
  ctx.beginPath();
  ctx.arc(cx, cy, rA + h * 0.022, redStart - Math.PI / 2, end - Math.PI / 2);
  ctx.stroke();

  const labels = [['-20', 0], ['-10', 0.22], ['-7', 0.34], ['-5', 0.45], ['-3', 0.57], ['-1', 0.68], ['0', 0.76], ['+1', 0.85], ['+3', 1]];
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.font = 'bold ' + Math.round(h * 0.082) + 'px Barlow, sans-serif';
  labels.forEach((pair) => {
    const f = pair[1];
    const ca = start + (end - start) * f - Math.PI / 2;
    const col = f >= 0.76 ? '#C03A2E' : '#2A241A';
    ctx.strokeStyle = col;
    ctx.lineWidth = Math.max(1, h * 0.013);
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(ca) * (rA - h * 0.05), cy + Math.sin(ca) * (rA - h * 0.05));
    ctx.lineTo(cx + Math.cos(ca) * (rA + h * 0.05), cy + Math.sin(ca) * (rA + h * 0.05));
    ctx.stroke();
    ctx.fillStyle = col;
    const lw2 = ctx.measureText(pair[0]).width / 2;
    const lx = Math.max(lw2 + 2, Math.min(w - lw2 - 2, cx + Math.cos(ca) * (rA + h * 0.135)));
    ctx.fillText(pair[0], lx, cy + Math.sin(ca) * (rA + h * 0.135));
  });

  ctx.fillStyle = '#2A241A';
  ctx.font = 'bold ' + Math.round(h * 0.15) + 'px Barlow, sans-serif';
  ctx.fillText('VU', cx, h * 0.66);

  const na = start + (end - start) * m.v - Math.PI / 2;
  ctx.strokeStyle = '#1A150D';
  ctx.lineWidth = Math.max(1.5, h * 0.018);
  ctx.shadowColor = 'rgba(0,0,0,0.35)';
  ctx.shadowBlur = 3;
  ctx.beginPath();
  ctx.moveTo(cx + Math.cos(na) * h * 0.14, cy + Math.sin(na) * h * 0.14);
  ctx.lineTo(cx + Math.cos(na) * (rA + h * 0.035), cy + Math.sin(na) * (rA + h * 0.035));
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = 'rgba(0,0,0,0.28)';
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, w - 2, h - 2);
}

function drawScope(canvas) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  if (!INST.scopeInit) {
    ctx.fillStyle = '#03140B';
    ctx.fillRect(0, 0, w, h);
    INST.scopeInit = true;
  }
  ctx.fillStyle = 'rgba(3,20,11,0.20)';
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = 'rgba(70,255,158,0.07)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, h / 2);
  ctx.lineTo(w, h / 2);
  ctx.moveTo(w / 2, 0);
  ctx.lineTo(w / 2, h);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(w / 2, h / 2, w * 0.33, 0, Math.PI * 2);
  ctx.stroke();

  const t = INST.dt;
  const act = instrumentsActive() ? 1 : 0.1;
  const phosphor = S.phosphor;
  const traces = [
    { f1: 2.1, f2: 5.3, a: h * 0.17, sp: 1.6, off: 0 },
    { f1: 3.2, f2: 7.9, a: h * 0.11, sp: -2.2, off: h * 0.07 },
    { f1: 1.3, f2: 11.0, a: h * 0.07, sp: 3.1, off: -h * 0.09 },
  ];
  ctx.lineWidth = 1.4;
  ctx.shadowColor = phosphor;
  ctx.shadowBlur = 6;
  ctx.strokeStyle = phosphor;
  traces.forEach((tr) => {
    ctx.beginPath();
    for (let x = 0; x <= w; x += 2) {
      const p = (x / w) * Math.PI * 2;
      const y = h / 2 + (tr.off + Math.sin(p * tr.f1 + t * tr.sp) * tr.a + Math.sin(p * tr.f2 - t * tr.sp * 1.7) * tr.a * 0.45) * act;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  });
  ctx.shadowBlur = 0;
}

function startInstruments() {
  if (INST.raf) return;
  const loop = () => {
    if (S.page !== 'transcribe') { INST.raf = null; return; }
    INST.dt += 0.016;
    if (S.capturing) {
      INST.driveMic = analyserLevel(CAP.micAn);
      INST.driveSys = CAP.sysAn ? analyserLevel(CAP.sysAn) : 0;
    } else {
      INST.driveMic = null;
      INST.driveSys = null;
    }
    const scope = $('inst-scope'), vm = $('inst-vu-mic'), vs = $('inst-vu-sys');
    if (scope) drawScope(scope);
    if (vm) drawVU(vm, 'mic');
    if (vs) drawVU(vs, 'sys');
    INST.raf = requestAnimationFrame(loop);
  };
  INST.raf = requestAnimationFrame(loop);
}

/* ══════════════════ transcribe screen ══════════════════ */
let providersLoadGen = 0; // generation counter to prevent race on rapid provider loads
async function ensureProviders() {
  if (S.providers.length) return;
  const gen = ++providersLoadGen;
  const provs = await api('/api/providers');
  if (gen !== providersLoadGen) return; // stale response
  // `configured` already reflects reality for both kinds: for local
  // providers the backend probed check_health (package actually
  // importable), for hosted providers it means a key is saved.
  S.providers = provs.map(p => ({
    id: p.id,
    name: p.name,
    ready: p.configured,
    needsKey: p.needs_key,
    statusText: p.name + (!p.needs_key
      ? (p.configured ? ' · local · ready' : ' · local · not installed')
      : (p.configured ? ' · key connected · ready' : ' · no key — see service panel')),
    models: [p.default_model].filter(Boolean),
    modelsFetched: false,
  }));
  // Default to the first ready provider (usually the zero-setup local one)
  // instead of always index 0, so a broken/uninstalled provider never
  // silently becomes the pre-selected default.
  const firstReady = S.providers.findIndex(p => p.ready);
  if (firstReady >= 0) S.providerIdx = firstReady;
  if (curProv().id === 'moonshine') S.langIdx = 0;
}

async function fetchModelsFor(idx) {
  const p = S.providers[idx];
  if (!p || p.modelsFetched) return;
  try {
    const r = await api('/api/providers/' + p.id + '/models');
    const models = (r.models || []).map(m => typeof m === 'string' ? m : (m.id || m.name)).filter(Boolean);
    if (models.length) p.models = models;
    p.modelsFetched = true;
  } catch { /* keep default model */ }
}

function curProv() { return S.providers[S.providerIdx] || { name: '—', models: ['—'], ready: false, statusText: '—' }; }
function curModel() { const p = curProv(); return p.models[S.modelIdx % p.models.length] || '—'; }

function deckKey(id, symbol, cap, state, title) {
  // state: 'active' | 'disabled' | 'inert' | {led:color}
  const inert = state === 'inert';
  const disabled = state === 'disabled';
  return `
  <div class="key-stack">
    <div class="led" id="${id}-led"></div>
    <button class="key${inert ? ' inert' : ''}" id="${id}" ${disabled ? 'disabled' : ''} title="${escapeHtml(title)}">${symbol}</button>
    <div class="cap">${cap}</div>
  </div>`;
}

function reelsSvg(idPrefix) {
  const reel = (r1, y1, y2) => `
    <svg viewBox="0 0 50 50" style="display:block;width:100%;height:100%">
      <circle cx="25" cy="25" r="${r1}" fill="none" stroke="#27292C" stroke-width="2"></circle>
      <g class="${idPrefix}-reel" style="transform-origin:25px 25px">
        <circle cx="25" cy="25" r="6" fill="#3D4045" stroke="#1E1F21"></circle>
        <line x1="25" y1="${y1}" x2="25" y2="${y2}" stroke="#1E1F21" stroke-width="2"></line>
        <line x1="${y1}" y1="25" x2="${y2}" y2="25" stroke="#1E1F21" stroke-width="2"></line>
      </g>
    </svg>`;
  return `
  <div class="deck-window">
    <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:space-between;padding:0 12px">
      <div style="width:50px;height:50px;flex-shrink:0">${reel(15, 11, 39)}</div>
      <div style="width:30px;height:1.5px;background:#4A4030"></div>
      <div style="width:50px;height:50px;flex-shrink:0">${reel(21, 8, 42)}</div>
    </div>
  </div>`;
}

async function renderTranscribe() {
  const root = $('page-transcribe');
  try { await ensureProviders(); } catch (e) { toast(e.message, 'error'); }
  await fetchModelsFor(S.providerIdx);
  const prov = curProv();

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Transcribe</h1>
      <div class="page-status" id="tx-prov-status"></div>
    </div>

    <!-- deck unit (4U) -->
    <div class="unit" style="padding:18px 20px 14px">
      <div style="display:grid;grid-template-columns:1fr auto 1fr;gap:18px;align-items:stretch;padding:0 14px">
        <div style="display:flex;flex-direction:column;gap:10px;min-width:0">
          <div class="t-unit" style="text-align:center">Deck A · Input</div>
          <div id="deck-a-window">
            <button class="deck-drop" id="deck-drop">
              <span style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);letter-spacing:0.06em">DROP AUDIO HERE — OR CLICK TO BROWSE</span>
              <span style="font-family:var(--f-mono);font-size:9px;color:var(--label-faint);letter-spacing:0.04em">MP3 · WAV · M4A · FLAC · OGG · MP4</span>
            </button>
          </div>
          <div id="deck-a-status" style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);text-align:center;padding:0 4px;min-height:26px;line-height:1.3">No media loaded</div>
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px;justify-items:center">
            ${deckKey('key-rew-a', '◀◀', 'Rew', 'inert', 'Rewind — no mapped action')}
            ${deckKey('key-play-a', '▶', 'Play', 'disabled', 'Load media first')}
            ${deckKey('key-ff-a', '▶▶', 'FF', 'inert', 'Fast-forward — no mapped action')}
            ${deckKey('key-rec', '●', 'Rec', 'active', 'Live capture — asks before recording')}
            ${deckKey('key-eject', '⏏', 'Eject', 'active', 'Eject / swap file')}
          </div>
        </div>
        <div style="width:1px;background:var(--edge);align-self:stretch;margin:4px 0"></div>
        <div style="display:flex;flex-direction:column;gap:10px;min-width:0">
          <div class="t-unit" style="text-align:center">Deck B · Output</div>
          <div id="deck-b-window">${reelsSvg('deckb')}</div>
          <div id="deck-b-status" style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);text-align:center;padding:0 4px;min-height:26px;line-height:1.3">Idle — output writes here</div>
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px;justify-items:center">
            ${deckKey('key-rew-b', '◀◀', 'Rew', 'inert', 'Rewind — no mapped action')}
            ${deckKey('key-play-b', '▶', 'Play', 'disabled', 'Preview voice sample — available in Voice roster')}
            ${deckKey('key-ff-b', '▶▶', 'FF', 'inert', 'Fast-forward — no mapped action')}
            <div style="visibility:hidden"><button tabindex="-1" aria-hidden="true" class="key"></button></div>
            ${deckKey('key-open-done', '⏹', 'View', 'active', 'View finished transcript')}
          </div>
        </div>
      </div>
    </div>

    <!-- display bridge -->
    <div class="unit" style="display:flex;align-items:center;gap:16px;padding:12px 40px">
      <div style="display:flex;flex-direction:column;align-items:center;gap:4px">
        <div style="width:106px;height:106px;background:#0A0C0A;border-radius:9px;border:2px solid var(--edge);box-shadow:inset 0 0 16px rgba(0,0,0,0.9);display:flex;align-items:center;justify-content:center">
          <div style="width:92px;height:92px;border-radius:50%;overflow:hidden;background:#03140B;box-shadow:inset 0 0 18px rgba(0,0,0,0.85)">
            <canvas id="inst-scope" width="92" height="92" style="display:block;width:92px;height:92px"></canvas>
          </div>
        </div>
        <div class="t-cap-sm">Input scope</div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:center;gap:4px;flex:1;min-width:0">
        <div style="width:100%;max-width:250px;height:104px;border-radius:5px;border:2px solid var(--edge);overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,0.5)">
          <canvas id="inst-vu-mic" width="250" height="104" style="display:block;width:100%;height:104px"></canvas>
        </div>
        <div class="t-cap-sm">Mic · L</div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:center;gap:4px;flex:1;min-width:0">
        <div style="width:100%;max-width:250px;height:104px;border-radius:5px;border:2px solid var(--edge);overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,0.5)">
          <canvas id="inst-vu-sys" width="250" height="104" style="display:block;width:100%;height:104px"></canvas>
        </div>
        <div class="t-cap-sm">System · R</div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:center;gap:10px">
        <span class="vfd--vert" id="inst-monitor">STANDBY</span>
        <div style="display:flex;flex-direction:column;align-items:center;gap:4px" title="Lights only when mic (L) and system (R) are both present">
          <span class="led-dot" id="inst-stereo-lamp"></span>
          <span style="font-family:var(--f-mono);font-size:7.5px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim)">Stereo</span>
        </div>
      </div>
    </div>

    <!-- meter row (running only) -->
    <div class="unit" id="tx-meter" style="display:none">
      <div style="display:flex;align-items:center;gap:22px;padding:14px 26px;flex-wrap:wrap">
        <div id="tx-meter-leds" class="bargraph" style="height:16px;flex:1;min-width:180px"></div>
        <span id="tx-meter-nix"></span>
        <span id="tx-meter-elapsed"></span>
        <div style="display:flex;gap:18px" id="tx-stages"></div>
        <button class="btn btn--red" id="tx-cancel">✕ Cancel — resumable later</button>
      </div>
    </div>

    <!-- signal path -->
    <div class="unit">
      <div style="display:flex;flex-direction:column;gap:10px;padding:16px 20px 18px">
        <div style="display:flex;align-items:center;justify-content:space-between">
          <div class="t-unit">Signal path</div>
          <div id="tx-path-note" style="font-family:var(--f-mono);font-size:10px;color:var(--label-faint);text-transform:uppercase;letter-spacing:0.06em">Applies to the next job</div>
        </div>
        <div class="mfd-wrap">
          <div class="mfd-handle"></div>
          <div class="mfd-bezel">
            <div class="mfd-btncol-wrap"><div class="mfd-btncol" id="mfd-leftcol"></div></div>
            <div class="mfd-seam"></div>
            <div class="mfd-screen" id="mfd-screenwrap">
              <div class="mfd-rows" id="mfd-screen"></div>
              <div class="mfd-band" id="mfd-band"></div>
            </div>
            <div class="mfd-seam"></div>
            <div class="mfd-navcol">
              <div class="mfd-navbtn mfd-chevron mfd-chevron-up" id="mfd-btn-up"></div>
              <div class="mfd-navbtn mfd-ok" id="mfd-btn-ok"></div>
              <div class="mfd-navbtn mfd-chevron mfd-chevron-down" id="mfd-btn-down"></div>
            </div>
          </div>
          <div class="mfd-handle"></div>
        </div>
        <div class="mfd-hint" id="mfd-hint"></div>
      </div>
    </div>

    <!-- cost estimate -->
    <div class="unit" id="tx-cost-estimate" style="display:none;border-radius:3px;padding:10px 34px;font-family:var(--f-mono);font-size:11px;color:var(--body);gap:10px;align-items:center;border-top:3px solid var(--panel-lo)"></div>

    <!-- status strip (arm state + done navigation) -->
    <div class="unit" style="border-radius:3px">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 22px;gap:16px">
        <div id="tx-arm-text" style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.08em"></div>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="led-dot" id="tx-status-led"></div>
          <button class="key key--wide" id="tx-view-results" style="display:none">☰ View transcript</button>
        </div>
      </div>
    </div>`;

  wireTranscribe();
  syncTranscribe();
  startInstruments();
}

function wireTranscribe() {
  wireTranscribeDrop();
  $('key-eject').addEventListener('click', () => {
    if (S.running) { toast('Job in progress — cancel first', 'info'); return; }
    if (S.capturing) { toast('Recording — press ● to stop first', 'info'); return; }
    if (S.tapeLoaded) ejectTape(); else $('file-input').click();
  });
  $('key-play-a').addEventListener('click', startJob);
  $('key-rec').addEventListener('click', () => {
    if (S.permPending) return;
    if (S.capturing) stopLiveCapture();
    else if (!S.running) openRecModal();
  });
  const openDone = () => { if (S.doneId) navigate('detail', S.doneId); };
  $('key-open-done').addEventListener('click', openDone);
  $('tx-view-results').addEventListener('click', openDone);
  $('tx-cancel').addEventListener('click', (e) => withBusy(e.currentTarget, cancelJob));
  wireMfd();
}

function setVfd(id, text) {
  const w = $(id);
  if (!w) return;
  w.firstElementChild.textContent = text;
  armVfdMarquees(w.parentElement);
}

/* ══════════════════ Signal Path VFD panel ══════════════════ */
let mfdEditing = null;      // category key in wheel-edit mode, or null = browse
let mfdAdvanced = false;    // Advanced (Fine Adjust) screen open
let mfdAdvIdx = 0;          // highlighted row within Advanced (0-4)
let mfdAdvSelected = false; // editing the highlighted Advanced field
let mfdFlashKey = null;     // category key currently flashing green (binary toggle confirm)
let mfdBusy = false;        // guards provider switch while awaiting fetchModelsFor

const MFD_SPEAKER_OPTS = ['Auto-detect'].concat([...Array(12)].map((_, i) => String(i + 1)));
const MFD_CREATIVITY_OPTS = ['0 · Strict', '1', '2', '3', '4', '5 · Balanced', '6', '7', '8', '9', '10 · Creative'];

function mfdSingleSpeaker() { return S.mode === 'dictation' || S.mode === 'voice_note' || S.mode === 'voice_dump'; }

function mfdCatDefs() {
  const prov = curProv();
  const single = mfdSingleSpeaker();
  return [
    { key: 'provider', label: 'Provider', desc: 'Cloud or local transcription engine.',
      opts: S.providers.map(p => p.name), idx: S.providerIdx, meta: prov.statusText },
    { key: 'model', label: 'Model', desc: 'Specific model variant for the selected provider.',
      opts: prov.models, idx: S.modelIdx % prov.models.length },
    { key: 'language', label: 'Language', desc: 'Spoken language of the audio.',
      opts: LANGUAGES, idx: S.langIdx,
      locked: prov.id === 'moonshine', lockedMsg: 'Moonshine is English-only — switch provider to change language' },
    { key: 'mode', label: 'Mode', desc: 'Meeting: multi-speaker minutes. Dictation: voice notes, no speakers, offers to reformat after. Voice Dump: audit / stream-of-consciousness dump, split into separate notes you review afterwards. Auto: let the app decide.',
      opts: ['Auto', 'Meeting', 'Dictation', 'Voice Note', 'Voice Dump'],
      idx: S.mode === 'meeting' ? 1 : S.mode === 'dictation' ? 2 : S.mode === 'voice_note' ? 3 : S.mode === 'voice_dump' ? 4 : 0 },
    { key: 'speakers', label: 'Speakers', desc: 'Identify who spoke when (diarization).',
      opts: ['On', 'Off'], idx: (single || !S.diarize) ? 1 : 0, binary: true,
      locked: single, displayOverride: single ? 'N/A' : null },
    { key: 'autocorrect', label: 'Auto-Correct', desc: 'Run the LLM correction pass automatically after transcription.',
      opts: ['On', 'Off'], idx: S.autoCorrect ? 0 : 1, binary: true },
  ];
}

function mfdAdvFieldDefs() {
  return [
    { key: 'speakerCount', label: 'Speaker count', type: 'wheel', opts: MFD_SPEAKER_OPTS,
      idx: S.advSpeakerCount == null ? 0 : S.advSpeakerCount,
      setIdx: (i) => { S.advSpeakerCount = i === 0 ? null : i; } },
    { key: 'title', label: 'Meeting title', type: 'text', val: S.advTitle, placeholder: '(none set)',
      setVal: (v) => { S.advTitle = v; } },
    { key: 'creativity', label: 'Creativity', type: 'wheel', opts: MFD_CREATIVITY_OPTS,
      idx: S.advTemperature, setIdx: (i) => { S.advTemperature = i; } },
    { key: 'context', label: 'Context', type: 'text', val: S.advContext, placeholder: '(none pasted)', multiline: true,
      setVal: (v) => { S.advContext = v; } },
    { key: 'back', label: '◄ BACK TO BROWSE', type: 'action' },
  ];
}

function mfdOnCatClick(key) {
  if (S.running || mfdAdvanced) return;
  if (mfdEditing && key !== mfdEditing) return;
  const c = mfdCatDefs().find(c => c.key === key);
  if (!c) return;
  if (c.locked) { if (c.lockedMsg) toast(c.lockedMsg, 'info'); return; }
  if (c.binary) {
    if (key === 'speakers') S.diarize = !S.diarize;
    else if (key === 'autocorrect') S.autoCorrect = !S.autoCorrect;
    mfdEditing = null;
    mfdFlashKey = key;
    syncTranscribe();
    setTimeout(() => { mfdFlashKey = null; renderMfd(); }, 500);
    return;
  }
  mfdEditing = (mfdEditing === key) ? null : key;
  renderMfd();
}

async function mfdChangeProvider(dir) {
  if (mfdBusy) return;
  mfdBusy = true;
  const n = S.providers.length;
  S.providerIdx = ((S.providerIdx + dir) % n + n) % n;
  S.modelIdx = 0;
  // Moonshine only ever decodes as English (backend hardcodes it) — lock
  // the language wheel so picking e.g. Spanish here doesn't silently
  // produce English-decoded garbage with no error.
  if (curProv().id === 'moonshine') S.langIdx = 0;
  await fetchModelsFor(S.providerIdx);
  mfdBusy = false;
  syncTranscribe();
}

function mfdNav(dir) {
  if (S.running) return;
  if (mfdAdvanced) {
    if (mfdAdvSelected) {
      const f = mfdAdvFieldDefs()[mfdAdvIdx];
      if (f.type === 'wheel') {
        const n = f.opts.length;
        f.setIdx(((f.idx + dir) % n + n) % n);
        syncTranscribe();
      }
      return;
    }
    const len = mfdAdvFieldDefs().length;
    mfdAdvIdx = ((mfdAdvIdx + dir) % len + len) % len;
    renderMfd();
    return;
  }
  if (!mfdEditing) return;
  const c = mfdCatDefs().find(c => c.key === mfdEditing);
  if (!c || c.binary) return;
  if (c.key === 'provider') { mfdChangeProvider(dir); return; }
  const n = c.opts.length;
  const newIdx = ((c.idx + dir) % n + n) % n;
  if (c.key === 'model') S.modelIdx = newIdx;
  else if (c.key === 'language') S.langIdx = newIdx;
  else if (c.key === 'mode') S.mode = ['auto', 'meeting', 'dictation', 'voice_note', 'voice_dump'][newIdx];
  syncTranscribe();
}

function mfdConfirmAdvInput() {
  const f = mfdAdvFieldDefs()[mfdAdvIdx];
  const el = $('mfd-input');
  if (el && f.setVal) f.setVal(el.value);
  mfdAdvSelected = false;
  syncTranscribe();
}

function mfdOnOk() {
  if (S.running) return;
  if (mfdAdvanced) {
    if (mfdAdvSelected) { mfdConfirmAdvInput(); return; }
    const f = mfdAdvFieldDefs()[mfdAdvIdx];
    if (f.type === 'action') { mfdAdvanced = false; mfdAdvIdx = 0; renderMfd(); return; }
    mfdAdvSelected = true;
    renderMfd();
    return;
  }
  if (mfdEditing) { mfdEditing = null; renderMfd(); return; }
  mfdAdvanced = true; mfdAdvIdx = 0; mfdAdvSelected = false;
  renderMfd();
}

function wireMfd() {
  $('mfd-btn-up').addEventListener('click', () => mfdNav(-1));
  $('mfd-btn-down').addEventListener('click', () => mfdNav(1));
  $('mfd-btn-ok').addEventListener('click', mfdOnOk);
}

function fitMfdMarquee() {
  document.querySelectorAll('#mfd-screenwrap .mfd-valwrap').forEach(wrap => {
    const span = wrap.querySelector('span');
    if (!span) return;
    span.classList.remove('mfd-scroll');
    span.style.removeProperty('--mfd-marquee-dist');
    span.style.removeProperty('--mfd-marquee-dur');
    if (!motionAllowed()) return;
    const over = span.scrollWidth - wrap.clientWidth;
    if (over > 4) {
      span.style.setProperty('--mfd-marquee-dist', '-' + over + 'px');
      span.style.setProperty('--mfd-marquee-dur', Math.max(3, over / 18) + 's');
      span.classList.add('mfd-scroll');
    }
  });
}

function renderMfdButtons() {
  const cats = mfdCatDefs();
  $('mfd-leftcol').innerHTML = cats.map(c => {
    let cls = 'mfd-btn';
    if (!mfdAdvanced && mfdEditing === c.key) cls += ' on';
    else if (mfdEditing || mfdAdvanced || S.running) cls += ' dim';
    if (mfdFlashKey === c.key) cls += ' flash';
    return '<div class="' + cls + '" data-cat="' + c.key + '" title="' + escapeHtml(c.desc) + '"></div>';
  }).join('');
  document.querySelectorAll('#mfd-leftcol .mfd-btn').forEach(b => {
    b.addEventListener('click', () => mfdOnCatClick(b.dataset.cat));
  });
}

function renderMfdAdvancedScreen(band) {
  const fields = mfdAdvFieldDefs();
  const f = fields[mfdAdvIdx];

  if (mfdAdvSelected && f.type === 'text') {
    const existing = $('mfd-input');
    if (existing && existing.dataset.key === f.key) return; // already focused/editing — don't rebuild under the user
  }

  if (mfdAdvSelected && f.type === 'wheel') {
    const n = f.opts.length;
    const prev = f.opts[(f.idx - 1 + n) % n], cur = f.opts[f.idx], next = f.opts[(f.idx + 1) % n];
    $('mfd-screen').innerHTML =
      '<div class="mfd-wheel" style="grid-row:1/-1">' +
      '<div class="mfd-label" style="margin-bottom:10px">' + escapeHtml(f.label) + '</div>' +
      '<span class="mfd-ghost">' + escapeHtml(prev) + '</span><span class="mfd-value" style="color:var(--green);text-shadow:0 0 5px rgba(95,203,122,0.7)">' + escapeHtml(cur) + '</span><span class="mfd-ghost">' + escapeHtml(next) + '</span>' +
      '</div>';
    band.innerHTML = '<div class="mfd-desc">Up/Down to adjust. OK to confirm.</div>';
    return;
  }

  if (mfdAdvSelected && f.type === 'text') {
    const tag = f.multiline ? 'textarea' : 'input';
    const extra = f.multiline ? ' rows="3"' : ' type="text"';
    $('mfd-screen').innerHTML =
      '<div class="mfd-wheel" style="grid-row:1/-1;align-items:stretch;padding:0 4px">' +
      '<div class="mfd-label" style="margin-bottom:10px">' + escapeHtml(f.label) + '</div>' +
      '<' + tag + ' class="mfd-input" id="mfd-input" data-key="' + f.key + '"' + extra + '></' + tag + '>' +
      '</div>';
    const el = $('mfd-input');
    el.value = f.val || '';
    if (!f.multiline) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); mfdConfirmAdvInput(); } });
    band.innerHTML = '<div class="mfd-desc">Type or paste. ' + (f.multiline ? 'OK to confirm.' : 'Enter or OK to confirm.') + '</div>';
    setTimeout(() => { el.focus(); const v = el.value; el.value = ''; el.value = v; }, 0);
    return;
  }

  $('mfd-screen').innerHTML = fields.map((fld, i) => {
    const active = i === mfdAdvIdx ? ' active' : '';
    if (fld.type === 'action') {
      return '<div class="mfd-row mfd-adv-row mfd-adv-back' + active + '"><span class="mfd-label">' + escapeHtml(fld.label) + '</span></div>';
    }
    const display = fld.type === 'wheel' ? fld.opts[fld.idx] : (fld.val ? fld.val.replace(/\n+/g, ' ') : fld.placeholder);
    return '<div class="mfd-row mfd-adv-row' + active + '"><div class="mfd-tick"></div><span class="mfd-label" style="width:130px;flex-shrink:0">' + escapeHtml(fld.label) + '</span>' +
           '<div class="mfd-valwrap"><span class="mfd-value">' + escapeHtml(display) + '</span></div></div>';
  }).join('');
  band.innerHTML = '<div class="mfd-desc">Fine Adjust — per-job overrides. Up/Down to choose a field, OK to select.</div>';
  fitMfdMarquee();
}

function renderMfdScreen() {
  const band = $('mfd-band');
  if (mfdAdvanced) { renderMfdAdvancedScreen(band); return; }

  if (mfdEditing) {
    const c = mfdCatDefs().find(c => c.key === mfdEditing);
    const n = c.opts.length;
    const prev = c.opts[(c.idx - 1 + n) % n], cur = c.opts[c.idx], next = c.opts[(c.idx + 1) % n];
    $('mfd-screen').innerHTML =
      '<div class="mfd-wheel" style="grid-row:1/-1">' +
      '<div class="mfd-label" style="margin-bottom:10px">' + escapeHtml(c.label) + '</div>' +
      '<span class="mfd-ghost">' + escapeHtml(prev) + '</span><span class="mfd-value">' + escapeHtml(cur) + '</span><span class="mfd-ghost">' + escapeHtml(next) + '</span>' +
      '</div>';
    band.innerHTML = '<div class="mfd-desc">' + escapeHtml(c.desc) + '</div>' + (c.meta ? '<div class="mfd-meta">' + escapeHtml(c.meta) + '</div>' : '');
    fitMfdMarquee();
    return;
  }

  $('mfd-screen').innerHTML = mfdCatDefs().map(c => {
    const val = c.displayOverride != null ? c.displayOverride : c.opts[c.idx];
    const flashCls = mfdFlashKey === c.key ? ' flash' : '';
    return '<div class="mfd-row"><div class="mfd-tick" style="opacity:' + (c.locked ? '0.2' : '0.4') + '"></div><span class="mfd-label" style="width:96px;flex-shrink:0">' + escapeHtml(c.label) + '</span>' +
           '<div class="mfd-valwrap"><span class="mfd-value' + flashCls + '">' + escapeHtml(val) + '</span></div></div>';
  }).join('');
  band.innerHTML = '<div class="mfd-desc">Press a category to adjust it, or OK for Fine Adjust.</div>';
  fitMfdMarquee();
}

function renderMfdNav() {
  const ok = $('mfd-btn-ok');
  ok.className = 'mfd-navbtn mfd-ok' + (mfdEditing || mfdAdvanced ? ' on' : '');
}

function renderMfdHint() {
  const h = $('mfd-hint');
  if (!h) return;
  if (mfdAdvanced && mfdAdvSelected) {
    const f = mfdAdvFieldDefs()[mfdAdvIdx];
    h.textContent = f.type === 'wheel' ? 'Up/Down: adjust ' + f.label + ' · OK: confirm.' : 'Type or paste into ' + f.label + ' · OK: confirm.';
  } else if (mfdAdvanced) {
    h.textContent = 'Fine Adjust — Up/Down: choose field · OK: select · Back row exits to browse.';
  } else if (mfdEditing) {
    const c = mfdCatDefs().find(c => c.key === mfdEditing);
    h.textContent = 'Editing ' + c.label + ' — Up/Down to roll, OK to confirm.';
  } else {
    h.textContent = 'Browse mode — pick a category on the left, or press OK for Fine Adjust.';
  }
}

function renderMfd() {
  if (S.page !== 'transcribe' || !$('mfd-leftcol')) return;
  renderMfdButtons();
  renderMfdScreen();
  renderMfdNav();
  renderMfdHint();
}

function stageLeds() {
  // Honest stage mapping: upload done once the POST returned; transcribe while
  // chunks are moving; diarize/finalize once every chunk is done but the
  // transcript is still processing (backend merges + diarizes then).
  const st = S.stage;
  const defs = [
    { label: 'Initialize', done: st !== 'upload', on: st === 'upload' },
    { label: 'Transcribe', done: st === 'diarize' || st === 'finalize', on: st === 'transcribe' },
    { label: 'Diarize', done: st === 'finalize', on: st === 'diarize' },
    { label: 'Finalize', done: false, on: st === 'finalize' },
  ];
  // Non-blocking 5th stage: only appears once a run's auto-correct pass is
  // actually being tracked (see pollCorrectionStatus) — never a dead slot.
  if (S.correctionPending || S.correctionStatus) {
    defs.push({
      label: 'Correct',
      done: S.correctionStatus === 'completed',
      on: S.correctionPending && S.correctionStatus !== 'completed',
      fault: S.correctionStatus === 'failed',
    });
  }
  return defs.map(d => `
    <div style="display:flex;flex-direction:column;align-items:center;gap:4px">
      ${ledDot(d.fault ? RED : d.done ? GREEN : d.on ? AMBER : null, d.fault || d.done || d.on, 7)}
      <div style="font-family:var(--f-mono);font-size:9px;text-transform:uppercase;color:var(--label-dim)">${d.label}</div>
    </div>`).join('');
}

function syncTranscribe() {
  if (S.page !== 'transcribe' || !$('tx-prov-status')) return;
  const prov = curProv();
  const canStart = S.tapeLoaded && !S.running && prov.ready;

  // header provider status
  const psColor = prov.ready ? GREEN : AMBER;
  $('tx-prov-status').style.color = psColor;
  $('tx-prov-status').innerHTML = ledDot(psColor, true, 9) + escapeHtml(prov.statusText);

  // deck A window: drop zone vs reels
  const winA = $('deck-a-window');
  const wantReels = S.tapeLoaded || S.capturing;
  if (wantReels && !winA.querySelector('.deck-window')) winA.innerHTML = reelsSvg('decka');
  if (!wantReels && !winA.querySelector('.deck-drop')) {
    winA.innerHTML = `
      <button class="deck-drop" id="deck-drop">
        <span style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);letter-spacing:0.06em">DROP AUDIO HERE — OR CLICK TO BROWSE</span>
        <span style="font-family:var(--f-mono);font-size:9px;color:var(--label-faint);letter-spacing:0.04em">MP3 · WAV · M4A · FLAC · OGG · MP4</span>
      </button>`;
    wireTranscribeDrop();
  }
  const spin = motionAllowed() && (S.running || S.capturing);
  document.querySelectorAll('.decka-reel').forEach(g => g.style.animation = spin ? 'reel-spin 2.4s linear infinite' : 'none');
  const spinB = motionAllowed() && S.running && S.stage === 'finalize';
  document.querySelectorAll('.deckb-reel').forEach(g => g.style.animation = spinB ? 'reel-spin 2.4s linear infinite' : 'none');

  // deck statuses
  const dA = $('deck-a-status');
  if (S.capturing) {
    dA.textContent = '● REC — mic' + (S.stereoLive ? ' (L) + system (R)' : '') + ' — ' + formatTime((Date.now() - (S.captureStartedAt || Date.now())) / 1000);
    dA.style.color = RED;
  } else if (S.running) {
    dA.textContent = S.indeterminate
      ? 'Reading: ' + S.tapeName + ' — ' + formatTime((Date.now() - S.jobStartedAt) / 1000) + ' elapsed'
      : 'Reading (' + S.pct + '%): ' + S.tapeName;
    dA.style.color = AMBER;
  } else if (S.tapeLoaded) {
    const mb = S.tapeFile ? ' · ' + fmtBytes(S.tapeFile.size) : '';
    dA.textContent = S.tapeName + mb + ' · loaded';
    dA.style.color = AMBER;
  } else {
    dA.textContent = 'No media loaded';
    dA.style.color = 'var(--label-dim)';
  }
  const dB = $('deck-b-status');
  if (S.running) {
    dB.textContent = S.stage === 'diarize' || S.stage === 'finalize' ? 'Writing output — diarizing' : 'Standing by — transcription in progress';
    dB.style.color = S.stage === 'diarize' || S.stage === 'finalize' ? AMBER : 'var(--label-dim)';
  } else if (S.jobDone) {
    dB.textContent = 'Transcript written — press ⏹ to view';
    dB.style.color = GREEN;
  } else {
    dB.textContent = 'Idle — output writes here';
    dB.style.color = 'var(--label-dim)';
  }

  // play key + status LED
  const playKey = $('key-play-a');
  playKey.disabled = !canStart;
  playKey.title = canStart ? 'Start transcription' : S.running ? 'Job running' : !S.tapeLoaded ? 'Load media first' : 'Provider needs a key — see service panel';
  const ledColor = (S.running || canStart) ? GREEN : null;
  const statusLed = $('tx-status-led');
  statusLed.style.background = ledColor || 'var(--edge)';
  statusLed.style.boxShadow = ledColor ? '0 0 5px ' + GREEN : 'none';
  const recLed = $('key-rec-led');
  if (S.permPending) {
    recLed.style.background = AMBER;
    recLed.style.boxShadow = '0 0 8px ' + AMBER;
    recLed.classList.add('pulse-amber');
  } else {
    recLed.classList.remove('pulse-amber');
    recLed.style.background = S.capturing ? RED : 'var(--edge)';
    recLed.style.boxShadow = S.capturing ? '0 0 5px ' + RED : 'none';
  }
  $('key-rec').title = S.permPending ? 'Waiting for microphone permission…' : S.capturing ? 'Stop recording' : 'Live capture — asks before recording';

  // status strip
  const armText = $('tx-arm-text');
  const viewBtn = $('tx-view-results');
  if (S.jobDone && S.doneId) {
    armText.textContent = 'Transcription complete — ' + formatDur(S.doneDuration);
    viewBtn.style.display = '';
  } else {
    viewBtn.style.display = 'none';
    armText.textContent = S.running
      ? 'Job in progress — settings locked'
      : S.tapeLoaded
        ? 'Armed — ' + prov.name + ' · ' + curModel() + ' · ' + LANGUAGES[S.langIdx]
        : 'Load a tape to arm the transport';
  }

  // meter row — stays visible past the run's own completion while a
  // non-blocking auto-correct poll is still watching this transcript (2.2).
  $('tx-meter').style.display = (S.running || S.correctionPending) ? '' : 'none';
  if (S.running) {
    if (S.indeterminate) {
      // no chunk data — one amber "working" cell + elapsed clock, never a fake %
      $('tx-meter-leds').innerHTML = [...Array(11)].map((_, i) => i === 0
        ? '<span style="background:' + AMBER + ';box-shadow:0 0 4px ' + AMBER + '"></span>'
        : '<span></span>').join('');
      $('tx-meter-nix').outerHTML = '<span id="tx-meter-nix">' + nixie(formatTime((Date.now() - S.jobStartedAt) / 1000)) + '</span>';
      $('tx-meter-elapsed').outerHTML = '<span id="tx-meter-elapsed"></span>';
    } else {
      const lit = Math.round(S.pct / 100 * 11);
      $('tx-meter-leds').innerHTML = [...Array(11)].map((_, i) => i < lit
        ? '<span style="background:' + AMBER + ';box-shadow:0 0 4px ' + AMBER + '"></span>'
        : '<span></span>').join('');
      $('tx-meter-nix').outerHTML = '<span id="tx-meter-nix">' + nixie(S.pct + '%') + '</span>';
      // secondary readout alongside percent — dim so it doesn't compete with it
      $('tx-meter-elapsed').outerHTML = '<span id="tx-meter-elapsed">' + nixie(formatTime((Date.now() - S.jobStartedAt) / 1000), 'dim') + '</span>';
    }
    $('tx-stages').innerHTML = stageLeds();
    // Cancel is only real once the backend knows the transcript (chunked
    // runs). A sync run in flight has nothing cancellable — say so.
    const cancelBtn = $('tx-cancel');
    cancelBtn.style.display = '';
    cancelBtn.disabled = !S.runningId;
    cancelBtn.title = S.runningId ? '' : "Quick local jobs can't be cancelled — this finishes on its own";
  } else if (S.correctionPending) {
    // trailing state: leds/nixies stay frozen at their last (100%) values;
    // only the Correct dot is still live. Nothing here is cancellable.
    $('tx-stages').innerHTML = stageLeds();
    $('tx-cancel').style.display = 'none';
  }

  // signal path
  $('tx-path-note').textContent = S.running ? 'Locked while running' : 'Applies to the next job';
  renderMfd();

  // instruments monitor + nav badge
  $('inst-monitor').textContent = S.permPending ? 'AWAITING MIC' : instrumentsActive() ? 'LIVE' : 'STANDBY';
  const lamp = $('inst-stereo-lamp');
  lamp.style.background = S.stereoLive ? GREEN : 'var(--edge)';
  lamp.style.boxShadow = S.stereoLive ? '0 0 6px ' + GREEN : 'none';
  $('nav-badge-transcribe').textContent = S.running || S.capturing ? 'REC' : '';
  updateCostEstimate();
}

async function updateCostEstimate() {
  // Show a live STT cost estimate on the Transcribe page when provider/model
  // are selected. Uses POST /api/costs/estimate when duration is known
  // (live capture or running job), otherwise shows per-minute rates.
  var box = $('tx-cost-estimate');
  if (!box || S.page !== 'transcribe') return;
  var prov = curProv();
  var model = curModel();
  if (!prov.id || prov.id === 'builtin' || prov.id === 'moonshine') {
    // Local providers are free — show a one-line note.
    box.style.display = '';
    box.innerHTML = '<span>Local transcription — no cost</span>';
    return;
  }
  if (!prov.ready || model === '—') {
    box.style.display = 'none';
    return;
  }
  var dur = 0;
  var durKnown = false;
  if (S.capturing) {
    dur = (Date.now() - (S.captureStartedAt || Date.now())) / 1000;
    durKnown = true;
  } else if (S.running && S.jobStartedAt) {
    dur = (Date.now() - S.jobStartedAt) / 1000;
    durKnown = true;
  }
  var est;
  try {
    var body = JSON.stringify({ provider: prov.id, model: model, duration_seconds: durKnown ? Math.max(dur, 1) : 0 });
    est = await api('/api/costs/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body });
  } catch (_) {
    box.style.display = '';
    box.innerHTML = '<span style="color:var(--amber)">Cost estimate unavailable</span>';
    return;
  }
  box.style.display = '';
  var label;
  if (durKnown && dur > 0) {
    label = 'Est. cost: ~$' + est.cost.toFixed(2) +
      ' (' + escapeHtml(est.rate_source) + ' · ~' + formatDur(dur) + ')';
  } else {
    label = escapeHtml(prov.name) + ' rates: $' + est.rate_per_minute.toFixed(4) + '/min' +
      ' (' + escapeHtml(est.rate_source) + ')';
  }
  box.innerHTML = '<span>' + label + '</span>';
}

function wireTranscribeDrop() {
  const drop = $('deck-drop');
  if (!drop) return;
  drop.addEventListener('click', () => $('file-input').click());
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('dragover'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('dragover');
    if (e.dataTransfer.files[0]) loadTape(e.dataTransfer.files[0]);
  });
}

function loadTape(file, isLiveStereo = false) {
  S.tapeFile = file;
  S.tapeName = file.name;
  S.tapeLoaded = true;
  S.tapeIsLiveStereo = isLiveStereo;
  S.jobDone = false;
  S.pct = 0;
  syncTranscribe();
}

function ejectTape() {
  S.tapeFile = null;
  S.tapeName = '';
  S.tapeLoaded = false;
  S.tapeIsLiveStereo = false;
  S.jobDone = false;
  S.pct = 0;
  syncTranscribe();
}

let txTicker = null;

// 1s heartbeat while a job runs: keeps the elapsed readout moving on runs
// with no chunk data, and flips Initialize→Transcribe on sync runs after
// model warm-up (no observable signal exists inside a single blocking call —
// 15s comfortably covers local model init).
function startTxTicker() {
  clearInterval(txTicker);
  txTicker = setInterval(() => {
    if (!S.running && !S.capturing) { clearInterval(txTicker); txTicker = null; return; }
    if (S.indeterminate && S.stage === 'upload' && Date.now() - S.jobStartedAt > 15000) {
      S.stage = 'transcribe';
    }
    if (S.page === 'transcribe') syncTranscribe();
  }, 1000);
}

async function startJob() {
  const prov = curProv();
  if (!S.tapeLoaded || S.running || !prov.ready || !S.tapeFile) return;
  const form = new FormData();
  form.append('file', S.tapeFile);
  if (S.tapeIsLiveStereo) form.append('capture_source', 'live_stereo');
  form.append('provider', prov.id);
  form.append('model', curModel());
  const lang = LANGUAGES[S.langIdx];
  form.append('language', lang === 'Auto-detect' ? 'auto' : lang.toLowerCase().slice(0, 2));
  form.append('temperature', String(S.advTemperature / 10));
  form.append('diarize', mfdSingleSpeaker() ? 'false' : (S.diarize ? 'true' : 'false'));
  form.append('auto_correct', S.autoCorrect ? 'true' : 'false');
  form.append('kind', S.mode);
  if (S.advSpeakerCount != null) form.append('num_speakers', String(S.advSpeakerCount));
  const title = S.advTitle.trim();
  if (title) form.append('title', title);
  const ctxDoc = S.advContext.trim();
  if (ctxDoc) form.append('context_doc', ctxDoc);

  S.running = true;
  S.jobDone = false;
  S.pct = 0;
  S.stage = 'upload';
  S.jobStartedAt = Date.now();
  S.indeterminate = false;
  stopCorrectionPoll();
  S.correctionPending = false;
  S.correctionStatus = null;
  startTxTicker();
  syncTranscribe();
  try {
    // Sync runs (short local files) block here until done; chunked runs
    // return as soon as jobs are queued. Stage stays at Initialize until
    // polling sees transcription actually moving.
    const initial = await api('/api/transcribe', { method: 'POST', body: form });
    S.runningId = initial.id;
    syncTranscribe();
    const finalData = await pollTranscript(initial.id);
    S.running = false;
    S.stage = null;
    S.runningId = null;
    S.indeterminate = false;
    // startJob() never navigates away on completion (the user stays on the
    // Transcribe deck to load the next tape), so this is the only chance to
    // refresh the rail badge/storage meter for a job watched in place.
    refreshRailChrome();
    // Non-blocking Correct indicator (2.2): the run itself already reported
    // done above — this only starts a side poll to light/clear a 5th stage
    // dot once the auto-correct pass (enqueued as part of the same pipeline
    // run) finishes. It never delays the toast or deck unlock.
    const cj = finalData.correction_job;
    if (S.autoCorrect && cj && !['completed', 'failed'].includes(cj.status)) {
      S.correctionPending = true;
      S.correctionStatus = cj.status;
      pollCorrectionStatus(finalData.id);
    } else if (cj) {
      S.correctionStatus = cj.status;
    }
    if (finalData.status === 'cancelled') {
      toast('Transcription cancelled — resume from the channel bank', 'info');
      S.pct = 0;
    } else if (finalData.status === 'partial') {
      toast('Partially complete — some sections failed; retry from the channel bank', 'error');
      S.jobDone = true;
      S.doneId = finalData.id;
      S.doneDuration = finalData.duration_seconds;
    } else {
      toast('Transcription complete');
      S.jobDone = true;
      S.doneId = finalData.id;
      S.doneDuration = finalData.duration_seconds;
      S.pct = 100;
    }
    S.tapeLoaded = false;
    S.tapeFile = null;
    S.tapeName = '';
    S.tapeIsLiveStereo = false;
    syncTranscribe();
  } catch (e) {
    S.running = false;
    S.stage = null;
    S.runningId = null;
    S.indeterminate = false;
    toast('Transcription failed: ' + e.message, 'error');
    syncTranscribe();
  }
}

async function pollTranscript(id) {
  while (true) {
    const data = await api('/api/transcripts/' + id);
    const qs = data.queue_status;
    if (qs && qs.chunks_total) {
      S.indeterminate = false;
      S.pct = Math.round(qs.chunks_done / qs.chunks_total * 100);
      if (qs.chunks_done >= qs.chunks_total) {
        S.stage = S.diarize ? 'diarize' : 'finalize';
      } else if (qs.state === 'transcribing' || qs.chunks_done > 0) {
        S.stage = 'transcribe';
      }
      // else: chunks exist but nothing has run yet — still Initialize
    } else if (data.status === 'processing') {
      // no chunk jobs on this run — nothing to derive a percent from
      S.indeterminate = true;
    }
    if (['completed', 'failed', 'partial', 'cancelled'].includes(data.status)) {
      if (data.status === 'failed') throw new Error(data.error || 'Transcription failed');
      return data;
    }
    if (S.page === 'transcribe') syncTranscribe();
    await new Promise(res => setTimeout(res, 2000));
  }
}

let correctionPollTimer = null;
let correctionPollGen = 0;

function stopCorrectionPoll() {
  clearTimeout(correctionPollTimer);
  correctionPollTimer = null;
  correctionPollGen++; // invalidates any in-flight tick from a prior run
}

// Page-scoped, non-blocking: watches a completed run's auto-correct job so the
// 5th "Correct" stage dot can light/clear without gating the run's own
// completion (see startJob's 2.2 comment for why this isn't inside pollTranscript).
async function pollCorrectionStatus(id) {
  const gen = ++correctionPollGen;
  async function tick() {
    if (gen !== correctionPollGen) return; // superseded by a newer run
    try {
      const data = await api('/api/transcripts/' + id);
      const cj = data.correction_job;
      S.correctionStatus = cj ? cj.status : null;
      if (!cj || ['completed', 'failed'].includes(cj.status)) {
        S.correctionPending = false;
        if (S.page === 'transcribe') syncTranscribe();
        return;
      }
    } catch { /* transient — retry next tick */ }
    if (gen !== correctionPollGen) return;
    if (S.page === 'transcribe') syncTranscribe();
    correctionPollTimer = setTimeout(tick, 2000);
  }
  tick();
}

async function cancelJob() {
  if (!S.runningId) {
    toast("Nothing to cancel yet — this run has no cancellable sections", 'info');
    return;
  }
  try {
    const r = await api('/api/transcripts/' + S.runningId + '/cancel', { method: 'POST' });
    if (r && r.cancelled === 0) {
      toast('Nothing left to cancel — the running section finishes, then the job stops', 'info');
    } else {
      toast('Cancelling — ' + (r.cancelled ?? 0) + ' pending sections stopped', 'info');
    }
  } catch (e) { toast(e.message, 'error'); }
}

/* Rec modal — consent copy is design-mandated, verbatim from the prototype. */
function openRecModal() {
  openModal(`
    <div style="display:flex;align-items:center;gap:9px;margin-bottom:12px">
      <span style="width:9px;height:9px;border-radius:50%;background:${RED};box-shadow:0 0 6px ${RED}"></span>
      <span style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em">Start a live capture?</span>
    </div>
    <p class="modal-body">This records your microphone (left channel) and system audio (right channel) until you press Stop. Nothing has been recorded yet.</p>
    <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-bottom:18px">The recording stays on this machine.</div>
    <div class="modal-actions">
      <button id="rec-notnow" class="btn btn--ghost btn--sm">Not now</button>
      <button id="rec-start" class="btn btn--amber btn--sm">● Start recording</button>
    </div>`);
  $('rec-notnow').addEventListener('click', closeModal);
  $('rec-start').addEventListener('click', () => {
    closeModal();
    startLiveCapture();
  });
}

/* ══════════════════ live capture ══════════════════
   Mic → left channel, system audio (display capture) → right channel.
   STEREO lamp lights only when both tracks are genuinely live.
   Recording stays client-side; it loads Deck A as a tape when stopped. */
const CAP = { rec: null, chunks: [], mic: null, disp: null, actx: null, micAn: null, sysAn: null, buf: null };

function analyserLevel(an) {
  if (!an) return 0;
  an.getByteTimeDomainData(CAP.buf);
  let sum = 0;
  for (let i = 0; i < CAP.buf.length; i++) {
    const d = (CAP.buf[i] - 128) / 128;
    sum += d * d;
  }
  // RMS scaled so normal speech drives the needle into the upper half
  return Math.min(1, Math.sqrt(sum / CAP.buf.length) * 4);
}

async function startLiveCapture() {
  if (S.capturing || S.running) return;
  S.permPending = true;
  syncTranscribe();
  toast('Requesting microphone…', 'info');
  let mic;
  try {
    mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    S.permPending = false;
    syncTranscribe();
  } catch {
    S.permPending = false;
    toast('Microphone permission denied — nothing was recorded', 'error');
    syncTranscribe();
    return;
  }
  let disp = null;
  if (navigator.mediaDevices.getDisplayMedia) {
    try {
      const d = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      if (d.getAudioTracks().length) {
        disp = d;
        d.getVideoTracks().forEach(t => t.stop()); // audio only — no video is kept
      } else {
        d.getTracks().forEach(t => t.stop());
        toast('No system audio in that share — recording mic only', 'info');
      }
    } catch {
      toast('System audio declined — recording mic only', 'info');
    }
  }

  // From here on the streams are live but not yet owned by CAP — any throw
  // (AudioContext, MediaRecorder) must stop their tracks or they leak.
  let actx = null, rec;
  try {
    actx = new AudioContext();
    const dest = actx.createMediaStreamDestination();
    const merger = actx.createChannelMerger(2);
    merger.connect(dest);
    CAP.buf = new Uint8Array(256);

    const micSrc = actx.createMediaStreamSource(mic);
    CAP.micAn = actx.createAnalyser();
    CAP.micAn.fftSize = 256;
    micSrc.connect(CAP.micAn);
    micSrc.connect(merger, 0, 0);

    CAP.sysAn = null;
    if (disp) {
      const sysSrc = actx.createMediaStreamSource(disp);
      CAP.sysAn = actx.createAnalyser();
      CAP.sysAn.fftSize = 256;
      sysSrc.connect(CAP.sysAn);
      sysSrc.connect(merger, 0, 1);
    }

    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    rec = new MediaRecorder(dest.stream, { mimeType: mime });
    CAP.chunks = [];
    rec.ondataavailable = (e) => { if (e.data.size) CAP.chunks.push(e.data); };
    rec.onstop = finishLiveCapture;
    rec.start(1000);
  } catch (e) {
    [mic, disp].forEach(s => s && s.getTracks().forEach(t => t.stop()));
    if (actx) actx.close();
    CAP.micAn = null;
    CAP.sysAn = null;
    S.permPending = false;
    toast('Could not start the recorder: ' + e.message, 'error');
    syncTranscribe();
    return;
  }

  CAP.rec = rec;
  CAP.mic = mic;
  CAP.disp = disp;
  CAP.actx = actx;
  S.capturing = true;
  S.captureStartedAt = Date.now();
  S.stereoLive = !!disp;
  startTxTicker();
  syncTranscribe();
  toast(disp ? 'Recording mic + system audio' : 'Recording mic only', 'info');
}

function stopLiveCapture() {
  if (!S.capturing || !CAP.rec) return;
  CAP.rec.stop(); // finishLiveCapture runs from onstop
}

function finishLiveCapture() {
  const wasStereo = !!CAP.disp; // capture before CAP.disp is nulled below
  const blob = new Blob(CAP.chunks, { type: 'audio/webm' });
  [CAP.mic, CAP.disp].forEach(s => s && s.getTracks().forEach(t => t.stop()));
  if (CAP.actx) CAP.actx.close();
  CAP.rec = null; CAP.mic = null; CAP.disp = null; CAP.actx = null;
  CAP.micAn = null; CAP.sysAn = null;
  S.capturing = false;
  S.stereoLive = false;
  INST.driveMic = null;
  INST.driveSys = null;
  const now = new Date();
  const stamp = String(now.getHours()).padStart(2, '0') + String(now.getMinutes()).padStart(2, '0');
  if (blob.size > 0) {
    loadTape(new File([blob], 'live_capture_' + stamp + '.webm', { type: 'audio/webm' }), wasStereo);
    toast('Capture loaded onto Deck A — press START to transcribe');
  } else {
    toast('Nothing was recorded', 'info');
    syncTranscribe();
  }
}
/* ══════════════════ channel bank ══════════════════ */
let dashPollTimer = null;
let bankPollTimer = null;
let bankListCache = [];

// Per-state expanded fields — only values the API actually provides.
function bankDetailFields(t, sv) {
  const pipeline = (t.provider || '—') + ' · ' + (t.model || '—');
  const qs = t.queue_status || {};
  const jp = t.job_progress || {};
  switch (sv.word) {
    case 'done':
      return [['Recorded', timeAgo(t.created_at)], ['Speakers', String(t.speaker_count || '—')], ['Pipeline', pipeline]];
    case 'running':
      return [['Step', 'transcribing · chunk ' + ((qs.chunks_done ?? 0) + 1) + ' of ' + (qs.chunks_total ?? '?')],
              ['Elapsed', formatDur((Date.now() - new Date(t.created_at + 'Z').getTime()) / 1000)],
              ['Pipeline', pipeline]];
    case 'waiting':
      return [['Step', 'rate-limited · resumes in ~' + (qs.resume_in_seconds ?? '?') + 's'],
              ['Chunks done', (qs.chunks_done ?? 0) + ' of ' + (qs.chunks_total ?? '?')],
              ['Pipeline', pipeline]];
    case 'queued':
      return [['Chunks done', (qs.chunks_done ?? 0) + ' of ' + (qs.chunks_total ?? '—')],
              ['Duration', formatDur(t.duration_seconds)], ['Pipeline', pipeline]];
    case 'failed':
      return [['Reason', t.error || 'unknown'], ['Duration', formatDur(t.duration_seconds)], ['Pipeline', pipeline]];
    case 'cancelled':
      return [['Progress at cancel', sv.pct + '%'], ['Duration', formatDur(t.duration_seconds)], ['Pipeline', pipeline]];
    case 'partial':
      return [['Failed sections', String(jp.failed ?? '?')], ['Duration', formatDur(t.duration_seconds)], ['Pipeline', pipeline]];
    default:
      return [['Status', sv.word], ['Duration', formatDur(t.duration_seconds)], ['Pipeline', pipeline]];
  }
}

// The per-note_type extras shown under a board card's body preview.
// Voice notes and voice-dump items both come out of the same
// _structure_from_text chain, so their `structured` payloads share one
// shape — one renderer for both board grids means they can't drift.
function noteStructuredBits(n) {
  let structuredBits = '';
  if (n.note_type === 'todo' && n.structured && Array.isArray(n.structured.items)) {
    structuredBits = n.structured.items.slice(0, 3).map(it =>
      '<div style="font-size:12px;color:var(--body);padding:2px 0;border-top:1px solid var(--seg-edge)">' +
        '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:' +
          (it.priority === 'high' ? 'var(--red)' : it.priority === 'low' ? 'var(--label-dim)' : 'var(--nixie)') +
          ';color:#04140C;text-transform:uppercase;letter-spacing:0.05em;font-family:var(--f-mono);margin-right:6px">' + escapeHtml(it.priority || 'med') + '</span>' +
        escapeHtml((it.text || '').slice(0, 80)) +
      '</div>'
    ).join('');
  } else if (n.note_type === 'reminder' && n.structured) {
    structuredBits =
      (n.structured.trigger ? '<div style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.06em">When</div><div style="font-size:12px">' + escapeHtml(n.structured.trigger) + '</div>' : '') +
      (n.structured.subject ? '<div style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.06em;margin-top:4px">What</div><div style="font-size:12px">' + escapeHtml(n.structured.subject) + '</div>' : '');
  } else if (n.note_type === 'idea' && n.structured && Array.isArray(n.structured.tags) && n.structured.tags.length) {
    structuredBits = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:6px">' + n.structured.tags.slice(0, 4).map(t =>
      '<span style="font-size:9px;padding:2px 7px;border-radius:9px;background:var(--panel-hi);color:var(--body);border:1px solid var(--inset-edge);text-transform:lowercase;letter-spacing:0.04em;font-family:var(--f-mono)">' + escapeHtml(t) + '</span>'
    ).join('') + '</div>';
  } else if (n.note_type === 'journal' && n.structured && Array.isArray(n.structured.themes) && n.structured.themes.length) {
    structuredBits = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:6px">' + n.structured.themes.slice(0, 4).map(t =>
      '<span style="font-size:9px;padding:2px 7px;border-radius:9px;background:var(--panel-hi);color:var(--body);border:1px solid var(--inset-edge);text-transform:lowercase;letter-spacing:0.04em;font-family:var(--f-mono)">' + escapeHtml(t) + '</span>'
    ).join('') + '</div>';
  }
  return structuredBits;
}

async function loadVoiceNotes() {
  // The board page: a card grid of recent voice notes, grouped by
  // note_type. Mirrors the loadTranscripts() render shape so the
  // chassis styling picks it up. Click a card to open the source
  // transcript (the user reads/edits the note on the detail Notes
  // tab, not here).
  const root = $('page-voicenotes');
  refreshRailChrome();
  let data;
  try { data = await api('/api/voice-notes'); } catch (e) { toast(e.message, 'error'); return; }
  const notes = data.voice_notes || [];
  if (!notes.length) {
    root.innerHTML =
      '<div class="page-head">' +
        '<h1 class="t-title">Voice notes</h1>' +
        '<div class="page-status page-status--ok">0 notes</div>' +
      '</div>' +
      '<div class="empty-unit">No voice notes yet. Record one in <a href="#" data-nav="transcribe" style="color:var(--nixie)">Transcribe</a> with the mode toggle set to VOICE NOTE.</div>';
    root.querySelector('[data-nav]')?.addEventListener('click', (e) => { e.preventDefault(); navigate('transcribe'); });
    return;
  }
  const cards = notes.map(n => {
    const typeColor = NOTE_TYPE_COLORS[n.note_type] || NOTE_TYPE_COLORS.general;
    const typeLabel = NOTE_TYPE_LABELS[n.note_type] || n.note_type;
    const preview = (n.body || '').slice(0, 220) + ((n.body || '').length > 220 ? '…' : '');
    const structuredBits = noteStructuredBits(n);
    return `
      <div class="unit voice-note-card" data-tid="${n.transcript_id}" style="padding:18px 22px;cursor:pointer">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${typeColor};box-shadow:0 0 5px ${typeColor}"></span>
          <span style="font-family:var(--f-cond);font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:0.1em;color:${typeColor}">${escapeHtml(typeLabel)}</span>
          <span style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);margin-left:auto">${timeAgo(n.created_at)}</span>
        </div>
        <h3 style="font-family:var(--f-cond);font-weight:700;font-size:16px;line-height:1.25;margin:0 0 6px">${escapeHtml(n.title || 'Voice note')}</h3>
        <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-bottom:10px">From "${escapeHtml(n.transcript_title || 'transcript')}" · ${formatDur(n.transcript_duration_seconds || 0)}</div>
        ${preview ? '<div style="font-size:12.5px;line-height:1.5;color:var(--body);white-space:pre-wrap;margin-bottom:8px">' + escapeHtml(preview) + '</div>' : ''}
        ${structuredBits ? '<div style="margin-top:8px">' + structuredBits + '</div>' : ''}
        <div style="display:flex;gap:6px;margin-top:12px;justify-content:flex-end">
          <button class="btn" data-vnact="discard" data-vnid="${n.id}" style="font-size:10px;padding:4px 10px">Discard</button>
          <button class="btn" data-vnact="open" data-tid="${n.transcript_id}" style="font-size:10px;padding:4px 10px">Open →</button>
        </div>
      </div>`;
  }).join('');
  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Voice notes</h1>
      <div class="page-status page-status--ok">${notes.length} note${notes.length !== 1 ? 's' : ''}</div>
    </div>
    <div class="voice-note-grid">${cards}</div>`;
  root.querySelectorAll('[data-vnact]').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation();
    if (b.dataset.vnact === 'open') navigate('detail', Number(b.dataset.tid));
    else if (b.dataset.vnact === 'discard') discardVoiceNote(Number(b.dataset.vnid));
  }));
  root.querySelectorAll('.voice-note-card').forEach(c => c.addEventListener('click', () => {
    navigate('detail', Number(c.dataset.tid));
  }));
}

async function discardVoiceNote(id) {
  if (!(await styledConfirm('Discard this voice note? The transcript stays.'))) return;
  try { await api('/api/voice-notes/' + id, { method: 'DELETE' }); toast('Voice note discarded'); loadVoiceNotes(); }
  catch (e) { toast(e.message, 'error'); }
}

async function loadVoiceDumpItems() {
  // The Dump notes board: finalized voice-dump items across every
  // transcript, most recent first. Same card grid and note_type
  // vocabulary as loadVoiceNotes() — the difference is the source (one
  // long stream-of-consciousness capture split into many items) and that
  // there is no per-item discard here: reviewing, editing and discarding
  // happen before finalize, on the transcript's own Dump Review tab.
  const root = $('page-dumpnotes');
  refreshRailChrome();
  let data;
  try { data = await api('/api/voice-dump-items'); } catch (e) { toast(e.message, 'error'); return; }
  const items = data.items || [];
  // Mark only the displayed items as seen so the badge reflects items
  // created after this visit. Scoped to loaded ids so items beyond the
  // pagination limit aren't silently hidden (issue #374).
  if (items.length) {
    api('/api/voice-dump-items/mark-seen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: items.map(i => i.id) }),
    }).then(() => refreshRailChrome()).catch(() => {});
  }
  if (!items.length) {
    root.innerHTML =
      '<div class="page-head">' +
        '<h1 class="t-title">Dump notes</h1>' +
        '<div class="page-status page-status--ok">0 notes</div>' +
      '</div>' +
      '<div class="empty-unit">No dump notes yet. Record one in <a href="#" data-nav="transcribe" style="color:var(--nixie)">Transcribe</a> with the mode toggle set to VOICE DUMP, then finalize the items it picks out.</div>';
    root.querySelector('[data-nav]')?.addEventListener('click', (e) => { e.preventDefault(); navigate('transcribe'); });
    return;
  }
  const cards = items.map(n => {
    const typeColor = NOTE_TYPE_COLORS[n.note_type] || NOTE_TYPE_COLORS.general;
    const typeLabel = NOTE_TYPE_LABELS[n.note_type] || n.note_type;
    const preview = (n.body || '').slice(0, 220) + ((n.body || '').length > 220 ? '…' : '');
    const structuredBits = noteStructuredBits(n);
    return `
      <div class="unit voice-note-card voice-dump-card" data-tid="${n.transcript_id}" style="padding:18px 22px;cursor:pointer">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${typeColor};box-shadow:0 0 5px ${typeColor}"></span>
          <span style="font-family:var(--f-cond);font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:0.1em;color:${typeColor}">${escapeHtml(typeLabel)}</span>
          <span style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);margin-left:auto">${timeAgo(n.created_at)}</span>
        </div>
        <h3 style="font-family:var(--f-cond);font-weight:700;font-size:16px;line-height:1.25;margin:0 0 6px">${escapeHtml(n.title || 'Dump note')}</h3>
        <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-bottom:10px">From "${escapeHtml(n.transcript_title || 'transcript')}" · ${formatDur(n.transcript_duration_seconds || 0)}</div>
        ${preview ? '<div style="font-size:12.5px;line-height:1.5;color:var(--body);white-space:pre-wrap;margin-bottom:8px">' + escapeHtml(preview) + '</div>' : ''}
        ${structuredBits ? '<div style="margin-top:8px">' + structuredBits + '</div>' : ''}
        <div style="display:flex;gap:6px;margin-top:12px;justify-content:flex-end">
          <button class="btn" data-vdact="open" data-tid="${n.transcript_id}" style="font-size:10px;padding:4px 10px">Open →</button>
        </div>
      </div>`;
  }).join('');
  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Dump notes</h1>
      <div class="page-status page-status--ok">${items.length} note${items.length !== 1 ? 's' : ''}</div>
    </div>
    <div class="voice-note-grid">${cards}</div>`;
  root.querySelectorAll('[data-vdact]').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation();
    if (b.dataset.vdact === 'open') navigate('detail', Number(b.dataset.tid));
  }));
  root.querySelectorAll('.voice-dump-card').forEach(c => c.addEventListener('click', () => {
    navigate('detail', Number(c.dataset.tid));
  }));
}

/* ══════════════════ bulk import page ══════════════════ */
const DEFAULT_BULK_DEFAULTS = {
  provider: 'moonshine', model: '', language: 'auto',
  diarize: false, auto_correct: true, kind: 'auto', num_speakers: null,
};

async function loadBulk() {
  if (!S.user) { showLogin(); return; }
  const root = $('page-bulk');
  root.innerHTML = '<div style="text-align:center;padding:60px"><div class="t-title">Loading Bulk Import…</div></div>';
  try {
    await ensureProviders();
    const settings = await api('/api/settings');
    S.bulkDefaults = settings.bulk_defaults || DEFAULT_BULK_DEFAULTS;
    const defaultProvId = S.bulkDefaults.provider || 'moonshine';
    const provIdx = S.providers.findIndex(p => p.id === defaultProvId);
    if (provIdx >= 0) {
      S.providerIdx = provIdx;
      await fetchModelsFor(provIdx);
    }
    renderBulk();
  } catch (e) {
    toast(e.message, 'error');
    root.innerHTML = '<div class="empty-unit">Failed to load Bulk Import page: ' + escapeHtml(e.message) + '</div>';
  }
}

function renderBulk() {
  const root = $('page-bulk');
  const prov = curProv();
  const n = S.bulkFiles.length;

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Bulk Import</h1>
      <div class="page-status page-status--ok">${n ? n + ' file' + (n !== 1 ? 's' : '') : 'No files loaded'}</div>
    </div>
    <div class="unit" style="padding:24px;margin-bottom:14px">
      <div id="bulk-drop" style="border:2px dashed var(--edge);border-radius:3px;padding:40px 20px;text-align:center;cursor:pointer;transition:border-color 0.2s">
        <div class="t-cap" style="font-size:11.5px;letter-spacing:0.08em;margin-bottom:4px">Drop audio files here — or click to browse</div>
        <div style="font-family:var(--f-mono);font-size:9px;color:var(--label-faint);letter-spacing:0.04em">MP3 · WAV · M4A · FLAC · OGG · MP4 · WEBM</div>
      </div>
      <input type="file" id="bulk-file-input" multiple accept=".mp3,.wav,.m4a,.flac,.ogg,.mp4,.webm" style="display:none">
    </div>
    ${n ? `
    <div class="unit" style="margin-bottom:14px">
      <div style="padding:18px 34px 14px;border-bottom:1px solid var(--edge)">
        <div class="t-unit" style="margin-bottom:12px">Global defaults</div>
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:end">
          <div class="field" style="min-width:140px">
            <label class="t-label">Provider</label>
            <select id="bulk-provider" class="inp">${(S.providers || []).map((p, i) =>
              '<option value="' + i + '" ' + (p.id === (S.bulkDefaults.provider || 'moonshine') ? 'selected' : '') + (p.ready ? '' : ' disabled') + '>' + escapeHtml(p.name) + (p.ready ? '' : ' (unavailable)') + '</option>'
            ).join('')}</select>
          </div>
          <div class="field" style="min-width:160px">
            <label class="t-label">Model</label>
            <select id="bulk-model" class="inp">${prov.models.map((m, i) =>
              '<option value="' + i + '" ' + (m === S.bulkDefaults.model ? 'selected' : '') + '>' + escapeHtml(m) + '</option>'
            ).join('')}</select>
          </div>
          <div class="field" style="min-width:110px">
            <label class="t-label">Language</label>
            <select id="bulk-language" class="inp">${LANGUAGES.map((l, i) =>
              '<option value="' + i + '" ' + (l === (S.bulkDefaults.language === 'auto' ? 'Auto-detect' : LANGUAGES.find(x => x.toLowerCase().slice(0, 2) === S.bulkDefaults.language) || 'Auto-detect') ? 'selected' : '') + '>' + escapeHtml(l) + '</option>'
            ).join('')}</select>
          </div>
          <div class="field" style="min-width:100px">
            <label class="t-label">Kind</label>
            <select id="bulk-kind" class="inp">
              <option value="auto" ${S.bulkDefaults.kind === 'auto' ? 'selected' : ''}>Auto</option>
              <option value="meeting" ${S.bulkDefaults.kind === 'meeting' ? 'selected' : ''}>Meeting</option>
              <option value="dictation" ${S.bulkDefaults.kind === 'dictation' ? 'selected' : ''}>Dictation</option>
            <option value="voice_note" ${S.bulkDefaults.kind === 'voice_note' ? 'selected' : ''}>Voice Note</option>
            <option value="voice_dump" ${S.bulkDefaults.kind === 'voice_dump' ? 'selected' : ''}>Audit / stream-of-consciousness dump</option>
          </select>
        </div>
        <div class="field">
            <label class="t-label">&nbsp;</label>
            <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
              <input type="checkbox" id="bulk-diarize" ${S.bulkDefaults.diarize ? 'checked' : ''}> Diarize
            </label>
          </div>
          <div class="field" id="bulk-speakers-field" style="min-width:80px;${S.bulkDefaults.diarize ? '' : 'display:none'}">
            <label class="t-label">Speakers</label>
            <input type="number" id="bulk-speakers" class="inp" min="1" max="12" value="${S.bulkDefaults.num_speakers || ''}" placeholder="auto" style="width:70px">
          </div>
          <div class="field">
            <label class="t-label">&nbsp;</label>
            <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
              <input type="checkbox" id="bulk-autocorrect" ${S.bulkDefaults.auto_correct ? 'checked' : ''}> Auto-correct
            </label>
          </div>
          <div class="field">
            <label class="t-label">&nbsp;</label>
            <button class="btn" id="bulk-apply-all" style="font-size:11px;padding:6px 14px;white-space:nowrap">Apply to All</button>
          </div>
        </div>
      </div>
    </div>
    <div class="unit" style="margin-bottom:14px;padding:0">
      <div style="padding:14px 34px;border-bottom:1px solid var(--edge)">
        <div style="display:flex;align-items:center;justify-content:space-between">
          <div class="t-unit">Files in batch</div>
          <button class="btn" id="bulk-reset-all" style="font-size:11px;padding:5px 12px">Reset to Defaults</button>
        </div>
      </div>
      ${S.bulkFiles.map((bf, i) => renderBulkFileRow(bf, i)).join('')}
    </div>
    <div class="unit" style="padding:16px 34px;display:flex;align-items:center;gap:14px">
      <button class="key key--wide" id="bulk-start" style="font-size:13px;padding:10px 28px;${S.bulkSubmitting ? 'opacity:0.4;pointer-events:none' : ''}">
        ${S.bulkSubmitting ? 'Submitting…' : 'Start Batch · ' + n + ' file' + (n !== 1 ? 's' : '')}
      </button>
      <span style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim)">
        Total: ${fmtBytes(S.bulkFiles.reduce((s, f) => s + f.file.size, 0))}
      </span>
    </div>
    ` : '<div class="empty-unit">Drop audio files above to get started. Each file gets its own settings row.</div>'}`;

  wireBulkDrop();
  wireBulkControls(root);
}

function renderBulkFileRow(bf, i) {
  return `
    <div class="bulk-file-row" data-bulk-idx="${i}" style="display:flex;align-items:center;gap:10px;padding:10px 34px;border-bottom:1px solid var(--seg-edge);flex-wrap:wrap">
      <div style="flex:1;min-width:180px">
        <div style="font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(bf.file.name)}</div>
        <div style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim)">${fmtBytes(bf.file.size)}</div>
      </div>
      <input type="text" class="inp bulk-title" data-bulk-idx="${i}" data-field="title" value="${escapeHtml(bf.title || '')}" placeholder="${escapeHtml(bf.file.name.replace(/\.[^.]+$/, '') || bf.file.name)}" style="width:150px;font-size:11px">
      <select class="inp bulk-field" data-bulk-idx="${i}" data-field="kind" style="font-size:11px;width:100px">
        <option value="auto" ${(bf.kind || S.bulkDefaults.kind) === 'auto' ? 'selected' : ''}>Auto</option>
        <option value="meeting" ${(bf.kind || S.bulkDefaults.kind) === 'meeting' ? 'selected' : ''}>Meeting</option>
        <option value="dictation" ${(bf.kind || S.bulkDefaults.kind) === 'dictation' ? 'selected' : ''}>Dictation</option>
        <option value="voice_note" ${(bf.kind || S.bulkDefaults.kind) === 'voice_note' ? 'selected' : ''}>Voice Note</option>
        <option value="voice_dump" ${(bf.kind || S.bulkDefaults.kind) === 'voice_dump' ? 'selected' : ''}>Audit / stream-of-consciousness dump</option>
      </select>
      <select class="inp bulk-field" data-bulk-idx="${i}" data-field="language" style="font-size:11px;width:100px">
        ${LANGUAGES.map(l => '<option value="' + l + '" ' + ((bf.language == null && l === 'Auto-detect') || bf.language === (l === 'Auto-detect' ? 'auto' : l.toLowerCase().slice(0, 2)) ? 'selected' : '') + '>' + escapeHtml(l) + '</option>').join('')}
      </select>
      <input type="number" class="inp bulk-field" data-bulk-idx="${i}" data-field="num_speakers" min="1" max="12" value="${bf.num_speakers != null ? bf.num_speakers : ''}" placeholder="auto" style="width:70px;font-size:11px;display:${(bf.diarize != null ? bf.diarize : S.bulkDefaults.diarize) ? '' : 'none'}">
      <label style="display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer;margin:0;white-space:nowrap">
        <input type="checkbox" class="bulk-field" data-bulk-idx="${i}" data-field="diarize" style="margin:0" ${(bf.diarize != null ? bf.diarize : S.bulkDefaults.diarize) ? 'checked' : ''}>Diarize
      </label>
      <button class="btn btn--red bulk-remove" data-bulk-idx="${i}" style="font-size:10px;padding:4px 8px">✕</button>
    </div>`;
}

function wireBulkDrop() {
  const drop = $('bulk-drop');
  const input = $('bulk-file-input');
  if (!drop || !input) return;
  drop.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    if (input.files.length) { addBulkFiles([...input.files]); input.value = ''; }
  });
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.style.borderColor = 'var(--nixie)'; });
  drop.addEventListener('dragleave', () => drop.style.borderColor = 'var(--edge)');
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.style.borderColor = 'var(--edge)';
    if (e.dataTransfer.files.length) addBulkFiles([...e.dataTransfer.files]);
  });
}

function addBulkFiles(newFiles) {
  for (const f of newFiles) {
    if (S.bulkFiles.some(bf => bf.file.name === f.name)) {
      toast('Duplicate: ' + f.name + ' already in list', 'info');
    }
    S.bulkFiles.push({
      file: f,
      title: null,
      kind: null,
      language: null,
      num_speakers: null,
      diarize: null,
    });
  }
  renderBulk();
}

function wireBulkControls(root) {
  $('bulk-provider')?.addEventListener('change', async () => {
    const idx = parseInt($('bulk-provider').value);
    S.providerIdx = idx;
    S.bulkDefaults.provider = S.providers[idx].id;
    await fetchModelsFor(idx);
    S.bulkDefaults.model = '';
    saveBulkDefaults();
    renderBulk();
  });
  $('bulk-model')?.addEventListener('change', () => {
    S.bulkDefaults.model = curProv().models[parseInt($('bulk-model').value)] || '';
    saveBulkDefaults();
  });
  $('bulk-language')?.addEventListener('change', () => {
    const v = LANGUAGES[$('bulk-language').value];
    S.bulkDefaults.language = v === 'Auto-detect' ? 'auto' : v.toLowerCase().slice(0, 2);
    saveBulkDefaults();
  });
  $('bulk-kind')?.addEventListener('change', () => {
    S.bulkDefaults.kind = $('bulk-kind').value;
    saveBulkDefaults();
  });
  $('bulk-diarize')?.addEventListener('change', () => {
    S.bulkDefaults.diarize = $('bulk-diarize').checked;
    const spk = $('bulk-speakers-field');
    if (spk) spk.style.display = S.bulkDefaults.diarize ? '' : 'none';
    if (!S.bulkDefaults.diarize) S.bulkDefaults.num_speakers = null;
    saveBulkDefaults();
  });
  $('bulk-speakers')?.addEventListener('change', () => {
    const v = $('bulk-speakers').value;
    S.bulkDefaults.num_speakers = v ? parseInt(v) : null;
    saveBulkDefaults();
  });
  $('bulk-autocorrect')?.addEventListener('change', () => {
    S.bulkDefaults.auto_correct = $('bulk-autocorrect').checked;
    saveBulkDefaults();
  });
  $('bulk-apply-all')?.addEventListener('click', () => {
    for (const bf of S.bulkFiles) {
      bf.kind = S.bulkDefaults.kind;
      bf.language = null;
      bf.num_speakers = S.bulkDefaults.num_speakers;
      bf.diarize = S.bulkDefaults.diarize;
      bf.title = null;
    }
    renderBulk();
    toast('Applied defaults to ' + S.bulkFiles.length + ' file' + (S.bulkFiles.length !== 1 ? 's' : ''), 'info');
  });
  $('bulk-reset-all')?.addEventListener('click', () => {
    for (const bf of S.bulkFiles) {
      bf.kind = null; bf.language = null; bf.num_speakers = null; bf.diarize = null; bf.title = null;
    }
    renderBulk();
    toast('All files reset to global defaults', 'info');
  });
  $('bulk-start')?.addEventListener('click', async () => {
    if (!S.bulkFiles.length || S.bulkSubmitting) return;
    const prov = curProv();
    const totalSize = S.bulkFiles.reduce((s, f) => s + f.file.size, 0);
    const langLabel = S.bulkDefaults.language === 'auto' ? 'Auto-detect' : (LANGUAGES.find(l => l.toLowerCase().slice(0, 2) === S.bulkDefaults.language) || 'Auto-detect');
    if (!(await styledConfirm(
      'Upload ' + S.bulkFiles.length + ' file' + (S.bulkFiles.length !== 1 ? 's' : '') +
      ' (' + fmtBytes(totalSize) + ') using ' + prov.name + '/' + (S.bulkDefaults.model || prov.models[0] || 'default') +
      '. Language: ' + langLabel + '. Diarize: ' + (S.bulkDefaults.diarize ? 'yes' : 'no') +
      '. Kind: ' + S.bulkDefaults.kind + '. Continue?'
    ))) return;
    S.bulkSubmitting = true;
    renderBulk();
    const form = new FormData();
    for (const bf of S.bulkFiles) form.append('files', bf.file);
    const settings = {
      kind: S.bulkDefaults.kind, provider: prov.id,
      model: S.bulkDefaults.model || prov.models[0] || '',
      language: S.bulkDefaults.language, diarize: S.bulkDefaults.diarize,
      auto_correct: S.bulkDefaults.auto_correct, num_speakers: S.bulkDefaults.num_speakers,
    };
    form.append('settings', JSON.stringify(settings));
    const fileOverrides = S.bulkFiles.map(bf => {
      const ov = {};
      if (bf.kind != null) ov.kind = bf.kind;
      if (bf.title != null) ov.title = bf.title;
      if (bf.language != null) ov.language = bf.language;
      if (bf.num_speakers != null) ov.num_speakers = bf.num_speakers;
      if (bf.diarize != null) ov.diarize = bf.diarize;
      return ov;
    });
    form.append('file_settings', JSON.stringify(fileOverrides));
    try {
      const r = await api('/api/bulk-transcribe', { method: 'POST', body: form });
      toast('Batch ' + r.batch_id + ': ' + r.transcripts.length + ' file' + (r.transcripts.length !== 1 ? 's' : '') + ' queued', 'ok');
      S.bulkFiles = [];
      S.bulkSubmitting = false;
      if (r.batch_id) navigate('queue');
      else renderBulk();
    } catch (e) {
      toast(e.message, 'error');
      S.bulkSubmitting = false;
      renderBulk();
    }
  });
  // delegated per-file events — assignment (not addEventListener) so repeated
  // renderBulk() calls don't stack handlers (match loadTranscripts at L2736).
  root.onchange = (e) => {
    const el = e.target;
    const idx = parseInt(el.dataset.bulkIdx);
    if (isNaN(idx) || idx < 0 || idx >= S.bulkFiles.length) return;
    if (el.classList.contains('bulk-title')) {
      S.bulkFiles[idx].title = el.value || null;
    } else if (el.classList.contains('bulk-field')) {
      const field = el.dataset.field;
      if (field === 'kind') S.bulkFiles[idx].kind = el.value;
      else if (field === 'language') S.bulkFiles[idx].language = el.value === 'Auto-detect' ? 'auto' : el.value.toLowerCase().slice(0, 2);
      else if (field === 'num_speakers') S.bulkFiles[idx].num_speakers = el.value ? parseInt(el.value) : null;
      else if (field === 'diarize') {
        S.bulkFiles[idx].diarize = el.checked;
        if (!el.checked) S.bulkFiles[idx].num_speakers = null;
        renderBulk();
      }
    }
  };
  root.onclick = (e) => {
    const btn = e.target.closest('.bulk-remove');
    if (!btn) return;
    const idx = parseInt(btn.dataset.bulkIdx);
    if (idx < 0 || idx >= S.bulkFiles.length) return;
    const removed = S.bulkFiles.splice(idx, 1);
    toast('Removed ' + (removed[0]?.file?.name || 'file'), 'info');
    renderBulk();
  };
}

function saveBulkDefaults() {
  clearTimeout(saveBulkDefaults._timer);
  saveBulkDefaults._timer = setTimeout(() => {
    api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bulk_defaults: S.bulkDefaults }),
    }).catch(() => { /* best-effort */ });
  }, 500);
}

async function loadTranscripts() {
  const root = $('page-transcripts');
  let list;
  try {
    list = await api('/api/transcripts?limit=100');
  } catch (e) { toast(e.message, 'error'); return; }

  bankListCache = list;
  refreshRailChrome(); // covers a job finishing while parked here across poll ticks
  const active = list.filter(t => t.status === 'processing').length;
  const openIds = new Set([...root.querySelectorAll('details[open]')].map(d => d.dataset.tid));

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Tape library</h1>
      <div class="page-status page-status--ok">${ledDot(GREEN, true, 9)}${list.length} channels · ${active} active</div>
    </div>
    <div class="unit" style="border-radius:3px;display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;padding:12px 34px">
      <input id="bank-search" class="inp" type="text" placeholder="Search transcripts…" value="${escapeHtml(S.bankQuery || '')}" style="font-size:12px;padding:8px 10px 8px 16px;flex:1;max-width:320px">
      <select id="bank-batch-filter" class="inp" style="font-size:12px;padding:8px 10px">
        <option value="" ${!S.batchFilter ? 'selected' : ''}>All transcripts</option>
        <option value="with-batch" ${S.batchFilter === 'with-batch' ? 'selected' : ''}>In a batch</option>
        <option value="no-batch" ${S.batchFilter === 'no-batch' ? 'selected' : ''}>Single uploads</option>
        ${(() => {
          const batchIds = [...new Set(list.filter(t => t.batch_id).map(t => t.batch_id))];
          return batchIds.map(bid => {
            const first = list.find(t => t.batch_id === bid);
            const label = (first ? (first.batch_id || 'Batch') : bid).slice(0, 30);
            return '<option value="' + escapeHtml(bid) + '" ' + (S.batchFilter === bid ? 'selected' : '') + '>' + escapeHtml(label) + (first ? ' (' + list.filter(t => t.batch_id === bid).length + ')' : '') + '</option>';
          }).join('');
        })()}
      </select>
      <select id="bank-sort" class="inp" style="font-size:12px;padding:8px 10px">
        <option value="date-desc" ${(!S.bankSort || S.bankSort === 'date-desc') ? 'selected' : ''}>Newest first</option>
        <option value="date-asc" ${S.bankSort === 'date-asc' ? 'selected' : ''}>Oldest first</option>
        <option value="title-asc" ${S.bankSort === 'title-asc' ? 'selected' : ''}>Title A–Z</option>
      </select>
    </div>
    <div id="bank-search-results"></div>
    <div id="bank-rows"></div>`;

  try {
    renderBankRows(openIds);
    if (S.bankQuery && S.bankQuery.trim().length >= 3) doServerSearch();
  } catch (e) {
    console.error('renderBankRows error:', e);
    root.insertAdjacentHTML('beforeend', '<div class="empty-unit">Error rendering tape library: ' + escapeHtml(e.message) + '</div>');
  }

  $('bank-search').addEventListener('input', () => {
    S.bankQuery = $('bank-search').value;
    if (S.bankQuery.trim().length >= 3) {
      doServerSearch();
    } else {
      S.bankSearchResults = null;
      $('bank-search-results').innerHTML = '';
      renderBankRows();
    }
  });
  $('bank-sort').addEventListener('change', () => {
    S.bankSort = $('bank-sort').value;
    renderBankRows();
  });
  $('bank-batch-filter')?.addEventListener('change', () => {
    S.batchFilter = $('bank-batch-filter').value;
    renderBankRows();
  });

  // Delegated on the stable `root` node (not per-row) so it keeps working
  // after renderBankRows() replaces #bank-rows' contents. Assignment (not
  // addEventListener) so it doesn't stack a duplicate handler on every poll.
  root.onclick = (e) => {
    // Batch pill click — filter to this batch
    const pill = e.target.closest('.batch-pill');
    if (pill) {
      e.preventDefault();
      S.batchFilter = pill.dataset.batchId;
      renderBankRows();
      return;
    }
    const b = e.target.closest('[data-act]');
    if (!b) return;
    e.preventDefault();
    const id = Number(b.dataset.id), act = b.dataset.act;
    if (act === 'open') { navigate('detail', id); return; }
    if (act === 'open-search') {
      var q = S.bankQuery ? S.bankQuery.trim() : '';
      S._searchJumpQuery = q;
      navigate('detail', id);
      return;
    }
    withBusy(b, async () => {
      try {
        if (act === 'cancel') { await api('/api/transcripts/' + id + '/cancel', { method: 'POST' }); toast('Cancelled — resumable later', 'info'); }
        if (act === 'resume') { const r = await api('/api/transcripts/' + id + '/resume', { method: 'POST' }); toast('Resumed ' + r.resumed + ' sections', 'info'); }
        if (act === 'retry') { const r = await api('/api/transcripts/' + id + '/retry-failed-chunks', { method: 'POST' }); toast('Retrying ' + r.retried + ' sections', 'info'); }
        if (act === 'rename') {
          const row = bankListCache.find(x => x.id === id);
          const name = await styledPrompt('Rename this transcript:', row ? (row.title || row.filename) : '');
          if (name === null || !name.trim()) return;
          const updated = await api('/api/transcripts/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: name.trim() }) });
          const idx = bankListCache.findIndex(x => x.id === id);
          if (idx >= 0) bankListCache[idx] = updated;
          renderBankRows();
          toast('Renamed', 'info');
          return;
        }
        if (act === 'delete') {
          if (!(await styledConfirm('Delete this transcript permanently?'))) return;
          await api('/api/transcripts/' + id, { method: 'DELETE' });
          toast('Transcript deleted');
        }
        loadTranscripts();
      } catch (err) { toast(err.message, 'error'); }
    });
  };

  clearTimeout(bankPollTimer);
  if (active > 0 && S.page === 'transcripts') {
    bankPollTimer = setTimeout(function pollTick() {
      if (S.page !== 'transcripts') return;
      if (S.bankQuery && S.bankQuery.trim().length >= 3) {
        api('/api/transcripts?limit=100').then(function(list) {
          bankListCache = list;
          doServerSearch();
        }).catch(function() {});
        bankPollTimer = setTimeout(pollTick, 4000);
      } else {
        loadTranscripts();
      }
    }, 4000);
  }
}

async function doServerSearch() {
  if (S.bankSearchController) S.bankSearchController.abort();
  S.bankSearchController = new AbortController();
  var q = S.bankQuery.trim();
  if (q.length < 3) { S.bankSearchResults = null; renderSearchResults(); return; }
  try {
    var resp = await api('/api/search?q=' + encodeURIComponent(q) + '&limit=20', { signal: S.bankSearchController.signal });
    S.bankSearchResults = resp.results || [];
  } catch (e) {
    if (e.name === 'AbortError') return;
    S.bankSearchResults = null;
  }
  renderSearchResults();
}

function renderSearchResults() {
  var container = $('bank-search-results');
  if (!container) return;
  var q = (S.bankQuery || '').trim();
  if (q.length < 3 || !S.bankSearchResults) { container.innerHTML = ''; return; }
  if (!S.bankSearchResults.length) {
    container.innerHTML = '<div class="empty-unit">No transcripts match your search</div>';
    return;
  }
  var rows = S.bankSearchResults.map(function(r) {
    var title = escapeHtml(r.title || r.filename || 'Untitled');
    var date = r.created_at ? timeAgo(r.created_at) : '';
    var snippet = escapeHtml(r.snippet || '');
    snippet = snippet.replace(/&lt;b&gt;/g, '<b>').replace(/&lt;\/b&gt;/g, '</b>');
    var sourceLabel = r.match_source === 'corrected_text' ? 'corrected' :
                      r.match_source === 'segment_text' ? 'segments' :
                      r.match_source === 'title' ? 'title' : 'transcript';
    return '<div class="unit" style="padding:10px 16px;cursor:pointer;margin-bottom:4px">' +
      '<div style="display:flex;justify-content:space-between;align-items:center">' +
        '<div style="font-weight:600;font-size:13px">' + title + '</div>' +
        '<div style="display:flex;gap:6px;align-items:center">' +
          '<span style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim)">' + escapeHtml(date) + '</span>' +
          '<span style="font-size:9px;padding:1px 6px;border:1px solid var(--inset-edge);border-radius:8px;color:var(--label-dim)">' + escapeHtml(sourceLabel) + '</span>' +
          '<button class="btn" style="font-size:10px;padding:3px 8px" data-act="open-search" data-id="' + r.transcript_id + '">Open</button>' +
        '</div>' +
      '</div>' +
      '<div style="font-size:12px;color:var(--label);margin-top:4px;line-height:1.5">' + snippet + '</div>' +
    '</div>';
  }).join('');
  container.innerHTML = rows;
}

function renderBankRows(preservedOpenIds) {
  const rowsContainer = $('bank-rows');
  const openIds = preservedOpenIds || new Set([...rowsContainer.querySelectorAll('details[open]')].map(d => d.dataset.tid));

  const q = (S.bankQuery || '').trim().toLowerCase();
  let filtered = q
    ? bankListCache.filter(t => (t.title || '').toLowerCase().includes(q)
      || (t.filename || '').toLowerCase().includes(q)
      || (t.tags || []).some(tag => tag.toLowerCase().includes(q)))
    : bankListCache.slice();

  // Apply batch filter
  if (S.batchFilter === 'with-batch') filtered = filtered.filter(t => t.batch_id);
  else if (S.batchFilter === 'no-batch') filtered = filtered.filter(t => !t.batch_id);
  else if (S.batchFilter) filtered = filtered.filter(t => t.batch_id === S.batchFilter);

  const sortFns = {
    'date-desc': (a, b) => new Date(b.created_at) - new Date(a.created_at),
    'date-asc': (a, b) => new Date(a.created_at) - new Date(b.created_at),
    'title-asc': (a, b) => (a.title || a.filename || '').localeCompare(b.title || b.filename || ''),
  };
  filtered.sort(sortFns[S.bankSort || 'date-desc']);

  const statusEl = $('bank-status');
  if (statusEl) {
    const activeCount = bankListCache.filter(t => t.status === 'processing').length;
    statusEl.innerHTML = `${ledDot(GREEN, true, 9)}${filtered.length} of ${bankListCache.length} channels · ${activeCount} active`;
  }

  if (!bankListCache.length) {
    rowsContainer.innerHTML = '<div class="empty-unit">No signals on the bank — load a tape on the Transcribe deck</div>';
    return;
  }
  if (!filtered.length) {
    rowsContainer.innerHTML = '<div class="empty-unit">No transcripts match your search</div>';
    return;
  }

  rowsContainer.innerHTML = filtered.map(t => {
    const sv = statusView(t);
    const fields = bankDetailFields(t, sv);
    const acts = ['<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="open" data-id="' + t.id + '">Open transcript</button>'];
    if (t.status === 'processing')
      acts.push('<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="cancel" data-id="' + t.id + '">Cancel — resumable</button>');
    if (t.status === 'cancelled')
      acts.push('<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="resume" data-id="' + t.id + '">Resume</button>');
    if (t.status === 'failed' || t.status === 'partial')
      acts.push('<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="retry" data-id="' + t.id + '">Retry</button>');
    acts.push('<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="rename" data-id="' + t.id + '">Rename</button>');
    acts.push('<button class="btn btn--red" style="font-size:12px;padding:6px 12px" data-act="delete" data-id="' + t.id + '">Delete</button>');
    const tags = Array.isArray(t.tags) ? t.tags : [];
    const tagPills = tags.length
      ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px">${tags.map(tag => `<span style="display:inline-block;font-family:var(--f-mono);font-size:10px;padding:2px 7px;border:1px solid var(--panel-lo);border-radius:9px;background:var(--panel-lo);color:var(--label);text-transform:lowercase;letter-spacing:0.02em" title="Tag from issue #171 auto-tagging">${escapeHtml(tag)}</span>`).join('')}</div>`
      : '';
    const batchPill = t.batch_id
      ? `<span class="batch-pill" data-batch-id="${escapeHtml(t.batch_id)}" title="Part of batch ${escapeHtml(t.batch_id)}" style="display:inline-block;font-family:var(--f-mono);font-size:9px;padding:1px 6px;border:1px solid var(--nixie);border-radius:8px;color:var(--nixie);text-transform:uppercase;letter-spacing:0.06em;cursor:pointer;margin-left:6px;vertical-align:middle">BATCH</span>`
      : '';
    return `
    <details class="unit" data-tid="${t.id}" ${openIds.has(String(t.id)) ? 'open' : ''}>
      <summary style="list-style:none;cursor:pointer;padding:12px 22px 12px 34px;display:grid;grid-template-columns:16px 1fr 190px 112px;align-items:center;gap:16px">
        <span class="row-chevron" style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim)" title="Click row to expand details">▸</span>
        <div style="min-width:0">
          <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(t.title || t.filename || 'Untitled')}${batchPill}</div>
          <div style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim);margin-top:2px">${escapeHtml(transcriptMeta(t))} · click to expand</div>
          ${tagPills}
        </div>
        ${sv.segments ? stageSegmentBar(sv.segments) : bargraph(sv.cells, 16)}
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px">
          ${nixie(sv.nix, sv.nixVariant)}
          <div class="status-badge status-badge--${escapeHtml(sv.word)}" data-word="${escapeHtml(sv.word)}">${escapeHtml(sv.word)}</div>
        </div>
      </summary>
      <div style="padding:12px 22px 16px 34px;border-top:1px solid var(--panel-lo)">
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:12px">
          ${fields.map(f => `<div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">${escapeHtml(f[0])}</div>${escapeHtml(f[1])}</div>`).join('')}
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">${acts.join('')}</div>
      </div>
    </details>`;
  }).join('');
}

/* ══════════════════ master job queue ══════════════════ */
let queuePollTimer = null;

// Batch state for Queue page grouping and Tape Library filtering
S.batchSnapshots = {};       // {batch_id: {active: N, failed: N}} for transition detection
S.batchFilter = '';          // current batch filter on Tape Library ('' = all, 'with-batch', 'no-batch', or batch_id)

function jobStatusView(j) {
  const total = j.progress && j.progress.total ? j.progress.total : 0;
  const done = j.progress ? j.progress.done : 0;
  const pct = total ? Math.round(done / total * 100) : 0;
  switch (j.status) {
    case 'completed': return { color: GREEN, lit: 11, nix: '100%', variant: '', word: 'done' };
    case 'failed': return { color: RED, lit: 3, nix: 'ERR', variant: 'fault', word: 'failed' };
    case 'partial': return { color: AMBER, lit: Math.max(1, Math.round(pct / 100 * 11)), nix: pct + '%', variant: '', word: 'partial' };
    case 'cancelled': return { color: AMBER, lit: 0, nix: pct + '%', variant: 'dim', word: 'cancelled' };
    case 'running': return { color: AMBER, lit: total ? Math.max(1, Math.round(pct / 100 * 11)) : 1, nix: total ? pct + '%' : '···', variant: '', word: 'running' };
    case 'waiting': return { color: AMBER, lit: Math.round(pct / 100 * 11), nix: pct + '%', variant: '', word: 'waiting' };
    default: return { color: null, lit: 0, nix: '·· %', variant: 'dim', word: 'queued' };
  }
}

function jobActions(j) {
  const acts = [];
  const btn = (act, label, red = false) =>
    `<button class="btn${red ? ' btn--red' : ''}" style="font-size:12px;padding:6px 12px;${red ? '' : 'border-color:var(--inset-edge)'}" data-jact="${act}" data-jid="${j.id}" data-tid="${j.transcript_id}">${label}</button>`;
  if (j.kind === 'transcription') {
    if (['running', 'queued', 'waiting'].includes(j.status)) acts.push(btn('t-cancel', 'Cancel — resumable'));
    if (j.status === 'cancelled') acts.push(btn('t-resume', 'Resume'));
    if (j.status === 'failed' || j.status === 'partial') acts.push(btn('t-retry', 'Retry'));
  } else {
    if (j.status === 'pending' || j.status === 'running') acts.push(btn('j-cancel', 'Cancel'));
    if (j.status === 'failed' || j.status === 'cancelled') acts.push(btn('j-rerun', 'Rerun'));
  }
  if (['completed', 'failed', 'partial', 'cancelled'].includes(j.status)) acts.push(btn('j-dismiss', 'Clear'));
  acts.push(btn('open', 'Open transcript'));
  return acts.join('');
}

const KIND_LABELS = {
  transcription: 'TRANSCRIBE', correction: 'CORRECT', summary: 'SUMMARIZE', rediarize: 'DIARIZE',
  voice_match: 'VOICE MATCH', voice_note: 'VOICE NOTE', voice_dump: 'VOICE DUMP', tagging: 'TAG',
  format_markdown: 'MD NOTE', format_email: 'EMAIL DRAFT', format_coding_prompt: 'CODE PROMPT', classify_intent: 'CLASSIFY',
};

// computeBatchAggregate lives in ./batch_aggregate.js -- kept dependency-free
// (no DOM/global references) so it can be unit-tested directly in Node
// without loading this whole browser script. esbuild inlines it into the
// bundle at build time, so the served file is still one self-contained
// script; nothing changes at runtime.
const { computeBatchAggregate } = require('./batch_aggregate.js');

async function loadQueue(opts = {}) {
  const root = $('page-queue');
  let data;
  try { data = await getJobs({force: opts.force || false}); } catch (e) { toast(e.message, 'error'); return; }
  const jobs = data.jobs || [];
  updateQueueBadge(data.active || 0);
  refreshRailChrome(); // covers a job finishing while parked here across poll ticks

  const openIds = new Set([...root.querySelectorAll('details[open]')].map(d => d.dataset.qid));
  const openBatchIds = new Set([...root.querySelectorAll('.batch-group[open]')].map(d => d.dataset.bid));

  // Separate transcription entries from LLM jobs for batch grouping
  const txs = jobs.filter(j => j.kind === 'transcription' && j.batch_id);
  const others = jobs.filter(j => j.kind !== 'transcription' || !j.batch_id);
  const batchMap = {};
  for (const j of txs) {
    if (!batchMap[j.batch_id]) batchMap[j.batch_id] = [];
    batchMap[j.batch_id].push(j);
  }

  // Render batch groups first, then non-batch entries
  let batchHtml = '';
  for (const [bid, group] of Object.entries(batchMap)) {
    const { counts, activeInBatch, failedInBatch, total, done, lit, batchColor, badgeWord, badgeClass, statusLine } =
      computeBatchAggregate(group);
    const batchCells = [...Array(11)].map((_, i) => ({ on: i < lit, color: batchColor }));
    const batchOpen = openBatchIds.has(bid);
    const titles = group.map(j => j.title || 'Untitled').filter(Boolean).slice(0, 3).join(', ') + (group.length > 3 ? ', ...' : '');
    const totalDurSec = group.reduce((s, j) => s + (j.duration_seconds || 0), 0);
    const durText = formatDur(totalDurSec) + (totalDurSec ? ' total' : '');

    // Detect batch completion transition
    const snap = S.batchSnapshots[bid];
    if (snap && snap.active > 0 && activeInBatch === 0) {
      if (failedInBatch > 0) {
        toast(`Batch complete with ${failedInBatch} failure${failedInBatch !== 1 ? 's' : ''}`, 'error');
      } else if (counts.cancelled > 0 && counts.completed === 0) {
        toast(`Batch cancelled (${counts.cancelled} file${counts.cancelled !== 1 ? 's' : ''})`, 'info');
      } else {
        toast(`Batch complete: ${done}/${total} files transcribed${counts.cancelled ? ' (' + counts.cancelled + ' cancelled)' : ''}`, 'info');
      }
    }
    S.batchSnapshots[bid] = { active: activeInBatch, failed: failedInBatch };

    batchHtml += `
    <details class="unit batch-group" data-bid="${escapeHtml(bid)}" ${batchOpen ? 'open' : ''}>
      <summary style="list-style:none;cursor:pointer;padding:12px 22px 12px 34px;display:grid;grid-template-columns:88px 1fr 170px 100px;align-items:center;gap:14px">
        <span class="vfd" style="width:88px"><span>BATCH</span></span>
        <div style="min-width:0">
          <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Batch — ${total} file${total !== 1 ? 's' : ''} — ${escapeHtml(titles)}</div>
          <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(statusLine)}${statusLine && durText ? ' · ' : ''}${escapeHtml(durText)}</div>
        </div>
        ${bargraph(batchCells)}
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px">
          ${nixie(String(done + '/' + total))}
          <div class="status-badge status-badge--${badgeClass}" data-word="${badgeWord}">${badgeWord}</div>
        </div>
      </summary>
      <div style="padding:12px 22px 14px 34px;border-top:1px solid var(--panel-lo);display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-bact="cancel" data-bid="${escapeHtml(bid)}">Cancel all</button>
        <button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-bact="open-batch" data-bid="${escapeHtml(bid)}">Open batch</button>
      </div>
      ${group.map(j => {
        const sv = jobStatusView(j);
        const cells = [...Array(11)].map((_, i) => ({ on: sv.color !== null && i < sv.lit, color: sv.color }));
        const prog = j.progress && j.progress.total
          ? ' · section ' + Math.min(j.progress.done + (j.status === 'running' ? 1 : 0), j.progress.total) + ' of ' + j.progress.total : '';
        const meta = [(j.provider || '—') + (j.model ? ' · ' + j.model : ''), j.status === 'running' ? 'working' + prog : null,
          j.error ? humanizeJobError(j.error) : null, timeAgo(j.created_at)].filter(Boolean).join(' · ');
        return `
      <details class="batch-entry" data-qid="${escapeHtml(String(j.id))}" ${openIds.has(String(j.id)) ? 'open' : ''} style="margin:0 0 1px 34px;border-left:1px solid var(--panel-lo)">
        <summary style="list-style:none;cursor:pointer;padding:10px 22px 10px 22px;display:grid;grid-template-columns:1fr 170px 100px;align-items:center;gap:14px">
          <div style="min-width:0">
            <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(j.title || 'Untitled')}</div>
            <div style="font-family:var(--f-mono);font-size:10.5px;color:${j.error ? 'var(--red)' : 'var(--label-dim)'};margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(meta)}</div>
          </div>
          ${bargraph(cells)}
          <div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px">
            ${nixie(sv.nix, sv.variant)}
            <div class="status-badge status-badge--${escapeHtml(sv.word)}" data-word="${escapeHtml(sv.word)}">${escapeHtml(sv.word)}</div>
          </div>
        </summary>
        <div style="padding:10px 22px 12px 22px;border-top:1px solid var(--panel-lo);display:flex;gap:8px;flex-wrap:wrap">
          ${jobActions(j)}
        </div>
      </details>`;
      }).join('')}
    </details>`;
  }

  // Non-batch entries render as before
  const otherRows = others.map(j => {
    const sv = jobStatusView(j);
    const cells = [...Array(11)].map((_, i) => ({ on: sv.color !== null && i < sv.lit, color: sv.color }));
    const prog = j.progress && j.progress.total
      ? ' · section ' + Math.min(j.progress.done + (j.status === 'running' ? 1 : 0), j.progress.total) + ' of ' + j.progress.total : '';
    const meta = [(j.provider || '—') + (j.model ? ' · ' + j.model : ''), j.status === 'running' ? 'working' + prog : null,
      j.error ? humanizeJobError(j.error) : null, timeAgo(j.created_at)].filter(Boolean).join(' · ');
    return `
    <details class="unit" data-qid="${escapeHtml(String(j.id))}" ${openIds.has(String(j.id)) ? 'open' : ''}>
      <summary style="list-style:none;cursor:pointer;padding:12px 22px 12px 34px;display:grid;grid-template-columns:88px 1fr 170px 100px;align-items:center;gap:14px">
        <span class="vfd" style="width:88px"><span>${KIND_LABELS[j.kind] || j.kind}</span></span>
        <div style="min-width:0">
          <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(j.title || 'Untitled')}</div>
          <div style="font-family:var(--f-mono);font-size:10.5px;color:${j.error ? 'var(--red)' : 'var(--label-dim)'};margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(meta)}</div>
        </div>
        ${bargraph(cells)}
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px">
          ${nixie(sv.nix, sv.variant)}
          <div class="status-badge status-badge--${escapeHtml(sv.word)}" data-word="${escapeHtml(sv.word)}">${escapeHtml(sv.word)}</div>
        </div>
      </summary>
      <div style="padding:12px 22px 14px 34px;border-top:1px solid var(--panel-lo);display:flex;gap:8px;flex-wrap:wrap">
        ${jobActions(j)}
      </div>
    </details>`;
  }).join('');

  const active = data.active || 0;
  const finishedCount = jobs.filter(j => ['completed', 'failed', 'partial', 'cancelled'].includes(j.status)).length;
  const gauge = data.rate_limit_gauge || {};
  let gaugeHtml = '';
  if (gauge.limit_seconds) {
    const usedFormatted = Math.round(gauge.used_seconds || 0).toLocaleString();
    const limitFormatted = Math.round(gauge.limit_seconds).toLocaleString();
    const resetH = gauge.resets_in_seconds ? Math.ceil(gauge.resets_in_seconds / 3600) : 0;
    const resetText = resetH > 0 ? ` · resets in ~${resetH}h` : '';
    gaugeHtml = `<div class="budget-gauge" style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim);background:var(--panel-lo);padding:4px 10px;border-radius:3px;border:1px solid var(--inset-edge)">${escapeHtml(usedFormatted)} / ${escapeHtml(limitFormatted)} audio-seconds used today${escapeHtml(resetText)}</div>`;
  }
  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Queue</h1>
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        ${gaugeHtml}
        <div class="page-status page-status--${active ? 'busy' : 'ok'}">${ledDot(active ? AMBER : GREEN, true, 9)}${jobs.length} jobs · ${active} active</div>
        ${finishedCount ? `<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-jact="clear-finished">Clear finished (${finishedCount})</button>` : ''}
      </div>
    </div>
    ${batchHtml + otherRows || '<div class="empty-unit">Queue idle — jobs appear here when the machine is working</div>'}`;

  // Wire batch-level actions
  root.querySelectorAll('[data-bact]').forEach(b => b.addEventListener('click', (e) => {
    e.preventDefault();
    const bact = b.dataset.bact, bid = b.dataset.bid;
    if (bact === 'open-batch') {
      navigate('transcripts');
      S.batchFilter = bid;
      setTimeout(() => { if (S.page === 'transcripts') loadTranscripts(); }, 50);
      return;
    }
    if (bact === 'cancel') {
      withBusy(b, async () => {
        try {
          const r = await api('/api/batches/' + encodeURIComponent(bid) + '/cancel', { method: 'POST' });
          toast('Cancelled ' + r.cancelled + ' file' + (r.cancelled !== 1 ? 's' : '') + ' in batch', 'info');
          loadQueue({force: true});
        } catch (err) { toast(err.message, 'error'); }
      });
    }
  }));

  root.querySelectorAll('[data-jact]').forEach(b => b.addEventListener('click', (e) => {
    e.preventDefault();
    const act = b.dataset.jact, jid = b.dataset.jid, tid = Number(b.dataset.tid);
    if (act === 'open') { navigate('detail', tid); return; }
    withBusy(b, async () => {
      try {
        if (act === 'j-cancel') { await apiRetry409('/api/jobs/' + jid + '/cancel', { method: 'POST' }); toast('Job cancelled', 'info'); }
        if (act === 'j-rerun') { await apiRetry409('/api/jobs/' + jid + '/rerun', { method: 'POST' }); toast('Job requeued', 'info'); }
        if (act === 't-cancel') { await api('/api/transcripts/' + tid + '/cancel', { method: 'POST' }); toast('Cancelled — resumable later', 'info'); }
        if (act === 't-resume') { const r = await api('/api/transcripts/' + tid + '/resume', { method: 'POST' }); toast('Resumed ' + r.resumed + ' sections', 'info'); }
        if (act === 't-retry') { const r = await api('/api/transcripts/' + tid + '/retry-failed-chunks', { method: 'POST' }); toast('Retrying ' + r.retried + ' sections', 'info'); }
        if (act === 'j-dismiss') { await api('/api/jobs/' + jid + '/dismiss', { method: 'POST' }); toast('Cleared', 'info'); }
        if (act === 'clear-finished') { const r = await api('/api/jobs/clear', { method: 'POST' }); toast('Cleared ' + r.cleared + ' finished job(s)', 'info'); }
        loadQueue({force: true});
      } catch (err) { toast(err.message, 'error'); }
    });
  }));

  clearTimeout(queuePollTimer);
  if (active > 0 && S.page === 'queue') {
    queuePollTimer = setTimeout(() => { if (S.page === 'queue') loadQueue(); }, 3000);
  }
}

function updateQueueBadge(active) {
  $('nav-badge-queue').textContent = active ? String(active).padStart(2, '0') : '';
}

async function refreshQueueBadge(force = false) {
  try {
    const data = await getJobs({force});
    updateQueueBadge(data.active || 0);
  } catch { /* badge is best-effort */ }
}

/* ══════════════════ costs page ══════════════════ */
async function loadCostsPage() {
  const root = $('page-costs');
  if (!root) return;
  let data;
  try {
    data = await api('/api/costs');
  } catch (e) {
    toast(e.message, 'error');
    return;
  }
  const providers = data.providers || {};
  const monthlyTotal = data.monthly_total || 0;
  const lifetimeTotal = data.lifetime_total || 0;
  const gauge = data.rate_limit_gauge || {};

  const providerKeys = Object.keys(providers);

  let rowsHtml = '';
  if (providerKeys.length === 0) {
    rowsHtml = '<div class="empty-unit">No transcription spend recorded this month</div>';
  } else {
    rowsHtml = providerKeys.map(p => {
      const pc = providers[p] || {};
      const sec = pc.total_seconds || 0;
      const cost = pc.total_cost || 0;
      const rate = pc.rate_per_minute || 0;
      const source = pc.rate_source || '—';
      const mins = (sec / 60).toFixed(1);
      return `
      <div class="unit" style="padding:14px 22px 14px 34px;margin-bottom:8px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
          <div>
            <div style="font-family:var(--f-cond);font-size:15px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase">${escapeHtml(p)}</div>
            <div style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim);margin-top:2px">${escapeHtml(mins)} min STT · rate: $${rate.toFixed(4)}/min (${escapeHtml(source)})</div>
          </div>
          <div style="font-family:var(--f-mono);font-size:16px;font-weight:700;color:var(--green)">
            $${cost.toFixed(2)}
          </div>
        </div>
      </div>`;
    }).join('');
  }

  const gaugeUsedSec = gauge.used_seconds || 0;
  const gaugeLimitSec = gauge.limit_seconds || 28800;
  const gaugeUsedFormatted = Math.round(gaugeUsedSec).toLocaleString();
  const gaugeLimitFormatted = Math.round(gaugeLimitSec).toLocaleString();
  const gaugeUsedCost = (gauge.used_cost || 0).toFixed(2);
  const gaugeLimitCost = (gauge.limit_cost || 0).toFixed(2);
  const resetH = gauge.resets_in_seconds ? Math.ceil(gauge.resets_in_seconds / 3600) : 0;
  const resetText = resetH > 0 ? ` · resets in ~${resetH}h` : '';

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Costs</h1>
      <div class="page-status page-status--ok">${ledDot(GREEN, true, 9)}${monthlyTotal > 0 ? '$' + monthlyTotal.toFixed(2) + ' this month' : 'No spend this month'}</div>
    </div>

    <div class="unit" style="padding:18px 22px 18px 34px;margin-bottom:16px">
      <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:16px">
        <div>
          <div class="t-label" style="margin-bottom:4px">Monthly Spend</div>
          <div style="font-family:var(--f-mono);font-size:22px;font-weight:700;color:var(--green)">$${monthlyTotal.toFixed(2)}</div>
          <div style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);margin-top:2px">Trailing 30 days</div>
        </div>
        <div>
          <div class="t-label" style="margin-bottom:4px">Lifetime Spend</div>
          <div style="font-family:var(--f-mono);font-size:22px;font-weight:700;color:var(--text)">$${lifetimeTotal.toFixed(2)}</div>
          <div style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);margin-top:2px">All-time total</div>
        </div>
        <div>
          <div class="t-label" style="margin-bottom:4px">Rate-Limit Budget</div>
          <div style="font-family:var(--f-mono);font-size:14px;font-weight:700;color:var(--text);margin-top:4px">${gaugeUsedFormatted} / ${gaugeLimitFormatted} audio-s</div>
          <div style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);margin-top:2px">~$${gaugeUsedCost} / $${gaugeLimitCost} today${escapeHtml(resetText)}</div>
        </div>
      </div>
    </div>

    <div style="font-family:var(--f-cond);font-size:13px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:var(--label-dim);margin:0 36px 8px">Provider Breakdown</div>
    ${rowsHtml}
  `;
}

/* ══════════════════ transcript detail ══════════════════ */

// Pure draft-item helpers live in ./dump_review.js -- kept dependency-free
// (no DOM/global references) so they can be unit-tested directly in Node
// without loading this whole browser script. esbuild inlines it into the
// bundle at build time, same as batch_aggregate.js above.
const { DUMP_NOTE_TYPES, normalizeDumpItems, materializeDumpItems } = require('./dump_review.js');

// The one registry for kind-gated detail tabs. detailTabsHtml (the chrome
// that offers the tab), renderDetailBody (the renderer that fulfills it)
// and loadTranscriptDetail's sticky-tab reset all derive from this, so a
// tab can never be offered by one and refused by another, and a new kind
// can't be added to the button row while the reset logic forgets it
// (AGENTS.md Complement Rule 3 and 4).
const KIND_TABS = { dictation: 'format', voice_note: 'notes', voice_dump: 'review' };

let detailData = null;
let detailLoadGen = 0; // generation counter to prevent race conditions on rapid clicks

let detailPollTimer = null;

// Per-line playback + voice-seed flags. All session-local: the shared
// Audio element is created lazily on first play, and seed flags live in
// memory until the user enrolls them — both reset when a DIFFERENT
// transcript opens (same-id reloads keep flags so a rename refresh
// doesn't wipe them).
let segAudio = null, segAudioTid = null, segPlayingBtn = null;
let seedClips = {}; // speaker label -> [{start, end}]
let videoFloating = false;
let videoFloatingTid = null; // transcript id the floating video belongs to

// Bulk re-tag mode: selectedSegments holds real indices into t.segments
// (not the filtered-view index, since search can filter the list).
let selectMode = false;
let selectedSegments = new Set();

function resetSegAudio() {
  if (segAudio) segAudio.pause();
  segAudio = null;
  segAudioTid = null;
  segPlayingBtn = null;
  seedClips = {};
  const v = $('seg-video');
  if (v) v.pause();
  const vf = $('seg-video-floating');
  if (vf) vf.pause();
  // Re-attach on transcript switch — floating is per-transcript, not global
  if (videoFloating) reattachVideo();
}

async function loadTranscriptDetail(id, opts = {}) {
  if (id == null) { navigate('transcripts'); return; }
  const gen = ++detailLoadGen;
  const prevId = detailData ? detailData.id : null;
  let fetched;
  try {
    fetched = await api('/api/transcripts/' + id);
  } catch (e) { toast(e.message, 'error'); return; }
  if (gen !== detailLoadGen) return; // stale response, a newer load is in flight
  detailData = fetched;
  if (prevId !== null && prevId !== detailData.id) resetSegAudio();
  if (S._searchJumpQuery) {
    S.query = S._searchJumpQuery;
    S._searchJumpQuery = null;
  } else if (!opts.preserveQuery) {
    S.query = '';
  }
  // S.detailTab is a global that survives navigation between transcripts —
  // if it's pointed at a kind-specific tab (format for dictation, notes
  // for voice_note, review for voice_dump) and the newly-opened transcript
  // is the wrong kind, fall back rather than leave a stale tab selection
  // that renderDetailBody would otherwise still act on. Driven off
  // KIND_TABS so a new kind-gated tab is reset without a second edit here.
  const kindTab = KIND_TABS[detailData.kind] || null;
  if (S.detailTab !== kindTab && Object.values(KIND_TABS).indexOf(S.detailTab) !== -1) S.detailTab = 'transcript';
  renderDetail();
  scheduleDetailPoll();
}

function _jobFingerprint(t) {
  const f = (j) => j ? j.status + ':' + (j.progress ? j.progress.done : 0) : '-';
  return f(t.correction_job) + '|' + f(t.summary_job) + '|' + f(t.voice_match_job) + '|' +
    f(t.format_markdown_job) + '|' + f(t.format_email_job) + '|' + f(t.format_coding_prompt_job) + '|' +
    f(t.classify_intent_job) + '|' + f(t.tagging_job) + '|' + f(t.voice_dump_job);
}

// While an LLM job is active for the open transcript, refresh quietly and
// re-render only when the job actually moved — no flicker mid-read.
function scheduleDetailPoll() {
  clearTimeout(detailPollTimer);
  const t = detailData;
  if (!t || !(llmJobActive(t.correction_job) || llmJobActive(t.summary_job) || llmJobActive(t.voice_match_job) ||
    llmJobActive(t.format_markdown_job) || llmJobActive(t.format_email_job) || llmJobActive(t.format_coding_prompt_job) ||
    llmJobActive(t.classify_intent_job) || llmJobActive(t.tagging_job) ||
    llmJobActive(t.voice_dump_job))) return;
  const fp = _jobFingerprint(t), id = t.id, prevActive = jobActiveSnapshot(t);
  detailPollTimer = setTimeout(async () => {
    if (S.page !== 'detail' || !detailData || detailData.id !== id) return;
    try {
      const fresh = await api('/api/transcripts/' + id);
      if (S.page !== 'detail' || !detailData || detailData.id !== id) return;
      detailData = fresh;
      if (_jobFingerprint(fresh) !== fp) await updateDetailJobStatus(fresh, prevActive);
      scheduleDetailPoll();
    } catch { /* transient — poll dies, next action revives it */ }
  }, 2500);
}

function detailTabsHtml() {
  const tabs = ['transcript', 'corrected', 'summary'];
  const kindTab = detailData ? KIND_TABS[detailData.kind] : null;
  if (kindTab) tabs.push(kindTab);
  return tabs.map(tb => {
    const on = S.detailTab === tb;
    return `
    <button data-tab="${tb}" style="display:flex;flex-direction:column;align-items:center;gap:5px;background:none;border:none;cursor:pointer;padding:0">
      ${ledDot(on ? GREEN : null, on, 6)}
      <span style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.04em;width:110px;height:30px;background:${on ? 'linear-gradient(180deg,#FFFFFF,#DEE0E1 55%,#A9ACAF)' : 'linear-gradient(180deg,#D8D9DA,#C6C8C9 55%,#B7B9BA)'};border:1px solid var(--key-edge);border-top-color:var(--key-top);border-radius:0 0 3px 3px;box-shadow:0 2px 0 var(--key-shadow),0 3px 4px rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;color:var(--key-ink)">${tb}</span>
    </button>`;
  }).join('');
}

function segmentsHtml(t) {
  const q = (S.query || '').trim().toLowerCase();
  const allSegs = t.segments || [];
  const segs = allSegs
    .map((sg, i) => ({ sg, i }))
    .filter(({ sg }) => !q || (sg.text || '').toLowerCase().includes(q) || (sg.speaker || '').toLowerCase().includes(q));
  if (!segs.length) {
    return '<div class="empty-unit">' +
      (q ? 'No segments match — clear the search or check job status' : 'No segments yet — check job status') + '</div>';
  }
  const segBtn = 'background:none;border:1px solid var(--inset-edge);border-radius:3px;width:24px;height:22px;cursor:pointer;font-size:10px;padding:0;flex-shrink:0';
  return segs.map(({ sg, i }) => {
    const dot = hashColor(sg.speaker || '');
    const seeded = sg.speaker && (seedClips[sg.speaker] || []).some(c => c.start === sg.start && c.end === sg.end);
    const checkbox = selectMode
      ? `<input type="checkbox" data-seg-select="${i}" ${selectedSegments.has(i) ? 'checked' : ''} style="margin-top:4px;flex-shrink:0">`
      : '';
    const controls = !(t.has_audio || t.has_video) ? '' : `
      <div style="display:flex;flex-direction:column;gap:4px;flex-shrink:0">
        <button data-seg-play data-start="${sg.start}" data-end="${sg.end}" title="Play this line from the recording" style="${segBtn};color:var(--label-dim)">▶</button>
        ${sg.speaker ? `<button data-seg-seed data-speaker="${escapeHtml(sg.speaker)}" data-start="${sg.start}" data-end="${sg.end}" title="${seeded ? 'Flagged as a voice seed — click to unflag' : 'Flag this line as a voice seed for enrollment'}" style="${segBtn};color:${seeded ? 'var(--nixie)' : 'var(--label-dim)'};${seeded ? 'border-color:var(--nixie);text-shadow:0 0 5px rgba(255,138,61,0.6)' : ''}">◈</button>` : ''}
      </div>`;
    const speakerLabel = sg.speaker
      ? `<span data-seg-rename="${escapeHtml(sg.speaker)}" title="Rename this speaker everywhere" style="font-family:var(--f-cond);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:0.05em;cursor:pointer;border-bottom:1px dotted var(--label-dim)">${escapeHtml(sg.speaker)}</span>`
      : `<span style="font-family:var(--f-cond);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:0.05em">Speaker</span>`;
    const lowConf = isLowConfidence(sg);
    return `
    <div style="display:flex;gap:16px;padding:12px 0;border-bottom:1px solid var(--seg-edge)">
      ${checkbox}
      ${controls}
      <div style="font-family:var(--f-mono);font-size:11px;color:var(--nixie);text-shadow:0 0 4px rgba(255,138,61,0.4);width:44px;flex-shrink:0;padding-top:2px">${formatTime(sg.start)}</div>
      <div style="min-width:0">
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">
          <span style="width:7px;height:7px;border-radius:50%;background:${dot};box-shadow:0 0 4px ${dot}"></span>
          ${speakerLabel}
          ${lowConf ? '<span title="Low-confidence speaker assignment — the diarizer was unsure here" style="font-family:var(--f-mono);font-size:10px;color:var(--nixie);cursor:help">?</span>' : ''}
        </div>
        <div style="font-size:13.5px;line-height:1.55;color:var(--body)">${escapeHtml(sg.text || '')}</div>
      </div>
    </div>`;
  }).join('');
}

/* ── per-line playback, seed flags, speaker rename ── */

function detailBodyClick(e) {
  const copyBtn = e.target.closest('[data-export-copy]');
  const dlBtn = e.target.closest('[data-export-dl]');
  if (copyBtn || dlBtn) { handleExportClick((copyBtn || dlBtn).dataset.exportCopy || (copyBtn || dlBtn).dataset.exportDl, !!copyBtn); return; }
  const assistantBtn = e.target.closest('[data-export-assistant]');
  if (assistantBtn) {
    const t = detailData;
    S._assistantPrefill = "Summarize the transcript '" + (t.title || t.filename || 'transcript') + "' and save as markdown";
    navigate('assistant');
    return;
  }
  const saveBtn = e.target.closest('[data-export-save]');
  if (saveBtn) {
    e.preventDefault();
    const b = saveBtn;
    return withBusy(b, async () => {
      try {
        const result = await api('/api/transcripts/' + detailData.id + '/export-markdown', { method: 'POST' });
        toast('Saved to ' + result.path, 'ok');
      } catch (e) { toast(e.message, 'error'); }
    });
  }
  const play = e.target.closest('[data-seg-play]');
  if (play) { segPlay(play); return; }
  const seed = e.target.closest('[data-seg-seed]');
  if (seed) { toggleSeed(seed); return; }
  const sel = e.target.closest('[data-seg-select]');
  if (sel) {
    const i = Number(sel.dataset.segSelect);
    if (sel.checked) selectedSegments.add(i); else selectedSegments.delete(i);
    const retagBtn = $('retag-selected-btn');
    if (retagBtn) retagBtn.textContent = 'Re-tag selected (' + selectedSegments.size + ')';
    return;
  }
  const ren = e.target.closest('[data-seg-rename]');
  if (ren && !selectMode) { renameSpeaker(ren.dataset.segRename); }
}

function segPlay(btn) {
  const t = detailData;
  const start = parseFloat(btn.dataset.start), end = parseFloat(btn.dataset.end);
  if (t.has_video) return segPlayVideo(btn, t, start, end);
  return segPlayAudio(btn, t, start, end);
}

// Wiring is keyed off the node itself (v._wired) — the node is rebuilt
// on every renderDetail() call (rename, job-poll-tick, tab switch,
// select-mode toggle — anything that re-renders the whole detail page),
// so a transcript-id-based guard would silently no-op after a
// mid-playback re-render (new node, no listeners, but the guard
// variable still says "already set up").
// v._wired resets to undefined on a fresh node automatically (it's a
// new object), so this both survives a re-render AND avoids stacking
// duplicate listeners across repeated clicks on the SAME node between
// re-renders.
function wireSegVideoElement(v) {
  if (v._wired) return;
  v.addEventListener('timeupdate', () => {
    if (v._stopAt != null && v.currentTime >= v._stopAt) v.pause();
  });
  v.addEventListener('pause', () => {
    if (segPlayingBtn) { segPlayingBtn.textContent = '▶'; segPlayingBtn = null; }
    // Clear the stop marker on every pause — unlike the detached
    // segAudio object, this element exposes native controls, and a
    // stale _stopAt would re-pause any user-initiated playback past
    // the last segment's end, making the controls appear broken.
    v._stopAt = null;
  });
  v.addEventListener('error', () => toast('Video failed to load', 'error'));
  v._wired = true;
}

function segPlayVideo(btn, t, start, end) {
  const v = videoFloating ? $('seg-video-floating') : $('seg-video');
  if (!v) return;
  wireSegVideoElement(v);
  if (segPlayingBtn === btn && !v.paused) { v.pause(); return; }
  if (segPlayingBtn) segPlayingBtn.textContent = '▶';
  const seekAndPlay = () => {
    v._stopAt = end;
    v.currentTime = start;
    v.play().catch(err => toast(err.message, 'error'));
  };
  if (v.readyState >= 1) seekAndPlay();
  else v.addEventListener('loadedmetadata', seekAndPlay, { once: true });
  segPlayingBtn = btn;
  btn.textContent = '■';
}

function segPlayAudio(btn, t, start, end) {
  if (!segAudio || segAudioTid !== t.id) {
    if (segAudio) segAudio.pause();
    segAudio = new Audio('/api/transcripts/' + t.id + '/audio');
    segAudioTid = t.id;
    segAudio.addEventListener('timeupdate', () => {
      if (segAudio._stopAt != null && segAudio.currentTime >= segAudio._stopAt) segAudio.pause();
    });
    segAudio.addEventListener('pause', () => {
      if (segPlayingBtn) { segPlayingBtn.textContent = '▶'; segPlayingBtn = null; }
    });
    segAudio.addEventListener('error', () => toast('Audio failed to load', 'error'));
  }
  if (segPlayingBtn === btn && !segAudio.paused) { segAudio.pause(); return; }
  if (segPlayingBtn) segPlayingBtn.textContent = '▶';
  const seekAndPlay = () => {
    segAudio._stopAt = end;
    segAudio.currentTime = start;
    segAudio.play().catch(err => toast(err.message, 'error'));
  };
  // Seeking before metadata arrives gets clamped to 0 — defer to the event.
  if (segAudio.readyState >= 1) seekAndPlay();
  else segAudio.addEventListener('loadedmetadata', seekAndPlay, { once: true });
  segPlayingBtn = btn;
  btn.textContent = '■';
}

/* ── floating video panel ── */

function detachVideo() {
  const src = $('seg-video');
  if (!src) return;
  const dock = $('video-dock');
  const vid = $('seg-video-floating');
  const cur = src.currentTime;
  const paused = src.paused;
  const stopAt = src._stopAt;
  // segPlayingBtn points at a node the upcoming renderDetail() destroys —
  // find its replacement afterward by (start, end), the only stable key
  // segment buttons carry across a re-render.
  const playingStart = (!paused && segPlayingBtn) ? segPlayingBtn.dataset.start : null;
  const playingEnd = (!paused && segPlayingBtn) ? segPlayingBtn.dataset.end : null;
  wireSegVideoElement(vid);
  vid.src = src.src;
  // Seeking before metadata arrives gets clamped to 0 — defer to the event.
  const seekAndPlay = () => {
    vid.currentTime = cur;
    vid._stopAt = stopAt;
    if (!paused) vid.play().catch(function() {});
  };
  if (vid.readyState >= 1) seekAndPlay();
  else vid.addEventListener('loadedmetadata', seekAndPlay, { once: true });
  videoFloating = true;
  videoFloatingTid = detailData ? detailData.id : null;
  dock.style.display = 'block';
  // Show PiP button only when supported
  if (document.pictureInPictureEnabled) $('video-dock-pip').style.display = '';
  renderDetail();
  if (playingStart != null) {
    const newBtn = document.querySelector('[data-seg-play][data-start="' + playingStart + '"][data-end="' + playingEnd + '"]');
    if (newBtn) { segPlayingBtn = newBtn; newBtn.textContent = '■'; }
  }
}

// Hides the dock and stops the floating video without touching the inline
// page — used when the floating video simply no longer applies (transcript
// switch, logout), where restoring playback position onto whatever's about
// to render would be restoring the WRONG video's position.
function closeVideoDock() {
  const vid = $('seg-video-floating');
  if (document.pictureInPictureElement === vid) document.exitPictureInPicture().catch(function() {});
  videoFloating = false;
  videoFloatingTid = null;
  $('video-dock').style.display = 'none';
  if (vid) vid.pause();
}

function reattachVideo() {
  const vid = $('seg-video-floating');
  const cur = vid ? vid.currentTime : 0;
  const paused = vid ? vid.paused : true;
  // Only the SAME transcript's video is safe to restore position onto —
  // if the transcript changed underneath the dock (switch while floating),
  // the freshly-rendered inline video belongs to a different recording.
  const sameTranscript = detailData && detailData.id === videoFloatingTid;
  closeVideoDock();
  renderDetail();
  if (sameTranscript) {
    // Restore position after re-render rebuilds the inline element
    setTimeout(function() {
      var v = $('seg-video');
      if (v) { v.currentTime = cur; if (!paused) v.play().catch(function() {}); }
    }, 150);
  }
}

function togglePiP() {
  var v = $('seg-video-floating');
  if (!v) return;
  if (document.pictureInPictureElement === v) {
    document.exitPictureInPicture();
  } else {
    v.requestPictureInPicture().catch(function() {});
  }
}

function initVideoDockDrag() {
  var dock = $('video-dock');
  var handle = $('video-dock-handle');
  if (!dock || !handle) return;
  var dragging = false, offX, offY;

  handle.addEventListener('mousedown', function(e) {
    if (e.target.closest('button')) return; // don't drag when clicking buttons
    dragging = true;
    offX = e.clientX - dock.getBoundingClientRect().left;
    offY = e.clientY - dock.getBoundingClientRect().top;
    dock.style.cursor = 'grabbing';
    e.preventDefault();
  });

  document.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    dock.style.right = 'auto';
    dock.style.bottom = 'auto';
    dock.style.left = Math.max(0, Math.min(e.clientX - offX, window.innerWidth - dock.offsetWidth)) + 'px';
    dock.style.top = Math.max(0, Math.min(e.clientY - offY, window.innerHeight - 40)) + 'px';
  });

  document.addEventListener('mouseup', function() {
    if (!dragging) return;
    dragging = false;
    dock.style.cursor = '';
  });
}

/* ── seed / enroll ── */

function syncEnrollMarkedBtn() {
  const btn = $('enroll-marked-btn');
  if (!btn) return;
  const has = markedSpeakers().length > 0;
  btn.disabled = !has;
  btn.title = has ? '' : 'Flag a line with the ◈ button first';
}

function toggleSeed(btn) {
  const sp = btn.dataset.speaker;
  const start = parseFloat(btn.dataset.start), end = parseFloat(btn.dataset.end);
  const list = seedClips[sp] = seedClips[sp] || [];
  const i = list.findIndex(c => c.start === start && c.end === end);
  if (i >= 0) list.splice(i, 1); else list.push({ start, end });
  if (!list.length) delete seedClips[sp];
  renderDetailBody(); // rows render flag state straight from seedClips
  syncEnrollMarkedBtn();
}

async function renameSpeaker(speaker) {
  const t = detailData;
  if (!t) return;
  const name = ((await styledPrompt('Rename "' + speaker + '" to:', speaker)) || '').trim();
  if (!name || name === speaker) return;
  try {
    const r = await api('/api/transcripts/' + t.id + '/speakers/rename', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: speaker, to: name }),
    });
    toast('Renamed ' + r.renamed + ' line' + (r.renamed !== 1 ? 's' : '') + ' to ' + name, 'info');
    if (seedClips[speaker]) { seedClips[name] = seedClips[speaker]; delete seedClips[speaker]; }
  } catch (e) { toast(e.message, 'error'); return; }
  await loadTranscriptDetail(t.id, { preserveQuery: true });
}

function markedSpeakers() {
  return Object.keys(seedClips).filter(sp => (seedClips[sp] || []).length);
}

function hasUnlabeledSpeakers(t) {
  return (t.segments || []).some(sg => {
    const sp = (sg.speaker || '').trim();
    return !sp || /^Speaker \d+$/i.test(sp);
  });
}

async function openEnrollMarkedModal() {
  const speakers = markedSpeakers();
  if (!speakers.length) { toast('No clips flagged — use the ◈ button on a line first', 'error'); return; }
  let voices = [];
  try { voices = await api('/api/voices'); } catch { /* picker still works with just "new name" */ }
  const options = voices.map(v => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)}</option>`).join('');
  const speakerOptions = speakers.map(sp => `<option value="${escapeHtml(sp)}">${escapeHtml(sp)} (${seedClips[sp].length} clip${seedClips[sp].length !== 1 ? 's' : ''})</option>`).join('');
  openModal(`
    <h2 class="modal-title">Enroll marked clips</h2>
    <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px">
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px">Flagged speaker</label>
        <select class="inp" id="enroll-marked-speaker" style="font-size:12px;padding:7px 9px">${speakerOptions}</select>
      </div>
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px">Roster name</label>
        <select class="inp" id="enroll-marked-existing" style="font-size:12px;padding:7px 9px">
          <option value="">— New name —</option>${options}
        </select>
        <input class="inp" id="enroll-marked-new" type="text" placeholder="New speaker name" style="font-size:12px;padding:7px 9px;margin-top:6px">
      </div>
    </div>
    <div class="modal-actions">
      <button id="enroll-marked-cancel" class="btn btn--ghost btn--sm">Cancel</button>
      <button id="enroll-marked-go" class="btn btn--amber btn--sm">Enroll</button>
    </div>`);
  $('enroll-marked-cancel').addEventListener('click', closeModal);
  $('enroll-marked-go').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    // Voice-embedding compute — runs 10-20s server-side, spinner keeps it from reading as frozen.
    const sp = $('enroll-marked-speaker').value;
    const existing = $('enroll-marked-existing').value;
    const newName = $('enroll-marked-new').value.trim();
    const name = existing || newName;
    if (!name) { toast('Pick an existing name or type a new one', 'error'); return; }
    const clips = seedClips[sp] || [];
    try {
      const p = await api('/api/transcripts/' + detailData.id + '/enroll-speaker', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, clips }),
      });
      toast('Voice profile "' + p.name + '" now has ' + p.sample_count + ' clip' + (p.sample_count !== 1 ? 's' : ''), 'info');
      toastVoiceWarning(p);
      delete seedClips[sp];
      closeModal();
      renderDetailBody();
      refreshRailChrome();
      syncEnrollMarkedBtn();
    } catch (e) { toast(e.message, 'error'); }
  }, { spinner: true }));
}

async function openRetagModal() {
  if (!selectedSegments.size) { toast('Select at least one line first', 'error'); return; }
  let voices = [];
  try { voices = await api('/api/voices'); } catch { /* picker still works with just "new name" */ }
  const options = voices.map(v => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)}</option>`).join('');
  openModal(`
    <h2 class="modal-title">Re-tag ${selectedSegments.size} line${selectedSegments.size !== 1 ? 's' : ''}</h2>
    <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px">
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px">Correct speaker</label>
        <select class="inp" id="retag-existing" style="font-size:12px;padding:7px 9px">
          <option value="">— New name —</option>${options}
        </select>
        <input class="inp" id="retag-new" type="text" placeholder="New speaker name" style="font-size:12px;padding:7px 9px;margin-top:6px">
      </div>
    </div>
    <div class="modal-actions">
      <button id="retag-cancel" class="btn btn--ghost btn--sm">Cancel</button>
      <button id="retag-go" class="btn btn--amber btn--sm">Re-tag</button>
    </div>`);
  $('retag-cancel').addEventListener('click', closeModal);
  $('retag-go').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const existing = $('retag-existing').value;
    const newName = $('retag-new').value.trim();
    const name = existing || newName;
    if (!name) { toast('Pick an existing name or type a new one', 'error'); return; }
    try {
      const r = await api('/api/transcripts/' + detailData.id + '/segments/retag', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ indices: Array.from(selectedSegments), speaker: name }),
      });
      toast('Re-tagged ' + r.retagged + ' line' + (r.retagged !== 1 ? 's' : ''), 'info');
      selectMode = false;
      selectedSegments = new Set();
      closeModal();
      await loadTranscriptDetail(detailData.id, { preserveQuery: true });
    } catch (e) { toast(e.message, 'error'); }
  }));
}

function llmJobActive(job) {
  return job && (job.status === 'pending' || job.status === 'running');
}

// Snapshot of which jobs are active, keyed by the same fields _jobFingerprint
// tracks. Used to detect a job crossing into or out of "active" between poll
// ticks — the one case that needs a full detail-body rebuild (completed
// content appearing, voice-match relabeling segments, a Format-tab "Suggested"
// badge changing once classify_intent_job lands).
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
    voice_dump: llmJobActive(t.voice_dump_job),
  };
}

// Poll-tick DOM patch: update the status badge, the three job-gated action
// buttons, and any currently-rendered job-running-progress container, all
// in place. Only when a job actually crosses into/out of "active" do we pay
// for a full renderDetailBody() — the segment list and page chrome are never
// touched by this path.
async function updateDetailJobStatus(t, prevActive) {
  const badge = $('detail-status-badge');
  if (badge) {
    const sv = statusView(t);
    badge.className = 'status-badge status-badge--' + sv.word;
    badge.dataset.word = sv.word;
    badge.textContent = sv.word;
  }
  const gatedButtons = [
    // voicematch has a second, permanent disable reason (no stored audio)
    // that never changes during polling — skip it entirely rather than
    // fight over which reason currently owns the disabled state.
    { id: 'btn-voicematch', job: t.voice_match_job, title: 'Voice match job already queued', skip: !t.has_audio },
    { id: 'btn-summarize', job: t.summary_job, title: 'Summary job already queued' },
    { id: 'btn-rerun', job: t.correction_job, title: 'Correction job already queued' },
  ];
  for (const { id: btnId, job, title, skip } of gatedButtons) {
    if (skip) continue;
    const btn = $(btnId);
    if (!btn) continue;
    const active = llmJobActive(job);
    btn.disabled = active;
    btn.title = active ? title : '';
  }
  const runningContainers = [
    { id: 'job-correction', job: t.correction_job, label: 'Correction' },
    { id: 'job-summary', job: t.summary_job, label: 'Summary' },
    { id: 'job-voice-match', job: t.voice_match_job, label: 'Voice match' },
    { id: 'job-format-markdown', job: t.format_markdown_job, label: 'Markdown note' },
    { id: 'job-format-email', job: t.format_email_job, label: 'Email draft' },
    { id: 'job-format-coding_prompt', job: t.format_coding_prompt_job, label: 'Claude Code prompt' },
    { id: 'job-tagging', job: t.tagging_job, label: 'Tagging' },
    { id: 'job-voice-dump', job: t.voice_dump_job, label: 'Voice dump' },
  ];
  for (const { id: containerId, job, label } of runningContainers) {
    if (!llmJobActive(job)) continue;
    const el = $(containerId);
    if (el) el.innerHTML = jobRunningUnit(job, label);
  }
  const newActive = jobActiveSnapshot(t);
  const crossed = Object.keys(prevActive).some(k => prevActive[k] !== newActive[k]);
  if (crossed) await renderDetailBody();
}

function jobRunningUnit(job, label) {
  const total = (job.progress && job.progress.total) || 0;
  const done = job.progress ? job.progress.done : 0;
  const section = total ? ' — section ' + Math.min(done + 1, total) + ' of ' + total : '';
  const text = job.status === 'pending' ? label + ' queued — waiting for a worker slot'
    : label + ' running' + section;
  return `
  <div class="unit" style="padding:20px 32px;display:flex;align-items:center;gap:12px">
    ${ledDot(AMBER, true, 9)}
    <span style="font-family:var(--f-mono);font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:${AMBER}">${escapeHtml(text)}</span>
    <span style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);margin-left:auto">${escapeHtml((job.provider || '') + (job.model ? ' · ' + job.model : ''))}</span>
  </div>`;
}

// Same convention as the manual-identify result box (runIdentify) — one way to
// spell a similarity in this app, not two.
function similarityPct(x) {
  return Math.round((x || 0) * 100) + '%';
}

// How close to the cutoff a match has to sit before it's flagged as thin.
// Not a server-side rule: the backend still applies its own threshold, this
// only decides what gets highlighted.
const LOW_MATCH_MARGIN = 0.05;

// Terminal-state companion to jobRunningUnit for voice match. jobRunningUnit is
// gated on llmJobActive, so it vanishes the moment the job finishes and the
// relabeled speaker names were left with no indication of how confident any of
// them were — a backend that over-matches, collapsing distinct speakers onto
// one profile, looked exactly like a good match (issue #311).
function voiceMatchSummaryUnit(job) {
  if (!job || job.status !== 'completed') return '';
  const r = job.result;
  if (!r || typeof r.considered !== 'number') return '';
  const threshold = typeof r.threshold === 'number' ? r.threshold : 0;
  const speakers = Array.isArray(r.speakers) ? r.speakers : [];
  const chips = speakers.map(s => {
    const thin = s.min_similarity != null && s.min_similarity < threshold + LOW_MATCH_MARGIN;
    // A single-line match has no spread to report, so mean IS the value.
    const detail = s.segments > 1
      ? similarityPct(s.mean_similarity) + ' avg · ' + similarityPct(s.min_similarity) + '–' + similarityPct(s.max_similarity)
      : similarityPct(s.mean_similarity);
    const tip = s.segments + ' line' + (s.segments !== 1 ? 's' : '') + ' relabeled to ' + s.name +
      (thin ? ' — weakest match sits within ' + Math.round(LOW_MATCH_MARGIN * 100) + ' points of the ' + similarityPct(threshold) + ' threshold' : '');
    return '<span title="' + escapeHtml(tip) + '" style="display:inline-flex;align-items:center;gap:7px;font-family:var(--f-mono);font-size:10px;' +
      'padding:3px 9px;border:1px solid ' + (thin ? AMBER : 'var(--panel-lo)') + ';border-radius:10px;background:var(--panel-lo);' +
      'color:' + (thin ? AMBER : 'var(--label)') + '">' +
      '<span>' + escapeHtml(s.name) + '</span>' +
      '<span>' + detail + '</span>' +
      '<span style="color:var(--label-dim)">×' + s.segments + '</span></span>';
  }).join('');
  const headline = r.matched + ' of ' + r.considered + ' line' + (r.considered !== 1 ? 's' : '') +
    ' matched at ' + similarityPct(threshold) + ' or better';
  return '<div class="unit" style="padding:12px 32px;margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
    ledDot(r.matched ? GREEN : AMBER, false, 9) +
    '<span style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim)">Voice match</span>' +
    '<span style="font-size:12.5px;color:var(--body)">' + escapeHtml(headline) + '</span>' +
    chips +
    // job.error doubles as a success-path notes field for this kind (skipped /
    // degraded / unmatchable counts). It reaches the Queue screen already; the
    // detail view had nowhere to show it once the running unit disappeared.
    (job.error ? '<span style="flex-basis:100%;font-family:var(--f-mono);font-size:10px;color:var(--label-dim)">' +
      escapeHtml(humanizeJobError(job.error)) + '</span>' : '') +
    '</div>';
}

/* ══════════════════ run history + diff ══════════════════ */

// Generic LCS-based diff over an array of tokens (words or lines).
// Returns [[type, token], ...] where type is 'eq' | 'del' | 'ins'.
function diffTokens(oldTokens, newTokens) {
  const m = oldTokens.length, n = newTokens.length;
  const dp = Array.from({ length: m + 1 }, () => new Uint32Array(n + 1));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = oldTokens[i] === newTokens[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (oldTokens[i] === newTokens[j]) { ops.push(['eq', oldTokens[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push(['del', oldTokens[i]]); i++; }
    else { ops.push(['ins', newTokens[j]]); j++; }
  }
  while (i < m) { ops.push(['del', oldTokens[i]]); i++; }
  while (j < n) { ops.push(['ins', newTokens[j]]); j++; }
  return ops;
}

// Word-level diff for prose; falls back to line-level on very large inputs —
// LCS is O(m*n), so two 3000-word transcripts already means a 9M-cell table.
// The fallback keeps the compare modal from hanging the page on long audio.
function textDiffHtml(oldText, newText) {
  let oldTok = (oldText || '').split(/(\s+)/);
  let newTok = (newText || '').split(/(\s+)/);
  if (oldTok.length * newTok.length > 4000000) {
    oldTok = (oldText || '').split('\n');
    newTok = (newText || '').split('\n');
  }
  return diffTokens(oldTok, newTok).map(([type, tok]) => {
    const esc = escapeHtml(tok);
    if (type === 'eq') return esc;
    if (type === 'del') return '<del style="background:rgba(255,80,80,.25);text-decoration:line-through">' + esc + '</del>';
    return '<ins style="background:rgba(80,255,120,.25);text-decoration:none">' + esc + '</ins>';
  }).join('');
}

// Diffs two lists of bullet strings after sorting each — avoids pure-reorder
// churn counting as a change. A bullet that's both reordered AND reworded
// still shows as remove+add; accepted approximation, see the design spec.
function bulletListDiffHtml(oldItems, newItems) {
  const oldSorted = [...(oldItems || [])].sort();
  const newSorted = [...(newItems || [])].sort();
  return diffTokens(oldSorted, newSorted).map(([type, item]) => {
    const esc = escapeHtml(item);
    if (type === 'eq') return '<div style="padding:2px 0">' + esc + '</div>';
    if (type === 'del') return '<div style="padding:2px 0;background:rgba(255,80,80,.15);text-decoration:line-through">' + esc + '</div>';
    return '<div style="padding:2px 0;background:rgba(80,255,120,.15)">' + esc + '</div>';
  }).join('');
}

function summaryDiffHtml(oldSummary, newSummary) {
  const sections = [
    ['Summary', textDiffHtml(oldSummary.short_summary || '', newSummary.short_summary || '')],
    ['Key points', bulletListDiffHtml(oldSummary.key_points, newSummary.key_points)],
    ['Action items', bulletListDiffHtml(oldSummary.action_items, newSummary.action_items)],
    ['Decisions', bulletListDiffHtml(oldSummary.decisions, newSummary.decisions)],
  ];
  return sections.map(([title, html]) =>
    '<div style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.05em;margin:10px 0 4px;color:' + AMBER + '">' + escapeHtml(title) + '</div>' + html
  ).join('');
}

// Not a text diff — segments compare structurally by index. If the two
// runs have different segment counts (re-diarization can merge/split
// speaker turns), only the shared prefix is compared and the length
// difference is called out rather than misaligning the rest.
function rediarizeDiffHtml(oldSegments, newSegments) {
  const n = Math.min(oldSegments.length, newSegments.length);
  let relabeled = 0, unchanged = 0;
  const changes = [];
  for (let i = 0; i < n; i++) {
    const o = oldSegments[i], nw = newSegments[i];
    if (o.speaker !== nw.speaker) {
      relabeled++;
      changes.push({ start: nw.start, end: nw.end, from: o.speaker, to: nw.speaker });
    } else {
      unchanged++;
    }
  }
  const lenDiff = newSegments.length - oldSegments.length;
  const header = '<div style="margin-bottom:8px">' + relabeled + ' segment(s) relabeled, ' + unchanged + ' unchanged' +
    (lenDiff ? ', segment count changed by ' + lenDiff : '') + '</div>';
  const rows = changes.map(c =>
    '<div style="padding:3px 0;font-family:var(--f-mono);font-size:12px">' + formatDur(c.start) + '–' + formatDur(c.end) + ': ' + escapeHtml(c.from) + ' → ' + escapeHtml(c.to) + '</div>'
  ).join('');
  return header + rows;
}

// Generic two-way compare modal. `fetchItems` resolves the pickable items
// (each with an `id`, a human `optionLabel`, and a `result` payload that's
// null/falsy when no snapshot exists for that item). `extractText` pulls
// the comparable string out of `result`; `renderDiff` renders the pair.
async function openCompareModal(title, fetchItems, extractText, renderDiff) {
  let items;
  try { items = await fetchItems(); }
  catch (e) { toast(e.message, 'error'); return; }
  if (items.length < 1) { toast('Nothing to compare yet', 'info'); return; }
  const optionHtml = items.map(it => `<option value="${it.id}">${escapeHtml(it.optionLabel)}${it.result ? '' : ' (no snapshot)'}</option>`).join('');
  openModal(`
    <h2 class="modal-title">${escapeHtml(title)}</h2>
    <div style="display:flex;gap:10px;margin-bottom:14px">
      <select id="compare-item-a" class="inp" style="flex:1;min-width:0;font-size:12px;padding:8px 10px">${optionHtml}</select>
      <select id="compare-item-b" class="inp" style="flex:1;min-width:0;font-size:12px;padding:8px 10px">${optionHtml}</select>
    </div>
    <div id="compare-diff-out" style="max-height:50vh;overflow:auto;font-size:13px;line-height:1.6;white-space:pre-wrap;padding:10px;border:1px solid var(--inset-edge)"></div>
    <div style="display:flex;justify-content:flex-end;margin-top:14px">
      <button id="compare-close" class="btn btn--ghost btn--sm">Close</button>
    </div>`);
  const byId = Object.fromEntries(items.map(it => [String(it.id), it]));
  const update = () => {
    const a = byId[$('compare-item-a').value], b = byId[$('compare-item-b').value];
    const out = $('compare-diff-out');
    if (!a.result || !b.result) { out.textContent = 'One or both runs predate history tracking — no snapshot to diff.'; return; }
    out.innerHTML = renderDiff(extractText(a.result), extractText(b.result));
  };
  $('compare-item-a').addEventListener('change', update);
  $('compare-item-b').addEventListener('change', update);
  if (items.length > 1) $('compare-item-b').selectedIndex = 1;
  update();
  $('compare-close').addEventListener('click', closeModal);
}

function transcriptPlainText(t) {
  const lines = (t.segments || [])
    .map(sg => (sg.speaker ? sg.speaker + ': ' : '') + (sg.text || '').trim())
    .filter(Boolean);
  return lines.length ? lines.join('\n') : (t.full_text || '').trim();
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied to clipboard', 'info');
  } catch (e) {
    toast('Copy failed: ' + e.message, 'error');
  }
}

function downloadTextFile(filename, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportToolbarHtml(kind) {
  const t = detailData;
  const openAssistantBtn = t
    ? '<button class="btn" data-export-assistant style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)" title="Open in Assistant">Assistant</button>'
    : '';
  const saveBtn = S.exportDir
    ? '<button class="btn" data-export-save="md" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)" title="Save as Markdown to ' + escapeHtml(S.exportDir) + '">Save as .md</button>'
    : '';
  return '<div style="display:flex;justify-content:flex-end;gap:8px;padding:0 32px 10px">' +
    '<button class="btn" data-export-copy="' + kind + '" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Copy</button>' +
    '<button class="btn" data-export-dl="' + kind + '" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Download .txt</button>' +
    saveBtn +
    openAssistantBtn +
    '</div>';
}

async function summaryPlainText(transcriptId) {
  const s = await api('/api/transcripts/' + transcriptId + '/summary');
  const sections = [
    ['Summary', s.short_summary ? [s.short_summary] : []],
    ['Key points', s.key_points || []],
    ['Action items', s.action_items || []],
    ['Decisions', s.decisions || []],
  ].filter(([, items]) => items.length);
  const text = sections.map(([title, items]) => title + '\n' + items.map(it => '- ' + it).join('\n')).join('\n\n');
  return { text, provider: s.provider, model: s.model };
}

async function handleExportClick(kind, copy) {
  const t = detailData;
  let text = '', header = '';
  if (kind === 'transcript') {
    text = transcriptPlainText(t);
    header = `[transcribed with ${t.provider}/${t.model}]`;
  } else if (kind === 'corrected') {
    text = t.corrected_text || '';
    header = `[${t.provider}/${t.model} · corrected by ${t.correction_model || 'unknown'}]`;
  } else if (kind === 'summary') {
    try {
      const s = await summaryPlainText(t.id);
      text = s.text;
      header = `[summarized with ${s.provider || 'unknown'}/${s.model || 'unknown'}]`;
    }
    catch (e) { toast('Could not load summary to export: ' + e.message, 'error'); return; }
  } else if (kind === 'format_markdown' || kind === 'format_email' || kind === 'format_coding_prompt') {
    try {
      const runs = (await api('/api/transcripts/' + t.id + '/runs/' + kind)).runs;
      const latest = runs.find(r => r.status === 'completed');
      text = (latest && latest.result && latest.result.text) || '';
      header = latest ? `[reformatted with ${latest.provider || 'unknown'}/${latest.model || 'unknown'}]` : '';
    } catch (e) { toast('Could not load result to export: ' + e.message, 'error'); return; }
  }
  if (!text.trim()) { toast('Nothing to export yet', 'info'); return; }
  const fullText = header + '\n\n' + text;
  if (copy) copyToClipboard(fullText);
  else downloadTextFile((t.title || t.filename || 'transcript').replace(/[^\w.-]+/g, '_') + '-' + kind + '.txt', fullText);
}

function correctedHtml(t) {
  if (llmJobActive(t.correction_job)) return '<div id="job-correction">' + jobRunningUnit(t.correction_job, 'Correction') + '</div>';
  if (t.correction_error) {
    return '<div class="unit" style="padding:20px 32px;font-size:13px;color:var(--red)">' +
      '<div class="t-cap" style="color:var(--red);margin-bottom:6px">Correction ' +
      (t.correction_error.startsWith('auto-correct skipped') ? 'skipped' : 'failed') + '</div>' +
      escapeHtml(t.correction_error) + '</div>';
  }
  if (t.corrected_text) {
    const model = t.correction_model
      ? '<div class="t-cap" style="padding:10px 0 4px">Corrected by ' + escapeHtml(t.correction_model) + '</div>'
      : '';
    // The correction pass preserves 'Speaker Name: text' lines — render them
    // like transcript segments. Paragraphs that don't parse fall back to prose.
    const paras = t.corrected_text.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
    const parsed = paras.map(p => {
      const m = p.match(/^([^:\n]{1,60}?):\s+([\s\S]+)$/);
      return m ? { speaker: m[1].trim(), text: m[2].trim() } : null;
    });
    const hits = parsed.filter(Boolean).length;
    if (paras.length && hits / paras.length >= 0.6) {
      const rows = paras.map((p, i) => {
        const seg = parsed[i];
        if (!seg) {
          return '<div style="padding:12px 0;border-bottom:1px solid var(--seg-edge);font-size:13.5px;line-height:1.55;color:var(--body)">' + escapeHtml(p) + '</div>';
        }
        const dot = hashColor(seg.speaker);
        return `
        <div style="display:flex;gap:16px;padding:12px 0;border-bottom:1px solid var(--seg-edge)">
          <div style="min-width:0">
            <div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">
              <span style="width:7px;height:7px;border-radius:50%;background:${dot};box-shadow:0 0 4px ${dot}"></span>
              <span style="font-family:var(--f-cond);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:0.05em">${escapeHtml(seg.speaker)}</span>
            </div>
            <div style="font-size:13.5px;line-height:1.55;color:var(--body)">${escapeHtml(seg.text)}</div>
          </div>
        </div>`;
      }).join('');
      return '<div class="unit" style="border-radius:3px;padding:6px 32px">' + rows + model + '</div>';
    }
    return '<div class="unit" style="padding:20px 32px;font-size:13.5px;line-height:1.6;color:var(--body)">' +
      '<div style="white-space:pre-wrap">' + escapeHtml(t.corrected_text) + '</div>' + model + '</div>';
  }
  return '<div class="empty-unit">Correction pass not run yet — use Re-run correction above' +
    (t.correction_model ? '' : ' (auto-correct was off for this job)') + '</div>';
}

// Dictation-only: reformat the transcript into other useful shapes.
const FORMAT_TARGETS = [
  { key: 'markdown', kind: 'format_markdown', label: 'Markdown note', hint: 'markdown' },
  { key: 'email', kind: 'format_email', label: 'Email draft', hint: 'email' },
  { key: 'coding_prompt', kind: 'format_coding_prompt', label: 'Claude Code prompt', hint: 'coding_prompt' },
];

const NOTE_TYPE_LABELS = {
  todo: 'Todo', idea: 'Idea', reminder: 'Reminder', journal: 'Journal', general: 'Note',
};
const NOTE_TYPE_COLORS = {
  todo: '#FF8A3D',       // nixie amber
  idea: '#7FE0C8',       // cyan
  reminder: '#FFCB6B',   // yellow
  journal: '#C8A6FF',    // violet
  general: '#A9ACAF',    // neutral
};

async function voiceNoteHtml(t) {
  // The chain writes to a VoiceNote row AND a LlmJob result_json. The
  // serializer already exposes voice_note_job.result_json, so we read
  // from there to avoid a follow-up /voice-note fetch — same shape
  // either way (the API endpoint and the serializer populate
  // identical JSON).
  const job = t.voice_note_job;
  const inFlight = job && (job.status === 'pending' || job.status === 'running');
  if (inFlight) {
    return '<div class="unit" style="padding:32px;text-align:center">' +
      '<div class="t-cap" style="font-size:11px;letter-spacing:0.16em;margin-bottom:12px">Voice note · ' +
        escapeHtml(job.progress ? (job.progress.done + ' of ' + job.progress.total) : 'queued') +
      '</div>' +
      '<div style="font-family:var(--f-mono);font-size:11.5px;color:var(--label-dim)">The LLM is figuring out what kind of note this is and writing it up. Watch the Queue screen for live progress.</div>' +
    '</div>';
  }
  if (job && job.status === 'failed') {
    return '<div class="unit" style="padding:32px">' +
      '<div class="t-cap" style="font-size:11px;letter-spacing:0.16em;margin-bottom:12px;color:var(--red)">Voice note chain failed</div>' +
      '<div style="font-family:var(--f-mono);font-size:11.5px;color:var(--label-dim)">' + escapeHtml(job.error || 'unknown error') + '</div>' +
      '<div style="margin-top:14px"><button class="btn" data-dact="rerun-voice-note" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)">Rerun chain</button></div>' +
    '</div>';
  }
  if (!job || !job.result_json) {
    return '<div class="empty-unit">No voice-note result yet. The chain runs automatically after transcription completes.</div>';
  }
  const r = job.result_json;
  const noteType = r.type || 'general';
  const typeLabel = NOTE_TYPE_LABELS[noteType] || noteType;
  const typeColor = NOTE_TYPE_COLORS[noteType] || NOTE_TYPE_COLORS.general;
  const title = r.title || t.title || 'Voice note';
  const body = r.body || '';
  const structured = r.structured || {};
  let structuredHtml = '';
  if (noteType === 'todo' && Array.isArray(structured.items) && structured.items.length) {
    structuredHtml = '<div style="margin-top:14px"><div class="t-cap" style="font-size:10px;letter-spacing:0.14em;margin-bottom:8px">Items</div>' +
      structured.items.map(it => {
        const pri = it.priority || 'medium';
        const priColor = pri === 'high' ? 'var(--red)' : pri === 'low' ? 'var(--label-dim)' : 'var(--nixie)';
        return '<div style="display:flex;gap:10px;align-items:flex-start;padding:6px 0;border-bottom:1px solid var(--seg-edge)">' +
          '<span style="font-size:9px;padding:2px 6px;border-radius:3px;background:' + priColor + ';color:#04140C;text-transform:uppercase;letter-spacing:0.05em;font-family:var(--f-mono);flex-shrink:0;margin-top:2px">' + escapeHtml(pri) + '</span>' +
          '<div style="min-width:0"><div style="font-size:13.5px;line-height:1.5">' + escapeHtml(it.text || '') + '</div>' +
            (it.due_date ? '<div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-top:2px">Due ' + escapeHtml(it.due_date) + '</div>' : '') +
          '</div></div>';
      }).join('') + '</div>';
  } else if (noteType === 'idea') {
    structuredHtml = '<div style="margin-top:14px">' +
      (structured.summary ? '<div style="font-size:13.5px;line-height:1.55">' + escapeHtml(structured.summary) + '</div>' : '') +
      (Array.isArray(structured.tags) && structured.tags.length
        ? '<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">' + structured.tags.map(t => '<span style="font-size:10px;padding:3px 9px;border-radius:9px;background:var(--panel-hi);color:var(--body);border:1px solid var(--inset-edge);text-transform:lowercase;letter-spacing:0.04em;font-family:var(--f-mono)">' + escapeHtml(t) + '</span>').join('') + '</div>'
        : '') +
    '</div>';
  } else if (noteType === 'reminder') {
    structuredHtml = '<div style="margin-top:14px">' +
      (structured.trigger ? '<div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:3px">When</div><div style="font-size:13.5px;line-height:1.5;margin-bottom:10px">' + escapeHtml(structured.trigger) + '</div>' : '') +
      (structured.subject ? '<div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:3px">What</div><div style="font-size:13.5px;line-height:1.5">' + escapeHtml(structured.subject) + '</div>' : '') +
    '</div>';
  } else if (noteType === 'journal') {
    structuredHtml = '<div style="margin-top:14px">' +
      (structured.mood ? '<div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:3px">Mood</div><div style="font-size:13.5px;line-height:1.5;margin-bottom:10px">' + escapeHtml(structured.mood) + '</div>' : '') +
      (Array.isArray(structured.themes) && structured.themes.length
        ? '<div style="display:flex;gap:6px;flex-wrap:wrap">' + structured.themes.map(t => '<span style="font-size:10px;padding:3px 9px;border-radius:9px;background:var(--panel-hi);color:var(--body);border:1px solid var(--inset-edge);text-transform:lowercase;letter-spacing:0.04em;font-family:var(--f-mono)">' + escapeHtml(t) + '</span>').join('') + '</div>'
        : '') +
    '</div>';
  }
  return '<div class="unit" style="padding:24px 32px">' +
    '<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">' +
      '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + typeColor + ';box-shadow:0 0 6px ' + typeColor + '"></span>' +
      '<span style="font-family:var(--f-cond);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:' + typeColor + '">' + escapeHtml(typeLabel) + '</span>' +
      (job.model ? '<span style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);margin-left:auto">' + escapeHtml((job.provider || '') + (job.model ? ' · ' + job.model : '')) + '</span>' : '') +
    '</div>' +
    '<h2 style="font-family:var(--f-cond);font-weight:700;font-size:22px;line-height:1.25;margin:0 0 12px">' + escapeHtml(title) + '</h2>' +
    (body ? '<div style="font-size:14px;line-height:1.6;color:var(--body);white-space:pre-wrap">' + escapeHtml(body) + '</div>' : '') +
    structuredHtml +
    '<div style="margin-top:18px;display:flex;gap:8px;align-items:center">' +
      '<button class="btn" data-dact="rerun-voice-note" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Rerun chain</button>' +
      '<button class="btn btn--red" data-dact="delete-voice-note" style="font-size:11px;padding:6px 12px">Discard note</button>' +
    '</div>' +
  '</div>';
}

/* ── Dump Review tab (voice_dump) ── */

// The edited draft lives here rather than on detailData because it is user
// state, and it has to survive the one renderDetailBody() the poll tick
// fires when the voice_dump job crosses out of "active". `key` pins it to a
// single transcript + job + job status, so a rerun (new job id) or a status
// change re-fetches instead of reusing a stale draft, while a tab switch or
// a save re-render reuses the in-progress edits.
let dumpReview = null;

function dumpReviewKey(t) {
  const j = t.voice_dump_job;
  return t.id + ':' + (j ? j.id : 0) + ':' + (j ? j.status : 'none');
}

// Only ever called for a completed job (see dumpReviewHtml) — a cancelled
// run can carry a committed items payload too, and presenting that as a
// finished draft would be wrong.
async function loadDumpReview(t) {
  const key = dumpReviewKey(t);
  if (dumpReview && dumpReview.key === key) return dumpReview;
  const job = t.voice_dump_job;
  // Draft items live on the job's result_json, which the transcript
  // serializer does NOT expose (serialize_llm_job in services/llm_jobs.py
  // has no result_json key) — /runs/<kind> is the only route that returns
  // it, so read it the same way formatHtml does.
  const runs = (await api('/api/transcripts/' + t.id + '/runs/voice_dump')).runs || [];
  const run = runs.find(r => job && r.id === job.id) || runs.find(r => r.status === 'completed');
  const raw = (run && run.result && run.result.items) || [];
  // There is no "finalized" flag on the job, so the only way to tell an
  // already-committed draft from a pending one is whether VoiceDumpItem
  // rows point back at this specific job.
  let finalized = [];
  if (raw.length) {
    try {
      const rows = (await api('/api/transcripts/' + t.id + '/voice-dump-items')).items || [];
      finalized = rows.filter(r => job && r.source_job_id === job.id);
    } catch { /* cross-check is advisory — fall through to the draft UI */ }
  }
  dumpReview = {
    key,
    state: !raw.length ? 'empty' : (finalized.length ? 'finalized' : 'draft'),
    items: normalizeDumpItems(raw),
    finalizedCount: finalized.length,
  };
  return dumpReview;
}

function dumpDeadEndUnit(caption, message, captionColor) {
  return '<div class="unit" style="padding:32px">' +
    '<div class="t-cap" style="font-size:11px;letter-spacing:0.16em;margin-bottom:12px' + (captionColor ? ';color:' + captionColor : '') + '">' + escapeHtml(caption) + '</div>' +
    '<div style="font-family:var(--f-mono);font-size:11.5px;color:var(--label-dim)">' + escapeHtml(message) + '</div>' +
    '<div style="margin-top:14px"><button class="btn" data-dact="rerun-voice-dump" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)">Rerun chain</button></div>' +
  '</div>';
}

async function dumpReviewHtml(t) {
  const job = t.voice_dump_job;
  if (!job) {
    return '<div class="empty-unit">No voice-dump chain yet. It runs automatically after transcription completes.</div>';
  }
  if (llmJobActive(job)) {
    // Container id matches updateDetailJobStatus's runningContainers entry
    // so the progress line advances in place on every poll tick instead of
    // freezing at whatever it said when the tab was opened.
    return '<div id="job-voice-dump">' + jobRunningUnit(job, 'Voice dump') + '</div>';
  }
  if (job.status === 'failed') {
    return dumpDeadEndUnit('Voice-dump chain failed', humanizeJobError(job.error), 'var(--red)');
  }
  if (job.status === 'cancelled') {
    // A cancelled run can still have written a partial items payload, so
    // gate on status, not on whether items exist — a half-segmented dump
    // is not something to hand the user as a finished draft.
    return dumpDeadEndUnit('Voice-dump chain cancelled', 'The run was cancelled, so its item list may be incomplete. Rerun the chain to get a full draft.');
  }
  let review;
  try { review = await loadDumpReview(t); }
  catch (e) { return '<div class="empty-unit">Could not load dump items: ' + escapeHtml(e.message) + '</div>'; }
  if (review.state === 'empty') {
    return dumpDeadEndUnit('No items found', 'The chain finished but did not split this transcript into any items.');
  }
  if (review.state === 'finalized') {
    const n = review.finalizedCount;
    return '<div class="unit" style="padding:32px">' +
      '<div class="t-cap" style="font-size:11px;letter-spacing:0.16em;margin-bottom:12px">Finalized · ' + n + ' note' + (n !== 1 ? 's' : '') + '</div>' +
      '<div style="font-family:var(--f-mono);font-size:11.5px;color:var(--label-dim)">This dump was already finalized. Reviewing, editing and discarding happen before finalize; the notes now live on the Dump notes board.</div>' +
      '<div style="margin-top:14px"><button class="btn" data-dact="open-dumpnotes" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)">Open Dump notes →</button></div>' +
    '</div>';
  }

  const items = review.items;
  const keptCount = items.filter(it => !it.discarded).length;
  const cards = items.map((it, i) => {
    const typeColor = NOTE_TYPE_COLORS[it.type] || NOTE_TYPE_COLORS.general;
    // An unknown type (the finalize route does not validate against
    // NOTE_TYPES) is offered as an extra option so it round-trips instead
    // of the select falling back to the first entry and the next save
    // silently rewriting it.
    const typeValues = DUMP_NOTE_TYPES.indexOf(it.type) === -1 ? DUMP_NOTE_TYPES.concat([it.type]) : DUMP_NOTE_TYPES;
    const typeOpts = typeValues.map(v =>
      '<option value="' + escapeHtml(v) + '"' + (v === it.type ? ' selected' : '') + '>' + escapeHtml(NOTE_TYPE_LABELS[v] || v) + '</option>').join('');
    const questions = it.clarifying_questions.length
      ? '<div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--seg-edge)">' +
        '<div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:8px">Clarifying questions — answers are appended to the body</div>' +
        it.clarifying_questions.map((q, qi) =>
          '<div style="margin-bottom:8px">' +
            '<div style="font-size:12.5px;line-height:1.45;color:var(--body);margin-bottom:4px">' + escapeHtml(q) + '</div>' +
            '<input class="inp" type="text" data-dfield="answer" data-di="' + i + '" data-dq="' + qi + '" value="' + escapeHtml(it.answers[qi] || '') + '" placeholder="Answer (optional)" style="font-size:12px;width:100%;padding:6px 9px">' +
          '</div>').join('') +
      '</div>'
      : '';
    return '<div class="unit" data-dump-item="' + i + '" style="padding:16px 22px;margin-bottom:10px' + (it.discarded ? ';opacity:0.45' : '') + '">' +
      '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">' +
        '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + typeColor + ';box-shadow:0 0 5px ' + typeColor + ';flex-shrink:0"></span>' +
        '<span style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim)">#' + (i + 1) + '</span>' +
        '<select class="inp" data-dfield="type" data-di="' + i + '" style="font-size:11px;padding:5px 8px;width:130px">' + typeOpts + '</select>' +
        '<label style="display:flex;align-items:center;gap:6px;margin-left:auto;font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.06em;color:var(--label-dim);cursor:pointer">' +
          '<input type="checkbox" data-dfield="discarded" data-di="' + i + '"' + (it.discarded ? ' checked' : '') + '>Discard' +
        '</label>' +
      '</div>' +
      '<input class="inp" type="text" data-dfield="title" data-di="' + i + '" value="' + escapeHtml(it.title) + '" placeholder="Title" style="font-size:14px;width:100%;padding:7px 10px;margin-bottom:8px">' +
      '<textarea class="inp" data-dfield="body" data-di="' + i + '" rows="5" placeholder="Body" style="font-size:13px;width:100%;padding:8px 10px;line-height:1.55;resize:vertical">' + escapeHtml(it.body) + '</textarea>' +
      questions +
    '</div>';
  }).join('');

  return '<div class="unit" style="padding:14px 22px;margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
      '<div class="t-cap" style="font-size:11px;letter-spacing:0.16em">Dump review · ' + items.length + ' item' + (items.length !== 1 ? 's' : '') + '</div>' +
      '<span id="dump-keep-count" style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim)">' + keptCount + ' of ' + items.length + ' will be kept</span>' +
      '<div style="display:flex;gap:8px;margin-left:auto">' +
        '<button class="btn" data-dact="dump-save-draft" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Save draft</button>' +
        '<button class="btn" data-dact="dump-finalize" style="font-size:11px;padding:6px 12px">Finalize</button>' +
      '</div>' +
    '</div>' + cards;
}

// Two-way binding for the Dump Review inputs: every edit writes straight
// back into dumpReview.items, so the draft survives a re-render and Save
// draft / Finalize never have to scrape values back out of the DOM.
function bindDumpReviewFields(root) {
  if (!dumpReview || dumpReview.state !== 'draft') return;
  root.querySelectorAll('[data-dfield]').forEach(el => {
    const evt = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
    el.addEventListener(evt, () => {
      const item = dumpReview.items[Number(el.dataset.di)];
      if (!item) return;
      const field = el.dataset.dfield;
      if (field === 'discarded') {
        item.discarded = el.checked;
        // Patch the card and the counter in place — a full re-render here
        // would drop the user's caret out of whatever field they were in.
        const card = el.closest('[data-dump-item]');
        if (card) card.style.opacity = el.checked ? '0.45' : '';
        const counter = $('dump-keep-count');
        if (counter) {
          const kept = dumpReview.items.filter(x => !x.discarded).length;
          counter.textContent = kept + ' of ' + dumpReview.items.length + ' will be kept';
        }
        return;
      }
      if (field === 'answer') { item.answers[Number(el.dataset.dq)] = el.value; return; }
      item[field] = el.value;
    });
  });
}

async function formatHtml(t) {
  const cards = await Promise.all(FORMAT_TARGETS.map(async (target) => {
    const job = t[target.kind + '_job'];
    const suggested = t.classify_intent_hint === target.hint;
    const badge = suggested
      ? `<span style="margin-left:8px;font-size:9px;padding:2px 7px;border-radius:9px;background:${GREEN};color:#04140C;text-transform:uppercase;letter-spacing:0.05em;font-family:var(--f-mono)">Suggested</span>`
      : '';
    const genBtn = `<button class="btn" data-dact="format-${target.key}" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)" ${llmJobActive(job) ? 'disabled title="Already queued"' : ''}>${job && job.status === 'completed' ? 'Regenerate' : 'Generate'}</button>`;

    let body;
    if (llmJobActive(job)) {
      body = '<div id="job-format-' + target.key + '">' + jobRunningUnit(job, target.label) + '</div>';
    } else if (job && job.status === 'failed') {
      body = '<div style="padding:14px 0;font-size:13px;color:var(--red)">' + escapeHtml(humanizeJobError(job.error)) + '</div>';
    } else if (job && job.status === 'completed') {
      try {
        const runs = (await api('/api/transcripts/' + t.id + '/runs/' + target.kind)).runs;
        const latest = runs.find(r => r.status === 'completed');
        const text = (latest && latest.result && latest.result.text) || '';
        body = text
          ? exportToolbarHtml(target.kind) + '<div style="padding:0 0 6px;font-size:13.5px;line-height:1.6;color:var(--body);white-space:pre-wrap">' + escapeHtml(text) + '</div>'
          : '<div style="padding:14px 0;font-size:13px;color:var(--label-dim)">No result recorded.</div>';
      } catch (e) {
        body = '<div style="padding:14px 0;font-size:13px;color:var(--red)">Could not load result: ' + escapeHtml(e.message) + '</div>';
      }
    } else {
      body = '<div style="padding:14px 0;font-size:13px;color:var(--label-dim)">Not generated yet.</div>';
    }

    return `<div class="unit" style="padding:16px 32px;margin-bottom:10px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <div style="font-family:var(--f-cond);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:0.05em;color:${AMBER}">${escapeHtml(target.label)}${badge}</div>
        ${genBtn}
      </div>
      ${body}
    </div>`;
  }));
  return cards.join('');
}

async function summaryHtml(t) {
  if (llmJobActive(t.summary_job)) return '<div id="job-summary">' + jobRunningUnit(t.summary_job, 'Summary') + '</div>';
  const failedBanner = (t.summary_job && t.summary_job.status === 'failed')
    ? '<div class="unit" style="padding:14px 32px;margin-bottom:10px;font-size:13px;color:var(--red)">' +
      '<div class="t-cap" style="color:var(--red);margin-bottom:6px">Summary failed</div>' +
      escapeHtml(humanizeJobError(t.summary_job.error)) + ' — rerun it from the Queue screen.' +
      (t.has_summary ? ' Showing the last successful summary below.' : '') + '</div>'
    : '';
  if (!t.has_summary) {
    return failedBanner || '<div class="empty-unit">No summary yet — press Summarize above</div>';
  }
  try {
    const s = await api('/api/transcripts/' + t.id + '/summary');
    const cards = [
      { title: 'Summary', items: s.short_summary ? [s.short_summary] : [] },
      { title: 'Key points', items: s.key_points || [] },
      { title: 'Action items', items: s.action_items || [] },
      { title: 'Decisions', items: s.decisions || [] },
    ].filter(c => c.items.length);
    return failedBanner + cards.map(c => `
      <div class="unit" style="padding:16px 32px">
        <div style="font-family:var(--f-cond);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;color:${AMBER}">${escapeHtml(c.title)}</div>
        ${c.items.map(it => `<div style="display:flex;gap:9px;font-size:13px;line-height:1.55;color:var(--body);padding:2px 0"><span style="color:${GREEN}">▪</span><span>${escapeHtml(it)}</span></div>`).join('')}
      </div>`).join('');
  } catch (e) {
    return '<div class="empty-unit">' + escapeHtml(e.message) + '</div>';
  }
}

function fmtCost(n) {
  // n is a float dollar amount; return a human-readable label.
  if (n == null || isNaN(n)) return '—';
  if (n === 0) return 'free';
  return '$' + n.toFixed(2);
}

function detailCostHtml(t) {
  // Build a one-line cost breakdown for the transcript detail metadata block.
  var c = t.cost;
  if (!c) return '';
  var parts = [];

  // STT line — always present
  var sttDur = c.stt.duration_seconds ? ' · ' + formatDur(c.stt.duration_seconds) : '';
  parts.push('<span><b>STT&nbsp;</b><span>' + fmtCost(c.stt.cost) +
    ' · ' + escapeHtml(c.stt.rate_source) + sttDur + '</span></span>');

  // Correction line — show if a correction job ran
  if (t.correction_model || (c.correction && c.correction.rate_source !== 'no completed job')) {
    var crSrc = c.correction ? c.correction.rate_source : '—';
    var crCost = c.correction ? c.correction.cost : 0;
    parts.push('<span><b>Correction&nbsp;</b><span>' + (crCost > 0 ? '$' + crCost.toFixed(2) : '—') +
      ' · ' + escapeHtml(crSrc) + '</span></span>');
  }

  // Summary line — show if a summary exists
  if (t.has_summary || (c.summary && c.summary.rate_source !== 'no completed job')) {
    var sSrc = c.summary ? c.summary.rate_source : '—';
    var sCost = c.summary ? c.summary.cost : 0;
    parts.push('<span><b>Summary&nbsp;</b><span>' + (sCost > 0 ? '$' + sCost.toFixed(2) : '—') +
      ' · ' + escapeHtml(sSrc) + '</span></span>');
  }

  if (!parts.length) return '';
  return '<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--seg-edge);font-family:var(--f-mono);font-size:11px;color:var(--body);display:flex;gap:20px;flex-wrap:wrap">' +
    parts.join('') + '</div>';
}

function renderDetail() {
  const t = detailData;
  if (!t) return;
  const root = $('page-detail');
  const sv = statusView(t);
  const kind = t.kind || 'meeting';
  // voice_note/voice_dump render as "Voice note"/"Voice dump" (the kind
  // value with an underscore reads poorly as a UI label — the user sees
  // the VFD values "VOICE NOTE"/"VOICE DUMP" elsewhere, this is the
  // matching title-case).
  const kindLabel = kind === 'voice_note' ? 'Voice note' : kind === 'voice_dump' ? 'Voice dump' : (kind.charAt(0).toUpperCase() + kind.slice(1));
  const cs = t.classification_status || 'override';
  let classStatusText = '';
  if (cs === 'pending') classStatusText = 'Classifying…';
  else if (cs === 'failed') classStatusText = 'Failed';
  else if (cs === 'uncertain') classStatusText = (t.classification_confidence != null ? Math.round(t.classification_confidence * 100) + '% — uncertain' : 'Uncertain');
  else if (cs === 'success') {
    classStatusText = (t.classification_confidence != null ? Math.round(t.classification_confidence * 100) + '% confidence' : '');
    var prov = t.classification_provenance;
    if (prov && prov.provider && prov.model) classStatusText += ' · ' + escapeHtml(prov.provider) + '/' + escapeHtml(prov.model);
  } else if (cs === 'override') classStatusText = 'Manual override';
  const extraActs = [];
  if (t.status === 'partial')
    extraActs.push('<button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="retry">Retry failed sections</button>');
  if (t.status === 'cancelled')
    extraActs.push('<button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="resume">Resume</button>');
  // src lives in the template attribute (not set imperatively after the
  // fact) because this whole node is destroyed and rebuilt on every
  // renderDetail() call (rename, job-poll-tick, tab switch, select-mode
  // toggle) — a freshly-rebuilt node must be immediately pointed at the
  // right URL with no follow-up JS.
  const videoHtml = t.has_video && !videoFloating
    ? `<div style="margin:0 36px 12px"><video id="seg-video" controls src="/api/transcripts/${t.id}/video" style="display:block;width:100%;max-height:260px;background:#000;border:1px solid var(--inset-edge);border-radius:4px"></video><div style="display:flex;justify-content:flex-end;margin-top:4px"><button id="video-detach-btn" class="btn" style="font-size:11px;padding:3px 10px;border-color:var(--inset-edge)">⤢ Detach</button></div></div>`
    : '';

  if (t.has_video && videoFloating) {
    var dockVid = $('seg-video-floating');
    // renderDetail() runs on far more than detach/reattach — poll ticks,
    // tab switches, select-mode toggles — none of which change the video.
    // Only reassign src (which forces a reload, dropping playback position)
    // when it's actually pointed at a different transcript.
    if (dockVid && videoFloatingTid !== t.id) {
      var wasPaused = dockVid.paused;
      dockVid.src = '/api/transcripts/' + t.id + '/video';
      videoFloatingTid = t.id;
      if (!wasPaused) dockVid.play().catch(function() {});
    }
    $('video-dock').style.display = 'block';
    if (document.pictureInPictureEnabled) $('video-dock-pip').style.display = '';
  }

  root.innerHTML = `
    <div class="page-head page-head--with-actions">
      <h1 class="t-title" style="min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(t.title || t.filename || 'Untitled')}</h1>
      <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;align-items:center">
        ${extraActs.join('')}
        <span style="font-family:var(--f-mono);font-size:9px;color:var(--label-faint);text-transform:uppercase;letter-spacing:0.06em">Transcribe</span>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="retranscribe" ${t.has_audio ? '' : 'disabled title="No stored audio for this transcript"'}>Re-transcribe</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="compare-versions">Compare versions</button>
        ${t.kind === 'dictation' ? '' : `
        <span style="font-family:var(--f-mono);font-size:9px;color:var(--label-faint);text-transform:uppercase;letter-spacing:0.06em">Diarize</span>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rediarize" ${t.has_audio ? '' : 'disabled title="No stored audio for this transcript"'}>Re-diarize</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rediarize-history">Rediarize history</button>
        `}
        <span style="font-family:var(--f-mono);font-size:9px;color:var(--label-faint);text-transform:uppercase;letter-spacing:0.06em">Voice</span>
        <button id="btn-voicematch" class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="voicematch" ${!t.has_audio ? 'disabled title="No stored audio for this transcript"' : (llmJobActive(t.voice_match_job) ? 'disabled title="Voice match job already queued"' : '')}>Match against voice roster</button>
        ${t.last_relabel ? `<button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="relabel-undo" title="${escapeHtml(t.last_relabel.description || '')}">Undo relabel</button>` : ''}
        <span style="font-family:var(--f-mono);font-size:9px;color:var(--label-faint);text-transform:uppercase;letter-spacing:0.06em">Correction</span>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="context">Add context</button>
        <button id="btn-summarize" class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="summarize" ${llmJobActive(t.summary_job) ? 'disabled title="Summary job already queued"' : ''}>Summarize</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="summary-history">Summary history</button>
        <button id="btn-rerun" class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rerun" ${llmJobActive(t.correction_job) ? 'disabled title="Correction job already queued"' : ''}>Re-run correction</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="correction-history">Correction history</button>
        <span style="display:inline-block;width:1px;height:24px;background:var(--edge);margin:0 4px;align-self:center"></span>
        <button class="btn btn--red" style="font-size:12px;padding:7px 14px" data-dact="delete">Delete</button>
      </div>
    </div>
    <div id="rerun-picker" style="display:none;margin:0 36px 14px"></div>
    <div id="retranscribe-picker" style="display:none;margin:0 36px 14px"></div>
    <div id="rediarize-picker" style="display:none;margin:0 36px 14px"></div>
    <div id="context-picker" style="display:none;margin:0 36px 14px"></div>
    <div class="unit" style="border-radius:3px;margin-bottom:14px;padding:14px 22px 14px 34px">
      <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:14px">
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Duration</div>${formatDur(t.duration_seconds)}</div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Provider</div>${escapeHtml((t.provider || '—') + (t.model ? ' · ' + t.model : ''))}</div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Status</div><span id="detail-status-badge" class="status-badge status-badge--${escapeHtml(sv.word)}" data-word="${escapeHtml(sv.word)}">${escapeHtml(sv.word)}</span></div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Speakers</div>${t.speaker_count || '—'}${t.diarization_method ? ` <span style="font-size:10px;color:var(--label-dim)">${escapeHtml(t.diarization_method)}${t.num_speakers ? '' : ' (auto)'}</span>` : ''}${(() => { const u = (t.segments || []).filter(isLowConfidence).length; return u ? ` <span style="font-size:10px;color:var(--nixie)" title="Lines where the speaker assignment is uncertain">${u} uncertain</span>` : ''; })()}</div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Segments</div>${(t.segments || []).length}</div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Mode</div><button class="btn" style="font-size:11px;padding:2px 10px;border-color:var(--inset-edge);cursor:pointer" title="Switch mode — explicit selection overrides auto-classification" data-dact="toggle-kind">${escapeHtml(kindLabel)}</button>${classStatusText ? ' <span style="font-size:10px;color:var(--label-dim)">' + escapeHtml(classStatusText) + '</span>' : ''}</div>
      </div>
      ${detailCostHtml(t)}
    </div>
    ${videoHtml}
    <div style="display:flex;gap:6px;align-items:flex-end;flex-wrap:wrap;margin-bottom:14px;padding:0 36px">
      ${detailTabsHtml()}
      <button id="enroll-marked-btn" class="btn" style="margin-left:auto;font-size:11px;padding:6px 12px;border-color:var(--inset-edge)" ${markedSpeakers().length ? '' : 'disabled title="Flag a line with the ◈ button first"'}>Enroll marked clips</button>
      <button id="select-mode-btn" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">${selectMode ? 'Cancel select' : 'Select lines…'}</button>
      ${selectMode ? `<button id="retag-selected-btn" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Re-tag selected (${selectedSegments.size})</button>` : ''}
      <input id="detail-search" class="inp" type="text" placeholder="Search transcript…" value="${escapeHtml(S.query)}" style="font-size:12px;width:220px;padding:8px 10px">
    </div>
    <div id="detail-body"></div>`;

  renderDetailBody();

  root.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => {
    S.detailTab = b.dataset.tab;
    renderDetail();
  }));
  $('detail-search').addEventListener('input', () => {
    S.query = $('detail-search').value;
    if (S.detailTab !== 'transcript') { S.detailTab = 'transcript'; renderDetail(); $('detail-search').focus(); }
    else renderDetailBody();
  });
  const enrollMarkedBtn = $('enroll-marked-btn');
  if (enrollMarkedBtn) enrollMarkedBtn.addEventListener('click', openEnrollMarkedModal);
  const selectModeBtn = $('select-mode-btn');
  if (selectModeBtn) selectModeBtn.addEventListener('click', () => {
    selectMode = !selectMode;
    if (!selectMode) selectedSegments.clear();
    renderDetail();
  });
  const retagBtn = $('retag-selected-btn');
  if (retagBtn) retagBtn.addEventListener('click', openRetagModal);
  root.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact, b)));
  // Delegated: segment rows re-render on search/poll, the container doesn't.
  $('detail-body').addEventListener('click', detailBodyClick);
  // Detach button is rebuilt on every render — wire it each time.
  var detachBtn = $('video-detach-btn');
  if (detachBtn) detachBtn.addEventListener('click', detachVideo);
}

async function renderDetailBody() {
  const t = detailData;
  const body = $('detail-body');
  if (S.detailTab === 'transcript') {
    const vm = llmJobActive(t.voice_match_job) ? '<div id="job-voice-match">' + jobRunningUnit(t.voice_match_job, 'Voice match') + '</div>' : '';
    // Deliberately not folded into `vm`: the nudge below is gated on `!vm`, and
    // a finished run that matched nothing must still offer "Match now".
    const vmDone = vm ? '' : voiceMatchSummaryUnit(t.voice_match_job);
    let nudge = '';
    if (!vm && t.kind !== 'dictation' && t.has_audio && hasUnlabeledSpeakers(t)) {
      try {
        const voices = await api('/api/voices');
        if (voices.length) {
          nudge = '<div class="unit" style="padding:12px 32px;margin-bottom:10px;font-size:13px;color:var(--body);display:flex;align-items:center;justify-content:space-between;gap:12px">' +
            '<span>' + voices.length + ' enrolled voice' + (voices.length !== 1 ? 's' : '') + ' might match unlabeled speakers here.</span>' +
            '<button class="btn" data-dact="voicematch" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Match now</button></div>';
        }
      } catch { /* roster fetch failing is non-fatal — just skip the nudge */ }
    }
    const tags = Array.isArray(t.tags) ? t.tags : [];
    const tagRow = tags.length
      ? '<div class="unit" style="padding:10px 32px;margin-bottom:10px;font-size:12.5px;color:var(--body);display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
        '<span style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim)">Tags</span>' +
        tags.map(tag => `<span style="display:inline-block;font-family:var(--f-mono);font-size:10px;padding:3px 9px;border:1px solid var(--panel-lo);border-radius:10px;background:var(--panel-lo);color:var(--label);text-transform:lowercase;letter-spacing:0.02em">${escapeHtml(tag)}</span>`).join('') +
        '</div>'
      : '';
    body.innerHTML = vm + vmDone + nudge + tagRow + exportToolbarHtml('transcript') + '<div class="unit" style="border-radius:3px;margin-top:' + (vm || vmDone || nudge ? '10px' : '0') + ';padding:6px 32px">' + segmentsHtml(t) + '</div>';
    body.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact, b)));
  } else if (S.detailTab === 'corrected') {
    body.innerHTML = (t.corrected_text ? exportToolbarHtml('corrected') : '') + correctedHtml(t);
  } else if (S.detailTab === 'format' && t.kind === 'dictation') {
    body.innerHTML = '<div class="empty-unit">Loading…</div>';
    body.innerHTML = await formatHtml(t);
    body.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact, b)));
  } else if (S.detailTab === 'format') {
    // Stale S.detailTab from a previously-open dictation transcript, left
    // over on a meeting transcript — loadTranscriptDetail resets this on
    // navigation, but render defensively here too rather than show live
    // Generate buttons for a feature this transcript doesn't support.
    body.innerHTML = '<div class="empty-unit">Not available for meeting transcripts</div>';
  } else if (S.detailTab === 'notes' && t.kind === 'voice_note') {
    body.innerHTML = '<div class="empty-unit">Loading voice note…</div>';
    body.innerHTML = await voiceNoteHtml(t);
  } else if (S.detailTab === 'notes') {
    body.innerHTML = '<div class="empty-unit">Not available for non-voice-note transcripts</div>';
  } else if (S.detailTab === 'review' && t.kind === 'voice_dump') {
    body.innerHTML = '<div class="empty-unit">Loading dump items…</div>';
    body.innerHTML = await dumpReviewHtml(t);
    // #detail-body's delegated click handler only dispatches export and
    // segment buttons, so [data-dact] has to be bound here — renderDetail's
    // own pass ran before this async branch filled the body.
    body.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact, b)));
    bindDumpReviewFields(body);
  } else if (S.detailTab === 'review') {
    body.innerHTML = '<div class="empty-unit">Not available for non-voice-dump transcripts</div>';
  } else {
    body.innerHTML = '<div class="empty-unit">Loading summary…</div>';
    body.innerHTML = (t.has_summary ? exportToolbarHtml('summary') : '') + await summaryHtml(t);
  }
}

async function detailAction(act, btn) {
  const t = detailData;
  if (!t) return;
  const opts = (act === 'summarize' || act === 'voicematch' || act.startsWith('format-')) ? { spinner: true } : {};
  return withBusy(btn, async () => {
  try {
    if (act === 'delete') {
      if (!(await styledConfirm('Delete this transcript permanently?'))) return;
      await api('/api/transcripts/' + t.id, { method: 'DELETE' });
      toast('Transcript deleted');
      navigate('transcripts');
      return;
    }
    if (act === 'toggle-kind') {
      // 3-state cycle: meeting → dictation → voice_note → meeting.
      // The PATCH endpoint guards against a kind change while the
      // transcript is still processing (it reads kind mid-pipeline to
      // decide diarization), so a status==='processing' transcript
      // would 400 here — that's intentional, the user has to wait
      // for the current job to settle.
      const newKind = t.kind === 'meeting' ? 'dictation' : t.kind === 'dictation' ? 'voice_note' : 'meeting';
      try {
        await api('/api/transcripts/' + t.id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind: newKind }),
        });
      } catch (e) {
        if (e.message && e.message.includes('400')) {
          toast('Wait for the current job to finish before changing kind', 'info');
        } else {
          throw e;
        }
        return;
      }
      toast('Switched to ' + (newKind === 'voice_note' ? 'voice note' : newKind) + ' mode', 'info');
      S.detailTab = newKind === 'voice_note' ? 'notes' : 'transcript';
      await loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'rerun-voice-note') {
      // Re-enqueue the chain with the same provider/model the
      // previous job used. The VoiceNote row is overwritten in place
      // when the chain completes.
      const j = t.voice_note_job;
      const provider = (j && j.provider) || 'groq';
      const model = (j && j.model) || '';
      const form = new FormData();
      form.append('provider', provider);
      form.append('model', model);
      await api('/api/transcripts/' + t.id + '/voice-note/rerun', { method: 'POST', body: form });
      toast('Voice-note chain re-queued', 'info');
      await loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'rerun-voice-dump') {
      // Only offered from the dead-end states (failed, cancelled, zero
      // items) — never from a completed draft or an already-finalized one.
      // A rerun replaces the job's result_json wholesale, so offering it
      // after finalize would orphan the notes the user already committed
      // and let a second finalize insert duplicate rows.
      const j = t.voice_dump_job;
      const form = new FormData();
      form.append('provider', (j && j.provider) || 'groq');
      form.append('model', (j && j.model) || '');
      await api('/api/transcripts/' + t.id + '/voice-dump/rerun', { method: 'POST', body: form });
      dumpReview = null;
      toast('Voice-dump chain re-queued', 'info');
      await loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'dump-save-draft') {
      if (!dumpReview || dumpReview.state !== 'draft') return;
      const payload = materializeDumpItems(dumpReview.items, t.voice_dump_job);
      // The route takes the bare item array as the body, not an {items:...}
      // envelope, and echoes back what it stored.
      const saved = await api('/api/transcripts/' + t.id + '/voice-dump/save-draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      // Re-seed from what was stored: the answered clarifying questions are
      // now part of the body and gone from the question list, so saving
      // again can't append the same answer a second time.
      dumpReview.items = normalizeDumpItems(saved.items || payload);
      toast('Draft saved', 'ok');
      await renderDetailBody();
      return;
    }
    if (act === 'dump-finalize') {
      if (!dumpReview || dumpReview.state !== 'draft') return;
      const payload = materializeDumpItems(dumpReview.items, t.voice_dump_job);
      const keep = payload.filter(it => !it.discarded);
      if (!keep.length) { toast('Every item is marked discard — nothing to finalize', 'info'); return; }
      if (!(await styledConfirm('Finalize ' + keep.length + ' item' + (keep.length !== 1 ? 's' : '') + ' as notes? Items marked discard are dropped.'))) return;
      const res = await api('/api/transcripts/' + t.id + '/voice-dump/finalize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const made = (res.items || []).length;
      dumpReview = null;
      toast('Finalized ' + made + ' note' + (made !== 1 ? 's' : ''), 'ok');
      navigate('dumpnotes');
      return;
    }
    if (act === 'open-dumpnotes') {
      navigate('dumpnotes');
      return;
    }
    if (act === 'delete-voice-note') {
      if (!(await styledConfirm('Discard this voice note? The transcript stays.'))) return;
      const list = await api('/api/voice-notes');
      const match = (list.voice_notes || []).find(n => n.transcript_id === t.id);
      if (match) {
        await api('/api/voice-notes/' + match.id, { method: 'DELETE' });
        toast('Voice note discarded');
      } else {
        toast('No voice-note row to discard', 'info');
      }
      await loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'retry') {
      const r = await api('/api/transcripts/' + t.id + '/retry-failed-chunks', { method: 'POST' });
      toast('Retrying ' + r.retried + ' sections', 'info');
      loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'resume') {
      const r = await api('/api/transcripts/' + t.id + '/resume', { method: 'POST' });
      toast('Resumed ' + r.resumed + ' sections', 'info');
      loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'summarize') {
      let settings = {};
      try { settings = await api('/api/settings'); } catch { /* backend defaults apply */ }
      const fd = new FormData();
      fd.append('provider', settings.summary_provider || 'groq');
      fd.append('model', settings.summary_model || 'llama-3.3-70b-versatile');
      await api('/api/transcripts/' + t.id + '/summarize', { method: 'POST', body: fd });
      toast('Summary queued — progress shows on the Summary tab and the Queue screen', 'info');
      S.detailTab = 'summary';
      refreshQueueBadge(true);
      await loadTranscriptDetail(t.id);
      return;
    }
    if (act.startsWith('format-')) {
      const target = act.slice('format-'.length); // markdown | email | coding_prompt
      let settings = {};
      try { settings = await api('/api/settings'); } catch { /* backend defaults apply */ }
      const fd = new FormData();
      fd.append('provider', settings.format_provider || 'local_llm');
      fd.append('model', settings.format_model || '');
      await api('/api/transcripts/' + t.id + '/format/' + target, { method: 'POST', body: fd });
      toast('Generating — progress shows on this tab and the Queue screen', 'info');
      S.detailTab = 'format';
      refreshQueueBadge(true);
      await loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'rerun') {
      toggleRerunPicker();
      return;
    }
    if (act === 'correction-history') {
      await openCompareModal(
        'Compare correction runs',
        async () => {
          const runs = (await api('/api/transcripts/' + t.id + '/runs/correction')).runs;
          return runs.filter(r => r.status === 'completed').map(r => ({
            id: r.id,
            optionLabel: (r.provider || '—') + (r.model ? '/' + r.model : '') + ' · ' + timeAgo(r.created_at),
            result: r.result,
          }));
        },
        result => result.corrected_text || '',
        textDiffHtml,
      );
      return;
    }
    if (act === 'summary-history') {
      await openCompareModal(
        'Compare summary runs',
        async () => {
          const runs = (await api('/api/transcripts/' + t.id + '/runs/summary')).runs;
          return runs.filter(r => r.status === 'completed').map(r => ({
            id: r.id,
            optionLabel: (r.provider || '—') + (r.model ? '/' + r.model : '') + ' · ' + timeAgo(r.created_at),
            result: r.result,
          }));
        },
        result => result,
        summaryDiffHtml,
      );
      return;
    }
    if (act === 'rediarize-history') {
      await openCompareModal(
        'Compare rediarize runs',
        async () => {
          const runs = (await api('/api/transcripts/' + t.id + '/runs/rediarize')).runs;
          return runs.filter(r => r.status === 'completed').map(r => ({
            id: r.id,
            optionLabel: timeAgo(r.created_at),
            result: r.result,
          }));
        },
        result => result.segments || [],
        rediarizeDiffHtml,
      );
      return;
    }
    if (act === 'relabel-undo') {
      const res = await api('/api/transcripts/' + t.id + '/relabel-undo', { method: 'POST' });
      toast('Undid ' + (res.description || res.undone), 'info');
      await loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'compare-versions') {
      await openCompareModal(
        'Compare transcript versions',
        async () => {
          const versions = (await api('/api/transcripts/' + t.id + '/versions')).versions;
          return versions.map(v => ({
            id: v.id,
            optionLabel: (v.provider || '—') + (v.model ? '/' + v.model : '') + ' · ' + timeAgo(v.created_at)
              + (v.status === 'completed' ? '' : ' (' + v.status + ')'),
            result: v.status === 'completed' ? { full_text: v.full_text } : null,
          }));
        },
        result => result.full_text || '',
        textDiffHtml,
      );
      return;
    }
    if (act === 'retranscribe') {
      toggleRetranscribePicker();
      return;
    }
    if (act === 'rediarize') {
      toggleRediarizePicker();
      return;
    }
    if (act === 'voicematch') {
      await runVoiceMatch();
      return;
    }
    if (act === 'context') {
      toggleContextPicker();
      return;
    }
  } catch (e) { toast(e.message, 'error'); }
  }, opts);
}

const LLM_PROVIDERS = [
  { id: 'groq', name: 'Groq' },
  { id: 'openai', name: 'OpenAI' },
  { id: 'openrouter', name: 'OpenRouter' },
  { id: 'local_llm', name: 'Local / Custom' },
];

// Old stored settings may still say 'local' (the pre-split id, shared with
// the transcription provider) — normalize on read so the <select> doesn't
// land on a value with no matching option.
function normalizeLlmProvider(id) {
  return id === 'local' ? 'local_llm' : id;
}

// Populate a model <select> from the curated catalog (labels carry live
// pricing for OpenRouter). local_llm fetches live models from the configured
// endpoint and shows a dropdown; free text remains the fallback when the
// endpoint is unconfigured or unreachable.
async function fillModelPicker(selectId, textId, provider, preferred) {
  const sel = $(selectId), txt = $(textId);
  const isLocal = provider === 'local_llm';
  sel.style.display = isLocal ? 'none' : '';
  txt.style.display = isLocal ? '' : 'none';
  if (isLocal) {
    // Fetch models from the local endpoint; fall back to free text when the
    // endpoint is unconfigured/unreachable (backend returns an empty list).
    sel.innerHTML = '<option>Loading…</option>';
    try {
      const r = await api('/api/correction-models/local_llm');
      const models = r.models || [];
      if (models.length) {
        // A previously saved custom id may not be in the live list; keep it
        // selectable rather than silently swapping to the first live model.
        if (preferred && !models.some(m => m.id === preferred)) {
          models.unshift({ id: preferred, label: preferred + ' (saved)' });
        }
        sel.style.display = '';
        txt.style.display = 'none';
        sel.innerHTML = models.map(m =>
          '<option value="' + escapeHtml(m.id) + '">' + escapeHtml(m.label || m.id) + '</option>').join('');
        if (preferred) sel.value = preferred;
        return;
      }
    } catch { /* fall through to free text */ }
    if (preferred) txt.value = preferred;
    return;
  }
  sel.innerHTML = '<option>Loading…</option>';
  try {
    const r = await api('/api/correction-models/' + provider);
    const models = r.models || [];
    sel.innerHTML = models.map(m =>
      '<option value="' + escapeHtml(m.id) + '">' + escapeHtml(m.label || m.id) + '</option>').join('')
      || '<option value="">No models listed</option>';
    if (preferred && models.some(m => m.id === preferred)) sel.value = preferred;
  } catch (e) {
    sel.innerHTML = '<option value="">' + escapeHtml(e.message) + '</option>';
  }
}

function llmPickerValue(selectId, textId, provider) {
  // local_llm shows either the live-model dropdown or the free-text input
  // (fillModelPicker decides); read whichever control is visible.
  if (provider === 'local_llm' && $(selectId).style.display === 'none') {
    return $(textId).value.trim();
  }
  return $(selectId).value;
}

async function toggleRerunPicker() {
  const box = $('rerun-picker');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = '<div class="unit" style="padding:12px 34px;font-size:12px;color:var(--label-dim)">Loading…</div>';
  let settings = {};
  try { settings = await api('/api/settings'); } catch { /* defaults below */ }
  const prov = normalizeLlmProvider(settings.correction_provider || 'groq');
  box.innerHTML = `
    <div class="unit" style="padding:12px 34px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span class="t-unit">Correction pass</span>
      <select id="rerun-provider" class="inp" style="padding:6px 8px;font-size:12px">
        ${LLM_PROVIDERS.map(p => '<option value="' + p.id + '"' + (p.id === prov ? ' selected' : '') + '>' + p.name + '</option>').join('')}
      </select>
      <select id="rerun-model" class="inp" style="padding:6px 8px;font-size:12px;min-width:230px"></select>
      <input id="rerun-model-text" class="inp" style="padding:6px 8px;font-size:12px;width:230px;display:none" placeholder="model served by your endpoint" title="Model name your local endpoint serves">
      <button id="rerun-go" class="btn btn--amber" style="font-size:12px;padding:7px 14px">Run correction</button>
    </div>`;
  await fillModelPicker('rerun-model', 'rerun-model-text', prov, settings.correction_model);
  $('rerun-provider').addEventListener('change', () =>
    fillModelPicker('rerun-model', 'rerun-model-text', $('rerun-provider').value, ''));
  $('rerun-go').addEventListener('click', (e) => withBusy(e.currentTarget, rerunCorrection));
}

async function rerunCorrection() {
  const t = detailData;
  const provider = $('rerun-provider').value;
  const model = llmPickerValue('rerun-model', 'rerun-model-text', provider);
  if (!model) { toast('Pick a model first', 'error'); return; }
  const fd = new FormData();
  fd.append('provider', provider);
  fd.append('model', model);
  try {
    await api('/api/transcripts/' + t.id + '/correct', { method: 'POST', body: fd });
    toast('Correction queued — progress shows on the Corrected tab and the Queue screen', 'info');
    $('rerun-picker').style.display = 'none';
    S.detailTab = 'corrected';
    refreshQueueBadge(true);
    await loadTranscriptDetail(t.id);
  } catch (e) { toast(e.message, 'error'); }
}

/* ── post-hoc reprocess pickers (re-transcribe / re-diarize / context) ── */

async function toggleRetranscribePicker() {
  const box = $('retranscribe-picker');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = '<div class="unit" style="padding:12px 34px;font-size:12px;color:var(--label-dim)">Loading…</div>';
  let provs = [];
  try { provs = await api('/api/providers'); } catch (e) { toast(e.message, 'error'); }
  const usable = provs.filter(p => !p.needs_key || p.configured);
  box.innerHTML = `
    <div class="unit" style="padding:12px 34px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span class="t-unit">Re-transcribe with</span>
      <select id="retx-provider" class="inp" style="padding:6px 8px;font-size:12px">
        ${usable.map(p => '<option value="' + escapeHtml(p.id) + '">' + escapeHtml(p.name) + '</option>').join('')}
      </select>
      <select id="retx-model" class="inp" style="padding:6px 8px;font-size:12px;min-width:230px"></select>
      <button id="retx-go" class="btn btn--amber" style="font-size:12px;padding:7px 14px">Run</button>
      <span style="font-size:11px;color:var(--label-dim)">Creates a new transcript — this one stays untouched.</span>
    </div>`;
  const fillModels = async () => {
    const sel = $('retx-model');
    sel.innerHTML = '<option value="">Loading…</option>';
    try {
      const r = await api('/api/providers/' + $('retx-provider').value + '/models');
      const models = (r.models || []).map(m => typeof m === 'string' ? m : (m.id || m.name)).filter(Boolean);
      sel.innerHTML = models.map(m => '<option value="' + escapeHtml(m) + '">' + escapeHtml(m) + '</option>').join('')
        || '<option value="">Provider default</option>';
    } catch { sel.innerHTML = '<option value="">Provider default</option>'; }
  };
  await fillModels();
  $('retx-provider').addEventListener('change', fillModels);
  $('retx-go').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const t = detailData;
    const fd = new FormData();
    fd.append('provider', $('retx-provider').value);
    const model = $('retx-model').value;
    if (model) fd.append('model', model);
    try {
      const nt = await api('/api/transcripts/' + t.id + '/retranscribe', { method: 'POST', body: fd });
      toast('Re-transcription started — opened the new transcript', 'info');
      refreshQueueBadge(true);
      await loadTranscriptDetail(nt.id);
    } catch (e) { toast(e.message, 'error'); }
  }));
}

async function toggleRediarizePicker() {
  const box = $('rediarize-picker');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  const t = detailData;
  box.innerHTML = `
    <div class="unit" style="padding:12px 34px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span class="t-unit">Re-diarize</span>
      <input id="rediar-speakers" class="inp" type="number" min="1" max="20" placeholder="auto"
             value="${t && t.num_speakers ? t.num_speakers : ''}"
             title="Number of speakers — clear the field to let pyannote auto-detect (auto-detect tends to over-split)" style="padding:6px 8px;font-size:12px;width:90px">
      <button id="rediar-go" class="btn btn--amber" style="font-size:12px;padding:7px 14px">Run</button>
      <span style="font-size:11px;color:var(--label-dim)">Updates speaker labels in place; re-run correction afterwards if you use the corrected text.</span>
    </div>`;
  $('rediar-go').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const t = detailData;
    const fd = new FormData();
    const n = $('rediar-speakers').value.trim();
    if (n) fd.append('num_speakers', n);
    try {
      await api('/api/transcripts/' + t.id + '/rediarize', { method: 'POST', body: fd });
      toast('Re-diarization queued — watch the Queue screen', 'info');
      box.style.display = 'none';
      refreshQueueBadge(true);
      await loadTranscriptDetail(t.id);
    } catch (e) { toast(e.message, 'error'); }
  }));
}

async function runVoiceMatch() {
  const t = detailData;
  if (!t) return;
  try {
    await api('/api/transcripts/' + t.id + '/voice-match', { method: 'POST' });
    toast('Matching against voice roster…', 'info');
    await loadTranscriptDetail(t.id, { preserveQuery: true });
  } catch (e) { toast(e.message, 'error'); }
}

async function toggleContextPicker() {
  const box = $('context-picker');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = `
    <div class="unit" style="padding:12px 34px;display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap">
      <span class="t-unit" style="padding-top:7px">Meeting context</span>
      <textarea id="ctx-doc" class="inp" rows="3" style="padding:7px 9px;flex:1;min-width:280px"
                placeholder="Paste the agenda or jargon-heavy notes — names and terms get added to your term glossary."></textarea>
      <button id="ctx-go" class="btn btn--amber" style="font-size:12px;padding:7px 14px">Extract terms</button>
    </div>`;
  $('ctx-go').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const t = detailData;
    const doc = $('ctx-doc').value.trim();
    if (!doc) { toast('Paste some context first', 'error'); return; }
    const fd = new FormData();
    fd.append('context_doc', doc);
    try {
      const r = await api('/api/transcripts/' + t.id + '/context', { method: 'POST', body: fd });
      const n = (r.terms || []).length;
      toast(n ? 'Added ' + n + ' term' + (n !== 1 ? 's' : '') + ' to your glossary' : 'No new terms found', 'info');
      box.style.display = 'none';
      // Applying the new terms takes a correction re-run — open that picker.
      if (n && $('rerun-picker').style.display === 'none') toggleRerunPicker();
    } catch (e) { toast(e.message, 'error'); }
  }, { spinner: true }));
}

/* ══════════════════ voice roster ══════════════════ */
let expandedVoice = null; // profile id currently showing its clip list
let clipAudio = null;
async function loadVoices() {
  const root = $('page-voices');
  let voices;
  try { voices = await api('/api/voices'); } catch (e) { toast(e.message, 'error'); return; }

  const cards = voices.map(v => {
    const initials = (v.name || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
    const meta = (v.sample_count || 0) + ' clip' + ((v.sample_count || 0) !== 1 ? 's' : '') + ' · ' + (v.embedding_model || '—');
    const open = expandedVoice === v.id;
    const clipRows = (v.clips || []).map(c => `
      <div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--seg-edge)">
        <button data-clip-play="${c.id}" data-vid="${v.id}" style="background:none;border:1px solid var(--inset-edge);border-radius:3px;width:24px;height:22px;cursor:pointer;font-size:10px;color:var(--label-dim)">▶</button>
        <span style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim)">${c.created_at ? new Date(c.created_at).toLocaleString() : ''}</span>
        <button data-clip-del="${c.id}" data-vid="${v.id}" style="margin-left:auto;background:none;border:none;color:var(--red);cursor:pointer;font-size:11px">Remove</button>
      </div>`).join('') || '<div style="font-size:11.5px;color:var(--label-dim);padding:6px 0">No clips yet</div>';
    return `
    <div class="unit" style="padding:11px 34px">
      <div style="display:grid;grid-template-columns:auto 1fr auto auto;align-items:center;gap:16px;cursor:pointer" data-voice-toggle="${v.id}">
        <div style="width:38px;height:38px;border-radius:50%;background:linear-gradient(155deg,#D4D6D8,#A9ACAF 70%);display:flex;align-items:center;justify-content:center;box-shadow:0 2px 4px rgba(0,0,0,0.5),inset 0 -2px 3px rgba(0,0,0,0.2);font-family:var(--f-cond);font-weight:700;font-size:14px;color:var(--key-ink)">${escapeHtml(initials)}</div>
        <div style="min-width:0">
          <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(v.name)}</div>
          <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-top:2px">${escapeHtml(meta)}</div>
        </div>
        <div style="font-size:12px;color:var(--label-dim)">${escapeHtml(v.notes || '')}</div>
        <button class="btn btn--red" data-vdel="${v.id}" style="font-size:11px;padding:5px 12px;background:none">Remove</button>
      </div>
      ${open ? `
      <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--seg-edge)">
        ${clipRows}
        <button data-add-clip="${v.id}" style="margin-top:8px;font-family:var(--f-mono);font-size:11px;background:none;border:1px dashed var(--dash);color:var(--label-dim);padding:6px 10px;border-radius:2px;cursor:pointer">+ Add clip</button>
      </div>` : ''}
    </div>`;
  }).join('');

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Voice roster</h1>
      <div style="display:flex;gap:8px">
        <button class="btn" id="voice-enroll-btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)">+ Enroll speaker…</button>
        <button class="btn" id="voice-identify-btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)">Identify a voice…</button>
      </div>
    </div>
    <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);letter-spacing:0.06em;margin:0 36px 14px">Profiles on this roster are matched against every diarized transcript to auto-name speakers.</div>
    ${voices.length ? cards : '<div class="empty-unit">Roster empty — enroll a speaker with a short voice sample</div>'}`;

  $('nav-badge-voices').textContent = String(voices.length).padStart(2, '0');
  $('voice-enroll-btn').addEventListener('click', openEnrollModal);
  $('voice-identify-btn').addEventListener('click', openIdentifyModal);
  root.querySelectorAll('[data-vdel]').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation();
    withBusy(b, async () => {
      if (!(await styledConfirm('Remove this voice profile from the roster?'))) return;
      try {
        await api('/api/voices/' + b.dataset.vdel, { method: 'DELETE' });
        toast('Profile removed');
        loadVoices();
      } catch (e) { toast(e.message, 'error'); }
    });
  }));
  root.querySelectorAll('[data-voice-toggle]').forEach(el => el.addEventListener('click', () => {
    const id = Number(el.dataset.voiceToggle);
    expandedVoice = expandedVoice === id ? null : id;
    loadVoices();
  }));
  root.querySelectorAll('[data-clip-play]').forEach(btn => btn.addEventListener('click', () => {
    if (clipAudio) clipAudio.pause();
    clipAudio = new Audio('/api/voices/' + btn.dataset.vid + '/clips/' + btn.dataset.clipPlay + '/audio');
    clipAudio.play().catch(err => toast(err.message, 'error'));
  }));
  root.querySelectorAll('[data-clip-del]').forEach(btn => btn.addEventListener('click', () => withBusy(btn, async () => {
    if (!(await styledConfirm('Remove this clip?'))) return;
    try {
      await api('/api/voices/' + btn.dataset.vid + '/clips/' + btn.dataset.clipDel, { method: 'DELETE' });
      toast('Clip removed');
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
  })));
  root.querySelectorAll('[data-add-clip]').forEach(btn => btn.addEventListener('click', () => openAddClipModal(Number(btn.dataset.addClip))));
}

let enrollFile = null;
function openEnrollModal() {
  enrollFile = null;
  openModal(`
    <h2 class="modal-title">Enroll a speaker</h2>
    <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:12px">
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px" for="enroll-name">Speaker name</label>
        <input class="inp" id="enroll-name" type="text" placeholder="e.g. Sarah Chen" style="font-size:12px;padding:7px 9px">
      </div>
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px" for="enroll-notes">Notes — optional</label>
        <input class="inp" id="enroll-notes" type="text" placeholder="role, context…" style="font-size:12px;padding:7px 9px">
      </div>
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px">Voice sample</label>
        <button id="enroll-file-btn" style="font-family:var(--f-mono);font-size:11px;background:var(--panel-lo);border:1px dashed var(--dash);color:var(--label-dim);padding:12px;border-radius:2px;cursor:pointer">Choose an audio file…</button>
      </div>
    </div>
    <div class="modal-callout">Enrolling saves this voice to the shared roster — future diarized transcripts will auto-name matching speakers.</div>
    <div class="modal-actions">
      <button id="enroll-cancel" class="btn btn--ghost btn--sm">Cancel</button>
      <button id="enroll-go" class="btn btn--amber btn--sm">Enroll to roster</button>
    </div>`);
  $('enroll-cancel').addEventListener('click', closeModal);
  $('enroll-file-btn').addEventListener('click', () => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'audio/*,.mp3,.wav,.m4a,.flac,.ogg';
    inp.addEventListener('change', () => {
      enrollFile = inp.files[0] || null;
      if (enrollFile) $('enroll-file-btn').textContent = enrollFile.name;
    });
    inp.click();
  });
  $('enroll-go').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const name = $('enroll-name').value.trim();
    if (!name) { toast('Speaker name required', 'error'); return; }
    if (!enrollFile) { toast('Choose a voice sample first', 'error'); return; }
    const fd = new FormData();
    fd.append('file', enrollFile);
    fd.append('name', name);
    fd.append('notes', $('enroll-notes').value.trim());
    try {
      const r = await api('/api/voices/enroll', { method: 'POST', body: fd });
      toast('Enrolled ' + name + ' to the roster');
      toastVoiceWarning(r);
      closeModal();
      refreshRailChrome();
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
  }, { spinner: true }));
}

let addClipFile = null;
function openAddClipModal(profileId) {
  addClipFile = null;
  openModal(`
    <h2 class="modal-title">Add a clip</h2>
    <div class="field" style="gap:4px;margin-bottom:16px">
      <label class="t-label" style="font-size:12px">Voice sample</label>
      <button id="add-clip-file-btn" style="font-family:var(--f-mono);font-size:11px;background:var(--panel-lo);border:1px dashed var(--dash);color:var(--label-dim);padding:12px;border-radius:2px;cursor:pointer">Choose an audio file…</button>
    </div>
    <div class="modal-actions">
      <button id="add-clip-cancel" class="btn btn--ghost btn--sm">Cancel</button>
      <button id="add-clip-go" class="btn btn--amber btn--sm">Add clip</button>
    </div>`);
  $('add-clip-cancel').addEventListener('click', closeModal);
  $('add-clip-file-btn').addEventListener('click', () => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'audio/*,.mp3,.wav,.m4a,.flac,.ogg';
    inp.addEventListener('change', () => {
      addClipFile = inp.files[0] || null;
      if (addClipFile) $('add-clip-file-btn').textContent = addClipFile.name;
    });
    inp.click();
  });
  $('add-clip-go').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    if (!addClipFile) { toast('Choose a voice sample first', 'error'); return; }
    const fd = new FormData();
    fd.append('file', addClipFile);
    try {
      const r = await api('/api/voices/' + profileId + '/clips', { method: 'POST', body: fd });
      toast('Clip added');
      toastVoiceWarning(r);
      closeModal();
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
  }, { spinner: true }));
}

let identifyFile = null, identifyThreshold = '65';
function openIdentifyModal() {
  identifyFile = null;
  identifyThreshold = '65';
  openModal(`
    <div style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px">Identify a voice</div>
    <div style="font-size:12.5px;color:var(--label-dim);margin-bottom:14px">Match a sample against the enrolled roster. Nothing is saved.</div>
    <button id="identify-file-btn" style="width:100%;font-family:var(--f-mono);font-size:11px;background:var(--panel-lo);border:1px dashed var(--dash);color:var(--label-dim);padding:12px;border-radius:2px;cursor:pointer;margin-bottom:14px">Choose an audio sample…</button>
    <div class="t-label" style="font-size:12px;margin-bottom:6px">Match strictness</div>
    <div style="display:flex;gap:6px;margin-bottom:16px" id="identify-thresholds"></div>
    <div id="identify-result" style="display:none;border:1px solid ${GREEN};border-radius:2px;padding:10px 14px;margin-bottom:16px;justify-content:space-between;align-items:center"></div>
    <div class="modal-actions">
      <button id="identify-close" class="btn btn--ghost btn--sm">Close</button>
      <button id="identify-go" class="btn btn--amber btn--sm">Run match</button>
    </div>`);
  renderThresholds();
  $('identify-close').addEventListener('click', closeModal);
  $('identify-file-btn').addEventListener('click', () => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'audio/*,.mp3,.wav,.m4a,.flac,.ogg';
    inp.addEventListener('change', () => {
      identifyFile = inp.files[0] || null;
      if (identifyFile) $('identify-file-btn').textContent = identifyFile.name;
    });
    inp.click();
  });
  $('identify-go').addEventListener('click', (e) => withBusy(e.currentTarget, runIdentify, { spinner: true }));
}

function renderThresholds() {
  const defs = [['50', '50% lenient'], ['65', '65% balanced'], ['80', '80% strict']];
  $('identify-thresholds').innerHTML = defs.map(([v, label]) => {
    const on = identifyThreshold === v;
    return `<button data-th="${v}" style="flex:1;font-family:var(--f-mono);font-size:10px;text-transform:uppercase;background:${on ? AMBER : 'var(--panel-lo)'};border:1px solid ${on ? AMBER : 'var(--inset-edge)'};color:${on ? 'var(--amber-ink)' : 'var(--label-dim)'};padding:8px 4px;border-radius:2px;cursor:pointer">${label}</button>`;
  }).join('');
  document.querySelectorAll('[data-th]').forEach(b => b.addEventListener('click', () => {
    identifyThreshold = b.dataset.th;
    $('identify-result').style.display = 'none';
    renderThresholds();
  }));
}

async function runIdentify() {
  if (!identifyFile) { toast('Choose an audio sample first', 'error'); return; }
  const fd = new FormData();
  fd.append('file', identifyFile);
  fd.append('threshold', String(Number(identifyThreshold) / 100));
  try {
    const r = await api('/api/voices/identify', { method: 'POST', body: fd });
    const box = $('identify-result');
    box.style.display = 'flex';
    const best = (r.matches || [])[0];
    if (best) {
      box.style.borderColor = GREEN;
      box.innerHTML = '<span style="font-size:13px;color:' + GREEN + ';font-weight:600">Match: ' + escapeHtml(best.name) + '</span>' +
        '<span style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim)">' + similarityPct(best.similarity) + ' similarity</span>';
    } else {
      box.style.borderColor = AMBER;
      box.innerHTML = '<span style="font-size:13px;color:' + AMBER + ';font-weight:600">No match above ' + identifyThreshold + '%</span>' +
        '<span style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim)">' + (r.total_profiles || 0) + ' profiles checked</span>';
    }
    // "0 matches" alone can't say whether nobody matched or nothing could be
    // compared at all — the server sends the distinction as `warning`.
    if (r.warning) {
      box.style.flexWrap = 'wrap';
      box.style.borderColor = AMBER;
      box.innerHTML += '<div style="flex-basis:100%;font-size:11.5px;line-height:1.4;color:var(--label-dim);margin-top:8px">' + escapeHtml(r.warning) + '</div>';
    }
  } catch (e) { toast(e.message, 'error'); }
}

/* ══════════════════ files: linked / orphaned inventory ══════════════════ */
function fmtBytes(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + ' GB';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB';
  if (n >= 1e3) return (n / 1e3).toFixed(0) + ' KB';
  return n + ' B';
}

async function renderFilesPage() {
  const root = $('page-files');
  let data;
  try { data = await api('/api/files'); } catch (e) { toast(e.message, 'error'); return; }

  const fileRow = (f, group) => `
    <div style="display:flex;align-items:center;gap:12px;padding:9px 22px 9px 34px;border-bottom:1px solid var(--seg-edge)">
      <input type="checkbox" data-file-select="${group}" data-name="${escapeHtml(f.name)}" style="flex-shrink:0">
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${
          f.transcript_title
            ? escapeHtml(f.transcript_title) + ' <span style="color:var(--label-dim)">(' + escapeHtml(f.field === 'video_path' ? 'video' : 'audio') + ')</span>'
            : escapeHtml(f.name)
        }</div>
        ${f.transcript_title
          ? `<div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(f.name)}</div>`
          : ''}
      </div>
      <div style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim);flex-shrink:0">${fmtBytes(f.size_bytes)}</div>
      <div style="font-family:var(--f-mono);font-size:10px;color:var(--label-faint);flex-shrink:0;width:90px;text-align:right">${f.modified_at ? timeAgo(f.modified_at) : ''}</div>
    </div>`;

  const section = (title, group, files, totalBytes) => `
    <div style="margin-top:22px">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin:0 36px 8px 36px">
        <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em">${escapeHtml(title)} — ${fmtBytes(totalBytes)} · ${files.length} file${files.length !== 1 ? 's' : ''}</div>
        <div style="display:flex;gap:8px">
          <button class="btn" data-files-select-all="${group}" style="font-size:11px;padding:5px 12px;border-color:var(--inset-edge)" ${files.length ? '' : 'disabled'}>Select all</button>
          <button class="btn btn--red" id="files-delete-${group}" data-files-delete="${group}" style="font-size:11px;padding:5px 12px" ${files.length ? '' : 'disabled'}>Delete selected (0)</button>
        </div>
      </div>
      ${files.length
        ? `<div class="unit unit--svc" style="padding:0">${files.map(f => fileRow(f, group)).join('')}</div>`
        : `<div class="empty-unit">No ${escapeHtml(title.toLowerCase())} files</div>`}
    </div>`;

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Files</h1>
      <div class="page-status" style="color:${GREEN}">${ledDot(GREEN, true, 9)}${fmtBytes(data.total_linked_bytes + data.total_orphaned_bytes)} on disk</div>
    </div>
    <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);letter-spacing:0.06em;margin:0 36px 4px">Linked files back an existing transcript. Orphaned files aren't referenced by any transcript — safe to remove once you no longer need them.</div>
    ${section('Linked', 'linked', data.linked, data.total_linked_bytes)}
    ${section('Orphaned', 'orphaned', data.orphaned, data.total_orphaned_bytes)}`;

  $('nav-badge-files').textContent = String(data.orphaned.length).padStart(2, '0');

  const updateDeleteCount = (group) => {
    const n = root.querySelectorAll('[data-file-select="' + group + '"]:checked').length;
    const btn = $('files-delete-' + group);
    if (btn) btn.textContent = 'Delete selected (' + n + ')';
  };
  ['linked', 'orphaned'].forEach(updateDeleteCount);

  root.querySelectorAll('[data-file-select]').forEach(cb =>
    cb.addEventListener('change', () => updateDeleteCount(cb.dataset.fileSelect)));

  root.querySelectorAll('[data-files-select-all]').forEach(btn => btn.addEventListener('click', () => {
    const group = btn.dataset.filesSelectAll;
    const boxes = [...root.querySelectorAll('[data-file-select="' + group + '"]')];
    const allChecked = boxes.length > 0 && boxes.every(b => b.checked);
    boxes.forEach(b => { b.checked = !allChecked; });
    updateDeleteCount(group);
  }));

  root.querySelectorAll('[data-files-delete]').forEach(btn =>
    btn.addEventListener('click', () => deleteSelectedFiles(btn.dataset.filesDelete)));
}

async function deleteSelectedFiles(group) {
  const root = $('page-files');
  const names = [...root.querySelectorAll('[data-file-select="' + group + '"]:checked')].map(el => el.dataset.name);
  if (!names.length) { toast('No files selected', 'error'); return; }
  if (!(await styledConfirm('Delete ' + names.length + ' file(s)? This cannot be undone.'))) return;
  try {
    const r = await api('/api/files/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }),
    });
    const msg = [];
    if (r.deleted.length) msg.push(r.deleted.length + ' deleted');
    if (r.skipped.length) msg.push(r.skipped.length + ' skipped (' + r.skipped.map(s => s.reason).join(', ') + ')');
    if (r.freed_bytes) msg.push('freed ' + fmtBytes(r.freed_bytes));
    toast(msg.join(' · ') || 'Nothing to delete', r.skipped.length ? 'info' : 'ok');
    renderFilesPage();
  } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
}
/* ══════════════════ rear service panel ══════════════════ */
const JACK_DEFS = [
  { id: 'groq', name: 'Groq', desc: 'Hosted Whisper — recommended default', placeholder: 'gsk_…', action: 'Fetch models', kind: 'key' },
  { id: 'openai', name: 'OpenAI', desc: 'whisper-1 hosted transcription', placeholder: 'sk-…', action: 'Fetch models', kind: 'key' },
  { id: 'replicate', name: 'Replicate', desc: 'Hosted whisper-large-v3-turbo', placeholder: 'r8_…', action: 'Fetch models', kind: 'key' },
  { id: 'openrouter', name: 'OpenRouter', desc: 'Unified model gateway', placeholder: 'sk-or-…', action: 'Fetch models', kind: 'key' },
  { id: 'local', name: 'Local / Custom (transcription)', desc: 'Whisper.cpp or any OpenAI-compatible transcription URL', placeholder: 'http://localhost:8080/v1', action: 'Test', kind: 'url' },
  { id: 'local_llm', name: 'Local / Custom (correction & summary)', desc: 'Ollama, LM Studio, or any OpenAI-compatible chat URL — independent of the transcription URL above', placeholder: 'http://localhost:11434/v1', action: 'Save', kind: 'url-save' },
  { id: 'hf', name: 'HuggingFace', desc: 'Required for pyannote speaker diarization', placeholder: 'hf_…', action: 'Verify', kind: 'hf' },
];

function jackRow(j, connected) {
  return `
  <div style="display:flex;align-items:center;gap:16px;padding:10px 0;border-bottom:1px solid var(--edge)">
    <div style="width:22px;height:22px;border-radius:50%;background:radial-gradient(circle at 40% 35%,#3A3D41,#131518 65%);border:2px solid #4A4E53;box-shadow:inset 0 0 6px rgba(0,0,0,0.9);flex-shrink:0"></div>
    <div style="flex:1;min-width:0">
      <div style="font-family:var(--f-cond);font-weight:600;font-size:13.5px;text-transform:uppercase;letter-spacing:0.05em">${escapeHtml(j.name)}</div>
      <div style="font-size:11.5px;color:var(--label-dim)">${escapeHtml(j.desc)}</div>
    </div>
    <input type="${j.kind.startsWith('url') ? 'text' : 'password'}" id="jack-input-${j.id}" placeholder="${escapeHtml(j.placeholder)}"
      style="font-family:var(--f-mono);font-size:11.5px;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:7px 9px;border-radius:2px;width:${j.kind.startsWith('url') ? 190 : 170}px">
    <button id="jack-act-${j.id}" style="font-family:var(--f-mono);font-size:9.5px;background:none;border:1px solid #3A3D41;color:var(--label-dim);padding:5px 9px;border-radius:2px;cursor:pointer;text-transform:uppercase">${escapeHtml(j.action)}</button>
    ${connected && j.kind === 'key' ? `<button id="jack-clear-${j.id}" style="font-family:var(--f-mono);font-size:9.5px;background:none;border:1px solid var(--red);color:var(--red);padding:5px 9px;border-radius:2px;cursor:pointer;text-transform:uppercase">Clear</button>` : ''}
    <div style="display:flex;flex-direction:column;align-items:center;gap:3px;width:64px">
      <div class="led-dot" id="jack-led-${j.id}" style="${connected ? 'background:' + GREEN + ';box-shadow:0 0 5px ' + GREEN : ''}"></div>
      <div style="font-family:var(--f-mono);font-size:8.5px;text-transform:uppercase;color:var(--label-dim)" id="jack-state-${j.id}">${connected ? 'linked' : 'open'}</div>
    </div>
  </div>`;
}

function setJackLed(id, connected) {
  const led = $('jack-led-' + id), st = $('jack-state-' + id);
  led.style.background = connected ? GREEN : 'var(--edge)';
  led.style.boxShadow = connected ? '0 0 5px ' + GREEN : 'none';
  st.textContent = connected ? 'linked' : 'open';
}

// Audio-cleanup panel (issue #317). The backend for these keys shipped with
// #270, but nothing in the UI ever sent them, so they were only reachable by
// hand-writing a PUT /api/settings request. One ordered registry drives the
// render, the toggle wiring, and the save payload, so those three can't drift
// into parallel hand-maintained lists. Order follows the pipeline order, with
// each numeric field directly under the toggle that switches it on.
//
// cleanup_demucs_enabled is deliberately absent: cleanup_demucs() in
// services/audio_cleanup.py is written and unit-tested but called from
// nowhere, so a toggle for it would persist and then do nothing. It belongs
// with #239, which also owns the consent flow for its multi-GB model
// download. tests/test_settings_ui_coverage.py enforces that exclusion, so
// wiring Demucs up means updating that test too.
//
// [element id, settings key, label, kind, unit, min, max]
// kind 'tog' is a boolean toggle; 'int'/'float' are numeric inputs clamped to
// [min, max] on save.
const CLEANUP_FIELDS = [
  ['cleanup-loudnorm', 'cleanup_loudnorm_enabled', 'Loudness normalize', 'tog'],
  ['cleanup-loudnorm-target', 'cleanup_loudnorm_target', 'Target loudness', 'float', 'LUFS', -70, -5],
  ['cleanup-highpass', 'cleanup_highpass_enabled', 'High-pass filter (80Hz)', 'tog'],
  ['cleanup-denoise', 'cleanup_denoise_enabled', 'Denoise', 'tog'],
  ['cleanup-vad', 'cleanup_vad_enabled', 'Voice activity detection', 'tog'],
  ['cleanup-vad-threshold', 'cleanup_vad_threshold', 'Speech probability', 'float', 'PROB', 0, 1],
  ['cleanup-vad-silence', 'cleanup_vad_min_silence_ms', 'Min silence', 'int', 'MS', 0, 10000],
  ['cleanup-hallu', 'cleanup_hallu_enabled', 'Hallucination filter', 'tog'],
  // Floor of 2, not 0: filter_hallucinations() returns segments untouched
  // when rep_window < 2 (services/audio_cleanup.py), so a lower value would
  // silently disable the filter while its toggle still read as on.
  ['cleanup-hallu-window', 'cleanup_hallu_rep_window', 'Repeat window', 'int', 'NGRAM', 2, 20],
  ['cleanup-hallu-logprob', 'cleanup_hallu_logprob_cutoff', 'Logprob cutoff', 'float', 'AVG', -10, 0],
  ['cleanup-hallu-nospeech', 'cleanup_hallu_no_speech_cutoff', 'No-speech cutoff', 'float', 'PROB', 0, 1],
];

// Toggles use the default .tog plate/track/paddle sizes so rack.css's
// `.tog.on .tog-paddle { top: 1px }` rule positions the paddle on its own —
// same as #tog-motion below. The audio-prep card's toggle overrides those
// sizes inline and so has to maintain `top` from JS; nothing here needs that.
function cleanupFieldRow([id, key, label, kind, unit], settings) {
  if (kind === 'tog') {
    return `
          <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
            <label class="t-label">${label}</label>
            <button id="${id}" class="tog ${settings[key] ? 'on' : ''}" style="background:none;border:none;cursor:pointer;padding:0">
              <span class="tog-plate"><span class="tog-track"><span class="tog-paddle"></span></span></span>
            </button>
          </div>`;
  }
  // Indented so it reads as belonging to the toggle above it.
  return `
          <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding-left:22px">
            <label class="t-label" style="color:var(--label-dim)" for="${id}">${label}</label>
            <div style="display:flex;align-items:center;gap:6px">
              <input id="${id}" type="text" value="${escapeHtml(String(settings[key]))}" style="font-family:var(--f-mono);font-size:11.5px;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:6px 8px;border-radius:2px;width:56px;text-align:right">
              <span style="font-family:var(--f-mono);font-size:9.5px;color:var(--label-dim)">${unit}</span>
            </div>
          </div>`;
}

async function loadSettingsPage() {
  const root = $('page-settings');
  let provs, settings, health, status, localLlmCfg, deviceToken;
  try {
    [provs, settings, health, status, localLlmCfg, deviceToken] = await Promise.all([
      api('/api/providers'), api('/api/settings'), api('/api/health'), api('/api/status'),
      // Not in the transcription provider registry — fetched separately.
      api('/api/providers/local_llm'),
      api('/api/settings/device-token'),
    ]);
  } catch (e) { toast(e.message, 'error'); return; }
  const provMap = Object.fromEntries(provs.map(p => [p.id, p]));
  const connected = {
    groq: !!(provMap.groq && provMap.groq.configured),
    openai: !!(provMap.openai && provMap.openai.configured),
    replicate: !!(provMap.replicate && provMap.replicate.configured),
    openrouter: !!(provMap.openrouter && provMap.openrouter.configured),
    local: !!(provMap.local && provMap.local.configured),
    local_llm: !!(localLlmCfg && localLlmCfg.api_url),
    hf: !!(settings.hf_token),
  };

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Rear service panel</h1>
      <div style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.1em">Setup controls — not needed during normal use</div>
    </div>

    <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Credential jacks — keys stay on this machine</div>
    <div class="unit unit--svc" style="padding:8px 22px 8px 34px">
      ${JACK_DEFS.map(j => jackRow(j, connected[j.id])).join('')}
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:30px">
      <div>
        <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Term glossary</div>
        <div id="term-glossary-panel" class="unit unit--svc" style="border-radius:3px;padding:16px 30px;height:100%">
          <div style="font-size:11.5px;color:var(--label-dim);margin-bottom:10px">Names and jargon the correction pass should recognize. Added by hand or auto-extracted from a pasted meeting context.</div>
          <div style="display:flex;gap:8px;margin-bottom:12px">
            <input id="hotword-new" type="text" placeholder="Add a term…" style="flex:1;font-family:var(--f-mono);font-size:11.5px;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:7px 9px;border-radius:2px;min-width:0">
            <button id="hotword-add" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:6px 14px;border-radius:2px;cursor:pointer">Add</button>
          </div>
          <div id="hotword-rows"></div>
        </div>
      </div>
      <div>
        <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Audio prep &amp; chunking</div>
        <div class="unit unit--svc" style="border-radius:3px;padding:16px 30px;height:100%;display:flex;flex-direction:column;gap:12px">
          ${[['audio-bitrate', 'Upload bitrate', 'KBPS', settings.bitrate_kbps],
             ['audio-chunk', 'Split files over', 'MB', settings.chunk_threshold_mb],
             ['audio-parallel', 'Parallel uploads', 'MAX', settings.max_concurrent_chunks]].map(([id, label, unit, val]) => `
          <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
            <label class="t-label" for="${id}">${label}</label>
            <div style="display:flex;align-items:center;gap:6px">
              <input id="${id}" type="text" value="${escapeHtml(String(val))}" style="font-family:var(--f-mono);font-size:11.5px;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:6px 8px;border-radius:2px;width:56px;text-align:right">
              <span style="font-family:var(--f-mono);font-size:9.5px;color:var(--label-dim)">${unit}</span>
            </div>
          </div>`).join('')}
          <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
            <label class="t-label">Auto-correct after transcribe</label>
            <button id="audio-autocorrect" class="tog ${settings.auto_correct ? 'on' : ''}" style="background:none;border:none;cursor:pointer;padding:0">
              <span class="tog-plate" style="width:26px;height:40px"><span class="tog-track" style="width:12px;height:27px"><span class="tog-paddle" style="height:11px;top:${settings.auto_correct ? '1px' : '15px'}"></span></span></span>
            </button>
          </div>
          <button id="audio-save" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px 14px;border-radius:2px;cursor:pointer;margin-top:auto">Save audio settings</button>
        </div>
      </div>
    </div>

    <div style="margin-top:30px">
      <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Audio cleanup &mdash; opt-in filters</div>
      <div class="unit unit--svc" style="border-radius:3px;padding:16px 34px;display:flex;flex-direction:column;gap:10px">
        <div style="font-size:11.5px;color:var(--label-dim)">Loudness, high-pass and denoise run on the audio before transcription; the hallucination filter runs on the segments after it. Every step is off by default except voice activity detection. A step that fails falls back to the original audio rather than failing the job, and voice activity detection applies to local builtin transcription only.</div>
        ${CLEANUP_FIELDS.map(f => cleanupFieldRow(f, settings)).join('')}
        <button id="cleanup-save" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px 14px;border-radius:2px;cursor:pointer;align-self:flex-start;margin-top:4px">Save cleanup settings</button>
      </div>
    </div>

    <div style="margin-top:30px">
      <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Correction &amp; summary defaults</div>
      <div class="unit unit--svc" style="border-radius:3px;padding:16px 34px;display:flex;flex-direction:column;gap:12px">
        <div style="font-size:11.5px;color:var(--label-dim)">Used by auto-correct after every job, the Summarize button, and — for dictation recordings — the auto-suggested format and the Markdown/Email/Coding-prompt reformat buttons. Keys come from the credential jacks above; the model lists are a curated cost-aware shortlist (OpenRouter shows live pricing).</div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <label class="t-label" style="width:110px" for="llm-corr-provider">Correction</label>
          <select id="llm-corr-provider" class="inp" style="padding:6px 8px;font-size:12px"></select>
          <select id="llm-corr-model" class="inp" style="padding:6px 8px;font-size:12px;min-width:250px"></select>
          <input id="llm-corr-model-text" class="inp" style="padding:6px 8px;font-size:12px;width:250px;display:none" placeholder="model served by your endpoint">
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <label class="t-label" style="width:110px" for="llm-sum-provider">Summary</label>
          <select id="llm-sum-provider" class="inp" style="padding:6px 8px;font-size:12px"></select>
          <select id="llm-sum-model" class="inp" style="padding:6px 8px;font-size:12px;min-width:250px"></select>
          <input id="llm-sum-model-text" class="inp" style="padding:6px 8px;font-size:12px;width:250px;display:none" placeholder="model served by your endpoint">
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <label class="t-label" style="width:110px" for="llm-fmt-provider">Reformat</label>
          <select id="llm-fmt-provider" class="inp" style="padding:6px 8px;font-size:12px"></select>
          <select id="llm-fmt-model" class="inp" style="padding:6px 8px;font-size:12px;min-width:250px"></select>
          <input id="llm-fmt-model-text" class="inp" style="padding:6px 8px;font-size:12px;width:250px;display:none" placeholder="model served by your endpoint">
        </div>
        <button id="llm-defaults-save" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px 14px;border-radius:2px;cursor:pointer;align-self:flex-start">Save defaults</button>
      </div>
    </div>

    <div style="margin-top:30px">
      <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Device token — headless capture devices</div>
      <div class="unit unit--svc" style="border-radius:3px;padding:16px 34px;display:flex;flex-direction:column;gap:12px">
        <div style="font-size:11.5px;color:var(--label-dim)">Lets a device with no browser (e.g. a standalone recorder) upload directly to /api/transcribe using a bearer token instead of logging in. Regenerating invalidates the previous token immediately.</div>
        <div id="device-token-status" style="font-family:var(--f-mono);font-size:11.5px;color:var(--label)">${deviceToken.has_token ? `Token active since ${new Date(deviceToken.created_at + 'Z').toLocaleString()}` : 'No token generated'}</div>
        <div id="device-token-value" style="display:none;font-family:var(--f-mono);font-size:11.5px;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px;border-radius:2px;word-break:break-all"></div>
        <div style="display:flex;gap:8px">
          <button id="device-token-generate" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px 14px;border-radius:2px;cursor:pointer">${deviceToken.has_token ? 'Regenerate' : 'Generate'}</button>
          <button id="device-token-revoke" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px 14px;border-radius:2px;cursor:pointer" ${deviceToken.has_token ? '' : 'disabled'}>Revoke</button>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:30px">
      <div>
        <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Environment readout</div>
        <div class="unit unit--svc" style="border-radius:3px;padding:16px 30px;display:flex;align-items:center;gap:16px;height:100%">
          <div style="background:var(--nixie-bg);border:1px solid var(--nixie-edge);border-radius:3px;padding:8px 12px;box-shadow:inset 0 0 10px rgba(0,0,0,0.85);font-family:var(--f-tube);font-size:14px;color:var(--nixie);text-shadow:0 0 3px var(--nixie),0 0 9px rgba(255,138,61,0.5);letter-spacing:0.1em">V${escapeHtml(health.version || '?')} · LOCAL</div>
          <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);line-height:1.7;text-transform:uppercase">FastAPI + SQLite<br>Diarization: ${health.diarization_backend ? 'ML ready (pyannote)' : 'basic (heuristic)'}<br>Voice ID: ${escapeHtml(String(health.voice_id_backend || '—'))}</div>
        </div>
      </div>
      <div>
        <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Maintenance</div>
        <div class="unit unit--svc" style="border-radius:3px;padding:12px 30px;height:100%;display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;align-items:center;gap:8px">
            <label class="t-label" for="export-dir-input" style="white-space:nowrap">Export directory</label>
            <input id="export-dir-input" type="text" value="${escapeHtml(settings.export_directory || '')}" placeholder="e.g. C:\\Users\\you\\Documents\\Vault" style="font-family:var(--f-mono);font-size:11px;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:6px 8px;border-radius:2px;flex:1;min-width:0">
            <button id="export-dir-save" style="font-family:var(--f-cond);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:6px 12px;border-radius:2px;cursor:pointer;white-space:nowrap">Save</button>
          </div>
          <div style="display:flex;align-items:center;justify-content:flex-end;gap:8px">
            ${S.isAdmin ? `<button id="svc-invite-code" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--amber);color:var(--amber);padding:7px 16px;border-radius:2px;cursor:pointer">Generate invite code</button>` : ''}
            ${S.isAdmin ? `<button id="svc-reset-code" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--amber);color:var(--amber);padding:7px 16px;border-radius:2px;cursor:pointer">Generate reset code</button>` : ''}
            <button id="svc-logout" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--red);color:var(--red);padding:7px 16px;border-radius:2px;cursor:pointer">Log out</button>
          </div>
        </div>
      </div>
    </div>

    <div style="margin-top:30px">
      <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Faceplate</div>
      <div class="unit unit--svc" style="border-radius:3px;padding:16px 34px;display:flex;align-items:center;gap:44px;flex-wrap:wrap">
        <button class="ctl" id="ctl-theme" title="Faceplate era — chassis colors only; hardware stays true">
          <span class="knob-plate"><span class="knob-grip" id="knob-theme"></span></span>
          <span class="stack"><span class="name">Faceplate</span>${vfd('', 'vfd-theme')}</span>
        </button>
        <button class="ctl" id="ctl-phosphor" title="Oscilloscope trace color">
          <span class="knob-plate"><span class="knob-grip" id="knob-phosphor"></span></span>
          <span class="stack"><span class="name">Phosphor</span>${vfd('', 'vfd-phosphor')}</span>
        </button>
        <button class="ctl" id="ctl-motion" title="Reel spin and marquee animation">
          <span class="tog" id="tog-motion"><span class="tog-plate"><span class="tog-track"><span class="tog-paddle"></span></span></span></span>
          <span class="stack"><span class="name">Motion</span>${vfd('', 'vfd-motion')}</span>
        </button>
        <div style="font-family:var(--f-mono);font-size:10px;color:var(--label-faint);text-transform:uppercase;letter-spacing:0.06em;margin-left:auto">Saved on this browser</div>
      </div>
    </div>`;

  renderHotwordRows();
  syncFaceplate();

  // LLM defaults pickers (correction + summary)
  const provOpts = LLM_PROVIDERS.map(p => '<option value="' + p.id + '">' + p.name + '</option>').join('');
  $('llm-corr-provider').innerHTML = provOpts;
  $('llm-sum-provider').innerHTML = provOpts;
  $('llm-fmt-provider').innerHTML = provOpts;
  $('llm-corr-provider').value = normalizeLlmProvider(settings.correction_provider || 'groq');
  $('llm-sum-provider').value = normalizeLlmProvider(settings.summary_provider || 'groq');
  $('llm-fmt-provider').value = normalizeLlmProvider(settings.format_provider || 'groq');
  fillModelPicker('llm-corr-model', 'llm-corr-model-text', $('llm-corr-provider').value, settings.correction_model);
  fillModelPicker('llm-sum-model', 'llm-sum-model-text', $('llm-sum-provider').value, settings.summary_model);
  fillModelPicker('llm-fmt-model', 'llm-fmt-model-text', $('llm-fmt-provider').value, settings.format_model);
  $('llm-corr-provider').addEventListener('change', () =>
    fillModelPicker('llm-corr-model', 'llm-corr-model-text', $('llm-corr-provider').value, ''));
  $('llm-sum-provider').addEventListener('change', () =>
    fillModelPicker('llm-sum-model', 'llm-sum-model-text', $('llm-sum-provider').value, ''));
  $('llm-fmt-provider').addEventListener('change', () =>
    fillModelPicker('llm-fmt-model', 'llm-fmt-model-text', $('llm-fmt-provider').value, ''));
  $('llm-defaults-save').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const body = {
      correction_provider: $('llm-corr-provider').value,
      correction_model: llmPickerValue('llm-corr-model', 'llm-corr-model-text', $('llm-corr-provider').value),
      summary_provider: $('llm-sum-provider').value,
      summary_model: llmPickerValue('llm-sum-model', 'llm-sum-model-text', $('llm-sum-provider').value),
      format_provider: $('llm-fmt-provider').value,
      format_model: llmPickerValue('llm-fmt-model', 'llm-fmt-model-text', $('llm-fmt-provider').value),
    };
    if (!body.correction_model || !body.summary_model || !body.format_model) { toast('Pick a model for each row', 'error'); return; }
    try {
      await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      toast('Correction, summary & reformat defaults saved');
    } catch (e) { toast(e.message, 'error'); }
  }));

  // credential jacks
  JACK_DEFS.forEach(j => {
    $('jack-act-' + j.id).addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
      const val = $('jack-input-' + j.id).value.trim();
      try {
        if (j.kind === 'hf') {
          if (val) await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ hf_token: val }) });
          const s = await api('/api/settings');
          setJackLed('hf', !!s.hf_token);
          toast(s.hf_token ? 'HuggingFace token saved' : 'No token on file', s.hf_token ? 'ok' : 'info');
          return;
        }
        if (val) {
          const body = j.kind.startsWith('url') ? { api_url: val } : { api_key: val };
          await api('/api/providers/' + j.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        }
        if (j.kind === 'url-save') {
          // No transcription-backend registry entry for this provider (it's
          // LLM-only), so there's no /models endpoint to round-trip against.
          setJackLed(j.id, !!val);
          toast(val ? j.name + ': URL saved' : j.name + ': cleared');
          return;
        }
        // key/url kinds only reach here — hf and url-save both return above,
        // so this guard can't block hf's status-check or url-save's clear.
        if (!val) { toast('Enter a key first', 'info'); return; }
        const r = await api('/api/providers/' + j.id + '/models');
        const n = (r.models || []).length;
        setJackLed(j.id, true);
        toast(j.name + ': ' + n + ' models available' + (r.live ? '' : ' (cached list)'));
        S.providers = []; // refetch on next transcribe visit
      } catch (e) {
        setJackLed(j.id, false);
        toast(j.name + ': ' + e.message, 'error');
      }
    }, { spinner: true }));
    // Clear button for key-type jacks — confirmation then empty-string PUT
    const clearBtn = $('jack-clear-' + j.id);
    if (clearBtn) {
      clearBtn.addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
        if (!(await styledConfirm('Clear the saved ' + j.name + ' API key? This cannot be undone.'))) return;
        try {
          await api('/api/providers/' + j.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: '' }) });
          setJackLed(j.id, false);
          $('jack-input-' + j.id).value = '';
          toast(j.name + ' key cleared');
          S.providers = [];
          // Reload the settings page to remove the Clear button
          loadSettingsPage();
        } catch (e) { toast(e.message, 'error'); }
      }));
    }
  });

  // glossary
  $('hotword-add').addEventListener('click', addHotword);
  $('hotword-new').addEventListener('keydown', (e) => { if (e.key === 'Enter') addHotword(); });

  // audio prep
  $('audio-autocorrect').addEventListener('click', () => {
    const tog = $('audio-autocorrect');
    const on = !tog.classList.contains('on');
    tog.classList.toggle('on', on);
    tog.querySelector('.tog-paddle').style.top = on ? '1px' : '15px';
  });
  $('audio-save').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const body = {
      bitrate_kbps: parseInt($('audio-bitrate').value, 10) || 128,
      chunk_threshold_mb: parseInt($('audio-chunk').value, 10) || 20,
      max_concurrent_chunks: parseInt($('audio-parallel').value, 10) || 4,
      auto_correct: $('audio-autocorrect').classList.contains('on'),
    };
    try {
      await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      S.autoCorrect = body.auto_correct;
      toast('Audio settings saved');
    } catch (e) { toast(e.message, 'error'); }
  }));

  // audio cleanup (issue #317) — one loop over CLEANUP_FIELDS for the
  // toggles, one PUT carrying every field in the registry.
  for (const [id, , , kind] of CLEANUP_FIELDS) {
    if (kind !== 'tog') continue;
    $(id).addEventListener('click', () => $(id).classList.toggle('on'));
  }
  $('cleanup-save').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const body = {};
    for (const [id, key, , kind, , min, max] of CLEANUP_FIELDS) {
      if (kind === 'tog') { body[key] = $(id).classList.contains('on'); continue; }
      // Not `parseFloat(v) || default` like the audio-prep card above: 0 is a
      // legitimate value for the probability and silence fields, and `||`
      // would silently swap it for the default. Unparseable input leaves the
      // setting at whatever was loaded rather than resetting it.
      const n = kind === 'int' ? parseInt($(id).value, 10) : parseFloat($(id).value);
      body[key] = Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : settings[key];
    }
    try {
      await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      // Keep the closure in step with what's now stored. Without this, a
      // second save in the same page session would fall back to the values
      // this page loaded with rather than the ones just written.
      Object.assign(settings, body);
      // Show what was actually stored — a clamped entry would otherwise leave
      // the box displaying a value the server never received.
      for (const [id, key, , kind] of CLEANUP_FIELDS) {
        if (kind !== 'tog') $(id).value = String(body[key]);
      }
      toast('Cleanup settings saved');
    } catch (e) { toast(e.message, 'error'); }
  }));

  $('svc-logout').addEventListener('click', (e) => withBusy(e.currentTarget, logout));
  // Admin-only reset-code generator — only exists when S.isAdmin is true
  const resetBtn = $('svc-reset-code');
  if (resetBtn) resetBtn.addEventListener('click', showGenerateResetCode);
  // Admin-only registration invite generator (issue #395)
  const inviteBtn = $('svc-invite-code');
  if (inviteBtn) inviteBtn.addEventListener('click', showGenerateInviteCode);

  $('export-dir-save').addEventListener('click', (e) => withBusy(e.currentTarget, async () => {
    const val = $('export-dir-input').value.trim();
    try {
      await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ export_directory: val }) });
      S.exportDir = val;
      toast(val ? 'Export directory saved' : 'Export directory cleared');
    } catch (e) { toast(e.message, 'error'); }
  }));

  $('device-token-generate').addEventListener('click', async () => {
    try {
      const result = await api('/api/settings/device-token', { method: 'POST' });
      const valueBox = $('device-token-value');
      valueBox.textContent = result.token;
      valueBox.style.display = 'block';
      $('device-token-status').textContent =
        `Token active since ${new Date(result.created_at + 'Z').toLocaleString()}`;
      $('device-token-generate').textContent = 'Regenerate';
      $('device-token-revoke').disabled = false;
      toast('Device token generated. Copy it now, it will not be shown again.', 'success');
    } catch (e) { toast(e.message, 'error'); }
  });
  $('device-token-revoke').addEventListener('click', async () => {
    try {
      await api('/api/settings/device-token', { method: 'DELETE' });
      toast('Device token revoked.', 'success');
      loadSettingsPage();
    } catch (e) { toast(e.message, 'error'); }
  });

  // faceplate prefs
  $('ctl-theme').addEventListener('click', () => {
    const i = (THEME_ORDER.indexOf(S.theme) + 1) % THEME_ORDER.length;
    applyTheme(THEME_ORDER[i]);
    syncFaceplate();
  });
  $('ctl-phosphor').addEventListener('click', () => {
    const i = (PHOSPHORS.findIndex(p => p.value === S.phosphor) + 1) % PHOSPHORS.length;
    applyPhosphor(PHOSPHORS[i].value);
    syncFaceplate();
  });
  $('ctl-motion').addEventListener('click', () => {
    applyMotion(!S.motion);
    syncFaceplate();
  });
}

function syncFaceplate() {
  if (!$('knob-theme')) return;
  const ti = Math.max(0, THEME_ORDER.indexOf(S.theme));
  const pi = Math.max(0, PHOSPHORS.findIndex(p => p.value === S.phosphor));
  $('knob-theme').style.transform = 'rotate(' + (-60 + ti * 24) + 'deg)';
  $('knob-phosphor').style.transform = 'rotate(' + (-60 + pi * 24) + 'deg)';
  setVfd('vfd-theme', S.theme);
  setVfd('vfd-phosphor', PHOSPHORS[pi] ? PHOSPHORS[pi].name : '—');
  $('tog-motion').classList.toggle('on', S.motion);
  setVfd('vfd-motion', S.motion ? 'ON' : 'OFF');
}

async function renderHotwordRows() {
  let words;
  try { words = await api('/api/hotwords'); } catch (e) { toast(e.message, 'error'); return; }
  const box = $('hotword-rows');
  if (!box) return;
  box.innerHTML = words.length ? words.map(h => `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px solid var(--edge)">
      <div style="display:flex;align-items:center;gap:8px;min-width:0">
        <span style="font-family:var(--f-mono);font-size:12px">${escapeHtml(h.term)}</span>
        <span style="font-family:var(--f-mono);font-size:8.5px;color:var(--label-dim);border:1px solid var(--edge);border-radius:2px;padding:1px 5px;text-transform:uppercase">${escapeHtml(h.source || 'manual')}</span>
      </div>
      <button data-hwdel="${h.id}" title="Remove term" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:13px;padding:0 4px">×</button>
    </div>`).join('')
    : '<div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-faint);padding:8px 0">No terms yet.</div>';
  box.querySelectorAll('[data-hwdel]').forEach(b => b.addEventListener('click', () => withBusy(b, async () => {
    try {
      await api('/api/hotwords/' + b.dataset.hwdel, { method: 'DELETE' });
      renderHotwordRows();
    } catch (e) { toast(e.message, 'error'); }
  })));
}

async function addHotword() {
  const inp = $('hotword-new');
  const term = inp.value.trim();
  if (!term) return;
  return withBusy($('hotword-add'), async () => {
    try {
      await api('/api/hotwords', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ term }) });
      inp.value = '';
      renderHotwordRows();
    } catch (e) { toast(e.message, 'error'); }
  });
}

/* ══════════════════ init ══════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  const pwMeta = document.querySelector('meta[name="wd-password-min-length"]');
  if (pwMeta) { S.passwordMinLength = parseInt(pwMeta.content, 10) || 8; }
  loadPrefs();
  document.querySelectorAll('.rail-btn').forEach(b =>
    b.addEventListener('click', () => navigate(b.dataset.nav)));
  $('auth-form').addEventListener('submit', submitAuth);
  $('auth-toggle').addEventListener('click', toggleAuthMode);
  $('modal-overlay').addEventListener('click', (e) => {
    if (e.target === $('modal-overlay')) closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    closeModal();
  });
  $('file-input').addEventListener('change', (e) => {
    if (e.target.files[0]) loadTape(e.target.files[0]);
    e.target.value = '';
  });
  // Account recovery links (login page)
  $('auth-forgot-username').addEventListener('click', (e) => withBusy(e.currentTarget, showForgotUsername));
  $('auth-reset-code').addEventListener('click', showResetCode);
  var dockClose = $('video-dock-close');
  if (dockClose) dockClose.addEventListener('click', reattachVideo);
  var dockPip = $('video-dock-pip');
  if (dockPip) dockPip.addEventListener('click', togglePiP);
  initVideoDockDrag();
  // Register service worker for static asset caching (cache-first) and
  // offline shell support. Fire-and-forget — the SW's install/activate
  // lifecycle runs independently and doesn't block the app.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js');
  }
  checkAuth();
});

/* ══════════════════ test-hook surface ══════════════════ */
/* Expose the internal symbols Playwright / screenshot tooling depends on.
   rack.js itself executes as a classic script so these are already window
   globals in dev, but esbuild --bundle wraps the top-level scope in the
   minified output (rack.min.js) which hides them.  This explicit block
   works identically in both, survives future build-config changes, and
   makes the supported test-hook surface self-documenting. */
if (typeof window !== 'undefined') {
  Object.assign(window, { navigate, S, syncTranscribe, renderDetail, curProv, logout, api, loadCostsPage, _jobFingerprint, startJob });
}
