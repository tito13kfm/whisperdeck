# Run History Phase 1: Export Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every copied/downloaded transcript, correction, or summary carries a leading line naming the provider/model that produced it; `Summary` rows record their provider (currently only `model` is stored).

**Architecture:** Two independent, small changes: (1) a new `provider` column on `Summary`, populated by `TranscriptionService.summarize()` and exposed by `_serialize_summary`; (2) `handleExportClick` in `static/rack.js` prepends a one-line `[stage with provider/model]` header before copying/downloading.

**Tech Stack:** Python/FastAPI/SQLAlchemy backend, vanilla JS frontend (no bundler, no npm — `static/rack.js` is a single hand-written file).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-06-run-history-and-comparison-design.md`. This plan implements only that spec's Phase 1.
- No frontend test framework exists in this repo — frontend steps are verified by manual browser check (Playwright MCP against a throwaway server instance), following the same pattern used for the queue-row-collapse fix (see `.claude/skills/e2e-ux-audit/SKILL.md`'s Setup section for spinning up an isolated instance).
- SQLite migrations use the existing `ensure_columns(engine, table_name, {col: sql_type})` helper in `database/__init__.py` — additive, idempotent, no table rebuild.

---

### Task 1: Add `Summary.provider` column and populate it

**Files:**
- Modify: `database/__init__.py:108-121` (Summary model), `database/__init__.py:252-253` (ensure_columns calls in `init_db`)
- Modify: `services/transcription.py:270-288` (`summarize()`'s Summary create/update block)
- Test: `tests/test_summarize_local_provider.py`

**Interfaces:**
- Produces: `Summary.provider` column (`String(64)`, default `""`), populated with the same `provider_name` string already passed into `summarize()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_summarize_local_provider.py` (after the last test, i.e. after line 112):

```python
def test_summarize_stores_provider_on_summary_row(db_session, tmp_path):
    user, transcript = _make_user_and_transcript(db_session)
    svc = TranscriptionService(str(tmp_path))
    fake_post = AsyncMock(return_value=_chat_response(
        '{"short_summary": "s", "key_points": [], "action_items": [], "decisions": []}'
    ))
    with patch("httpx.AsyncClient.post", fake_post):
        summary = asyncio.run(svc.summarize(
            db_session, user.id, transcript.id, api_key="", provider_name="local",
            provider_config={"api_url": "http://box:8080/v1"}, model="llama3",
        ))
    assert summary.provider == "local"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_summarize_local_provider.py::test_summarize_stores_provider_on_summary_row -v`
Expected: FAIL with `AttributeError: 'Summary' object has no attribute 'provider'` (column doesn't exist on the model yet).

- [ ] **Step 3: Add the column to the model and the migration**

In `database/__init__.py`, in the `Summary` class (around line 117, the `model` column line), add:

```python
    model = Column(String(64), default="")
    provider = Column(String(64), default="")
```

In `init_db()`, after the existing `ensure_columns(engine, "llm_jobs", ...)` call (line 253), add:

```python
    ensure_columns(engine, "summaries", {"provider": "TEXT"})
```

- [ ] **Step 4: Populate it in `summarize()`**

In `services/transcription.py`, in the existing-vs-new branch (lines 270-288), add `provider` alongside the existing `model` assignment in both branches:

```python
        existing = db.query(Summary).filter(Summary.transcript_id == transcript_id).first()
        if existing:
            existing.short_summary = summary_data.get("short_summary", "")
            existing.key_points = summary_data.get("key_points", [])
            existing.action_items = summary_data.get("action_items", [])
            existing.decisions = summary_data.get("decisions", [])
            existing.model = model
            existing.provider = provider_name
            existing.created_at = utcnow_naive()
            summary = existing
        else:
            summary = Summary(
                transcript_id=transcript_id,
                short_summary=summary_data.get("short_summary", ""),
                key_points=summary_data.get("key_points", []),
                action_items=summary_data.get("action_items", []),
                decisions=summary_data.get("decisions", []),
                model=model,
                provider=provider_name,
            )
            db.add(summary)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_summarize_local_provider.py -v`
Expected: PASS (all tests in the file, including the new one).

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, no failures — count should be one higher than before this task (one new test added).

- [ ] **Step 7: Commit**

```bash
git add database/__init__.py services/transcription.py tests/test_summarize_local_provider.py
git commit -m "feat: record provider on Summary rows, not just model"
```

---

### Task 2: Serialize `Summary.provider` in the API response

**Files:**
- Modify: `app.py:188-200` (`_serialize_summary`)
- Test: `tests/test_llm_jobs.py`

**Interfaces:**
- Consumes: `Summary.provider` from Task 1.
- Produces: `GET /api/transcripts/{id}/summary` response now includes a `"provider"` key.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm_jobs.py`, after `test_summarize_route_enqueues_job` (after line 304):

