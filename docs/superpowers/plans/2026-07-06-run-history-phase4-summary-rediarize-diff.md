# Run History Phase 4: Summary + Rediarize History and Compare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Past summary and rediarize runs stay inspectable and comparable, using rendering suited to their shape — structured section-by-section diff for summaries, a relabel-count structural compare for rediarize (not a text diff — segments aren't prose).

**Architecture:** Extends Phase 2's `LlmJob.result_json` snapshot to the `summary` and `rediarize` kinds, extends the backfill to cover them, and reuses Phase 2's `openCompareModal` unchanged with two new renderer functions: `summaryDiffHtml` (word-diff on the short summary, sorted-bullet-list diff on each section) and `rediarizeDiffHtml` (counts relabeled vs. unchanged segments, lists the changed spans).

**Tech Stack:** Python/FastAPI/SQLAlchemy backend, vanilla JS frontend (no bundler/npm).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-06-run-history-and-comparison-design.md`. This plan implements that spec's Phase 4 — the last phase.
- Depends on Phase 2 being merged (uses `LlmJob.result_json`, `backfill_llm_job_result_snapshots`, `openCompareModal`, `diffTokens`, `textDiffHtml` unchanged) and Phase 1 (uses `Summary.provider`).
- Phase 2's `GET /api/transcripts/{id}/runs/{kind}` endpoint already accepts `summary` and `rediarize` as valid `kind` values (it was written kind-agnostic from the start) — no backend route change needed for that endpoint in this phase.
- Summary/rediarize diffs are intentionally approximate, not exact text diffs — sorted-bullet-list diff can still show a reworded-and-reordered bullet as remove+add, and rediarize's per-index segment comparison assumes segment count doesn't change between runs (see Task 3 for the fallback when it does). This is an accepted tradeoff from the spec, not a bug to fix here.
- There is no dedicated "Diarization" tab in the UI — rediarize output is merged directly into the Transcript tab's segments. The "Rediarize history" control goes in the main detail toolbar next to the existing "Re-diarize" button, matching where "Correction history" was placed next to "Re-run correction" in Phase 2.
- No frontend test framework — frontend steps verified by manual browser check (see Phase 1 Task 3 Step 3 for the pattern).

---

### Task 1: Populate `result_json` for summary and rediarize completions

**Files:**
- Modify: `services/llm_jobs.py:215-253` (summary and rediarize branches of `run_llm_job`)
- Test: `tests/test_llm_jobs.py`

**Interfaces:**
- Produces: `LlmJob.result_json` populated for `kind == "summary"` as `{"short_summary", "key_points", "action_items", "decisions"}` and for `kind == "rediarize"` as `{"segments": [...]}`.

- [ ] **Step 1: Write the failing test for summary**

Add to `tests/test_llm_jobs.py`, after `test_run_llm_job_correction_saves_result_snapshot` (added in Phase 2's Task 1). `run_llm_job`'s summary branch calls `transcription_service.summarize(...)`, so this needs a real `TranscriptionService` instance (same pattern `test_summarize_local_provider.py` uses), not a mock object:

```python
def test_run_llm_job_summary_saves_result_snapshot(db_session, tmp_path):
    from services.transcription import TranscriptionService
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "summary", "groq", "m1")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse(
        '{"short_summary": "s", "key_points": ["a"], "action_items": [], "decisions": []}'
    ))
    factory = lambda: _NoCloseSession(db_session)
    svc = TranscriptionService(str(tmp_path))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=svc))

    db_session.refresh(job)
    assert job.result_json == {"short_summary": "s", "key_points": ["a"], "action_items": [], "decisions": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_run_llm_job_summary_saves_result_snapshot -v`
Expected: FAIL — `job.result_json` is `None` (nothing populates it yet for the summary branch).

- [ ] **Step 3: Populate it in the summary branch**

In `services/llm_jobs.py`, the summary branch (lines 215-227) currently discards `summarize()`'s return value. Change:

```python
        elif job.kind == "summary":
            job.progress_total = 1
            db.commit()
            try:
                await transcription_service.summarize(
                    db, job.user_id, job.transcript_id, api_key=api_key,
                    provider_name=job.provider, provider_config=provider_config,
                    model=job.model,
                )
                job.progress_done = 1
                _finish(db, job, "completed")
            except Exception as e:
                _finish(db, job, "failed", str(e))
```

to:

```python
        elif job.kind == "summary":
            job.progress_total = 1
            db.commit()
            try:
                summary = await transcription_service.summarize(
                    db, job.user_id, job.transcript_id, api_key=api_key,
                    provider_name=job.provider, provider_config=provider_config,
                    model=job.model,
                )
                job.result_json = {
                    "short_summary": summary.short_summary,
                    "key_points": summary.key_points or [],
                    "action_items": summary.action_items or [],
                    "decisions": summary.decisions or [],
                }
                job.progress_done = 1
                db.commit()
                _finish(db, job, "completed")
            except Exception as e:
                _finish(db, job, "failed", str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_run_llm_job_summary_saves_result_snapshot -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for rediarize**

Add to `tests/test_llm_jobs.py`, after the summary test just added. This follows the same pattern as `test_run_llm_job_correction_uses_local_llm_url_independent_of_stt` for mocking a service dependency — here, a fake diarization service:

```python
def test_run_llm_job_rediarize_saves_result_snapshot(db_session, tmp_path):
    segs = [{"start": 0, "end": 1, "speaker": "Speaker A", "text": "hi"}]
    user, t = _make_user_and_transcript(db_session, segments=segs)
    audio_path = tmp_path / "a.mp3"
    audio_path.write_bytes(b"x")
    t.audio_path = str(audio_path)
    db_session.commit()

    job = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    job.status = "running"
    db_session.commit()

    new_segments = [{"start": 0, "end": 1, "speaker": "Speaker B", "text": "hi"}]

    class _FakeDiarizationService:
        async def diarize_and_merge(self, *args, **kwargs):
            return new_segments, 1

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None, diarization_service=_FakeDiarizationService()))

    db_session.refresh(job)
    assert job.result_json == {"segments": new_segments}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_run_llm_job_rediarize_saves_result_snapshot -v`
Expected: FAIL — `job.result_json` is `None`.

- [ ] **Step 7: Populate it in the rediarize branch**

In `services/llm_jobs.py`, the rediarize branch (lines 228-253), change:

```python
                merged, speaker_count = await diarization_service.diarize_and_merge(
                    transcript.audio_path,
                    num_speakers=transcript.num_speakers,
                    segments=transcript.segments or [],
                    hf_token=user_settings.get("hf_token"),
                )
                transcript.segments = merged
                transcript.speaker_count = speaker_count
                transcript.updated_at = utcnow_naive()
                job.progress_done = 1
                db.commit()
                _finish(db, job, "completed")
```

to:

```python
                merged, speaker_count = await diarization_service.diarize_and_merge(
                    transcript.audio_path,
                    num_speakers=transcript.num_speakers,
                    segments=transcript.segments or [],
                    hf_token=user_settings.get("hf_token"),
                )
                transcript.segments = merged
                transcript.speaker_count = speaker_count
                transcript.updated_at = utcnow_naive()
                job.progress_done = 1
                job.result_json = {"segments": merged}
                db.commit()
                _finish(db, job, "completed")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py -v -k "saves_result_snapshot"`
Expected: PASS (3 tests: correction from Phase 2, summary and rediarize from this task)

- [ ] **Step 9: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add services/llm_jobs.py tests/test_llm_jobs.py
git commit -m "feat: snapshot summary and rediarize output for run history"
```

---

### Task 2: Extend the backfill to summary and rediarize

**Files:**
- Modify: `database/__init__.py` (`backfill_llm_job_result_snapshots`, added in Phase 2's Task 1; and its call site in `init_db`)
- Test: `tests/test_llm_job_history_backfill.py` (added in Phase 2's Task 1)

**Interfaces:**
- Consumes: `backfill_llm_job_result_snapshots(SessionLocal, kinds)` from Phase 2, extending its `kinds` default and adding `summary`/`rediarize` branches.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm_job_history_backfill.py`:

```python
def test_backfill_fills_latest_completed_summary_job(db_session):
    from database import Summary
    user = User(username="backfillop3", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="completed")
    db_session.add(t)
    db_session.commit()
    db_session.add(Summary(transcript_id=t.id, short_summary="s", key_points=["a"], action_items=[], decisions=[], model="m1", provider="groq"))
    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="summary", status="completed", provider="groq", model="m1")
    db_session.add(job)
    db_session.commit()

    backfill_llm_job_result_snapshots(lambda: _NoCloseSession(db_session), kinds=("correction", "summary", "rediarize"))

    db_session.refresh(job)
    assert job.result_json == {"short_summary": "s", "key_points": ["a"], "action_items": [], "decisions": []}


def test_backfill_fills_latest_completed_rediarize_job_from_current_segments(db_session):
    user = User(username="backfillop4", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    segs = [{"start": 0, "end": 1, "speaker": "A", "text": "hi"}]
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="completed", segments=segs)
    db_session.add(t)
    db_session.commit()
    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="rediarize", status="completed", provider="", model="")
    db_session.add(job)
    db_session.commit()

    backfill_llm_job_result_snapshots(lambda: _NoCloseSession(db_session), kinds=("correction", "summary", "rediarize"))

    db_session.refresh(job)
    assert job.result_json == {"segments": segs}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_job_history_backfill.py -v -k "summary_job or rediarize_job"`
Expected: FAIL — both assert `None == {...}` (the function only handles `kind == "correction"` so far).

- [ ] **Step 3: Extend the backfill function**

In `database/__init__.py`, in `backfill_llm_job_result_snapshots` (added in Phase 2's Task 1), change the branch:

```python
            if kind == "correction" and transcript.corrected_text:
                job.result_json = {"corrected_text": transcript.corrected_text}
```

to:

```python
            if kind == "correction" and transcript.corrected_text:
                job.result_json = {"corrected_text": transcript.corrected_text}
            elif kind == "summary":
                summary = db.query(Summary).filter(Summary.transcript_id == transcript_id).first()
                if summary:
                    job.result_json = {
                        "short_summary": summary.short_summary,
                        "key_points": summary.key_points or [],
                        "action_items": summary.action_items or [],
                        "decisions": summary.decisions or [],
                    }
            elif kind == "rediarize" and transcript.segments:
                job.result_json = {"segments": transcript.segments}
```

Also change the function's default parameter from `kinds: tuple = ("correction",)` to `kinds: tuple = ("correction", "summary", "rediarize")`, and update its call site in `init_db()` if that call passed an explicit `kinds=` argument (Phase 2's plan calls it with no `kinds` argument, relying on the default — if that's still the case, no call-site change is needed; only the default value changes).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_job_history_backfill.py -v`
Expected: PASS (all backfill tests, including Phase 2's correction-only ones — the default `kinds` change must not break those, since correction is still in the tuple)

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add database/__init__.py tests/test_llm_job_history_backfill.py
git commit -m "feat: backfill summary and rediarize run snapshots"
```

---

### Task 3: Summary and rediarize compare UI

**Files:**
- Modify: `static/rack.js` (new renderer functions near `textDiffHtml`, added in Phase 2's Task 3; toolbar at lines 2135-2148; `detailAction`)

**Interfaces:**
- Consumes: `openCompareModal`, `diffTokens` from Phase 2 (unchanged); `GET /api/transcripts/{id}/runs/summary` and `.../runs/rediarize` from Phase 2's kind-agnostic endpoint; `formatDur` (existing helper, used elsewhere in the file for duration formatting).
- Produces: `bulletListDiffHtml(oldItems: string[], newItems: string[]): string`, `summaryDiffHtml(oldSummary: object, newSummary: object): string`, `rediarizeDiffHtml(oldSegments: array, newSegments: array): string` — not consumed elsewhere, this is the last phase.

- [ ] **Step 1: Add the summary and rediarize diff renderers**

In `static/rack.js`, insert immediately after `textDiffHtml` (added in Phase 2's Task 3, right before `openCompareModal`):

```javascript
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
```

- [ ] **Step 2: Add "Summary history" and "Rediarize history" buttons**

In `static/rack.js:2141` and `:2144`, change:

```javascript
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rediarize" ${t.has_audio ? '' : 'disabled title="No stored audio for this transcript"'}>Re-diarize</button>
```

to (adding a history button right after it):

```javascript
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rediarize" ${t.has_audio ? '' : 'disabled title="No stored audio for this transcript"'}>Re-diarize</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="rediarize-history">Rediarize history</button>
```

And change:

```javascript
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="summarize" ${llmJobActive(t.summary_job) ? 'disabled title="Summary job already queued"' : ''}>Summarize</button>
```

to:

```javascript
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="summarize" ${llmJobActive(t.summary_job) ? 'disabled title="Summary job already queued"' : ''}>Summarize</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="summary-history">Summary history</button>
```

- [ ] **Step 3: Wire both buttons in `detailAction`**

In `static/rack.js`, in `detailAction`, add two new branches near the `correction-history` branch added in Phase 2:

```javascript
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
```

- [ ] **Step 4: Manual browser verification**

1. Spin up an isolated instance (per Phase 1's Task 3 Step 3 pattern), register, upload a short audio fixture with at least two distinguishable speakers, wait for completion with auto-correct on.
2. Run Summarize, wait for completion, run it again with a different provider/model. Click "Summary history" — confirm two options appear; select both — confirm the diff pane shows a word-level diff under "Summary" and sorted-bullet diffs under "Key points" / "Action items" / "Decisions".
3. Run Re-diarize, wait for completion, run it again (e.g. with a different `num_speakers` hint). Click "Rediarize history" — confirm two options appear; select both — confirm the pane reports a relabeled/unchanged count and lists the changed spans with timestamps and old→new speaker labels.
4. Confirm both modals close cleanly via the Close button.

- [ ] **Step 5: Commit**

```bash
git add static/rack.js
git commit -m "feat: summary and rediarize run history with structured compare"
```

---

## Done criteria

- `pytest -q` passes with 4 new backend tests on top of Phase 3's suite (Task 1: 2 tests, Task 2: 2 tests).
- Manual browser check (Task 3, Step 4) confirms both summary and rediarize history/compare flows work end to end.
- This completes the spec in `docs/superpowers/specs/2026-07-06-run-history-and-comparison-design.md` — issue #11 can be closed once this phase merges.
