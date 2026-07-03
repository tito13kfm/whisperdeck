# Hotword Glossary + Correction Pass UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-shipped hotword glossary + correction-pass backend into `static/index.html` so a user can manage the glossary, paste context docs, toggle auto-correct, view corrected transcripts, and manually re-run correction — all through the UI.

**Architecture:** `static/index.html` is a single-file vanilla-JS app (no build step, no framework). All five UI pieces extend existing patterns already in the file: the Settings page's card layout, the upload page's collapsible Advanced block, and the detail page's tab-switching (`switchDetailTab`). No backend changes.

**Tech Stack:** Plain HTML/CSS/JS in one file, `fetch` for API calls, no bundler.

## Global Constraints

- No backend changes — all endpoints already exist and are tested (`/api/hotwords`, `/api/settings`, `/api/transcribe` with `context_doc`, `/api/transcripts/{id}` (`corrected_text`/`correction_error`/`correction_model`), `/api/transcripts/{id}/correct`).
- Follow the file's existing conventions: inline styles matching neighboring elements, `toast(msg, 'error'|'success'|'info')` for all user-facing errors/confirmations, `escapeHtml()` around any API-derived text rendered as HTML.
- No JS test harness exists in this project. Every task's verification step is a manual click-through against a running server (`run.bat`, serves `http://localhost:9781`).
- The Corrected tab shows one plain-text blob (`corrected_text`) — never per-segment, never a diff view (backend does not store per-segment corrections).

---

### Task 1: Glossary management card (Settings page)

**Files:**
- Modify: `static/index.html:627` (insert new `.set-card` after the HuggingFace provider card, before the "General" card)
- Modify: `static/index.html:1299-1355` (`loadSettings()`)
- Modify: `static/index.html:1581` (settings nav handler)

**Interfaces:**
- Consumes: `GET /api/hotwords` → `[{id, term, source, created_at}]`; `POST /api/hotwords` body `{term}` → `{id, term, source, created_at}`; `DELETE /api/hotwords/{id}` → `{ok: true}` or 404.
- Produces: `loadHotwords()`, `addHotword()`, `deleteHotword(id)` — global functions, called from HTML `onclick`/nav handlers.

- [ ] **Step 1: Add the glossary card HTML**

Insert this new `.set-card` right after the closing `</div>` of the HuggingFace provider card (after line 626, before the `<div class="set-card">` that starts the "General" section at line 628):

```html
        <div class="set-card">
          <h4><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>Hotword Glossary</h4>
          <p>Names and jargon the correction pass should recognize. Manually added or auto-extracted from a pasted context doc at upload time.</p>
          <div style="display:flex;gap:8px;margin-bottom:12px">
            <input type="text" id="newHotword" placeholder="Add a term..." style="flex:1;padding:7px 10px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-family:var(--font);outline:none" onkeydown="if(event.key==='Enter')addHotword()">
            <button class="btn btn-sm btn-primary" onclick="addHotword()">Add</button>
          </div>
          <div id="hotwordList"></div>
        </div>
```

- [ ] **Step 2: Add `loadHotwords()`, `addHotword()`, `deleteHotword()`**

Add these functions right after `loadSettings()` closes (after line 1355, before `async function saveProviderKey(name) {`):

```javascript
async function loadHotwords() {
  try {
    const r = await fetch(API + '/api/hotwords');
    const words = await r.json();
    renderHotwords(words);
  } catch (e) {}
}

function renderHotwords(words) {
  const list = document.getElementById('hotwordList');
  if (!words.length) {
    list.innerHTML = '<div style="font-size:12px;color:var(--text-muted);padding:8px 0">No hotwords yet.</div>';
    return;
  }
  list.innerHTML = words.map(w => `
    <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:12px;color:var(--text-primary)">${escapeHtml(w.term)}</span>
        <span style="font-size:9px;background:var(--bg-page);color:var(--text-muted);padding:1px 6px;border-radius:3px">${escapeHtml(w.source)}</span>
      </div>
      <button class="btn btn-xs btn-ghost" onclick="deleteHotword(${w.id})" style="color:var(--error)">×</button>
    </div>
  `).join('');
}

async function addHotword() {
  const input = document.getElementById('newHotword');
  const term = input.value.trim();
  if (!term) return;
  try {
    const r = await fetch(API + '/api/hotwords', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ term }),
    });
    if (!r.ok) throw new Error(await r.text());
    input.value = '';
    await loadHotwords();
    toast('Hotword added', 'success');
  } catch (e) {
    toast('Failed to add hotword: ' + (e.message || e), 'error');
  }
}

async function deleteHotword(id) {
  try {
    const r = await fetch(API + '/api/hotwords/' + id, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    await loadHotwords();
    toast('Hotword removed', 'success');
  } catch (e) {
    toast('Failed to remove hotword: ' + (e.message || e), 'error');
  }
}
```