```python
def test_summary_endpoint_exposes_provider(db_session):
    from database import Summary
    from app import _serialize_summary
    user, t = _make_user_and_transcript(db_session)
    db_session.add(Summary(transcript_id=t.id, short_summary="s", model="m1", provider="groq"))
    db_session.commit()
    db_session.refresh(t)
    result = _serialize_summary(t.summary)
    assert result["provider"] == "groq"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_summary_endpoint_exposes_provider -v`
Expected: FAIL with `KeyError: 'provider'`.

- [ ] **Step 3: Add the field**

In `app.py`, in `_serialize_summary` (lines 188-200), add `"provider": s.provider,` next to the existing `"model": s.model,` line:

```python
        "model": s.model,
        "provider": s.provider,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_summary_endpoint_exposes_provider -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_llm_jobs.py
git commit -m "feat: expose Summary.provider in the transcript summary API"
```

---

### Task 3: Prepend provider/model metadata line to exports

**Files:**
- Modify: `static/rack.js:2023-2046` (`summaryPlainText`, `handleExportClick`)

**Interfaces:**
- Consumes: `detailData.provider` / `.model` / `.correction_model` (already present on every transcript API response, per `app.py:_serialize_transcript`); `Summary.provider` / `.model` from Task 2's endpoint response.
- Produces: no new function signatures consumed elsewhere — `summaryPlainText`'s return type changes from `string` to `{text, provider, model}`; its only caller (`handleExportClick`) is updated in the same step.

- [ ] **Step 1: Change `summaryPlainText` to also return provider/model**

Replace `static/rack.js:2023-2032`:

```javascript
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
```

- [ ] **Step 2: Prepend the metadata line in `handleExportClick`**

Replace `static/rack.js:2034-2046`:

```javascript
async function handleExportClick(kind, copy) {
  const t = detailData;
  let text = '', header = '';
  if (kind === 'transcript') {
    text = transcriptPlainText(t);
    header = `[transcribed with ${t.provider}/${t.model}]`;
  } else if (kind === 'corrected') {
    text = t.corrected_text || '';
    header = `[corrected with ${t.correction_model || 'unknown'}]`;
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
```

- [ ] **Step 3: Manual browser verification**

No frontend test framework exists — verify directly against a real running instance:

1. Spin up an isolated instance (data dir + port, per `.claude/skills/e2e-ux-audit/SKILL.md`'s Setup section), register a test user, upload a short audio fixture from `tests/fixtures/`, wait for transcription to complete.
2. Open the transcript detail page. Click "Copy" on the Transcript tab. Read the clipboard (`await navigator.clipboard.readText()` via `browser_evaluate`, or paste into a scratch textbox) — confirm it starts with `[transcribed with <provider>/<model>]` followed by a blank line then the transcript text.
3. Run correction (or wait for auto-correct), then Copy on the Corrected tab — confirm the header reads `[corrected with <provider>/<model>]`.
4. Run Summarize, then Copy on the Summary tab — confirm the header reads `[summarized with <provider>/<model>]`.
5. Click "Download .txt" on any tab — confirm the downloaded file's first line matches the same header (filename itself is unchanged, still `<title>-<kind>.txt`).
6. Tear down the throwaway server process.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: prepend provider/model metadata line to transcript exports"
```

---

## Done criteria

- `pytest -q` passes with 2 new tests (Tasks 1-2) on top of the current suite.
- Manual browser check (Task 3, Step 3) confirms all three export kinds (transcript/corrected/summary) carry the correct leading metadata line on both copy and download.
