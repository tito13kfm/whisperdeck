# Run History Phase 2: Correction History + Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every past correction run against a transcript stays inspectable, and any two can be diffed word-for-word — so you can tell whether a cheaper provider/model held up on quality.

**Architecture:** `LlmJob` gains a `result_json` snapshot column, populated when a correction job completes. A new endpoint lists a transcript's correction-job history. The frontend gets a generic, reusable compare-modal (picker of two items + diff render) and a word-level LCS diff utility, wired into the Corrected tab. A one-time backfill fills `result_json` for the latest pre-existing completed correction job per transcript, from `Transcript.corrected_text`, so history isn't empty on day one.

**Tech Stack:** Python/FastAPI/SQLAlchemy backend, vanilla JS frontend (no bundler/npm).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-06-run-history-and-comparison-design.md`. This plan implements that spec's Phase 2 (correction only — summary and rediarize come in Phase 4, transcription comes in Phase 3).
- Depends on Phase 1 being merged (this plan does not re-touch `Summary` or the export header — assume those already exist).
- `Transcript.corrected_text` / `Summary` remain canonical for "current output" everywhere they're already read (Corrected tab, Summary tab, exports). `LlmJob.result_json` is a side channel used only by the new history/diff UI — nothing existing changes to read from it.
- No frontend test framework — frontend steps verified by manual browser check against a throwaway server instance (see Phase 1's Task 3 Step 3 for the pattern).
- The word-diff utility introduced here (`diffTokens`, `textDiffHtml`, `openCompareModal`) is designed to be reused unchanged by Phase 3 (transcription versions) and Phase 4 (summary/rediarize) — do not rename its parameters without checking those plans.

---

### Task 1: Add `LlmJob.result_json`, populate it on correction completion, backfill existing data

**Files:**
- Modify: `database/__init__.py:78-96` (LlmJob model), `database/__init__.py:252-255` (ensure_columns + new backfill call in `init_db`)
- Modify: `services/llm_jobs.py:195-214` (correction branch of `run_llm_job`)
- Test: `tests/test_llm_jobs.py`

**Interfaces:**
- Produces: `LlmJob.result_json` (nullable JSON). For `kind == "correction"`: `{"corrected_text": "..."}`. Populated going forward by `run_llm_job`; backfilled once for pre-existing data by `backfill_llm_job_result_snapshots(SessionLocal)`.

- [ ] **Step 1: Write the failing test for result_json on completion**

Add to `tests/test_llm_jobs.py`, after `test_run_llm_job_correction_completes_with_progress` (after line 188):

```python
def test_run_llm_job_correction_saves_result_snapshot(db_session):
    segs = [{"start": 0, "end": 1, "speaker": "S", "text": "hello"}]
    user, t = _make_user_and_transcript(db_session, segments=segs)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse("S: fixed hello"))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.result_json == {"corrected_text": t.corrected_text}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_run_llm_job_correction_saves_result_snapshot -v`
Expected: FAIL with `AttributeError: 'LlmJob' object has no attribute 'result_json'`.

- [ ] **Step 3: Add the column and migration**

In `database/__init__.py`, in the `LlmJob` class (around line 93, the `dismissed` column line), add:

```python
    dismissed = Column(Boolean, default=False)  # hides a terminal job from the Queue screen only
    result_json = Column(JSON, nullable=True)  # output snapshot for history/diff — see run_llm_job
```

In `init_db()`, change line 253 from:

```python
    ensure_columns(engine, "llm_jobs", {"dismissed": "BOOLEAN DEFAULT 0"})
```

to:

```python
    ensure_columns(engine, "llm_jobs", {"dismissed": "BOOLEAN DEFAULT 0", "result_json": "JSON"})
```

- [ ] **Step 4: Populate it in `run_llm_job`'s correction branch**

In `services/llm_jobs.py`, in the correction branch (lines 205-214), change:

```python
            result = await correct_transcript(
                db, transcript, api_key=api_key, provider_name=job.provider,
                model=job.model, provider_config=provider_config,
                progress_cb=progress, cancel_cb=cancelled,
            )
            if result == "ok":
                _finish(db, job, "completed")
            elif result == "failed":
                _finish(db, job, "failed", transcript.correction_error)