- [ ] **Step 3: Wire `loadHotwords()` into the settings nav handler**

At line 1581, change:

```javascript
    else if (page === 'settings') { navigate(page); loadSettings(); loadAudioSettings(); }
```

to:

```javascript
    else if (page === 'settings') { navigate(page); loadSettings(); loadAudioSettings(); loadHotwords(); }
```

- [ ] **Step 4: Manual verification**

Run: `run.bat` (or `python app.py` from repo root with `.venv` active), open `http://localhost:9781`, log in/register.

1. Navigate to Settings — confirm "Hotword Glossary" card appears below the HuggingFace card, showing "No hotwords yet."
2. Type "Groq" into the input, click Add (or press Enter) — confirm it appears in the list with a `manual` badge and a success toast.
3. Click the `×` next to it — confirm it disappears with a success toast.
4. Reload the page, navigate to Settings again — confirm the list still loads correctly (empty after the delete in step 3).

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat: add hotword glossary management UI to Settings page"
```

---

### Task 2: Context doc field (Upload → Advanced)

**Files:**
- Modify: `static/index.html:527-532` (`#txAdvanced` block)
- Modify: `static/index.html:912-926` (`startTx()`)

**Interfaces:**
- Consumes: nothing new (reads its own textarea value).
- Produces: `context_doc` form field sent on `POST /api/transcribe` when non-empty.

- [ ] **Step 1: Add the textarea to the Advanced block**

Replace the `#txAdvanced` block (lines 527-532):

```html
        <div class="cfg-adv" id="txAdvanced">
          <div class="cfg-g">
            <div class="cfg-f"><label>Temperature</label><input type="number" id="txTemp" step="0.1" min="0" max="1" value="0"></div>
            <div class="cfg-f"><label>Title (optional)</label><input type="text" id="txTitle" placeholder="Meeting name..."></div>
          </div>
        </div>
```

with:

```html
        <div class="cfg-adv" id="txAdvanced">
          <div class="cfg-g">
            <div class="cfg-f"><label>Temperature</label><input type="number" id="txTemp" step="0.1" min="0" max="1" value="0"></div>
            <div class="cfg-f"><label>Title (optional)</label><input type="text" id="txTitle" placeholder="Meeting name..."></div>
          </div>
          <div class="cfg-f" style="margin-top:10px">
            <label>Context doc (optional)</label>
            <textarea id="txContextDoc" rows="3" placeholder="Paste meeting agenda, notes, or jargon-heavy text here — names and terms will be added to your hotword glossary." style="width:100%;padding:7px 10px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-family:var(--font);outline:none;resize:vertical"></textarea>
          </div>
        </div>
```

- [ ] **Step 2: Send it in `startTx()`**

In `startTx()`, after the existing `if (title) form.append('title', title);` line (line 925), add:

```javascript
  const contextDoc = document.getElementById('txContextDoc').value.trim();
  if (contextDoc) form.append('context_doc', contextDoc);
```

- [ ] **Step 3: Manual verification**

Run the server as in Task 1.

