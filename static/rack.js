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
function loadTranscripts() { $('page-transcripts').innerHTML = '<div class="empty-unit">Channel bank — coming in Phase 4</div>'; }
function loadTranscriptDetail() { $('page-detail').innerHTML = '<div class="empty-unit">Detail — coming in Phase 4</div>'; }
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
