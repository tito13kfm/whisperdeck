# Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the 6 open GitHub issues (#2, #4, #5, #6, #7, #8, #9 — #9 is bundled two-finding) from the UX audit and empty-Bearer bug report: two backend correctness bugs, three small UI-consistency fixes, and two feature additions (export, list search/sort).

**Architecture:** No new services or dependencies. Backend fixes are two isolated edits to existing LLM-call functions (`services/correction.py`, `services/transcription.py`). Frontend fixes/features are additive changes inside the existing single-file vanilla-JS app (`static/rack.js`) and its stylesheet (`static/rack.css`), reusing the existing `openModal`/`closeModal` primitive and `toast()` helper — no new libraries, no build step (there is none today).

**Tech Stack:** FastAPI + SQLAlchemy (backend), vanilla JS + hand-rolled templating (frontend), httpx for outbound LLM calls, pytest for backend tests. There is no JS test framework or build tool in this repo — frontend tasks are verified by manually driving the running app (dev server + browser), not by unit tests.

## Global Constraints

- Never send `Authorization: Bearer ` with an empty token — omit the header entirely when there's no key (applies to every outbound LLM call site).
- Preserve the existing dark "rack" visual language (CSS vars `--amber`, `--red`, `--green`/`GREEN`, `--label-dim`, `--inset-edge`, `--panel-lo`, fonts `--f-mono`/`--f-cond`) — no new visual system.
- Any UI text change that could be used as an e2e/audit selector must be grepped across `.claude/skills/e2e-test-app/SKILL.md`, `.claude/skills/e2e-ux-audit/SKILL.md`, and `tests/` before landing (per project convention: changed UI text/labels can break selectors elsewhere).
- Backend changes get a pytest regression test (TDD: failing test first). Frontend-only changes get a documented manual verification procedure instead, since no JS test harness exists in this repo.
- Frequent, small commits — one per task, following existing commit style (no AI-authorship lines).

---

## File Structure

| File | Responsibility | Tasks touching it |
|---|---|---|
| `services/correction.py` | LLM chat-completion header building (correction + context-extraction) | 1 |
| `services/transcription.py` | LLM chat-completion header building (summarize) | 1 |
| `tests/test_correction_routing.py` | Regression test for correction's keyless-local header | 1 |
| `tests/test_summarize_local_provider.py` (new) | Regression test for summarize's keyless-local header | 1 |
| `static/rack.js` | All frontend logic: summary rendering, modals, nav render, list render, detail render, export | 2–9 |
| `static/rack.css` | Chevron rotation rule for Task 5 | 5 |
| `static/index.html` | Nav label text | 4 |

---

### Task 1: Stop sending an empty `Authorization: Bearer` header to keyless LLM providers

**Files:**
- Modify: `services/correction.py:62-67`
- Modify: `services/transcription.py:236-244`
- Modify: `tests/test_correction_routing.py` (extend `test_local_provider_uses_saved_api_url`, add one new test)
- Create: `tests/test_summarize_local_provider.py`

**Interfaces:**
- Consumes: nothing new — `_chat_completion(prompt, api_key, provider_name, model, json_mode, provider_config)` in `correction.py` and `TranscriptionService.summarize(db, user_id, transcript_id, api_key, provider_name, provider_config, model)` in `transcription.py` keep their existing signatures.
- Produces: nothing new — this is a pure bugfix, no new symbols for later tasks.

**Root cause:** both call sites unconditionally build `headers={"Authorization": f"Bearer {api_key}", ...}`. When `api_key` is `""` (the normal case for a local/keyless provider, see `KEYLESS_PROVIDERS` in `services/settings.py:29`), this sends a literal empty-token header, which `httpx` rejects with `Illegal header value b'Bearer '` before the request even reaches the local server (confirmed live in issue #2 and in `.claude/skills/e2e-test-app/SKILL.md:472-476`). Fix: only attach `Authorization` when there's a real key.

- [ ] **Step 1: Write the failing test for correction.py**

Add to `tests/test_correction_routing.py`, in the "provider routing" section right after `test_local_provider_uses_saved_api_url`:

```python
def test_local_provider_omits_auth_header_when_no_key(db_session):
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_response("fixed"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(correct_transcript(
            db_session, transcript, api_key="", provider_name="local", model="llama3",
            provider_config={"api_url": "http://box:8080/v1"},
        ))
    headers = fake_post.await_args.kwargs["headers"]
    assert "Authorization" not in headers
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_correction_routing.py::test_local_provider_omits_auth_header_when_no_key -v`
Expected: FAIL — `assert "Authorization" not in headers` fails because the header is `"Bearer "`.

- [ ] **Step 3: Fix `services/correction.py`**

Replace lines 62-67:

```python
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{_api_base(provider_name, provider_config)}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_body,
        )
```

with:

```python
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{_api_base(provider_name, provider_config)}/chat/completions",
            headers=headers,
            json=request_body,
        )
```

- [ ] **Step 4: Run the correction tests to confirm they pass**

Run: `python -m pytest tests/test_correction_routing.py -v`
Expected: all tests PASS, including the new one and the existing `test_auto_correction_uses_settings_provider_and_pool_key` (which asserts `headers["Authorization"] == "Bearer sk-or-pool"` — still holds since that call has a real key).

- [ ] **Step 5: Write the failing test for transcription.py's summarize()**

Create `tests/test_summarize_local_provider.py`:

```python
"""Summarize: keyless local providers must not receive an empty Bearer header."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from database import Transcript, User
from services.transcription import TranscriptionService


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _chat_response(content):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def _make_user_and_transcript(db_session):
    user = User(username="summarizer", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="f.mp3", status="completed",
        full_text="raw meeting text", segments=[],
    )
    db_session.add(t)
    db_session.commit()
    return user, t


def test_summarize_local_provider_omits_auth_header_when_no_key(db_session, tmp_path):
    user, transcript = _make_user_and_transcript(db_session)
    svc = TranscriptionService(str(tmp_path))
    fake_post = AsyncMock(return_value=_chat_response(
        '{"short_summary": "s", "key_points": [], "action_items": [], "decisions": []}'
    ))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(svc.summarize(
            db_session, user.id, transcript.id, api_key="", provider_name="local",
            provider_config={"api_url": "http://box:8080/v1"}, model="llama3",
        ))
    headers = fake_post.await_args.kwargs["headers"]
    assert "Authorization" not in headers
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `python -m pytest tests/test_summarize_local_provider.py -v`
Expected: FAIL — header is `"Bearer "`, not absent.

- [ ] **Step 7: Fix `services/transcription.py`**

Replace lines 236-244:

```python
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
```

with:

```python
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{api_base}/chat/completions",
                headers=headers,
                json=request_body,
            )
