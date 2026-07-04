/* WhisperDeck — Signal Rack frontend
   Vanilla JS, no build step. Values trace to design_handoff_whisperdesk_signal_rack/. */
'use strict';

/* ══════════════════ state ══════════════════ */
const S = {
  page: 'dashboard',
  user: null,
  authMode: 'login',          // login | register
  detailId: null,
  detailTab: 'transcript',
  query: '',
  // transcribe
  tapeLoaded: false,
  tapeName: '',
  tapeFile: null,
  running: false,
  runningId: null,
  pct: 0,
  stage: null,                // upload | transcribe | diarize | finalize
  jobDone: false,
  doneId: null,
  // settings state (persisted server-side)
  providers: [],
  providerIdx: 0,
  models: [],
  modelIdx: 0,
  langIdx: 0,
  diarize: true,
  autoCorrect: false,
  // live capture
  capturing: false,
  stereoLive: false,
  // prefs (localStorage)
  theme: 'charcoal',
  phosphor: '#5CFFAC',
  motion: true,
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

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: 'same-origin', ...opts });
  if (res.status === 401) { showLogin(); throw new Error('Not signed in'); }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const detail = data && (data.detail || data.error) ? (data.detail || data.error) : ('HTTP ' + res.status);
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

/* ══════════════════ component render helpers ══════════════════ */

// nixie readout: str rendered per-glyph with ghost-8 behind. variant: '', 'dim', 'fault'
// color overrides the tube glow (e.g. green "ML" diarization stat).
function nixie(str, variant = '', color = null) {
  const style = color ? ' style="color:' + color + ';text-shadow:0 0 3px ' + color + ',0 0 9px rgba(255,138,61,0.5)"' : '';
  const glyphs = String(str).split('').map(ch =>
    '<i><b' + style + '>' + escapeHtml(ch) + '</b></i>').join('');
  return '<span class="nixie ' + variant + '">' + glyphs + '</span>';
}

// 11-cell LED bargraph. cells: array of {on, color}
function bargraph(cells, height = 14) {
  const inner = cells.map(c => c.on
    ? '<span style="background:' + c.color + ';box-shadow:0 0 4px ' + c.color + '"></span>'
    : '<span></span>').join('');
  return '<span class="bargraph" style="height:' + height + 'px">' + inner + '</span>';
}

function ledDot(color, glow = true, size = 8) {
  if (!color) return '<span class="led-dot" style="width:' + size + 'px;height:' + size + 'px"></span>';
  return '<span class="led-dot" style="width:' + size + 'px;height:' + size + 'px;background:' + color +
    (glow ? ';box-shadow:0 0 5px ' + color : '') + '"></span>';
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
   (Monitor recents, Channel bank rows, detail meta must always agree.) */
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
      if (qs.state === 'queued' || (!qs.state && pct === 0)) {
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
  return { cells, nix, nixVariant, word, color: color || 'var(--label-dim)', pct, status };
}

/* ══════════════════ navigation ══════════════════ */
const PAGES = ['dashboard', 'transcribe', 'transcripts', 'detail', 'voices', 'settings'];

function navigate(page, data) {
  if (!PAGES.includes(page)) page = 'dashboard';
  S.page = page;
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
    detail: () => loadTranscriptDetail(S.detailId),
    voices: loadVoices,
    settings: loadSettingsPage,
  };
  (loaders[page] || (() => {}))();
}

/* ══════════════════ modal primitive ══════════════════ */
function openModal(html) {
  $('modal-box').innerHTML = html;
  $('modal-overlay').classList.add('open');
}
function closeModal() {
  $('modal-overlay').classList.remove('open');
  $('modal-box').innerHTML = '';
}

/* ══════════════════ auth ══════════════════ */
function showLogin() {
  $('page-login').style.display = 'flex';
  $('app-shell').style.display = 'none';
}
function showApp() {
  $('page-login').style.display = 'none';
  $('app-shell').style.display = 'flex';
  navigate('dashboard');
}

