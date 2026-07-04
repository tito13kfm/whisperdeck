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
/* ══════════════════ transcribe: instruments (verbatim from prototype logic) ══════════════════ */
const INST = { dt: 0, raf: null, vuMeters: {}, scopeInit: false, driveMic: null, driveSys: null };

function instrumentsActive() { return S.running || S.capturing; }

function drawVU(canvas, key) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const m = INST.vuMeters[key] || (INST.vuMeters[key] = { v: 0, target: 0, next: 0 });
  // during live capture the drive is the real analyser level for this channel
  const override = key === 'mic' ? INST.driveMic : INST.driveSys;
  const drive = instrumentsActive() ? (override ?? 0.75) : 0.03;
  if (INST.dt > m.next) {
    m.target = Math.min(1, drive * (0.3 + Math.random() * 0.7) + (Math.random() < 0.1 ? 0.2 * drive : 0));
    m.next = INST.dt + 0.12 + Math.random() * 0.32;
  }
  m.v += (m.target - m.v) * (m.target > m.v ? 0.3 : 0.06);

  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, '#F3E9C9');
  g.addColorStop(1, '#E0D2A8');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  const lamp = ctx.createRadialGradient(w / 2, h * 0.15, 8, w / 2, h * 0.15, w * 0.7);
  lamp.addColorStop(0, instrumentsActive() ? 'rgba(255,196,110,0.26)' : 'rgba(255,196,110,0.10)');
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
async function ensureProviders() {
  if (S.providers.length) return;
  const provs = await api('/api/providers');
  S.providers = provs.map(p => ({
    id: p.id,
    name: p.name,
    ready: !p.needs_key || p.configured,
    needsKey: p.needs_key,
    statusText: p.name + (!p.needs_key ? ' · local · ready' : (p.configured ? ' · key connected · ready' : ' · no key — see service panel')),
    models: [p.default_model].filter(Boolean),
    modelsFetched: false,
  }));
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
            ${deckKey('key-open-done', '⏹', 'Stop/Eject', 'active', 'Open finished transcript')}
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
        <div style="display:flex;gap:18px" id="tx-stages"></div>
        <button class="btn btn--red" id="tx-cancel">✕ Cancel — resumable later</button>
      </div>
    </div>

    <!-- signal path -->
    <div class="unit">
      <div style="display:flex;flex-direction:column;gap:13px;padding:12px 34px 16px">
        <div style="display:flex;align-items:center;justify-content:space-between">
          <div class="t-unit">Signal path</div>
          <div id="tx-path-note" style="font-family:var(--f-mono);font-size:10px;color:var(--label-faint);text-transform:uppercase;letter-spacing:0.06em">Applies to the next job</div>
        </div>
        <div style="display:flex;justify-content:center;gap:44px;order:2">
          <button class="ctl" id="ctl-provider" title="Transcription provider">
            <span class="knob-plate"><span class="knob-grip" id="knob-provider"></span></span>
            <span class="stack"><span class="name">Provider</span>${vfd('', 'vfd-provider')}</span>
          </button>
          <button class="ctl" id="ctl-model" title="Model — list comes from the selected provider">
            <span class="knob-plate"><span class="knob-grip" id="knob-model"></span></span>
            <span class="stack"><span class="name">Model</span>${vfd('', 'vfd-model')}</span>
          </button>
          <button class="ctl" id="ctl-lang" title="Spoken language">
            <span class="knob-plate"><span class="knob-grip" id="knob-lang"></span></span>
            <span class="stack"><span class="name">Language</span>${vfd('', 'vfd-lang')}</span>
          </button>
        </div>
        <div style="display:flex;justify-content:center;gap:44px;order:1">
          <button class="ctl" id="ctl-diarize" title="Identify who spoke when (diarization)">
            <span class="tog" id="tog-diarize"><span class="tog-plate"><span class="tog-track"><span class="tog-paddle"></span></span></span></span>
            <span class="stack"><span class="name">Speakers</span>${vfd('', 'vfd-diarize')}</span>
          </button>
          <button class="ctl" id="ctl-autocorrect" title="Run the LLM correction pass automatically">
            <span class="tog" id="tog-autocorrect"><span class="tog-plate"><span class="tog-track"><span class="tog-paddle"></span></span></span></span>
            <span class="stack"><span class="name">Auto-correct</span>${vfd('', 'vfd-autocorrect')}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- fine adjust -->
    <details class="unit">
      <summary style="list-style:none;cursor:pointer;padding:13px 26px;display:flex;align-items:center;gap:10px;font-family:var(--f-cond);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:0.04em"><span style="color:var(--label-dim);font-size:11px">▸</span> Fine adjust — speakers, title, creativity, context</summary>
      <div style="padding:14px 26px 18px;border-top:1px solid var(--panel-lo)">
        <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:14px">
          <div class="field" style="min-width:170px">
            <label class="t-label" for="tx-speakers">Speaker count</label>
            <input class="inp" id="tx-speakers" type="text" placeholder="auto-detect" style="padding:7px 9px">
            <div class="t-hint">Blank = auto-detect.</div>
          </div>
          <div class="field" style="min-width:210px;flex:1">
            <label class="t-label" for="tx-title">Meeting title</label>
            <input class="inp" id="tx-title" type="text" placeholder="optional" style="padding:7px 9px">
            <div class="t-hint">Names the saved transcript.</div>
          </div>
          <div class="field" style="min-width:130px">
            <label class="t-label" for="tx-temp">Creativity</label>
            <input class="inp" id="tx-temp" type="text" value="0" style="padding:7px 9px">
            <div class="t-hint">0 = strict. Technical: temperature.</div>
          </div>
        </div>
        <div class="field">
          <label class="t-label" for="tx-context">Meeting context</label>
          <textarea class="inp" id="tx-context" rows="2" placeholder="Paste the agenda or jargon-heavy notes — names and terms get added to your term glossary." style="padding:7px 9px"></textarea>
        </div>
      </div>
    </details>

    <!-- start strip -->
    <div class="unit" style="border-radius:3px">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 22px;gap:16px">
        <div id="tx-arm-text" style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.08em"></div>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="led-dot" id="tx-start-led"></div>
          <button class="key key--wide" id="tx-start" disabled>▶ Start transcription</button>
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
    if (S.capturing) stopLiveCapture();
    else if (!S.running) openRecModal();
  });
  $('key-open-done').addEventListener('click', () => {
    if (S.doneId) navigate('detail', S.doneId);
  });
  $('tx-start').addEventListener('click', startJob);
  $('tx-cancel').addEventListener('click', cancelJob);
  $('ctl-provider').addEventListener('click', async () => {
    if (S.running) return;
    S.providerIdx = (S.providerIdx + 1) % S.providers.length;
    S.modelIdx = 0;
    await fetchModelsFor(S.providerIdx);
    syncTranscribe();
  });
  $('ctl-model').addEventListener('click', () => {
    if (S.running) return;
    S.modelIdx = (S.modelIdx + 1) % curProv().models.length;
    syncTranscribe();
  });
  $('ctl-lang').addEventListener('click', () => {
    if (S.running) return;
    S.langIdx = (S.langIdx + 1) % LANGUAGES.length;
    syncTranscribe();
  });
  $('ctl-diarize').addEventListener('click', () => {
    if (S.running) return;
    S.diarize = !S.diarize;
    syncTranscribe();
  });
  $('ctl-autocorrect').addEventListener('click', () => {
    if (S.running) return;
    S.autoCorrect = !S.autoCorrect;
    syncTranscribe();
  });
}

