/* WhisperDeck — Signal Rack frontend
   Vanilla JS, no build step. Values trace to design_handoff_whisperdesk_signal_rack/. */
'use strict';

/* ══════════════════ state ══════════════════ */
const S = {
  page: 'dashboard',
  user: null,
  isAdmin: false,
  authMode: 'login',          // login | register
  detailId: null,
  detailTab: 'transcript',
  query: '',
  bankQuery: '',
  bankSort: 'date-desc',
  // transcribe
  tapeLoaded: false,
  tapeName: '',
  tapeFile: null,
  running: false,
  runningId: null,
  pct: 0,
  stage: null,                // upload (=Initialize) | transcribe | diarize | finalize
  jobStartedAt: null,
  indeterminate: false,       // running with no chunk data — show elapsed, not %
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

let csrfToken = null;

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
  const method = (opts.method || 'GET').toUpperCase();
  const isMutation = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);
  const headers = { ...(opts.headers || {}) };
  if (isMutation && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const res = await fetch(path, { credentials: 'same-origin', ...opts, headers });
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
  return { cells, nix, nixVariant, word, color: color || 'var(--label-dim)', pct, status };
}

/* ══════════════════ navigation ══════════════════ */
const PAGES = ['dashboard', 'transcribe', 'transcripts', 'queue', 'detail', 'voices', 'settings'];

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
    queue: loadQueue,
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
    await refreshCsrfToken();
    const me = await api('/api/me');
    S.user = me && (me.username || me.user || null);
    S.isAdmin = !!(me && me.is_admin);
    if (S.user) $('rail-operator').textContent = 'Operator: ' + S.user + (S.isAdmin ? ' (admin)' : '');
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
  // "Find username" only makes sense on the login side; reset code flows work either way.
  $('auth-forgot-username').style.display = S.authMode === 'login' ? '' : 'none';
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
  $('fp-generate').addEventListener('click', doGenerate);
  $('fp-username').addEventListener('keydown', (e) => { if (e.key === 'Enter') doGenerate(); });
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
    <div class="modal-actions">
      <button id="rc-close" class="btn btn--ghost btn--sm">Cancel</button>
      <button id="rc-submit" class="btn btn--amber btn--sm">Reset password</button>
    </div>`);
  $('rc-close').addEventListener('click', closeModal);
  const doReset = async () => {
    const token = $('rc-token').value.trim();
    const password = $('rc-password').value;
    if (!token || !password) { toast('Both fields are required', 'error'); return; }
    try {
      const r = await api('/api/reset-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, new_password: password }) });
      toast('Password reset — signed in as ' + r.username);
      closeModal();
      await refreshCsrfToken();
      await checkAuth();
    } catch (e) { toast(e.message, 'error'); }
  };
  $('rc-submit').addEventListener('click', doReset);
  $('rc-password').addEventListener('keydown', (e) => { if (e.key === 'Enter') doReset(); });
  $('rc-token').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('rc-password').focus(); });
}

async function submitAuth(ev) {
  ev.preventDefault();
  const username = $('auth-user').value.trim();
  const password = $('auth-pass').value;
  if (!username || !password) { toast('Operator and password required', 'error'); return; }
  try {
    await api('/api/' + S.authMode /* api-paths: /api/login /api/register */, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    $('auth-led').classList.add('ok');
    await refreshCsrfToken();
    await checkAuth();
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function logout() {
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

async function loadDashboard() {
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
    // Moonshine only ever decodes as English (backend hardcodes it) — lock
    // the language knob so picking e.g. Spanish here doesn't silently
    // produce English-decoded garbage with no error.
    if (curProv().id === 'moonshine') S.langIdx = 0;
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
    if (curProv().id === 'moonshine') {
      toast('Moonshine is English-only — switch provider to change language', 'info');
      return;
    }
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
    { label: 'Initialize', done: st !== 'upload', on: st === 'upload' },
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
    dA.textContent = S.indeterminate
      ? 'Reading: ' + S.tapeName + ' — ' + formatTime((Date.now() - S.jobStartedAt) / 1000) + ' elapsed'
      : 'Reading (' + S.pct + '%): ' + S.tapeName;
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
    if (S.indeterminate) {
      // no chunk data — one amber "working" cell + elapsed clock, never a fake %
      $('tx-meter-leds').innerHTML = [...Array(11)].map((_, i) => i === 0
        ? '<span style="background:' + AMBER + ';box-shadow:0 0 4px ' + AMBER + '"></span>'
        : '<span></span>').join('');
      $('tx-meter-nix').outerHTML = '<span id="tx-meter-nix">' + nixie(formatTime((Date.now() - S.jobStartedAt) / 1000)) + '</span>';
    } else {
      const lit = Math.round(S.pct / 100 * 11);
      $('tx-meter-leds').innerHTML = [...Array(11)].map((_, i) => i < lit
        ? '<span style="background:' + AMBER + ';box-shadow:0 0 4px ' + AMBER + '"></span>'
        : '<span></span>').join('');
      $('tx-meter-nix').outerHTML = '<span id="tx-meter-nix">' + nixie(S.pct + '%') + '</span>';
    }
    $('tx-stages').innerHTML = stageLeds();
    // Cancel is only real once the backend knows the transcript (chunked
    // runs). A sync run in flight has nothing cancellable — say so.
    const cancelBtn = $('tx-cancel');
    cancelBtn.disabled = !S.runningId;
    cancelBtn.title = S.runningId ? '' : "Quick local jobs can't be cancelled — this finishes on its own";
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

let txTicker = null;

// 1s heartbeat while a job runs: keeps the elapsed readout moving on runs
// with no chunk data, and flips Initialize→Transcribe on sync runs after
// model warm-up (no observable signal exists inside a single blocking call —
// 15s comfortably covers local model init).
function startTxTicker() {
  clearInterval(txTicker);
  txTicker = setInterval(() => {
    if (!S.running) { clearInterval(txTicker); txTicker = null; return; }
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
  S.jobStartedAt = Date.now();
  S.indeterminate = false;
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
    toast('Could not start the recorder: ' + e.message, 'error');
    return;
  }

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

async function loadTranscripts() {
  const root = $('page-transcripts');
  let list;
  try {
    list = await api('/api/transcripts?limit=100');
  } catch (e) { toast(e.message, 'error'); return; }

  bankListCache = list;
  const active = list.filter(t => t.status === 'processing').length;
  const openIds = new Set([...root.querySelectorAll('details[open]')].map(d => d.dataset.tid));

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Tape library</h1>
      <div class="page-status page-status--ok">${ledDot(GREEN, true, 9)}${list.length} channels · ${active} active</div>
    </div>
    <div style="display:flex;gap:10px;margin-bottom:14px;padding:0 4px">
      <input id="bank-search" class="inp" type="text" placeholder="Search title or filename…" value="${escapeHtml(S.bankQuery || '')}" style="font-size:12px;padding:8px 10px 8px 16px;flex:1;max-width:320px">
      <select id="bank-sort" class="inp" style="font-size:12px;padding:8px 10px">
        <option value="date-desc" ${(!S.bankSort || S.bankSort === 'date-desc') ? 'selected' : ''}>Newest first</option>
        <option value="date-asc" ${S.bankSort === 'date-asc' ? 'selected' : ''}>Oldest first</option>
        <option value="title-asc" ${S.bankSort === 'title-asc' ? 'selected' : ''}>Title A–Z</option>
      </select>
    </div>
    <div id="bank-rows"></div>`;

  renderBankRows(openIds);

  $('bank-search').addEventListener('input', () => {
    S.bankQuery = $('bank-search').value;
    renderBankRows();
  });
  $('bank-sort').addEventListener('change', () => {
    S.bankSort = $('bank-sort').value;
    renderBankRows();
  });

  // Delegated on the stable `root` node (not per-row) so it keeps working
  // after renderBankRows() replaces #bank-rows' contents. Assignment (not
  // addEventListener) so it doesn't stack a duplicate handler on every poll.
  root.onclick = async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    e.preventDefault();
    const id = Number(b.dataset.id), act = b.dataset.act;
    try {
      if (act === 'open') { navigate('detail', id); return; }
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
  };

  clearTimeout(bankPollTimer);
  if (active > 0 && S.page === 'transcripts') {
    bankPollTimer = setTimeout(() => { if (S.page === 'transcripts') loadTranscripts(); }, 4000);
  }
}