```

- [ ] **Step 8: Run both new/changed test files and the full backend suite**

Run: `python -m pytest tests/test_summarize_local_provider.py tests/test_correction_routing.py -v`
Expected: all PASS.

Run: `python -m pytest -q`
Expected: 121 passed (119 existing + 2 new), same pre-existing deprecation warnings as before, no new failures.

- [ ] **Step 9: Commit**

```bash
git add services/correction.py services/transcription.py tests/test_correction_routing.py tests/test_summarize_local_provider.py
git commit -m "fix: omit Authorization header for keyless local LLM providers"
```

---

### Task 2: Stop hiding a new summary failure behind a stale successful summary

**Files:**
- Modify: `static/rack.js:1894-1901`

**Interfaces:**
- Consumes: `t.summary_job` (`{status, error}` or falsy), `t.has_summary` (bool) — both already provided by `_serialize_transcript` on the backend, unchanged.
- Produces: nothing new for later tasks.

**Root cause:** `summaryHtml()` only shows the "Summary failed" branch when `!t.has_summary`. If a transcript already has a summary from an earlier successful run and a re-run fails, `t.has_summary` is still `true`, so the failure branch is skipped and the stale old summary renders with no indication anything went wrong (issue #5, `static/rack.js:1896`).

- [ ] **Step 1: Edit `summaryHtml()`**

Current (lines 1894-1901):

```javascript
async function summaryHtml(t) {
  if (llmJobActive(t.summary_job)) return jobRunningUnit(t.summary_job, 'Summary');
  if (t.summary_job && t.summary_job.status === 'failed' && !t.has_summary) {
    return '<div class="unit" style="padding:20px 32px;font-size:13px;color:var(--red)">' +
      '<div class="t-cap" style="color:var(--red);margin-bottom:6px">Summary failed</div>' +
      escapeHtml(t.summary_job.error || 'unknown error') + ' — rerun it from the Queue screen.</div>';
  }
  if (!t.has_summary) return '<div class="empty-unit">No summary yet — press Summarize above</div>';
```

Replace with:

```javascript
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
```

Then, later in the same function, the existing success-path `return cards.map(...)` (around line 1910-1914) must prepend `failedBanner` so the banner still shows above the stale content:

Current:

```javascript
    return cards.map(c => `
      <div class="unit" style="padding:16px 32px">
        <div style="font-family:var(--f-cond);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;color:${AMBER}">${escapeHtml(c.title)}</div>
        ${c.items.map(it => `<div style="display:flex;gap:9px;font-size:13px;line-height:1.55;color:var(--body);padding:2px 0"><span style="color:${GREEN}">▪</span><span>${escapeHtml(it)}</span></div>`).join('')}
      </div>`).join('');
```

Replace with:

```javascript
    return failedBanner + cards.map(c => `
      <div class="unit" style="padding:16px 32px">
        <div style="font-family:var(--f-cond);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;color:${AMBER}">${escapeHtml(c.title)}</div>
        ${c.items.map(it => `<div style="display:flex;gap:9px;font-size:13px;line-height:1.55;color:var(--body);padding:2px 0"><span style="color:${GREEN}">▪</span><span>${escapeHtml(it)}</span></div>`).join('')}
      </div>`).join('');
```

The `catch` branch at the bottom of `summaryHtml` (fetch of `/api/transcripts/{id}/summary` failing) is unrelated and stays as-is.

- [ ] **Step 2: Manually verify (no JS test harness in this repo)**

Start the dev server (`e2e-test-app` skill's Setup section describes the isolated local-server invocation, or run whatever command the project normally uses to serve `app.py`). In a browser:

1. Upload a short audio fixture from `tests/fixtures/`, let it complete, click **Summarize**, wait for success — confirm the summary cards render normally (no banner).
2. Break the summary provider (e.g. temporarily point the `local` provider's `api_url` at a closed port, or unset its key while pointed at a paid provider) and click **Summarize** again on the *same* transcript.
3. Confirm: the red "Summary failed" banner now appears above the old cards, and the old cards are still visible below it (not silently replaced, not silently hidden).
4. Restore the provider config afterward.

- [ ] **Step 3: Commit**

```bash
git add static/rack.js
git commit -m "fix: show summary failure banner even when a prior successful summary exists"
```

---

### Task 3: Replace native `confirm()`/`prompt()` dialogs with the app's styled modal

**Files:**
- Modify: `static/rack.js:289-297` (add two new helper functions near `openModal`/`closeModal`)
- Modify: `static/rack.js:1414` (Channel bank list delete)
- Modify: `static/rack.js:1724` (speaker rename)
- Modify: `static/rack.js:2012` (transcript detail delete)
- Modify: `static/rack.js:2306` (voice profile remove)
- Modify: `static/rack.js:2324` (voice clip remove)

**Interfaces:**
- Produces: `styledConfirm(message: string): Promise<boolean>` and `styledPrompt(message: string, defaultValue?: string): Promise<string|null>` — later tasks (Task 9) reuse `styledPrompt`.
- Consumes: existing `openModal(html)`, `closeModal()`, `escapeHtml(str)`.

**Root cause:** issue #6 — three (in practice five, once the audit's list is checked against the code: rename speaker, delete transcript ×2 call sites, remove voice profile, remove voice clip) destructive/naming actions use `window.confirm`/`window.prompt`, which render as unstyled native browser chrome instead of the app's own modal (already used for "Enroll marked clips"). This task fixes all five call sites for consistency, not just the three named in the issue body.

- [ ] **Step 1: Add the two helper functions**

Insert immediately after `closeModal()` (after line 297) in `static/rack.js`:

```javascript
function styledConfirm(message) {
  return new Promise(resolve => {
    openModal(`
      <div style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:14px">${escapeHtml(message)}</div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button class="btn" id="styled-confirm-cancel" style="font-size:12px;border-color:var(--inset-edge)">Cancel</button>
        <button class="btn btn--red" id="styled-confirm-ok" style="font-size:12px">Confirm</button>
      </div>`);
    $('styled-confirm-cancel').addEventListener('click', () => { closeModal(); resolve(false); });
    $('styled-confirm-ok').addEventListener('click', () => { closeModal(); resolve(true); });
  });
}

function styledPrompt(message, defaultValue) {
  return new Promise(resolve => {
    openModal(`
      <div style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:12px">${escapeHtml(message)}</div>
      <input class="inp" id="styled-prompt-input" type="text" value="${escapeHtml(defaultValue || '')}" style="font-size:13px;padding:8px 10px;width:100%;margin-bottom:16px">
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button class="btn" id="styled-prompt-cancel" style="font-size:12px;border-color:var(--inset-edge)">Cancel</button>
        <button id="styled-prompt-ok" style="font-family:var(--f-mono);font-size:11px;font-weight:700;background:${AMBER};color:var(--amber-ink);border:none;padding:8px 14px;border-radius:2px;cursor:pointer">OK</button>
      </div>`);
    const input = $('styled-prompt-input');
    input.focus();
    input.select();
    const submit = () => { const v = input.value; closeModal(); resolve(v); };
    $('styled-prompt-cancel').addEventListener('click', () => { closeModal(); resolve(null); });
    $('styled-prompt-ok').addEventListener('click', submit);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  });
}
```

- [ ] **Step 2: Replace the Channel bank list delete confirm (line 1414)**

The enclosing handler at line 1405 is already `async (e) => { ... }`. Current line 1413-1417:

```javascript
      if (act === 'delete') {
        if (!window.confirm('Delete this transcript permanently?')) return;
        await api('/api/transcripts/' + id, { method: 'DELETE' });
        toast('Transcript deleted');
      }
```

Replace with:

```javascript
      if (act === 'delete') {
        if (!(await styledConfirm('Delete this transcript permanently?'))) return;
        await api('/api/transcripts/' + id, { method: 'DELETE' });
        toast('Transcript deleted');
      }
```

- [ ] **Step 3: Replace the speaker rename prompt (line 1724)**

`renameSpeaker` is already `async`. Current:

```javascript
async function renameSpeaker(speaker) {
  const t = detailData;
  if (!t) return;
  const name = (window.prompt('Rename "' + speaker + '" to:', speaker) || '').trim();
  if (!name || name === speaker) return;
```

Replace with:

```javascript
async function renameSpeaker(speaker) {
  const t = detailData;
  if (!t) return;
  const name = ((await styledPrompt('Rename "' + speaker + '" to:', speaker)) || '').trim();
  if (!name || name === speaker) return;
```

- [ ] **Step 4: Replace the transcript detail delete confirm (line 2012)**

Find the `detailAction` function's `delete` branch (it mirrors Step 2's pattern):

```javascript
      if (!window.confirm('Delete this transcript permanently?')) return;
```

Replace with:

```javascript
      if (!(await styledConfirm('Delete this transcript permanently?'))) return;
```

(Confirm the enclosing `detailAction` function is already `async` — it is, since it already `await`s API calls elsewhere in the same function.)

- [ ] **Step 5: Replace the voice profile remove confirm (line 2306)**

Current:

```javascript
      if (!window.confirm('Remove this voice profile from the roster?')) return;
```

Replace with:

```javascript
      if (!(await styledConfirm('Remove this voice profile from the roster?'))) return;
```

Confirm the enclosing handler is `async`; if it is a plain (non-async) function, add `async` to its declaration and check its caller doesn't rely on a synchronous return value (voice roster click handlers in this file are event listeners, whose return value is ignored, so this is safe).

- [ ] **Step 6: Replace the voice clip remove confirm (line 2324)**

Current:

```javascript
      if (!window.confirm('Remove this clip?')) return;
```

Replace with:

```javascript
      if (!(await styledConfirm('Remove this clip?'))) return;
```

Same `async` check as Step 5.

- [ ] **Step 7: Grep for any remaining native dialogs**

Run: `grep -n "window.confirm\|window.prompt" static/rack.js`
Expected: no matches.

- [ ] **Step 8: Manually verify**

In the browser: trigger each of the five actions (delete from Channel bank list, delete from detail view, rename a speaker, remove a voice profile, remove a voice clip) and confirm each now shows the app's dark styled modal instead of a native browser dialog, that Cancel aborts the action, and that Confirm/OK proceeds exactly as before.

- [ ] **Step 9: Commit**

```bash
git add static/rack.js
git commit -m "fix: replace native confirm/prompt dialogs with the app's styled modal"
```

---

### Task 4: Rename "Channel bank" nav label to "Tape library"

**Files:**
- Modify: `static/index.html:61`
- Modify: `static/rack.js:1400`
- Modify: `static/rack.js:214` (comment only)

**Interfaces:** none — pure text change, no new symbols.

**Root cause:** issue #4 — "Channel bank" doesn't read as "your transcript list" to a first-time user, unlike every other nav label. Audit's recommended replacement, "Tape library", is adopted as-is (most consistent with existing tape/deck/transport terminology already in the app).

- [ ] **Step 1: Grep for every occurrence of the string before touching anything**

Run: `grep -rn "Channel bank" static/ .claude/skills/ tests/`
Expected (from investigation during planning): `static/index.html:61` (nav label), `static/rack.js:1400` (page `<h1>`), `static/rack.js:214` (a comment). No hits under `tests/` or the e2e skill files — confirm this is still true before editing; if a new hit appears (e.g. from work done between planning and execution), add it to this task's scope.

- [ ] **Step 2: Update the nav label in `static/index.html`**

Line 61, current:

```html
      <span class="led"></span><span class="lbl">Channel bank</span><span class="badge" id="nav-badge-transcripts"></span>
```

Replace with:

```html
      <span class="led"></span><span class="lbl">Tape library</span><span class="badge" id="nav-badge-transcripts"></span>
```

- [ ] **Step 3: Update the page heading in `static/rack.js`**

Line 1400, current:

```javascript
      <h1 class="t-title">Channel bank</h1>
```

Replace with:

```javascript
      <h1 class="t-title">Tape library</h1>
```

- [ ] **Step 4: Update the comment at line 214**

Current:

```javascript
   (Monitor recents, Channel bank rows, detail meta must always agree.) */
```

Replace with:

```javascript
   (Monitor recents, Tape library rows, detail meta must always agree.) */
```

- [ ] **Step 5: Manually verify**

Load the app in a browser, confirm the left nav rail now reads "Tape library", and that clicking it still navigates to the same transcript-list page with the same heading.

- [ ] **Step 6: Commit**

```bash
git add static/index.html static/rack.js
git commit -m "rename: Channel bank nav label to Tape library for discoverability"
```

---

### Task 5: Add a visual "click to expand" cue to Tape library rows

**Files:**
- Modify: `static/rack.js:1376-1388` (row template inside `loadTranscripts`)
- Modify: `static/rack.css` (append new rule)

**Interfaces:** none new.

**Root cause:** issue #9, finding 1 — clicking a Tape library row only expands an inline `<details>` panel; nothing visually signals that (as opposed to opening the transcript), which is the pattern everywhere else in the app. Lowest-risk fix per the issue's own suggested options: add a clear visual hint rather than changing what a click does (changing click targets risks e2e selector breakage and is a bigger behavior change than this bug warrants).

- [ ] **Step 1: Add a chevron marker to the row template**

Current (lines 1376-1388):

```javascript
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
```

Replace with:

```javascript
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
          <div style="font-family:var(--f-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.05em;color:${sv.color}">${escapeHtml(sv.word)}</div>
        </div>
      </summary>
```

- [ ] **Step 2: Add the chevron rotation rule**

Append to the end of `static/rack.css` (after line 537):

```css
details.unit[open] > summary .row-chevron { transform: rotate(90deg); }
.row-chevron { display: inline-block; transition: transform 0.15s ease; }
```

- [ ] **Step 3: Manually verify**

In the browser, open the Tape library page with at least one transcript present. Confirm: each row shows a small `▸` chevron and a "· click to expand" hint in the meta line; clicking a row rotates the chevron to `▾` (via the `[open]` CSS attribute selector, no JS needed) and expands the details panel; the "Open transcript" button inside the expanded panel still opens the transcript as before.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js static/rack.css
git commit -m "feat: add expand-vs-open visual cue to Tape library rows"
```

---

### Task 6: Add a voice-match nudge when unlabeled speakers exist and the roster is non-empty

**Files:**
- Modify: `static/rack.js` — `renderDetailBody()` (around line 1993-2005) and the transcript tab's rendering.

**Interfaces:**
- Consumes: `GET /api/voices` (existing, returns array of `{name, ...}`), `t.segments` (existing), `t.has_audio` (existing), `t.voice_match_job` (existing, checked via `llmJobActive`).
- Produces: nothing new for later tasks.

**Root cause:** issue #9, finding 2 — after enrolling a voice and uploading audio with the same speaker, nothing hints that "Match against voice roster" (existing button, `data-dact="voicematch"`) would help. Add a lightweight banner on the transcript tab when: the roster is non-empty, the transcript has unlabeled/generic-labeled segments, audio is available to match against, and no voice-match job is already running.

- [ ] **Step 1: Add an "unlabeled speaker" detector**

Add near `markedSpeakers()` (after line 1739 in the current file) a small helper:

```javascript
function hasUnlabeledSpeakers(t) {
  return (t.segments || []).some(sg => {
    const sp = (sg.speaker || '').trim();
    return !sp || /^Speaker \d+$/i.test(sp);
  });
}
```

- [ ] **Step 2: Fetch the roster and render the nudge in the transcript tab**

Current `renderDetailBody()` (lines 1993-2005):

```javascript
async function renderDetailBody() {
  const t = detailData;
  const body = $('detail-body');
  if (S.detailTab === 'transcript') {
    const vm = llmJobActive(t.voice_match_job) ? jobRunningUnit(t.voice_match_job, 'Voice match') : '';
    body.innerHTML = vm + '<div class="unit" style="border-radius:3px;margin-top:' + (vm ? '10px' : '0') + ';padding:6px 32px">' + segmentsHtml(t) + '</div>';
  } else if (S.detailTab === 'corrected') {
    body.innerHTML = correctedHtml(t);
  } else {
    body.innerHTML = '<div class="empty-unit">Loading summary…</div>';
    body.innerHTML = await summaryHtml(t);
  }
}
```

Replace the `transcript` branch with:

```javascript
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
    body.innerHTML = vm + nudge + '<div class="unit" style="border-radius:3px;margin-top:' + (vm || nudge ? '10px' : '0') + ';padding:6px 32px">' + segmentsHtml(t) + '</div>';
    body.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact)));
  } else if (S.detailTab === 'corrected') {
```

(The extra `data-dact` listener binding on `#detail-body` here is needed because the row-level `Match now` button lives inside `detail-body`, which does not get the delegated `[data-dact]` binding that `renderDetail()` attaches to the whole `root` — that delegated listener only covers the header action bar rendered at `renderDetail()` time, not content injected later by `renderDetailBody()`.)

- [ ] **Step 3: Manually verify**

1. Enroll a voice profile ("Alice") via the Voice roster page (existing flow).
2. Upload a second transcript containing a speaker whose segments are labeled `Speaker 1`/`Speaker 2` (i.e. not yet renamed/matched) and that has stored audio.
3. Open that transcript's detail view, transcript tab. Confirm the nudge banner appears above the segment list, mentioning the enrolled voice count, with a working "Match now" button that triggers the same voice-match flow as the header's "Match against voice roster" button.
4. Confirm the nudge does **not** appear when: the roster is empty, the transcript has no unlabeled speakers, there's no stored audio, or a voice-match job is already running.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: nudge voice-match when unlabeled speakers and a non-empty roster exist"
```

---

### Task 7: Add copy/download export for transcript, corrected, and summary views

**Files:**
- Modify: `static/rack.js` — new helpers, plus edits to `segmentsHtml`/`correctedHtml`/`summaryHtml`'s callers to attach export controls per tab.

**Interfaces:**
- Produces: `exportPlainText(t): string` (raw transcript as plain text), `copyToClipboard(text: string): Promise<void>`, `downloadTextFile(filename: string, text: string): void`.
- Consumes: `t.segments`, `t.full_text`, `t.corrected_text`, and the summary object already fetched inside `summaryHtml`.

**Root cause:** issue #7 — no way to get transcript/corrected/summary text out of the app short of manual text selection. Add a small toolbar (Copy, Download) above each of the three detail tabs' content.

- [ ] **Step 1: Add the export helpers**

Add near `escapeHtml`/`toast` (any top-level utility area — place directly before `function correctedHtml(t)` at line 1847):

```javascript
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
```

- [ ] **Step 2: Wire the toolbar into the transcript tab**

In the `renderDetailBody()` transcript branch edited in Task 6, change the final `body.innerHTML` line from:

```javascript
    body.innerHTML = vm + nudge + '<div class="unit" style="border-radius:3px;margin-top:' + (vm || nudge ? '10px' : '0') + ';padding:6px 32px">' + segmentsHtml(t) + '</div>';
```

to:

```javascript
    body.innerHTML = vm + nudge + exportToolbarHtml('transcript') + '<div class="unit" style="border-radius:3px;margin-top:' + (vm || nudge ? '10px' : '0') + ';padding:6px 32px">' + segmentsHtml(t) + '</div>';
```

- [ ] **Step 3: Wire the toolbar into the corrected tab**

Current (in the `else if (S.detailTab === 'corrected')` branch):

```javascript
  } else if (S.detailTab === 'corrected') {
    body.innerHTML = correctedHtml(t);
```

Replace with:

```javascript
  } else if (S.detailTab === 'corrected') {
    body.innerHTML = (t.corrected_text ? exportToolbarHtml('corrected') : '') + correctedHtml(t);
```

- [ ] **Step 4: Wire the toolbar into the summary tab**

Current:

```javascript
  } else {
    body.innerHTML = '<div class="empty-unit">Loading summary…</div>';
    body.innerHTML = await summaryHtml(t);
  }
```

Replace with:

```javascript
  } else {
    body.innerHTML = '<div class="empty-unit">Loading summary…</div>';
    body.innerHTML = (t.has_summary ? exportToolbarHtml('summary') : '') + await summaryHtml(t);
  }
```

(Summary export re-fetches `/api/transcripts/{id}/summary` and formats it directly in Step 5, rather than scraping the rendered `.unit` DOM — the API response is the source of truth, not the derived HTML, and scraping would also risk picking up Task 2's failure-banner markup, which is also a `.unit`.)

- [ ] **Step 5: Add the click handler for export buttons**

`renderDetail()` (around line 1988) already has:

```javascript
  root.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact)));
  // Delegated: segment rows re-render on search/poll, the container doesn't.
  $('detail-body').addEventListener('click', detailBodyClick);