async function checkAuth() {
  try {
    const me = await api('/api/me');
    S.user = me && (me.username || me.user || null);
    if (S.user) $('rail-operator').textContent = 'Operator: ' + S.user;
    showApp();
  } catch {
    showLogin();
  }
}

function toggleAuthMode() {
  S.authMode = S.authMode === 'login' ? 'register' : 'login';
  $('auth-title').textContent = S.authMode === 'login' ? 'Operator sign-in' : 'Register operator';
  $('auth-submit').textContent = S.authMode === 'login' ? 'Power on' : 'Register';
  $('auth-toggle').textContent = S.authMode === 'login' ? 'No account? Register' : 'Have an account? Sign in';
}

async function submitAuth(ev) {
  ev.preventDefault();
  const username = $('auth-user').value.trim();
  const password = $('auth-pass').value;
  if (!username || !password) { toast('Operator and passcode required', 'error'); return; }
  try {
    await api('/api/' + S.authMode, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    $('auth-led').style.background = GREEN;
    $('auth-led').style.boxShadow = '0 0 5px ' + GREEN;
    await checkAuth();
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function logout() {
  try { await api('/api/logout', { method: 'POST' }); } catch { /* session may be gone */ }
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

async function loadDashboard() {
  const root = $('page-dashboard');
  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Monitor</h1>
      <div class="page-status" style="color:${GREEN}">${ledDot(GREEN, true, 9)}${escapeHtml(greeting())}</div>
    </div>
    <div class="unit" id="dash-stats" style="display:grid;grid-template-columns:repeat(4,1fr);padding:8px 28px"></div>
    <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:20px 0 8px 36px">Recent signals</div>
    <div id="dash-recents"></div>`;
  try {
    const [st, recents] = await Promise.all([
      api('/api/status'),
      api('/api/transcripts?limit=5'),
    ]);
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

    $('dash-recents').innerHTML = (recents && recents.length) ? recents.map(t => {
      const sv = statusView(t);
      return `
      <button class="unit" data-open="${t.id}" style="display:grid;grid-template-columns:1fr 170px 100px;align-items:center;gap:16px;padding:11px 30px">
        <span style="min-width:0">
          <span style="display:block;font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(t.title || t.filename || 'Untitled')}</span>
          <span style="display:block;font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-top:2px">${escapeHtml(transcriptMeta(t))}</span>
        </span>
        ${bargraph(sv.cells)}
        <span style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.05em;color:${sv.color};text-align:right">${escapeHtml(sv.word)}</span>
      </button>`;
    }).join('') : '<div class="empty-unit">No signals yet — load a tape on the Transcribe deck</div>';
    $('dash-recents').querySelectorAll('[data-open]').forEach(b =>
      b.addEventListener('click', () => navigate('detail', Number(b.dataset.open))));
  } catch (e) {
    toast(e.message, 'error');
  }
}
function renderTranscribe() { $('page-transcribe').innerHTML = '<div class="empty-unit">Transcribe — coming in Phase 5</div>'; }
/* ══════════════════ channel bank ══════════════════ */
let bankPollTimer = null;

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

async function loadTranscripts() {
  const root = $('page-transcripts');
  let list;
  try {
    list = await api('/api/transcripts?limit=100');
  } catch (e) { toast(e.message, 'error'); return; }

  const active = list.filter(t => t.status === 'processing').length;
  const openIds = new Set([...root.querySelectorAll('details[open]')].map(d => d.dataset.tid));

  const rows = list.map(t => {
    const sv = statusView(t);
    const fields = bankDetailFields(t, sv);
    const acts = ['<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="open" data-id="' + t.id + '">Open transcript</button>'];
    if (t.status === 'processing')
      acts.push('<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="cancel" data-id="' + t.id + '">Cancel — resumable</button>');
    if (t.status === 'cancelled')
      acts.push('<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="resume" data-id="' + t.id + '">Resume</button>');
    if (t.status === 'failed' || t.status === 'partial')
      acts.push('<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="retry" data-id="' + t.id + '">Retry</button>');
    acts.push('<button class="btn btn--red" style="font-size:12px;padding:6px 12px" data-act="delete" data-id="' + t.id + '">Delete</button>');
    return `
    <details class="unit" data-tid="${t.id}" ${openIds.has(String(t.id)) ? 'open' : ''}>
      <summary style="list-style:none;cursor:pointer;padding:12px 22px 12px 34px;display:grid;grid-template-columns:1fr 190px 112px;align-items:center;gap:16px">
        <div style="min-width:0">
          <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(t.title || t.filename || 'Untitled')}</div>
          <div style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim);margin-top:2px">${escapeHtml(transcriptMeta(t))}</div>
        </div>
        ${bargraph(sv.cells, 16)}
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px">
          ${nixie(sv.nix, sv.nixVariant)}
          <div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.05em;color:${sv.color}">${escapeHtml(sv.word)}</div>
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

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Channel bank</h1>
      <div class="page-status" style="color:${GREEN}">${ledDot(GREEN, true, 9)}${list.length} channels · ${active} active</div>
    </div>
    ${list.length ? rows : '<div class="empty-unit">No signals on the bank — load a tape on the Transcribe deck</div>'}`;

  root.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', async (e) => {
    e.preventDefault();
    const id = Number(b.dataset.id), act = b.dataset.act;
    try {
      if (act === 'open') { navigate('detail', id); return; }
      if (act === 'cancel') { await api('/api/transcripts/' + id + '/cancel', { method: 'POST' }); toast('Cancelled — resumable later', 'info'); }
      if (act === 'resume') { const r = await api('/api/transcripts/' + id + '/resume', { method: 'POST' }); toast('Resumed ' + r.resumed + ' sections', 'info'); }
      if (act === 'retry') { const r = await api('/api/transcripts/' + id + '/retry-failed-chunks', { method: 'POST' }); toast('Retrying ' + r.retried + ' sections', 'info'); }
      if (act === 'delete') {
        if (!window.confirm('Delete this transcript permanently?')) return;
        await api('/api/transcripts/' + id, { method: 'DELETE' });
        toast('Transcript deleted');
      }
      loadTranscripts();
    } catch (err) { toast(err.message, 'error'); }
  }));

  clearTimeout(bankPollTimer);
  if (active > 0 && S.page === 'transcripts') {
    bankPollTimer = setTimeout(() => { if (S.page === 'transcripts') loadTranscripts(); }, 4000);
  }
}

/* ══════════════════ transcript detail ══════════════════ */
let detailData = null;

async function loadTranscriptDetail(id) {
  if (id == null) { navigate('transcripts'); return; }
  try {
    detailData = await api('/api/transcripts/' + id);
  } catch (e) { toast(e.message, 'error'); return; }
  S.query = '';
  renderDetail();
}

function detailTabsHtml() {
  return ['transcript', 'corrected', 'summary'].map(tb => {
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
  const segs = (t.segments || []).filter(sg =>
    !q || (sg.text || '').toLowerCase().includes(q) || (sg.speaker || '').toLowerCase().includes(q));
  if (!segs.length) {
    return '<div style="padding:30px;text-align:center;font-family:var(--f-mono);font-size:11px;color:var(--label-dim)">' +
      (q ? 'NO SEGMENTS MATCH — CLEAR THE SEARCH OR CHECK JOB STATUS' : 'NO SEGMENTS YET — CHECK JOB STATUS') + '</div>';
  }
  return segs.map(sg => {
    const dot = hashColor(sg.speaker || '');
    return `
    <div style="display:flex;gap:16px;padding:12px 0;border-bottom:1px solid var(--seg-edge)">
      <div style="font-family:var(--f-mono);font-size:11px;color:var(--nixie);text-shadow:0 0 4px rgba(255,138,61,0.4);width:44px;flex-shrink:0;padding-top:2px">${formatTime(sg.start)}</div>
      <div style="min-width:0">
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">
          <span style="width:7px;height:7px;border-radius:50%;background:${dot};box-shadow:0 0 4px ${dot}"></span>
          <span style="font-family:var(--f-cond);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:0.05em">${escapeHtml(sg.speaker || 'Speaker')}</span>
        </div>
        <div style="font-size:13.5px;line-height:1.55;color:var(--body)">${escapeHtml(sg.text || '')}</div>
      </div>
    </div>`;
  }).join('');
}

function correctedHtml(t) {
  if (t.correction_error) {
    return '<div class="unit" style="padding:20px 32px;font-size:13px;color:var(--red)">' +
      '<div class="t-cap" style="color:var(--red);margin-bottom:6px">Correction failed</div>' +
      escapeHtml(t.correction_error) + '</div>';
  }
  if (t.corrected_text) {
    return '<div class="unit" style="padding:20px 32px;font-size:13.5px;line-height:1.6;color:var(--body);white-space:pre-wrap">' +
      escapeHtml(t.corrected_text) + '</div>';
  }
  return '<div class="empty-unit">Correction pass not run yet — use Re-run correction above' +
    (t.correction_model ? '' : ' (auto-correct was off for this job)') + '</div>';
}

async function summaryHtml(t) {
  if (!t.has_summary) return '<div class="empty-unit">No summary yet — press Summarize above</div>';
  try {
    const s = await api('/api/transcripts/' + t.id + '/summary');
    const cards = [
      { title: 'Summary', items: s.short_summary ? [s.short_summary] : [] },
      { title: 'Key points', items: s.key_points || [] },
      { title: 'Action items', items: s.action_items || [] },
      { title: 'Decisions', items: s.decisions || [] },
    ].filter(c => c.items.length);
    return cards.map(c => `
      <div class="unit" style="padding:16px 32px">
        <div style="font-family:var(--f-cond);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;color:${AMBER}">${escapeHtml(c.title)}</div>
        ${c.items.map(it => `<div style="display:flex;gap:9px;font-size:13px;line-height:1.55;color:var(--body);padding:2px 0"><span style="color:${GREEN}">▪</span><span>${escapeHtml(it)}</span></div>`).join('')}
      </div>`).join('');
  } catch (e) {
    return '<div class="empty-unit">' + escapeHtml(e.message) + '</div>';
  }
}

function renderDetail() {
  const t = detailData;
  if (!t) return;
  const root = $('page-detail');
  const sv = statusView(t);
  const extraActs = [];
  if (t.status === 'partial')
    extraActs.push('<button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="retry">Retry failed sections</button>');
  if (t.status === 'cancelled')
    extraActs.push('<button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="resume">Resume</button>');

  root.innerHTML = `
    <div class="page-head" style="gap:14px">
      <h1 class="t-title" style="min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(t.title || t.filename || 'Untitled')}</h1>
      <div style="display:flex;gap:8px;flex-shrink:0">
        ${extraActs.join('')}
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="summarize">Summarize</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rerun">Re-run correction</button>
        <button class="btn btn--red" style="font-size:12px;padding:7px 14px" data-dact="delete">Delete</button>
      </div>
    </div>
    <div id="rerun-picker" style="display:none;margin:0 36px 14px"></div>
    <div class="unit" style="border-radius:3px;margin-bottom:14px;padding:14px 22px 14px 34px">
      <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:14px">
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Duration</div>${formatDur(t.duration_seconds)}</div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Provider</div>${escapeHtml((t.provider || '—') + (t.model ? ' · ' + t.model : ''))}</div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Status</div><span style="color:${sv.color}">${escapeHtml(sv.word)}</span></div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Speakers</div>${t.speaker_count || '—'}</div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Segments</div>${(t.segments || []).length}</div>
      </div>
    </div>
    <div style="display:flex;gap:6px;align-items:flex-end;margin-bottom:14px;padding:0 36px">
      ${detailTabsHtml()}
      <input id="detail-search" class="inp" type="text" placeholder="Search transcript…" value="${escapeHtml(S.query)}" style="margin-left:auto;font-size:12px;width:220px;padding:8px 10px">
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
  root.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact)));
}