function renderBankRows(preservedOpenIds) {
  const rowsContainer = $('bank-rows');
  const openIds = preservedOpenIds || new Set([...rowsContainer.querySelectorAll('details[open]')].map(d => d.dataset.tid));

  const q = (S.bankQuery || '').trim().toLowerCase();
  const filtered = q
    ? bankListCache.filter(t => (t.title || '').toLowerCase().includes(q) || (t.filename || '').toLowerCase().includes(q))
    : bankListCache.slice();
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
    return `
    <details class="unit" data-tid="${t.id}" ${openIds.has(String(t.id)) ? 'open' : ''}>
      <summary style="list-style:none;cursor:pointer;padding:12px 22px 12px 34px;display:grid;grid-template-columns:16px 1fr 190px 112px;align-items:center;gap:16px">
        <span class="row-chevron" style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim)" title="Click row to expand details">▸</span>
        <div style="min-width:0">
          <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(t.title || t.filename || 'Untitled')}</div>
          <div style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim);margin-top:2px">${escapeHtml(transcriptMeta(t))} · click to expand</div>
        </div>
        ${bargraph(sv.cells, 16)}
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

const KIND_LABELS = { transcription: 'TRANSCRIBE', correction: 'CORRECT', summary: 'SUMMARIZE', rediarize: 'DIARIZE' };

async function loadQueue() {
  const root = $('page-queue');
  let data;
  try { data = await api('/api/jobs?limit=50'); } catch (e) { toast(e.message, 'error'); return; }
  const jobs = data.jobs || [];
  updateQueueBadge(data.active || 0);

  const openIds = new Set([...root.querySelectorAll('details[open]')].map(d => d.dataset.qid));

  const rows = jobs.map(j => {
    const sv = jobStatusView(j);
    const cells = [...Array(11)].map((_, i) => ({ on: sv.color !== null && i < sv.lit, color: sv.color }));
    const prog = j.progress && j.progress.total
      ? ' · section ' + Math.min(j.progress.done + (j.status === 'running' ? 1 : 0), j.progress.total) + ' of ' + j.progress.total
      : '';
    const meta = [(j.provider || '—') + (j.model ? ' · ' + j.model : ''),
                  j.status === 'running' ? 'working' + prog : null,
                  j.error || null,
                  timeAgo(j.created_at)].filter(Boolean).join(' · ');
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
  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Queue</h1>
      <div style="display:flex;align-items:center;gap:14px">
        <div class="page-status page-status--${active ? 'busy' : 'ok'}">${ledDot(active ? AMBER : GREEN, true, 9)}${jobs.length} jobs · ${active} active</div>
        ${finishedCount ? `<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-jact="clear-finished">Clear finished (${finishedCount})</button>` : ''}
      </div>
    </div>
    ${jobs.length ? rows : '<div class="empty-unit">Queue idle — jobs appear here when the machine is working</div>'}`;

  root.querySelectorAll('[data-jact]').forEach(b => b.addEventListener('click', async (e) => {
    e.preventDefault();
    const act = b.dataset.jact, jid = b.dataset.jid, tid = Number(b.dataset.tid);
    try {
      if (act === 'open') { navigate('detail', tid); return; }
      if (act === 'j-cancel') { await api('/api/jobs/' + jid + '/cancel', { method: 'POST' }); toast('Job cancelled', 'info'); }
      if (act === 'j-rerun') { await api('/api/jobs/' + jid + '/rerun', { method: 'POST' }); toast('Job requeued', 'info'); }
      if (act === 't-cancel') { await api('/api/transcripts/' + tid + '/cancel', { method: 'POST' }); toast('Cancelled — resumable later', 'info'); }
      if (act === 't-resume') { const r = await api('/api/transcripts/' + tid + '/resume', { method: 'POST' }); toast('Resumed ' + r.resumed + ' sections', 'info'); }
      if (act === 't-retry') { const r = await api('/api/transcripts/' + tid + '/retry-failed-chunks', { method: 'POST' }); toast('Retrying ' + r.retried + ' sections', 'info'); }
      if (act === 'j-dismiss') { await api('/api/jobs/' + jid + '/dismiss', { method: 'POST' }); toast('Cleared', 'info'); }
      if (act === 'clear-finished') { const r = await api('/api/jobs/clear', { method: 'POST' }); toast('Cleared ' + r.cleared + ' finished job(s)', 'info'); }
      loadQueue();
    } catch (err) { toast(err.message, 'error'); }
  }));

  clearTimeout(queuePollTimer);
  if (active > 0 && S.page === 'queue') {
    queuePollTimer = setTimeout(() => { if (S.page === 'queue') loadQueue(); }, 3000);
  }
}