function setVfd(id, text) {
  const w = $(id);
  if (!w) return;
  w.firstElementChild.textContent = text;
  armVfdMarquees(w.parentElement);
}

function stageLeds() {
  // Honest stage mapping: upload done once the POST returned; transcribe while
  // chunks are moving; diarize/finalize once every chunk is done but the
  // transcript is still processing (backend merges + diarizes then).
  const st = S.stage;
  const defs = [
    { label: 'Upload', done: st !== 'upload', on: st === 'upload' },
    { label: 'Transcribe', done: st === 'diarize' || st === 'finalize', on: st === 'transcribe' },
    { label: 'Diarize', done: st === 'finalize', on: st === 'diarize' },
    { label: 'Finalize', done: false, on: st === 'finalize' },
  ];
  return defs.map(d => `
    <div style="display:flex;flex-direction:column;align-items:center;gap:4px">
      ${ledDot(d.done ? GREEN : d.on ? AMBER : null, d.done || d.on, 7)}
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
    dA.textContent = '● REC — mic' + (S.stereoLive ? ' (L) + system (R)' : ' only') + ' — press ● to stop';
    dA.style.color = RED;
  } else if (S.running) {
    dA.textContent = 'Reading (' + S.pct + '%): ' + S.tapeName;
    dA.style.color = AMBER;
  } else if (S.tapeLoaded) {
    const mb = S.tapeFile ? ' · ' + (S.tapeFile.size / 1048576).toFixed(1) + ' MB' : '';
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
    dB.textContent = 'Transcript written — press ⏹ to open it';
    dB.style.color = GREEN;
  } else {
    dB.textContent = 'Idle — output writes here';
    dB.style.color = 'var(--label-dim)';
  }

  // play key + start key + LEDs
  const playKey = $('key-play-a'), startKey = $('tx-start');
  playKey.disabled = !canStart;
  playKey.title = canStart ? 'Start transcription' : S.running ? 'Job running' : !S.tapeLoaded ? 'Load media first' : 'Provider needs a key — see service panel';
  startKey.disabled = !canStart;
  const ledColor = (S.running || canStart) ? GREEN : null;
  ['key-play-a-led', 'tx-start-led'].forEach(id => {
    const el = $(id);
    el.style.background = ledColor || 'var(--edge)';
    el.style.boxShadow = ledColor ? '0 0 5px ' + GREEN : 'none';
  });
  const recLed = $('key-rec-led');
  recLed.style.background = S.capturing ? RED : 'var(--edge)';
  recLed.style.boxShadow = S.capturing ? '0 0 5px ' + RED : 'none';
  $('key-rec').title = S.capturing ? 'Stop recording' : 'Live capture — asks before recording';

  // arm strip
  $('tx-arm-text').textContent = S.running
    ? 'Job in progress — settings locked'
    : S.tapeLoaded
      ? 'Armed — ' + prov.name + ' · ' + curModel() + ' · ' + LANGUAGES[S.langIdx]
      : 'Load a tape to arm the transport';

  // meter row
  $('tx-meter').style.display = S.running ? '' : 'none';
  if (S.running) {
    const lit = Math.round(S.pct / 100 * 11);
    $('tx-meter-leds').innerHTML = [...Array(11)].map((_, i) => i < lit
      ? '<span style="background:' + AMBER + ';box-shadow:0 0 4px ' + AMBER + '"></span>'
      : '<span></span>').join('');
    $('tx-meter-nix').outerHTML = '<span id="tx-meter-nix">' + nixie(S.pct + '%') + '</span>';
    $('tx-stages').innerHTML = stageLeds();
  }

  // signal path
  $('tx-path-note').textContent = S.running ? 'Locked while running' : 'Applies to the next job';
  document.querySelectorAll('.ctl').forEach(c => c.classList.toggle('locked', S.running));
  $('knob-provider').style.transform = 'rotate(' + (-60 + S.providerIdx * 20) + 'deg)';
  $('knob-model').style.transform = 'rotate(' + (-60 + (S.modelIdx % prov.models.length) * 24) + 'deg)';
  $('knob-lang').style.transform = 'rotate(' + (-60 + S.langIdx * 18) + 'deg)';
  setVfd('vfd-provider', prov.name);
  setVfd('vfd-model', curModel());
  setVfd('vfd-lang', LANGUAGES[S.langIdx]);
  $('tog-diarize').classList.toggle('on', S.diarize);
  $('tog-autocorrect').classList.toggle('on', S.autoCorrect);
  setVfd('vfd-diarize', S.diarize ? 'ON' : 'OFF');
  setVfd('vfd-autocorrect', S.autoCorrect ? 'ON' : 'OFF');

  // instruments monitor + nav badge
  $('inst-monitor').textContent = instrumentsActive() ? 'LIVE' : 'STANDBY';
  const lamp = $('inst-stereo-lamp');
  lamp.style.background = S.stereoLive ? GREEN : 'var(--edge)';
  lamp.style.boxShadow = S.stereoLive ? '0 0 6px ' + GREEN : 'none';
  $('nav-badge-transcribe').textContent = S.running || S.capturing ? 'REC' : '';
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

function loadTape(file) {
  S.tapeFile = file;
  S.tapeName = file.name;
  S.tapeLoaded = true;
  S.jobDone = false;
  S.pct = 0;
  syncTranscribe();
}

function ejectTape() {
  S.tapeFile = null;
  S.tapeName = '';
  S.tapeLoaded = false;
  S.jobDone = false;
  S.pct = 0;
  syncTranscribe();
}

async function startJob() {
  const prov = curProv();
  if (!S.tapeLoaded || S.running || !prov.ready || !S.tapeFile) return;
  const form = new FormData();
  form.append('file', S.tapeFile);
  form.append('provider', prov.id);
  form.append('model', curModel());
  const lang = LANGUAGES[S.langIdx];
  form.append('language', lang === 'Auto-detect' ? 'auto' : lang.toLowerCase().slice(0, 2));
  form.append('temperature', ($('tx-temp') && $('tx-temp').value) || '0');
  form.append('diarize', S.diarize ? 'true' : 'false');
  const n = $('tx-speakers') && $('tx-speakers').value.trim();
  if (n) form.append('num_speakers', n);
  const title = $('tx-title') && $('tx-title').value.trim();
  if (title) form.append('title', title);
  const ctxDoc = $('tx-context') && $('tx-context').value.trim();
  if (ctxDoc) form.append('context_doc', ctxDoc);

  S.running = true;
  S.jobDone = false;
  S.pct = 0;
  S.stage = 'upload';
  syncTranscribe();
  try {
    const initial = await api('/api/transcribe', { method: 'POST', body: form });
    S.runningId = initial.id;
    S.stage = 'transcribe';
    syncTranscribe();
    const finalData = await pollTranscript(initial.id);
    S.running = false;
    S.stage = null;
    S.runningId = null;
    if (finalData.status === 'cancelled') {
      toast('Transcription cancelled — resume from the channel bank', 'info');
      S.pct = 0;
    } else if (finalData.status === 'partial') {
      toast('Partially complete — some sections failed; retry from the channel bank', 'error');
      S.jobDone = true;
      S.doneId = finalData.id;
    } else {
      toast('Transcription complete');
      S.jobDone = true;
      S.doneId = finalData.id;
      S.pct = 100;
    }
    S.tapeLoaded = false;
    S.tapeFile = null;
    S.tapeName = '';
    syncTranscribe();
  } catch (e) {
    S.running = false;
    S.stage = null;
    S.runningId = null;
    toast('Transcription failed: ' + e.message, 'error');
    syncTranscribe();
  }
}

async function pollTranscript(id) {
  while (true) {
    const data = await api('/api/transcripts/' + id);
    const qs = data.queue_status;
    if (qs && qs.chunks_total) {
      S.pct = Math.round(qs.chunks_done / qs.chunks_total * 100);
      S.stage = qs.chunks_done >= qs.chunks_total ? (S.diarize ? 'diarize' : 'finalize') : 'transcribe';
    } else if (data.status === 'processing') {
      S.stage = 'transcribe';
    }
    if (['completed', 'failed', 'partial', 'cancelled'].includes(data.status)) {
      if (data.status === 'failed') throw new Error(data.error || 'Transcription failed');
      return data;
    }
    if (S.page === 'transcribe') syncTranscribe();
    await new Promise(res => setTimeout(res, 2000));
  }
}

async function cancelJob() {
  if (!S.runningId) return;
  try {
    await api('/api/transcripts/' + S.runningId + '/cancel', { method: 'POST' });
    toast('Cancelling…', 'info');
  } catch (e) { toast(e.message, 'error'); }
}

/* Rec modal — consent copy is design-mandated, verbatim from the prototype. */
function openRecModal() {
  openModal(`
    <div style="display:flex;align-items:center;gap:9px;margin-bottom:12px">
      <span style="width:9px;height:9px;border-radius:50%;background:${RED};box-shadow:0 0 6px ${RED}"></span>
      <span style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em">Start a live capture?</span>
    </div>
    <div style="font-size:13.5px;line-height:1.55;color:var(--body);margin-bottom:8px">This records your microphone (left channel) and system audio (right channel) until you press Stop. Nothing has been recorded yet.</div>
    <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-bottom:18px">The recording stays on this machine.</div>
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="btn" id="rec-notnow" style="font-size:12px;border-color:var(--inset-edge)">Not now</button>
      <button id="rec-start" style="font-family:var(--f-mono);font-size:11px;font-weight:700;background:${AMBER};color:var(--amber-ink);border:none;padding:8px 14px;border-radius:2px;cursor:pointer">● Start recording</button>
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
  let mic;
  try {
    mic = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    toast('Microphone permission denied — nothing was recorded', 'error');
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

  const actx = new AudioContext();
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
  const rec = new MediaRecorder(dest.stream, { mimeType: mime });
  CAP.chunks = [];
  rec.ondataavailable = (e) => { if (e.data.size) CAP.chunks.push(e.data); };
  rec.onstop = finishLiveCapture;
  rec.start(1000);

  CAP.rec = rec;
  CAP.mic = mic;
  CAP.disp = disp;
  CAP.actx = actx;
  S.capturing = true;
  S.stereoLive = !!disp;
  syncTranscribe();
  toast(disp ? 'Recording mic + system audio' : 'Recording mic only', 'info');
}

function stopLiveCapture() {
  if (!S.capturing || !CAP.rec) return;
  CAP.rec.stop(); // finishLiveCapture runs from onstop
}

function finishLiveCapture() {
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
    loadTape(new File([blob], 'live_capture_' + stamp + '.webm', { type: 'audio/webm' }));
    toast('Capture loaded onto Deck A — press START to transcribe');
  } else {
    toast('Nothing was recorded', 'info');
    syncTranscribe();
  }
}
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
/* ══════════════════ voice roster ══════════════════ */
async function loadVoices() {
  const root = $('page-voices');
  let voices;
  try { voices = await api('/api/voices'); } catch (e) { toast(e.message, 'error'); return; }

  const cards = voices.map(v => {
    const initials = (v.name || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
    const meta = (v.sample_count || 1) + ' sample' + ((v.sample_count || 1) !== 1 ? 's' : '') + ' · ' + (v.embedding_model || '—');
    return `
    <div class="unit" style="display:grid;grid-template-columns:auto 1fr auto auto;align-items:center;gap:16px;padding:11px 34px">
      <div style="width:38px;height:38px;border-radius:50%;background:linear-gradient(155deg,#D4D6D8,#A9ACAF 70%);display:flex;align-items:center;justify-content:center;box-shadow:0 2px 4px rgba(0,0,0,0.5),inset 0 -2px 3px rgba(0,0,0,0.2);font-family:var(--f-cond);font-weight:700;font-size:14px;color:var(--key-ink)">${escapeHtml(initials)}</div>
      <div style="min-width:0">
        <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(v.name)}</div>
        <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-top:2px">${escapeHtml(meta)}</div>
      </div>
      <div style="font-size:12px;color:var(--label-dim)">${escapeHtml(v.notes || '')}</div>
      <button class="btn btn--red" data-vdel="${v.id}" style="font-size:11px;padding:5px 12px;background:none">Remove</button>
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
  root.querySelectorAll('[data-vdel]').forEach(b => b.addEventListener('click', async () => {
    if (!window.confirm('Remove this voice profile from the roster?')) return;
    try {
      await api('/api/voices/' + b.dataset.vdel, { method: 'DELETE' });
      toast('Profile removed');
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
  }));
}

let enrollFile = null;
function openEnrollModal() {
  enrollFile = null;
  openModal(`
    <div style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:12px">Enroll a speaker</div>
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
    <div style="font-size:12px;line-height:1.5;color:${AMBER};margin-bottom:16px">Enrolling saves this voice to the shared roster — future diarized transcripts will auto-name matching speakers.</div>
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="btn" id="enroll-cancel" style="font-size:12px;border-color:var(--inset-edge)">Cancel</button>
      <button id="enroll-go" style="font-family:var(--f-mono);font-size:11px;font-weight:700;background:${AMBER};color:var(--amber-ink);border:none;padding:8px 14px;border-radius:2px;cursor:pointer">Enroll to roster</button>
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
  $('enroll-go').addEventListener('click', async () => {
    const name = $('enroll-name').value.trim();
    if (!name) { toast('Speaker name required', 'error'); return; }
    if (!enrollFile) { toast('Choose a voice sample first', 'error'); return; }
    const fd = new FormData();
    fd.append('file', enrollFile);
    fd.append('name', name);
    fd.append('notes', $('enroll-notes').value.trim());
    try {
      await api('/api/voices/enroll', { method: 'POST', body: fd });
      toast('Enrolled ' + name + ' to the roster');
      closeModal();
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
  });
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
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="btn" id="identify-close" style="font-size:12px;border-color:var(--inset-edge)">Close</button>
      <button id="identify-go" style="font-family:var(--f-mono);font-size:11px;font-weight:700;background:${AMBER};color:var(--amber-ink);border:none;padding:8px 14px;border-radius:2px;cursor:pointer">Run match</button>
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
  $('identify-go').addEventListener('click', runIdentify);
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
        '<span style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim)">' + Math.round((best.similarity || 0) * 100) + '% similarity</span>';
    } else {
      box.style.borderColor = AMBER;
      box.innerHTML = '<span style="font-size:13px;color:' + AMBER + ';font-weight:600">No match above ' + identifyThreshold + '%</span>' +
        '<span style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim)">' + (r.total_profiles || 0) + ' profiles checked</span>';
    }
  } catch (e) { toast(e.message, 'error'); }
}
/* ══════════════════ rear service panel ══════════════════ */
const JACK_DEFS = [
  { id: 'groq', name: 'Groq', desc: 'Hosted Whisper — recommended default', placeholder: 'gsk_…', action: 'Fetch models', kind: 'key' },
  { id: 'openai', name: 'OpenAI', desc: 'whisper-1 hosted transcription', placeholder: 'sk-…', action: 'Fetch models', kind: 'key' },
  { id: 'replicate', name: 'Replicate', desc: 'Hosted whisper-large-v3-turbo', placeholder: 'r8_…', action: 'Fetch models', kind: 'key' },
  { id: 'openrouter', name: 'OpenRouter', desc: 'Unified model gateway', placeholder: 'sk-or-…', action: 'Fetch models', kind: 'key' },
  { id: 'local', name: 'Local / Custom', desc: 'Whisper.cpp, Ollama, or any OpenAI-compatible URL', placeholder: 'http://localhost:8080/v1', action: 'Test', kind: 'url' },
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
    <input type="${j.kind === 'url' ? 'text' : 'password'}" id="jack-input-${j.id}" placeholder="${escapeHtml(j.placeholder)}"
      style="font-family:var(--f-mono);font-size:11.5px;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:7px 9px;border-radius:2px;width:${j.kind === 'url' ? 190 : 170}px">
    <button id="jack-act-${j.id}" style="font-family:var(--f-mono);font-size:9.5px;background:none;border:1px solid #3A3D41;color:var(--label-dim);padding:5px 9px;border-radius:2px;cursor:pointer;text-transform:uppercase">${escapeHtml(j.action)}</button>
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

async function loadSettingsPage() {
  const root = $('page-settings');
  let provs, settings, health, status;
  try {
    [provs, settings, health, status] = await Promise.all([
      api('/api/providers'), api('/api/settings'), api('/api/health'), api('/api/status'),
    ]);
  } catch (e) { toast(e.message, 'error'); return; }
  const provMap = Object.fromEntries(provs.map(p => [p.id, p]));
  const connected = {
    groq: !!(provMap.groq && provMap.groq.configured),
    openai: !!(provMap.openai && provMap.openai.configured),
    replicate: !!(provMap.replicate && provMap.replicate.configured),
    openrouter: !!(provMap.openrouter && provMap.openrouter.configured),
    local: !!(provMap.local && provMap.local.configured),
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
        <div class="unit unit--svc" style="border-radius:3px;padding:16px 30px;height:100%">
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

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:30px">
      <div>
        <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Environment readout</div>
        <div class="unit unit--svc" style="border-radius:3px;padding:16px 30px;display:flex;align-items:center;gap:16px;height:100%">
          <div style="background:var(--nixie-bg);border:1px solid var(--nixie-edge);border-radius:3px;padding:8px 12px;box-shadow:inset 0 0 10px rgba(0,0,0,0.85);font-family:var(--f-tube);font-size:14px;color:var(--nixie);text-shadow:0 0 3px var(--nixie),0 0 9px rgba(255,138,61,0.5);letter-spacing:0.1em">V${escapeHtml(health.version || '?')} · LOCAL</div>
          <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);line-height:1.7;text-transform:uppercase">FastAPI + SQLite<br>Diarization: ${health.diarization_backend ? 'ML ready (pyannote)' : 'basic (heuristic)'}<br>Voice ID: ${escapeHtml(String(health.voice_id_backend || '—'))}</div>
        </div>
      </div>
      <div>
        <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Maintenance — guarded</div>
        <div class="unit unit--svc" style="border-radius:3px;padding:14px 30px;height:100%">
          <div style="background:repeating-linear-gradient(45deg,${AMBER} 0 10px,#141518 10px 20px);padding:3px;border-radius:2px">
            <div style="background:var(--svc);border-radius:1px;padding:12px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px">
              <div style="font-family:var(--f-mono);font-size:10px;color:var(--label-dim);text-transform:uppercase;letter-spacing:0.06em">Ends this operator session</div>
              <button id="svc-logout" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--red);color:var(--red);padding:7px 16px;border-radius:2px;cursor:pointer">Log out</button>
            </div>
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

  // credential jacks
  JACK_DEFS.forEach(j => {
    $('jack-act-' + j.id).addEventListener('click', async () => {
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
          const body = j.kind === 'url' ? { api_url: val } : { api_key: val };
          await api('/api/providers/' + j.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        }
        const r = await api('/api/providers/' + j.id + '/models');
        const n = (r.models || []).length;
        setJackLed(j.id, true);
        toast(j.name + ': ' + n + ' models available' + (r.live ? '' : ' (cached list)'));
        S.providers = []; // refetch on next transcribe visit
      } catch (e) {
        setJackLed(j.id, false);
        toast(j.name + ': ' + e.message, 'error');
      }
    });
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
  $('audio-save').addEventListener('click', async () => {
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
  });

  $('svc-logout').addEventListener('click', logout);

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
  box.querySelectorAll('[data-hwdel]').forEach(b => b.addEventListener('click', async () => {
    try {
      await api('/api/hotwords/' + b.dataset.hwdel, { method: 'DELETE' });
      renderHotwordRows();
    } catch (e) { toast(e.message, 'error'); }
  }));
}

async function addHotword() {
  const inp = $('hotword-new');
  const term = inp.value.trim();
  if (!term) return;
  try {
    await api('/api/hotwords', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ term }) });
    inp.value = '';
    renderHotwordRows();
  } catch (e) { toast(e.message, 'error'); }
}

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
  $('file-input').addEventListener('change', (e) => {
    if (e.target.files[0]) loadTape(e.target.files[0]);
    e.target.value = '';
  });
  checkAuth();
});