```

to:

```python
            result = await correct_transcript(
                db, transcript, api_key=api_key, provider_name=job.provider,
                model=job.model, provider_config=provider_config,
                progress_cb=progress, cancel_cb=cancelled,
            )
            if result == "ok":
                job.result_json = {"corrected_text": transcript.corrected_text}
                db.commit()
                _finish(db, job, "completed")
            elif result == "failed":
                _finish(db, job, "failed", transcript.correction_error)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_run_llm_job_correction_saves_result_snapshot -v`
Expected: PASS

- [ ] **Step 6: Write the failing test for the backfill**

Add a new file `tests/test_llm_job_history_backfill.py`:

```python
"""Backfill: pre-existing completed LlmJob rows (from before result_json
existed) get a snapshot filled in from the transcript's current output, so
the run-history picker isn't empty for old data on first upgrade."""
from database import LlmJob, Transcript, User, backfill_llm_job_result_snapshots


def _session_factory(db_session):
    return lambda: db_session


class _NoCloseSession:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def test_backfill_fills_latest_completed_correction_job_only(db_session):
    user = User(username="backfillop", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="f.mp3", status="completed",
        full_text="raw", corrected_text="the final corrected text",
    )
    db_session.add(t)
    db_session.commit()

    older = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction", status="completed", provider="groq", model="m1")
    newer = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction", status="completed", provider="groq", model="m2")
    db_session.add_all([older, newer])
    db_session.commit()

    backfill_llm_job_result_snapshots(lambda: _NoCloseSession(db_session))

    db_session.refresh(older)
    db_session.refresh(newer)
    assert older.result_json is None  # superseded run — no snapshot ever existed for it
    assert newer.result_json == {"corrected_text": "the final corrected text"}


def test_backfill_is_idempotent(db_session):
    user = User(username="backfillop2", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="completed", corrected_text="text")
    db_session.add(t)
    db_session.commit()
    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction", status="completed", provider="groq", model="m1")
    db_session.add(job)
    db_session.commit()

    backfill_llm_job_result_snapshots(lambda: _NoCloseSession(db_session))
    db_session.refresh(job)
    assert job.result_json == {"corrected_text": "text"}

    # Second run must not error and must not touch already-backfilled rows.
    backfill_llm_job_result_snapshots(lambda: _NoCloseSession(db_session))
    db_session.refresh(job)
    assert job.result_json == {"corrected_text": "text"}
```

- [ ] **Step 7: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_job_history_backfill.py -v`
Expected: FAIL with `ImportError: cannot import name 'backfill_llm_job_result_snapshots'`.

- [ ] **Step 8: Implement the backfill function**

In `database/__init__.py`, add this function after `ensure_columns` (after line 234, before `def init_db`):

```python
def backfill_llm_job_result_snapshots(SessionLocal, kinds: tuple = ("correction",)) -> None:
    """One-time backfill: completed LlmJob rows that predate result_json have
    no output snapshot. Fill in the latest completed job per (transcript_id,
    kind) from the transcript's current output, so the run-history picker
    isn't empty for pre-existing data. Older, already-superseded completed
    jobs never had their output retained anywhere — they stay snapshot-less
    by design (not a bug: nothing before this feature kept that history).
    Safe to call on every startup — only touches rows still missing a
    snapshot, so it's a no-op once backfilled."""
    from sqlalchemy import func

    db = SessionLocal()
    try:
        latest_ids = (
            db.query(LlmJob.transcript_id, LlmJob.kind, func.max(LlmJob.id).label("max_id"))
            .filter(LlmJob.status == "completed", LlmJob.result_json.is_(None), LlmJob.kind.in_(kinds))
            .group_by(LlmJob.transcript_id, LlmJob.kind)
            .all()
        )
        for transcript_id, kind, max_id in latest_ids:
            job = db.query(LlmJob).filter(LlmJob.id == max_id).first()
            transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
            if not job or not transcript:
                continue
            if kind == "correction" and transcript.corrected_text:
                job.result_json = {"corrected_text": transcript.corrected_text}
        db.commit()
    finally:
        db.close()
```