1. Go to the upload page, select an audio file, click "Advanced" — confirm the "Context doc" textarea appears below Title.
2. Paste some text (e.g. "Agenda: discuss Project Falcon rollout"), leave provider set to a configured one (e.g. Groq with a valid key), start the transcription.
3. After it completes, go to Settings → Hotword Glossary — confirm new terms extracted from the pasted text appear with an `extracted` badge. (Requires a valid Groq key configured; if none is configured, confirm no error is thrown and the transcription still completes normally — extraction is best-effort server-side.)
4. Start another transcription leaving the context doc textarea empty — confirm no `context_doc` field appears in the request (check browser dev tools Network tab) and no new hotwords appear.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat: add context doc field to upload advanced options"
```

---

### Task 3: Auto-correct toggle (Settings → Audio & Chunking card)

**Files:**
- Modify: `static/index.html:644-659` (Audio & Chunking `.set-card`)
- Modify: `static/index.html:1469-1480` (`loadAudioSettings()`)
- Modify: `static/index.html:1498-1515` (`saveAudioSettings()`)

**Interfaces:**
- Consumes: `GET /api/settings` field `auto_correct` (bool); `PUT /api/settings` accepts `auto_correct` (bool).
- Produces: nothing new consumed elsewhere.

- [ ] **Step 1: Add the checkbox to the Audio & Chunking card**

In the Audio & Chunking card, replace this line (line 657):

```html
          <button class="btn btn-sm btn-primary" onclick="saveAudioSettings()">Save</button>
```

with:

```html
          <div class="cfg-f" style="margin-bottom:16px;display:flex;align-items:center;gap:8px">
            <input type="checkbox" id="setAutoCorrect" style="width:auto">
            <label style="margin:0">Auto-correct transcripts after transcription</label>
          </div>
          <button class="btn btn-sm btn-primary" onclick="saveAudioSettings()">Save</button>
```

- [ ] **Step 2: Read the setting in `loadAudioSettings()`**

In `loadAudioSettings()` (line 1469), after `document.getElementById('setMaxConcurrent').value = s.max_concurrent_chunks;` (line 1476), add:

```javascript
    document.getElementById('setAutoCorrect').checked = s.auto_correct !== false;
```

- [ ] **Step 3: Write it in `saveAudioSettings()`**

In `saveAudioSettings()` (line 1498), the `body` object currently reads:

```javascript
    const body = {
      bitrate_kbps: parseInt(document.getElementById('setBitrate').value, 10),
      chunk_threshold_mb: parseInt(document.getElementById('setChunkThreshold').value, 10),
      max_concurrent_chunks: parseInt(document.getElementById('setMaxConcurrent').value, 10),
    };
```

Change it to:

```javascript
    const body = {
      bitrate_kbps: parseInt(document.getElementById('setBitrate').value, 10),
      chunk_threshold_mb: parseInt(document.getElementById('setChunkThreshold').value, 10),
      max_concurrent_chunks: parseInt(document.getElementById('setMaxConcurrent').value, 10),
      auto_correct: document.getElementById('setAutoCorrect').checked,
    };
```

- [ ] **Step 4: Manual verification**

Run the server as in Task 1.

1. Go to Settings — confirm the "Auto-correct transcripts after transcription" checkbox appears above Save, checked by default (matches `auto_correct: True` default).
2. Uncheck it, click Save — confirm success toast.
3. Reload the page, go to Settings — confirm the checkbox is still unchecked (persisted).
4. Re-check it, Save, reload — confirm it's checked again.

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat: add auto-correct toggle to Settings audio card"
```

---

### Task 4: Corrected tab (Detail page)

**Files:**
- Modify: `static/index.html:568-577` (detail page tabs/panels)
- Modify: `static/index.html:1011-1074` (`loadTranscriptDetail()`)
- Modify: `static/index.html:1076-1082` (`switchDetailTab()`)