```

Since export buttons are re-created every time `renderDetailBody()` runs (same lifecycle problem as Task 6's nudge button), extend `detailBodyClick` (find its definition) to also handle export clicks. Add at the top of `detailBodyClick`'s body:

```javascript
async function summaryPlainText(transcriptId) {
  const s = await api('/api/transcripts/' + transcriptId + '/summary');
  const sections = [
    ['Summary', s.short_summary ? [s.short_summary] : []],
    ['Key points', s.key_points || []],
    ['Action items', s.action_items || []],
    ['Decisions', s.decisions || []],
  ].filter(([, items]) => items.length);
  return sections.map(([title, items]) => title + '\n' + items.map(it => '- ' + it).join('\n')).join('\n\n');
}

async function handleExportClick(kind, copy) {
  const t = detailData;
  let text = '';
  if (kind === 'transcript') text = transcriptPlainText(t);
  else if (kind === 'corrected') text = t.corrected_text || '';
  else if (kind === 'summary') {
    try { text = await summaryPlainText(t.id); }
    catch (e) { toast('Could not load summary to export: ' + e.message, 'error'); return; }
  }
  if (copy) copyToClipboard(text);
  else downloadTextFile((t.title || t.filename || 'transcript').replace(/[^\w.-]+/g, '_') + '-' + kind + '.txt', text);
}
```

First, read the actual current body of `detailBodyClick` in full (its definition, referenced at line 1990 as the handler bound via `$('detail-body').addEventListener('click', detailBodyClick);`) — do not guess its contents. Then prepend this dispatch at the very top of its body, before any of its existing logic:

```javascript
  const copyBtn = e.target.closest('[data-export-copy]');
  const dlBtn = e.target.closest('[data-export-dl]');
  if (copyBtn || dlBtn) { handleExportClick((copyBtn || dlBtn).dataset.exportCopy || (copyBtn || dlBtn).dataset.exportDl, !!copyBtn); return; }