function updateQueueBadge(active) {
  $('nav-badge-queue').textContent = active ? String(active).padStart(2, '0') : '';
}

async function refreshQueueBadge() {
  try {
    const data = await api('/api/jobs?limit=50');
    updateQueueBadge(data.active || 0);
  } catch { /* badge is best-effort */ }
}

/* ══════════════════ transcript detail ══════════════════ */
let detailData = null;

let detailPollTimer = null;

// Per-line playback + voice-seed flags. All session-local: the shared
// Audio element is created lazily on first play, and seed flags live in
// memory until the user enrolls them — both reset when a DIFFERENT
// transcript opens (same-id reloads keep flags so a rename refresh
// doesn't wipe them).
let segAudio = null, segAudioTid = null, segPlayingBtn = null;
let seedClips = {}; // speaker label -> [{start, end}]

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
}

async function loadTranscriptDetail(id, opts = {}) {
  if (id == null) { navigate('transcripts'); return; }
  const prevId = detailData ? detailData.id : null;
  try {
    detailData = await api('/api/transcripts/' + id);
  } catch (e) { toast(e.message, 'error'); return; }
  if (prevId !== null && prevId !== detailData.id) resetSegAudio();
  if (!opts.preserveQuery) S.query = '';
  renderDetail();
  scheduleDetailPoll();
}