async function renderDetailBody() {
  const t = detailData;
  const body = $('detail-body');
  if (S.detailTab === 'transcript') {
    body.innerHTML = '<div class="unit" style="border-radius:3px;padding:6px 32px">' + segmentsHtml(t) + '</div>';
  } else if (S.detailTab === 'corrected') {
    body.innerHTML = correctedHtml(t);
  } else {
    body.innerHTML = '<div class="empty-unit">Loading summary…</div>';
    body.innerHTML = await summaryHtml(t);
  }
}

async function detailAction(act) {
  const t = detailData;
  if (!t) return;
  try {
    if (act === 'delete') {
      if (!window.confirm('Delete this transcript permanently?')) return;
      await api('/api/transcripts/' + t.id, { method: 'DELETE' });
      toast('Transcript deleted');
      navigate('transcripts');
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
      toast('Summarizing…', 'info');
      const fd = new FormData();
      await api('/api/transcripts/' + t.id + '/summarize', { method: 'POST', body: fd });
      S.detailTab = 'summary';
      await loadTranscriptDetail(t.id);
      return;
    }
    if (act === 'rerun') {
      toggleRerunPicker();
      return;
    }
  } catch (e) { toast(e.message, 'error'); }
}

function toggleRerunPicker() {
  const box = $('rerun-picker');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = `
    <div class="unit" style="padding:12px 34px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span class="t-unit">Correction pass</span>
      <select id="rerun-provider" class="inp" style="padding:6px 8px;font-size:12px">
        <option value="groq">Groq</option>
        <option value="openai">OpenAI</option>
        <option value="openrouter">OpenRouter</option>
      </select>
      <input id="rerun-model" class="inp" style="padding:6px 8px;font-size:12px;width:230px" value="llama-3.3-70b-versatile" title="LLM used for the correction pass">
      <button id="rerun-go" class="btn btn--amber" style="font-size:12px;padding:7px 14px">Run correction</button>
    </div>`;
  $('rerun-go').addEventListener('click', rerunCorrection);
}