```

(`e` here is whatever the existing function's event parameter is already named — match it, don't introduce a second parameter name.) Only prepend; do not remove or restructure any of the function's existing logic below this block. The early `return` only fires on an export-button click, so every other existing code path in `detailBodyClick` is unaffected.

- [ ] **Step 6: Manually verify**

For each of the three tabs (transcript, corrected, summary) on a completed transcript with a corrected pass and a summary already run:
1. Click **Copy** — confirm a "Copied to clipboard" toast appears and pasting elsewhere yields readable plain text (speaker-prefixed lines for transcript, corrected text as-is, summary sections with bullet items for summary).
2. Click **Download .txt** — confirm a `.txt` file downloads with a sensible filename and the same content as Copy produced.
3. Confirm the toolbar does not appear on the corrected tab when no correction has run yet, and not on the summary tab when no summary exists yet (both already handled by the `t.corrected_text ? ... : ''` / `t.has_summary ? ... : ''` guards in Steps 3–4).

- [ ] **Step 7: Commit**

```bash
git add static/rack.js
git commit -m "feat: add copy/download export for transcript, corrected, and summary views"
```

---

### Task 8: Add search and sort to the Tape library list

**Files:**
- Modify: `static/rack.js` — `loadTranscripts()` (list render, currently the function containing lines ~1330-1426) and the global `S` state object.

**Interfaces:**
- Consumes: `S` (existing global UI-state object — confirm it's a plain mutable object, as used elsewhere e.g. `S.query`, `S.detailTab`, `S.page`).
- Produces: `S.bankQuery` (string), `S.bankSort` (one of `'date-desc'|'date-asc'|'title-asc'`), a module-level `bankListCache` array, and a `renderBankRows()` function — `renderBankRows` is consumed by Task 9 nowhere directly, but Task 9's rename handler must look up rows from `bankListCache`, not from a `list` variable local to a one-shot render (see Task 9).

**Root cause:** issue #8 — the Tape library list has no search/filter/sort, and duplicate filenames are indistinguishable at a glance. This task adds client-side search (matches title or filename, case-insensitive) and a sort dropdown, applied to the already-fetched list before rendering (`GET /api/transcripts` already returns everything needed; no backend change required for this part).

**Design constraint:** `loadTranscripts()` currently rebuilds `root.innerHTML` from scratch on every call. If the search input's own `input` handler called `loadTranscripts()` again, that would destroy and recreate the `#bank-search` node on every keystroke, dropping keyboard focus and cursor position (this file already solves the identical problem for the detail-search box at `static/rack.js:1973-1977` by only re-rendering the body, not the whole page). It would also refetch from the server on every keystroke for a purely client-side filter. This task avoids both problems by splitting `loadTranscripts()` into: a one-time full render (fetches from the server, renders the header/search/sort chrome plus an empty `#bank-rows` container, called on page navigation and by the poll timer) and `renderBankRows()` (filters/sorts the cached list and replaces only `#bank-rows`' contents, called by the search/sort input handlers — no refetch, no full-page rebuild, focus stays put).