Then call it from `init_db()`, right after the `ensure_columns(engine, "llm_jobs", ...)` line from Step 3:

```python
    SessionLocal = sessionmaker(bind=engine)
    backfill_llm_job_result_snapshots(SessionLocal)
    return engine, SessionLocal, migrated_tables
```

(This replaces the existing `SessionLocal = sessionmaker(bind=engine)` / `return engine, SessionLocal, migrated_tables` pair at the end of `init_db()` — same two lines, just with the backfill call inserted between them.)

Finally, add `backfill_llm_job_result_snapshots` to the `__all__` list at the bottom of the file (next to `"LlmJob"`).

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_job_history_backfill.py tests/test_llm_jobs.py -v`
Expected: PASS

- [ ] **Step 10: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 11: Commit**

```bash
git add database/__init__.py services/llm_jobs.py tests/test_llm_jobs.py tests/test_llm_job_history_backfill.py
git commit -m "feat: snapshot correction output on LlmJob for run history"
```

---

### Task 2: `GET /api/transcripts/{id}/runs/{kind}` endpoint

**Files:**
- Modify: `app.py` (insert after `voice_match_transcript`, i.e. after line 1088)
- Test: `tests/test_llm_jobs.py`

**Interfaces:**
- Consumes: `LlmJob.result_json` from Task 1.
- Produces: `GET /api/transcripts/{transcript_id}/runs/{kind}` → `{"runs": [{"id", "provider", "model", "status", "created_at", "result"}, ...]}`, newest first, `kind` restricted to `correction | summary | rediarize` (summary/rediarize return an empty list until Phase 4 populates their `result_json` — the endpoint itself is kind-agnostic from day one so Phase 4 doesn't need to touch it).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm_jobs.py`, after `test_summarize_route_enqueues_job` (after line 304, or after Phase 1's `test_summary_endpoint_exposes_provider` if that's already present):