function _jobFingerprint(t) {
  const f = (j) => j ? j.status + ':' + (j.progress ? j.progress.done : 0) : '-';
  return f(t.correction_job) + '|' + f(t.summary_job) + '|' + f(t.voice_match_job);
}

// While an LLM job is active for the open transcript, refresh quietly and
// re-render only when the job actually moved — no flicker mid-read.
function scheduleDetailPoll() {
  clearTimeout(detailPollTimer);
  const t = detailData;
  if (!t || !(llmJobActive(t.correction_job) || llmJobActive(t.summary_job) || llmJobActive(t.voice_match_job))) return;
  const fp = _jobFingerprint(t), id = t.id;
  detailPollTimer = setTimeout(async () => {
    if (S.page !== 'detail' || !detailData || detailData.id !== id) return;
    try {
      const fresh = await api('/api/transcripts/' + id);
      if (S.page !== 'detail' || !detailData || detailData.id !== id) return;
      detailData = fresh;
      if (_jobFingerprint(fresh) !== fp) renderDetail();
      scheduleDetailPoll();
    } catch { /* transient — poll dies, next action revives it */ }
  }, 2500);
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
    const controls = !t.has_audio ? '' : `
      <div style="display:flex;flex-direction:column;gap:4px;flex-shrink:0">
        <button data-seg-play data-start="${sg.start}" data-end="${sg.end}" title="Play this line from the recording" style="${segBtn};color:var(--label-dim)">▶</button>
        ${sg.speaker ? `<button data-seg-seed data-speaker="${escapeHtml(sg.speaker)}" data-start="${sg.start}" data-end="${sg.end}" title="${seeded ? 'Flagged as a voice seed — click to unflag' : 'Flag this line as a voice seed for enrollment'}" style="${segBtn};color:${seeded ? 'var(--nixie)' : 'var(--label-dim)'};${seeded ? 'border-color:var(--nixie);text-shadow:0 0 5px rgba(255,138,61,0.6)' : ''}">◈</button>` : ''}
      </div>`;
    const speakerLabel = sg.speaker
      ? `<span data-seg-rename="${escapeHtml(sg.speaker)}" title="Rename this speaker everywhere" style="font-family:var(--f-cond);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:0.05em;cursor:pointer;border-bottom:1px dotted var(--label-dim)">${escapeHtml(sg.speaker)}</span>`
      : `<span style="font-family:var(--f-cond);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:0.05em">Speaker</span>`;
    return `
    <div style="display:flex;gap:16px;padding:12px 0;border-bottom:1px solid var(--seg-edge)">
      ${checkbox}
      ${controls}
      <div style="font-family:var(--f-mono);font-size:11px;color:var(--nixie);text-shadow:0 0 4px rgba(255,138,61,0.4);width:44px;flex-shrink:0;padding-top:2px">${formatTime(sg.start)}</div>
      <div style="min-width:0">
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">
          <span style="width:7px;height:7px;border-radius:50%;background:${dot};box-shadow:0 0 4px ${dot}"></span>
          ${speakerLabel}
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
  $('enroll-marked-go').addEventListener('click', async () => {
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
      delete seedClips[sp];
      closeModal();
      renderDetailBody();
      syncEnrollMarkedBtn();
    } catch (e) { toast(e.message, 'error'); }
  });
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
  $('retag-go').addEventListener('click', async () => {
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
  });
}

function llmJobActive(job) {
  return job && (job.status === 'pending' || job.status === 'running');
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
      <select id="compare-item-a" class="inp" style="flex:1;font-size:12px;padding:8px 10px">${optionHtml}</select>
      <select id="compare-item-b" class="inp" style="flex:1;font-size:12px;padding:8px 10px">${optionHtml}</select>
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
  return '<div style="display:flex;justify-content:flex-end;gap:8px;padding:0 32px 10px">' +
    '<button class="btn" data-export-copy="' + kind + '" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Copy</button>' +
    '<button class="btn" data-export-dl="' + kind + '" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Download .txt</button></div>';
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
  }
  if (!text.trim()) { toast('Nothing to export yet', 'info'); return; }
  const fullText = header + '\n\n' + text;
  if (copy) copyToClipboard(fullText);
  else downloadTextFile((t.title || t.filename || 'transcript').replace(/[^\w.-]+/g, '_') + '-' + kind + '.txt', fullText);
}

function correctedHtml(t) {
  if (llmJobActive(t.correction_job)) return jobRunningUnit(t.correction_job, 'Correction');
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

async function summaryHtml(t) {
  if (llmJobActive(t.summary_job)) return jobRunningUnit(t.summary_job, 'Summary');
  const failedBanner = (t.summary_job && t.summary_job.status === 'failed')
    ? '<div class="unit" style="padding:14px 32px;margin-bottom:10px;font-size:13px;color:var(--red)">' +
      '<div class="t-cap" style="color:var(--red);margin-bottom:6px">Summary failed</div>' +
      escapeHtml(t.summary_job.error || 'unknown error') + ' — rerun it from the Queue screen.' +
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
    <div class="page-head page-head--with-actions">
      <h1 class="t-title" style="min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(t.title || t.filename || 'Untitled')}</h1>
      <div style="display:flex;gap:8px;flex-shrink:0">
        ${extraActs.join('')}
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="retranscribe" ${t.has_audio ? '' : 'disabled title="No stored audio for this transcript"'}>Re-transcribe</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="compare-versions">Compare versions</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rediarize" ${t.has_audio ? '' : 'disabled title="No stored audio for this transcript"'}>Re-diarize</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rediarize-history">Rediarize history</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="voicematch" ${!t.has_audio ? 'disabled title="No stored audio for this transcript"' : (llmJobActive(t.voice_match_job) ? 'disabled title="Voice match job already queued"' : '')}>Match against voice roster</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="context">Add context</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="summarize" ${llmJobActive(t.summary_job) ? 'disabled title="Summary job already queued"' : ''}>Summarize</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="summary-history">Summary history</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rerun" ${llmJobActive(t.correction_job) ? 'disabled title="Correction job already queued"' : ''}>Re-run correction</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="correction-history">Correction history</button>
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
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Status</div><span class="status-badge status-badge--${escapeHtml(sv.word)}" data-word="${escapeHtml(sv.word)}">${escapeHtml(sv.word)}</span></div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Speakers</div>${t.speaker_count || '—'}</div>
        <div style="font-size:12.5px"><div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--label-dim);margin-bottom:3px">Segments</div>${(t.segments || []).length}</div>
      </div>
    </div>
    <div style="display:flex;gap:6px;align-items:flex-end;margin-bottom:14px;padding:0 36px">
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
  root.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact)));
  // Delegated: segment rows re-render on search/poll, the container doesn't.
  $('detail-body').addEventListener('click', detailBodyClick);
}

async function renderDetailBody() {
  const t = detailData;
  const body = $('detail-body');
  if (S.detailTab === 'transcript') {
    const vm = llmJobActive(t.voice_match_job) ? jobRunningUnit(t.voice_match_job, 'Voice match') : '';
    let nudge = '';
    if (!vm && t.has_audio && hasUnlabeledSpeakers(t)) {
      try {
        const voices = await api('/api/voices');
        if (voices.length) {
          nudge = '<div class="unit" style="padding:12px 32px;margin-bottom:10px;font-size:13px;color:var(--body);display:flex;align-items:center;justify-content:space-between;gap:12px">' +
            '<span>' + voices.length + ' enrolled voice' + (voices.length !== 1 ? 's' : '') + ' might match unlabeled speakers here.</span>' +
            '<button class="btn" data-dact="voicematch" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Match now</button></div>';
        }
      } catch { /* roster fetch failing is non-fatal — just skip the nudge */ }
    }
    body.innerHTML = vm + nudge + exportToolbarHtml('transcript') + '<div class="unit" style="border-radius:3px;margin-top:' + (vm || nudge ? '10px' : '0') + ';padding:6px 32px">' + segmentsHtml(t) + '</div>';
    body.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact)));
  } else if (S.detailTab === 'corrected') {
    body.innerHTML = (t.corrected_text ? exportToolbarHtml('corrected') : '') + correctedHtml(t);
  } else {
    body.innerHTML = '<div class="empty-unit">Loading summary…</div>';
    body.innerHTML = (t.has_summary ? exportToolbarHtml('summary') : '') + await summaryHtml(t);
  }
}

async function detailAction(act) {
  const t = detailData;
  if (!t) return;
  try {
    if (act === 'delete') {
      if (!(await styledConfirm('Delete this transcript permanently?'))) return;
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
      let settings = {};
      try { settings = await api('/api/settings'); } catch { /* backend defaults apply */ }
      const fd = new FormData();
      fd.append('provider', settings.summary_provider || 'groq');
      fd.append('model', settings.summary_model || 'llama-3.3-70b-versatile');
      await api('/api/transcripts/' + t.id + '/summarize', { method: 'POST', body: fd });
      toast('Summary queued — progress shows on the Summary tab and the Queue screen', 'info');
      S.detailTab = 'summary';
      refreshQueueBadge();
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
// pricing for OpenRouter). local has no catalog — swap to free text.
async function fillModelPicker(selectId, textId, provider, preferred) {
  const sel = $(selectId), txt = $(textId);
  const isLocal = provider === 'local_llm';
  sel.style.display = isLocal ? 'none' : '';
  txt.style.display = isLocal ? '' : 'none';
  if (isLocal) {
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
  return provider === 'local_llm' ? $(textId).value.trim() : $(selectId).value;
}

async function toggleRerunPicker() {
  const box = $('rerun-picker');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
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
  $('rerun-go').addEventListener('click', rerunCorrection);
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
    refreshQueueBadge();
    await loadTranscriptDetail(t.id);
  } catch (e) { toast(e.message, 'error'); }
}

/* ── post-hoc reprocess pickers (re-transcribe / re-diarize / context) ── */

async function toggleRetranscribePicker() {
  const box = $('retranscribe-picker');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
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
  $('retx-go').addEventListener('click', async () => {
    const t = detailData;
    const fd = new FormData();
    fd.append('provider', $('retx-provider').value);
    const model = $('retx-model').value;
    if (model) fd.append('model', model);
    try {
      const nt = await api('/api/transcripts/' + t.id + '/retranscribe', { method: 'POST', body: fd });
      toast('Re-transcription started — opened the new transcript', 'info');
      refreshQueueBadge();
      await loadTranscriptDetail(nt.id);
    } catch (e) { toast(e.message, 'error'); }
  });
}

async function toggleRediarizePicker() {
  const box = $('rediarize-picker');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = `
    <div class="unit" style="padding:12px 34px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span class="t-unit">Re-diarize</span>
      <input id="rediar-speakers" class="inp" type="number" min="1" max="20" placeholder="auto"
             title="Number of speakers — leave blank to auto-detect" style="padding:6px 8px;font-size:12px;width:90px">
      <button id="rediar-go" class="btn btn--amber" style="font-size:12px;padding:7px 14px">Run</button>
      <span style="font-size:11px;color:var(--label-dim)">Updates speaker labels in place; re-run correction afterwards if you use the corrected text.</span>
    </div>`;
  $('rediar-go').addEventListener('click', async () => {
    const t = detailData;
    const fd = new FormData();
    const n = $('rediar-speakers').value.trim();
    if (n) fd.append('num_speakers', n);
    try {
      await api('/api/transcripts/' + t.id + '/rediarize', { method: 'POST', body: fd });
      toast('Re-diarization queued — watch the Queue screen', 'info');
      box.style.display = 'none';
      refreshQueueBadge();
      await loadTranscriptDetail(t.id);
    } catch (e) { toast(e.message, 'error'); }
  });
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
  $('ctx-go').addEventListener('click', async () => {
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
  });
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
  root.querySelectorAll('[data-vdel]').forEach(b => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!(await styledConfirm('Remove this voice profile from the roster?'))) return;
    try {
      await api('/api/voices/' + b.dataset.vdel, { method: 'DELETE' });
      toast('Profile removed');
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
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
  root.querySelectorAll('[data-clip-del]').forEach(btn => btn.addEventListener('click', async () => {
    if (!(await styledConfirm('Remove this clip?'))) return;
    try {
      await api('/api/voices/' + btn.dataset.vid + '/clips/' + btn.dataset.clipDel, { method: 'DELETE' });
      toast('Clip removed');
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
  }));
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
  $('add-clip-go').addEventListener('click', async () => {
    if (!addClipFile) { toast('Choose a voice sample first', 'error'); return; }
    const fd = new FormData();
    fd.append('file', addClipFile);
    try {
      await api('/api/voices/' + profileId + '/clips', { method: 'POST', body: fd });
      toast('Clip added');
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

async function loadSettingsPage() {
  const root = $('page-settings');
  let provs, settings, health, status, localLlmCfg;
  try {
    [provs, settings, health, status, localLlmCfg] = await Promise.all([
      api('/api/providers'), api('/api/settings'), api('/api/health'), api('/api/status'),
      // Not in the transcription provider registry — fetched separately.
      api('/api/providers/local_llm'),
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

    <div style="margin-top:30px">
      <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Correction &amp; summary defaults</div>
      <div class="unit unit--svc" style="border-radius:3px;padding:16px 34px;display:flex;flex-direction:column;gap:12px">
        <div style="font-size:11.5px;color:var(--label-dim)">Used by auto-correct after every job and by the Summarize button. Keys come from the credential jacks above; the model lists are a curated cost-aware shortlist (OpenRouter shows live pricing).</div>
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
        <button id="llm-defaults-save" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px 14px;border-radius:2px;cursor:pointer;align-self:flex-start">Save defaults</button>
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
        <div class="unit unit--svc" style="border-radius:3px;padding:12px 30px;height:100%;display:flex;align-items:center;justify-content:flex-end;gap:8px">
          ${S.isAdmin ? `<button id="svc-reset-code" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--amber);color:var(--amber);padding:7px 16px;border-radius:2px;cursor:pointer">Generate reset code</button>` : ''}
          <button id="svc-logout" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:0.03em;background:var(--input);border:1px solid var(--red);color:var(--red);padding:7px 16px;border-radius:2px;cursor:pointer">Log out</button>
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
  $('llm-corr-provider').value = normalizeLlmProvider(settings.correction_provider || 'groq');
  $('llm-sum-provider').value = normalizeLlmProvider(settings.summary_provider || 'groq');
  fillModelPicker('llm-corr-model', 'llm-corr-model-text', $('llm-corr-provider').value, settings.correction_model);
  fillModelPicker('llm-sum-model', 'llm-sum-model-text', $('llm-sum-provider').value, settings.summary_model);
  $('llm-corr-provider').addEventListener('change', () =>
    fillModelPicker('llm-corr-model', 'llm-corr-model-text', $('llm-corr-provider').value, ''));
  $('llm-sum-provider').addEventListener('change', () =>
    fillModelPicker('llm-sum-model', 'llm-sum-model-text', $('llm-sum-provider').value, ''));
  $('llm-defaults-save').addEventListener('click', async () => {
    const body = {
      correction_provider: $('llm-corr-provider').value,
      correction_model: llmPickerValue('llm-corr-model', 'llm-corr-model-text', $('llm-corr-provider').value),
      summary_provider: $('llm-sum-provider').value,
      summary_model: llmPickerValue('llm-sum-model', 'llm-sum-model-text', $('llm-sum-provider').value),
    };
    if (!body.correction_model || !body.summary_model) { toast('Pick a model for each row', 'error'); return; }
    try {
      await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      toast('Correction & summary defaults saved');
    } catch (e) { toast(e.message, 'error'); }
  });

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
    // Clear button for key-type jacks — confirmation then empty-string PUT
    const clearBtn = $('jack-clear-' + j.id);
    if (clearBtn) {
      clearBtn.addEventListener('click', async () => {
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
      });
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
  // Admin-only reset-code generator — only exists when S.isAdmin is true
  const resetBtn = $('svc-reset-code');
  if (resetBtn) resetBtn.addEventListener('click', showGenerateResetCode);

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
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    closeModal();
  });
  $('file-input').addEventListener('change', (e) => {
    if (e.target.files[0]) loadTape(e.target.files[0]);
    e.target.value = '';
  });
  // Account recovery links (login page)
  $('auth-forgot-username').addEventListener('click', showForgotUsername);
  $('auth-reset-code').addEventListener('click', showResetCode);
  checkAuth();
});