- [ ] **Step 1: Read the full current `loadTranscripts()` function before changing it**

Search for `async function loadTranscripts` and read the complete function body (fetch call through the closing `}` after the `bankPollTimer` scheduling, which Task 5's earlier excerpt showed ending around line 1426). Confirm the exact fetch line (e.g. `const list = await api('/api/transcripts?...')`) and the exact variable names used for the fetched array, the `active` count, and the `[data-act]` delegation block — the steps below assume the fetched array is called `list` and the delegation block is `root.querySelectorAll('[data-act]').forEach(...)`, matching every prior excerpt in this plan; adjust step code to the real names if they differ.

- [ ] **Step 2: Add module-level cache state**

Near the top-level `let bankPollTimer = null;` declaration (shown in Task 5's context, just above `loadTranscripts`), add:

```javascript
let bankListCache = [];
```

- [ ] **Step 3: Split `loadTranscripts()` into fetch+chrome vs. filter+rows**

Restructure `loadTranscripts()` so that after fetching (`const list = await api(...)`), it stores `bankListCache = list;`, computes `active` as before, renders the page chrome (heading, status line, search input, sort select, and an empty `<div id="bank-rows"></div>` placeholder) into `root.innerHTML` **without** the row markup inlined, then calls `renderBankRows()` to fill `#bank-rows`, and finally attaches the search/sort input listeners and the `[data-act]` delegation once, on `root` (delegation on the stable `root` ancestor keeps working even though `#bank-rows`' contents get replaced independently — event bubbling doesn't care that the specific row element is new).

```javascript
async function loadTranscripts() {
  const root = $('page-transcripts'); // use whatever ID the existing code already reads into `root`
  let list;
  try { list = await api('/api/transcripts?limit=200'); } catch (e) { toast(e.message, 'error'); return; } // keep the existing fetch call/URL as found in Step 1; this is illustrative
  bankListCache = list;
  const active = list.filter(t => ['pending', 'processing'].includes(t.status)).length; // keep the existing `active` computation as found in Step 1

  root.innerHTML = `
    <div class="page-head">
      <h1 class="t-title">Tape library</h1>
      <div class="page-status" id="bank-status" style="color:${GREEN}">${ledDot(GREEN, true, 9)}${list.length} channels · ${active} active</div>
    </div>
    <div style="display:flex;gap:10px;margin-bottom:14px;padding:0 4px">
      <input id="bank-search" class="inp" type="text" placeholder="Search title or filename…" value="${escapeHtml(S.bankQuery || '')}" style="font-size:12px;padding:8px 10px;flex:1;max-width:320px">
      <select id="bank-sort" class="inp" style="font-size:12px;padding:8px 10px">
        <option value="date-desc" ${(!S.bankSort || S.bankSort === 'date-desc') ? 'selected' : ''}>Newest first</option>
        <option value="date-asc" ${S.bankSort === 'date-asc' ? 'selected' : ''}>Oldest first</option>
        <option value="title-asc" ${S.bankSort === 'title-asc' ? 'selected' : ''}>Title A–Z</option>
      </select>
    </div>
    <div id="bank-rows"></div>`;

  renderBankRows();

  $('bank-search').addEventListener('input', () => {
    S.bankQuery = $('bank-search').value;
    renderBankRows();
  });
  $('bank-sort').addEventListener('change', () => {
    S.bankSort = $('bank-sort').value;
    renderBankRows();
  });
  root.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', async (e) => { /* ... */ }));
  // ^ keep this block's actual existing body verbatim (delete/cancel/resume/retry handlers from Task 9 etc.)
  //   — it's shown collapsed here only because Step 1 already has you reading the real version.

  clearTimeout(bankPollTimer);
  if (active > 0 && S.page === 'transcripts') {
    bankPollTimer = setTimeout(() => { if (S.page === 'transcripts') loadTranscripts(); }, 4000);
  }
}
```

Note the `[data-act]` delegation is bound once per `loadTranscripts()` call (on page load/navigation and on each 4-second poll while something's active) — it is never rebound by `renderBankRows()`, and it doesn't need to be, since it's attached to `root`, not to individual rows.

- [ ] **Step 4: Write `renderBankRows()`**

```javascript
function renderBankRows() {
  const rowsContainer = $('bank-rows');
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
  if (statusEl) statusEl.innerHTML = `${ledDot(GREEN, true, 9)}${filtered.length} of ${bankListCache.length} channels`;

  if (!bankListCache.length) {
    rowsContainer.innerHTML = '<div class="empty-unit">No signals on the bank — load a tape on the Transcribe deck</div>';
    return;
  }
  if (!filtered.length) {
    rowsContainer.innerHTML = '<div class="empty-unit">No transcripts match your search</div>';
    return;
  }
  rowsContainer.innerHTML = filtered.map(t => {
    // ... the exact per-row template body from the existing loadTranscripts() (the
    // `<details class="unit" ...>...</details>` block, including Task 5's chevron
    // and Task 9's Rename button) — move it here verbatim, referencing `t` as the
    // loop variable exactly as it already does.
  }).join('');
}
```

Move the entire existing row-template arrow function (the `list.map(t => { ... }).join('')` body that produces `rows`, including this plan's Task 5 chevron edit and Task 9's Rename button once those tasks have landed) into `renderBankRows()`'s `.map(t => { ... })`, verbatim, operating on `filtered` instead of `list`.

- [ ] **Step 5: Manually verify**

1. Load the Tape library page with at least 3 transcripts of differing titles/dates.
2. Click into the search box and type several characters continuously without re-clicking — confirm focus and cursor position are **not** lost between keystrokes (this was the bug this task's design avoids) and the list narrows live to title/filename matches, with the "N of M channels" line updating.
3. Open the browser Network tab while typing in the search box — confirm no `/api/transcripts` request fires per keystroke (only the initial page-load fetch and any active-job poll every 4s).
4. Clear the search — confirm the full list returns.
5. Switch the sort dropdown between "Newest first", "Oldest first", "Title A–Z" — confirm row order changes accordingly, without a network request.
6. With a job actively running (so the 4-second poll is active), confirm the poll's `loadTranscripts()` refresh doesn't reset the search box's typed value or dropdown selection (it re-renders the whole page chrome including the input's `value="${escapeHtml(S.bankQuery || '')}"`, so the previously-typed text reappears, though a poll firing mid-keystroke could still cost focus — acceptable given polls are 4s apart and typing bursts are much shorter, but call this out if manual testing shows it's noticeable).

- [ ] **Step 5: Commit**

```bash
git add static/rack.js
git commit -m "feat: add search and sort controls to the Tape library list"
```

---

### Task 9: Add an inline rename control to Tape library rows

**Files:**
- Modify: `static/rack.js` — the `acts` array building block inside `renderBankRows()`'s row template (that function is produced by Task 8; do this task after Task 8 lands).

**Interfaces:**
- Consumes: `styledPrompt` (from Task 3), `bankListCache` (from Task 8, module-level array of the raw fetched list), existing `PATCH /api/transcripts/{id}` (already supports `{"title": "..."}`, confirmed in `app.py:659-674` — no backend change needed).
- Produces: nothing new for later tasks.

**Root cause:** issue #8's "consider also" — duplicate filenames are visually indistinguishable; the backend already supports renaming via `PATCH`, but there's no UI control for it (only used by an unrelated speaker-rename flow via `styledPrompt`). Adding a "Rename" action button beside the existing row actions closes this gap cheaply now that Task 3 already built the styled-prompt primitive.

- [ ] **Step 1: Add a Rename action button**

Locate the `acts` array building code inside `renderBankRows()`'s row-template `.map(t => { ... })` (moved there by Task 8). It's right before the line pushing the "Delete" button (`acts.push('<button class="btn btn--red" ... data-act="delete" ...')`, originally at line 1375 before Task 8's restructuring):

```javascript
    acts.push('<button class="btn btn--red" style="font-size:12px;padding:6px 12px" data-act="delete" data-id="' + t.id + '">Delete</button>');
```

Insert a new line immediately before it:

```javascript
    acts.push('<button class="btn" style="font-size:12px;padding:6px 12px;border-color:var(--inset-edge)" data-act="rename" data-id="' + t.id + '">Rename</button>');
    acts.push('<button class="btn btn--red" style="font-size:12px;padding:6px 12px" data-act="delete" data-id="' + t.id + '">Delete</button>');
```

- [ ] **Step 2: Handle the new action**

The `[data-act]` delegation is bound once, on `root`, inside `loadTranscripts()` (per Task 8's Step 3) and already has an `if (act === 'delete') { ... }` branch. Add a sibling branch before it. Since renaming changes what's rendered but not the fetched set until a refresh, re-run `renderBankRows()` after a successful rename rather than a full `loadTranscripts()` (avoids an unnecessary refetch and matches Task 8's split):

```javascript
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
```

Note this branch returns early and skips whatever trailing refresh call the existing delegation block does after its other branches (visible once you've read the real function per Task 8's Step 1) — the other branches (cancel/resume/retry/delete) still need that trailing refresh since they change server-side job/transcript state in ways a client-side cache patch can't cheaply mirror; rename is the one case where patching the cache in place is correct and cheaper.

- [ ] **Step 3: Manually verify**

1. Click **Rename** on a Tape library row — confirm the styled prompt modal opens pre-filled with the current title (or filename if untitled).
2. Enter a new title and confirm — confirm the row's displayed title updates after the list reloads, and a "Renamed" toast appears.
3. Cancel the prompt — confirm nothing changes.
4. Confirm the detail view (`GET /api/transcripts/{id}`) reflects the new title too (it will, since it's the same `title` column).

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: add inline rename control to Tape library rows"
```

---

## Final verification (after all tasks)

- [ ] Run the full backend suite: `python -m pytest -q` — expect 121 passed, no new failures or new warnings.
- [ ] Run `grep -n "Channel bank\|window.confirm\|window.prompt" static/*.js static/*.html` — expect no matches.
- [ ] Re-run the relevant e2e-ux-audit journeys (Journeys 1, 3, 4, 5, 6) manually or via the `e2e-ux-audit` skill against a freshly started isolated server, and confirm each of the original findings (#4, #5, #6, #7, #8, #9) no longer reproduces.
- [ ] Close issues #2, #4, #5, #6, #7, #8, #9 on GitHub once merged (`gh issue close <n>` with a short "fixed in <commit>" comment), after user confirmation — closing issues is a visible action on shared state and should be confirmed, not assumed.