async function rerunCorrection() {
  const t = detailData;
  const fd = new FormData();
  fd.append('provider', $('rerun-provider').value);
  fd.append('model', $('rerun-model').value);
  toast('Running correction…', 'info');
  try {
    // route returns 200 even when the pass fails — the error rides in the body
    const res = await api('/api/transcripts/' + t.id + '/correct', { method: 'POST', body: fd });
    if (res && res.correction_error) {
      toast('Correction failed: ' + res.correction_error, 'error');
    } else {
      toast('Correction complete');
    }
    S.detailTab = 'corrected';
    await loadTranscriptDetail(t.id);
  } catch (e) { toast(e.message, 'error'); }
}
function loadVoices() { $('page-voices').innerHTML = '<div class="empty-unit">Voice roster — coming in Phase 6</div>'; }
function loadSettingsPage() { $('page-settings').innerHTML = '<div class="empty-unit">Service panel — coming in Phase 7</div>'; }

/* ══════════════════ init ══════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  loadPrefs();
  document.querySelectorAll('.rail-btn').forEach(b =>
    b.addEventListener('click', () => navigate(b.dataset.nav)));
  $('auth-form').addEventListener('submit', submitAuth);
  $('auth-toggle').addEventListener('click', toggleAuthMode);
  $('modal-overlay').addEventListener('click', (e) => {
    if (e.target === $('modal-overlay')) closeModal();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
  checkAuth();
});