```python
def test_runs_endpoint_lists_correction_history_newest_first(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    transcript_id = _upload(client).json()["id"]

    first = client.post(f"/api/transcripts/{transcript_id}/correct", data={"provider": "groq", "model": "m1"}).json()["job"]
    client.post(f"/api/jobs/{first['id']}/cancel")
    second = client.post(f"/api/jobs/{first['id']}/rerun").json()["job"]

    runs = client.get(f"/api/transcripts/{transcript_id}/runs/correction").json()["runs"]
    assert [r["id"] for r in runs] == [second["id"], first["id"]]
    assert runs[0]["provider"] == "groq" and runs[0]["model"] == "m1"


def test_runs_endpoint_rejects_unknown_kind(client):
    transcript_id = _upload(client).json()["id"]
    r = client.get(f"/api/transcripts/{transcript_id}/runs/bogus")
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_runs_endpoint_lists_correction_history_newest_first -v`
Expected: FAIL with 404 (route doesn't exist).

- [ ] **Step 3: Add the endpoint**

In `app.py`, insert after `voice_match_transcript` (after line 1088, before the blank line at 1090):

```python
@app.get("/api/transcripts/{transcript_id}/runs/{kind}")
async def transcript_runs(
    transcript_id: int,
    kind: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """History of every LLM job of `kind` run against this transcript,
    including dismissed ones (dismiss only hides a job from the Queue
    screen — the row and its result_json snapshot persist). Powers the
    run-comparison picker on the detail page."""
    if kind not in ("correction", "summary", "rediarize"):
        raise HTTPException(status_code=400, detail=f"Unknown run kind '{kind}'")
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    jobs = (
        db.query(LlmJob)
        .filter(LlmJob.transcript_id == transcript_id, LlmJob.kind == kind)
        .order_by(LlmJob.id.desc())
        .all()
    )
    return {"runs": [
        {
            "id": j.id, "provider": j.provider, "model": j.model, "status": j.status,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "result": j.result_json,
        }
        for j in jobs
    ]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py -v -k runs_endpoint`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_llm_jobs.py
git commit -m "feat: add GET /api/transcripts/{id}/runs/{kind} history endpoint"
```

---

### Task 3: Generic compare-modal + word-diff utility, wired into the Corrected tab

**Files:**
- Modify: `static/rack.js` (new section near `transcriptPlainText`, i.e. before line 1988; toolbar at lines 2135-2148; `detailAction` at line 2223)

**Interfaces:**
- Consumes: `GET /api/transcripts/{id}/runs/correction` from Task 2; `openModal`/`closeModal` (existing, line 292); `escapeHtml`, `timeAgo`, `toast`, `$`, `api` (existing helpers used throughout the file).
- Produces (for Phase 3 and Phase 4 to reuse verbatim):
  - `diffTokens(oldTokens: string[], newTokens: string[]): Array<[type, token]>` where `type` is `'eq' | 'del' | 'ins'`.
  - `textDiffHtml(oldText: string, newText: string): string` (HTML with `<del>`/`<ins>` spans).
  - `openCompareModal(title: string, fetchItems: () => Promise<Array<{id, optionLabel, result}>>, extractText: (result) => string, renderDiff: (oldText, newText) => string): Promise<void>`.

- [ ] **Step 1: Add the diff utility and generic compare modal**

In `static/rack.js`, insert this new section immediately before `function transcriptPlainText(t) {` (line 1989):

```javascript
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
    <div style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:14px">${escapeHtml(title)}</div>
    <div style="display:flex;gap:10px;margin-bottom:14px">
      <select id="compare-item-a" class="inp" style="flex:1;font-size:12px;padding:8px 10px">${optionHtml}</select>
      <select id="compare-item-b" class="inp" style="flex:1;font-size:12px;padding:8px 10px">${optionHtml}</select>
    </div>
    <div id="compare-diff-out" style="max-height:50vh;overflow:auto;font-size:13px;line-height:1.6;white-space:pre-wrap;padding:10px;border:1px solid var(--inset-edge)"></div>
    <div style="display:flex;justify-content:flex-end;margin-top:14px">
      <button class="btn" id="compare-close" style="font-size:12px;border-color:var(--inset-edge)">Close</button>
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
```

- [ ] **Step 2: Add a "History" button to the Corrected tab toolbar**

In `static/rack.js:2145`, change:

```javascript
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rerun" ${llmJobActive(t.correction_job) ? 'disabled title="Correction job already queued"' : ''}>Re-run correction</button>
```

to (adding a new button right after it):

```javascript
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rerun" ${llmJobActive(t.correction_job) ? 'disabled title="Correction job already queued"' : ''}>Re-run correction</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="correction-history">Correction history</button>
```

- [ ] **Step 3: Wire the button to `openCompareModal`**

In `static/rack.js`, in `detailAction` (starting line 2223), add a new branch right after the `rerun` branch (after line 2262, before `retranscribe`):

```javascript
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
```

- [ ] **Step 4: Manual browser verification**

1. Spin up an isolated instance (per Phase 1's Task 3 Step 3 pattern), register, upload a short audio fixture, wait for it to complete with auto-correct on.
2. On the detail page's Corrected tab, click "Correction history" — confirm a modal opens with one option per completed correction run (only one exists so far).
3. Trigger "Re-run correction" with a different provider/model (e.g. a second groq model, or `local`/`local_llm` if configured), wait for it to complete.
4. Click "Correction history" again — confirm two options now appear, newest first, each labeled `provider/model · time ago`.
5. Select each option in both dropdowns — confirm the diff pane highlights word-level insertions (green) and deletions (red/strikethrough) between the two runs' corrected text.
6. Close the modal — confirm it dismisses cleanly and the detail page is unaffected.

- [ ] **Step 5: Commit**

```bash
git add static/rack.js
git commit -m "feat: correction run history with word-level diff compare"
```

---

## Done criteria

- `pytest -q` passes with 5 new backend tests on top of Phase 1's suite (Task 1: 3 tests across two files, Task 2: 2 tests).
- Manual browser check (Task 3, Step 4) confirms the full history → pick two → diff flow works for correction runs.