**Interfaces:**
- Consumes: `t.corrected_text`, `t.correction_error` from the transcript object already fetched in `loadTranscriptDetail()` (both already returned by `GET /api/transcripts/{id}`, per Task 7 of the backend plan's `_serialize_transcript` change).
- Produces: `renderCorrected(t)` — called from `loadTranscriptDetail()`; a third detail-tab panel `#detailCorrected`.

- [ ] **Step 1: Add the tab button and panel**

Replace the detail tabs/panels block (lines 568-577):

```html
      <div class="detail-tabs" id="detailTabs">
        <button class="dt-tab active" data-tab="transcript" onclick="switchDetailTab('transcript')">Transcript</button>
        <button class="dt-tab" data-tab="summary" onclick="switchDetailTab('summary')">Summary</button>
      </div>
      <div class="detail-search" id="detailSearchWrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="text" id="detailSearch" placeholder="Search transcript..." oninput="searchDetail(this.value)">
      </div>
      <div id="detailTranscript"></div>
      <div id="detailSummary" style="display:none"></div>
```

with:

```html
      <div class="detail-tabs" id="detailTabs">
        <button class="dt-tab active" data-tab="transcript" onclick="switchDetailTab('transcript')">Transcript</button>
        <button class="dt-tab" data-tab="corrected" onclick="switchDetailTab('corrected')">Corrected</button>
        <button class="dt-tab" data-tab="summary" onclick="switchDetailTab('summary')">Summary</button>
      </div>
      <div class="detail-search" id="detailSearchWrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="text" id="detailSearch" placeholder="Search transcript..." oninput="searchDetail(this.value)">
      </div>
      <div id="detailTranscript"></div>
      <div id="detailCorrected" style="display:none"></div>
      <div id="detailSummary" style="display:none"></div>
```

- [ ] **Step 2: Update `switchDetailTab()`**

Replace `switchDetailTab()` (lines 1076-1082):

```javascript
function switchDetailTab(tab) {
  document.querySelectorAll('.dt-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
  document.getElementById('detailTranscript').style.display = tab === 'transcript' ? 'block' : 'none';
  document.getElementById('detailSummary').style.display = tab === 'summary' ? 'block' : 'none';
  document.getElementById('detailSearchWrap').style.display = tab === 'transcript' ? 'block' : 'none';
}
```

with:

```javascript
function switchDetailTab(tab) {
  document.querySelectorAll('.dt-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
  document.getElementById('detailTranscript').style.display = tab === 'transcript' ? 'block' : 'none';
  document.getElementById('detailCorrected').style.display = tab === 'corrected' ? 'block' : 'none';
  document.getElementById('detailSummary').style.display = tab === 'summary' ? 'block' : 'none';
  document.getElementById('detailSearchWrap').style.display = tab === 'transcript' ? 'block' : 'none';
}
```

- [ ] **Step 3: Reset the corrected panel's display in `loadTranscriptDetail()` and render it**

In `loadTranscriptDetail()`, after the existing reset lines (lines 1015-1016):

```javascript
  document.getElementById('detailSummary').style.display = 'none';
  document.getElementById('detailTranscript').style.display = 'block';
```

add:

```javascript
  document.getElementById('detailCorrected').style.display = 'none';
```

Then, after `currentDetailData = t;` (line 1022), add a call to a new render function:

```javascript
    renderCorrected(t);
```

Add the `renderCorrected` function right after `loadTranscriptDetail()` closes (after line 1074, before `function switchDetailTab(tab) {`):

```javascript
function renderCorrected(t) {
  const el = document.getElementById('detailCorrected');
  if (t.corrected_text) {
    el.innerHTML = `
      <div style="padding:16px 0;white-space:pre-wrap;font-size:13px;line-height:1.6;color:var(--text-primary)">${escapeHtml(t.corrected_text)}</div>
      <div id="correctionRerun"></div>
    `;
  } else if (t.correction_error) {
    el.innerHTML = `
      <div class="empty-state" style="padding:30px"><h3>Correction failed</h3><p style="color:var(--error)">${escapeHtml(t.correction_error)}</p></div>
      <div id="correctionRerun"></div>
    `;
  } else {
    el.innerHTML = `
      <div class="empty-state" style="padding:30px"><h3>No correction yet</h3><p>Correction hasn't run for this transcript yet.</p></div>
      <div id="correctionRerun"></div>
    `;
  }
  renderRerunControls();
}
```

- [ ] **Step 4: Manual verification**

Run the server as in Task 1, with `auto_correct` enabled and a valid Groq key configured (Task 3).

1. Upload and transcribe an audio file. After it completes, open the transcript detail page — confirm a "Corrected" tab appears between Transcript and Summary.
2. Click "Corrected" — confirm it shows the corrected text (or, if the correction call fails e.g. due to no Groq key, confirm it shows the "Correction failed" state with the error message, not a blank page or JS error in the console).
3. Open an older transcript created before this feature (or one with `auto_correct` disabled) — confirm the Corrected tab shows "No correction yet" instead of erroring.

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat: add Corrected tab to transcript detail page"
```

---

### Task 5: Manual re-run correction (Corrected tab)

**Files:**
- Modify: `static/index.html` (add `renderRerunControls()`, `rerunCorrection()` near `renderCorrected()`)

**Interfaces:**
- Consumes: `renderCorrected(t)` (Task 4) calls `renderRerunControls()` after setting `#detailCorrected`'s inner HTML; `currentTranscriptId` (existing global, set in `loadTranscriptDetail()`).
- Produces: `POST /api/transcripts/{id}/correct` with `provider`/`model` form fields, response re-rendered via `renderCorrected()`.

- [ ] **Step 1: Add `renderRerunControls()` and `rerunCorrection()`**

Add these two functions directly after `renderCorrected()` (added in Task 4):

```javascript
function renderRerunControls() {
  const wrap = document.getElementById('correctionRerun');
  if (!wrap) return;
  wrap.innerHTML = `
    <button class="btn btn-sm btn-ghost" onclick="showRerunPicker()">Re-run correction</button>
    <div id="rerunPicker" style="display:none;margin-top:10px;display:flex;gap:8px;align-items:center">
      <select id="rerunProvider" style="padding:6px 8px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary)">
        <option value="groq">Groq</option>
        <option value="openai">OpenAI</option>
      </select>
      <input type="text" id="rerunModel" placeholder="model name" value="llama-3.3-70b-versatile" style="padding:6px 8px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);width:180px">
      <button class="btn btn-sm btn-primary" onclick="rerunCorrection()">Run</button>
    </div>
  `;
}

function showRerunPicker() {
  const picker = document.getElementById('rerunPicker');
  picker.style.display = picker.style.display === 'none' ? 'flex' : 'none';
}

async function rerunCorrection() {
  if (!currentTranscriptId) return;
  const provider = document.getElementById('rerunProvider').value;
  const model = document.getElementById('rerunModel').value.trim();
  const btn = document.querySelector('#rerunPicker .btn-primary');
  btn.disabled = true;
  btn.textContent = 'Running...';
  try {
    const form = new FormData();
    form.append('provider', provider);
    if (model) form.append('model', model);
    const r = await fetch(API + '/api/transcripts/' + currentTranscriptId + '/correct', { method: 'POST', body: form });
    if (!r.ok) throw new Error(await r.text());
    const t = await r.json();
    currentDetailData = t;
    renderCorrected(t);
    toast('Correction re-run complete', 'success');
  } catch (e) {
    toast('Correction failed: ' + (e.message || e), 'error');
  }
  btn.disabled = false;
  btn.textContent = 'Run';
}
```

The initial `style="display:none;margin-top:10px;display:flex;..."` on `#rerunPicker` is intentional — the later `display:flex` in the same attribute is dead (the first `display:none` wins as written); fix it to a single `display:none` and let `showRerunPicker()` toggle between `'none'` and `'flex'`:

```html
    <div id="rerunPicker" style="display:none;margin-top:10px;gap:8px;align-items:center">
```

- [ ] **Step 2: Manual verification**

Run the server as in Task 1, with at least two providers configured (Groq and OpenAI, or Groq alone is enough to test the mechanism).

1. Open a transcript's Corrected tab, click "Re-run correction" — confirm the provider/model picker appears.
2. Leave Groq selected, change the model field to a different valid model name, click "Run" — confirm the button shows "Running...", then on completion the corrected text updates and a success toast appears.
3. Click "Re-run correction" again — confirm the picker toggles closed/open correctly (no duplicate controls stacking up).
4. Try re-running with an unconfigured provider (e.g. OpenAI with no API key saved) — confirm a clear error toast appears rather than a silent failure or broken UI state, and the button re-enables afterward.

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat: add manual correction re-run controls to Corrected tab"
```

---

## Spec coverage check (self-review)

- Glossary management (Settings page) → Task 1.
- Context doc field (Upload → Advanced) → Task 2.
- Auto-correct toggle (Settings → Audio card) → Task 3.
- Corrected tab, three states (text / error / empty) → Task 4.
- Manual re-run with provider/model picker → Task 5.
- Error handling via existing `toast()` convention → enforced in every task's implementation.
- No backend changes → confirmed, all tasks touch only `static/index.html`.
- Manual verification per component (no JS test harness) → included as Step in every task.
