# Run History Phase 3: Transcription Version Compare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `retranscribe` of the same original audio links back to a shared root, and any two versions of a transcript (original + reruns) can be diffed word-for-word on `full_text`.

**Architecture:** `Transcript` gains a `source_transcript_id` column, set by `retranscribe_transcript` and always pointing at the root (never chained). A new `/versions` endpoint resolves the root and returns every transcript sharing it. The frontend reuses Phase 2's `openCompareModal` / `textDiffHtml` unchanged, with a "Compare versions" button that only appears when more than one version exists.

**Tech Stack:** Python/FastAPI/SQLAlchemy backend, vanilla JS frontend (no bundler/npm).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-06-run-history-and-comparison-design.md`. This plan implements that spec's Phase 3.
- Depends on Phase 2 being merged — this plan reuses `openCompareModal`, `textDiffHtml` from `static/rack.js` without modification. Do not change their signatures here; if a change seems needed, that's a signal this plan's assumptions are wrong, stop and re-check Phase 2's code first.
- No frontend test framework — frontend steps verified by manual browser check (see Phase 1 Task 3 Step 3 for the pattern).

---

### Task 1: Add `Transcript.source_transcript_id`, set it on retranscribe

**Files:**
- Modify: `database/__init__.py:28-57` (Transcript model), `database/__init__.py:252` (ensure_columns)
- Modify: `app.py:725-760` (`retranscribe_transcript`)
- Test: `tests/test_posthoc_reprocess.py` (already has the exact `_upload`/`_pipeline_patches` fixtures needed for retranscribe — a real, non-mocked `TranscriptionService.transcribe()` runs against a fake backend, so `audio_path` gets persisted for real and the retranscribe precondition check passes naturally)

**Interfaces:**
- Produces: `Transcript.source_transcript_id` (nullable FK to `transcripts.id`). Always points at the root of a rerun chain — if the transcript being retranscribed already has `source_transcript_id` set, that same value is copied forward rather than pointing at the immediate parent, so every version of one original recording points at one common root.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_posthoc_reprocess.py`, after `test_retranscribe_creates_new_row_and_keeps_original` (after line 82):

```python
def test_retranscribe_chain_sets_source_transcript_id_to_root(client, db_session):
    client.put("/api/settings", json={"auto_correct": False})
    original = _upload(client, text="first pass").json()

    p1, p2, p3 = _pipeline_patches(text="second pass")
    with p1, p2, p3:
        first_rerun = client.post(
            f"/api/transcripts/{original['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        ).json()

    p1, p2, p3 = _pipeline_patches(text="third pass")
    with p1, p2, p3:
        second_rerun = client.post(
            f"/api/transcripts/{first_rerun['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        ).json()

    db_session.expire_all()
    first = db_session.query(Transcript).filter(Transcript.id == first_rerun["id"]).first()
    second = db_session.query(Transcript).filter(Transcript.id == second_rerun["id"]).first()
    assert first.source_transcript_id == original["id"]
    # A rerun of a rerun still points at the original root, not its immediate parent.
    assert second.source_transcript_id == original["id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posthoc_reprocess.py::test_retranscribe_chain_sets_source_transcript_id_to_root -v`
Expected: FAIL with `AttributeError: 'Transcript' object has no attribute 'source_transcript_id'`.

- [ ] **Step 3: Add the column and migration**

In `database/__init__.py`, in the `Transcript` class (around line 52, the `created_at`/`updated_at` lines), add:

```python
    queue_dismissed = Column(Boolean, default=False)  # hides a terminal transcription entry from the Queue screen only
    source_transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=True)  # root transcript this was retranscribed from, for version comparison
    created_at = Column(DateTime, default=utcnow_naive)
```

In `init_db()`, change line 252 from:

```python
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER", "processed_size_bytes": "INTEGER", "corrected_text": "TEXT", "correction_error": "TEXT", "correction_model": "TEXT", "queue_dismissed": "BOOLEAN DEFAULT 0"})
```

to:

```python
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER", "processed_size_bytes": "INTEGER", "corrected_text": "TEXT", "correction_error": "TEXT", "correction_model": "TEXT", "queue_dismissed": "BOOLEAN DEFAULT 0", "source_transcript_id": "INTEGER"})
```

- [ ] **Step 4: Set it in `retranscribe_transcript`**

In `app.py`, in `retranscribe_transcript` (lines 725-760), change the `return await _run_transcription_pipeline(...)` call. First, capture the pipeline's result instead of returning it directly, so the new transcript's `source_transcript_id` can be set afterward:

```python
    root_id = t.source_transcript_id or t.id
    result = await _run_transcription_pipeline(
        db, current_user, Path(t.audio_path),
        filename=t.filename,
        title=t.title,
        provider=provider,
        model=model,
        language=language if language is not None else t.language,
        temperature=0.0,
        diarize=diarize if diarize is not None else bool(t.diarize_requested),
        num_speakers=num_speakers if num_speakers is not None else t.num_speakers,
    )
    new_transcript = db.query(Transcript).filter(Transcript.id == result["id"]).first()
    if new_transcript:
        new_transcript.source_transcript_id = root_id
        db.commit()
    return result
```

(`_run_transcription_pipeline` always returns `_serialize_transcript(db, transcript)` — a plain dict with an `"id"` key — from both its chunked branch, `app.py:538`, and its inline branch, `app.py:577`. No shape-detection needed.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posthoc_reprocess.py::test_retranscribe_chain_sets_source_transcript_id_to_root -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add database/__init__.py app.py tests/test_posthoc_reprocess.py
git commit -m "feat: link retranscribe output to a shared root transcript"
```

---

### Task 2: Expose `source_transcript_id` and add `GET /api/transcripts/{id}/versions`

**Files:**
- Modify: `app.py:146-185` (`_serialize_transcript`), `app.py` (new route, insert after Phase 2's `transcript_runs` route)
- Test: `tests/test_posthoc_reprocess.py`

**Interfaces:**
- Consumes: `Transcript.source_transcript_id` from Task 1.
- Produces: `_serialize_transcript` response now includes `"source_transcript_id"`. `GET /api/transcripts/{id}/versions` → `{"versions": [{"id", "provider", "model", "created_at", "full_text"}, ...]}` for every transcript sharing the resolved root (including the root itself), oldest first.

- [ ] **Step 1: Add `source_transcript_id` to `_serialize_transcript`**

In `app.py`, in `_serialize_transcript` (lines 146-185), add `"source_transcript_id": t.source_transcript_id,` next to `"id": t.id,` (line 156):

```python
    return {
        "id": t.id,
        "source_transcript_id": t.source_transcript_id,
        "title": t.title,
```

- [ ] **Step 2: Write the failing test for the versions endpoint**

Add to `tests/test_posthoc_reprocess.py`, after the test added in Task 1:

```python
def test_versions_endpoint_returns_root_and_all_reruns(client, db_session):
    client.put("/api/settings", json={"auto_correct": False})
    original = _upload(client, text="first pass").json()

    p1, p2, p3 = _pipeline_patches(text="second pass")
    with p1, p2, p3:
        rerun = client.post(
            f"/api/transcripts/{original['id']}/retranscribe",
            data={"provider": "groq", "model": "whisper-large-v3"},
        ).json()

    versions = client.get(f"/api/transcripts/{original['id']}/versions").json()["versions"]
    assert {v["id"] for v in versions} == {original["id"], rerun["id"]}

    # Querying from the rerun side must resolve to the same group.
    versions_from_rerun = client.get(f"/api/transcripts/{rerun['id']}/versions").json()["versions"]
    assert {v["id"] for v in versions_from_rerun} == {original["id"], rerun["id"]}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posthoc_reprocess.py::test_versions_endpoint_returns_root_and_all_reruns -v`
Expected: FAIL with 404 (route doesn't exist).

- [ ] **Step 4: Add the endpoint**

In `app.py`, insert directly after the `transcript_runs` route added in Phase 2's Task 2:

```python
@app.get("/api/transcripts/{transcript_id}/versions")
async def transcript_versions(
    transcript_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every transcript sharing the same root as this one (itself included)
    — the set of retranscribe reruns of one original recording."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    root_id = t.source_transcript_id or t.id
    versions = (
        db.query(Transcript)
        .filter(
            Transcript.user_id == current_user.id,
            (Transcript.id == root_id) | (Transcript.source_transcript_id == root_id),
        )
        .order_by(Transcript.id.asc())
        .all()
    )
    return {"versions": [
        {
            "id": v.id, "provider": v.provider, "model": v.model,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "full_text": v.full_text,
        }
        for v in versions
    ]}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posthoc_reprocess.py -v -k versions`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_posthoc_reprocess.py
git commit -m "feat: add GET /api/transcripts/{id}/versions endpoint"
```

---

### Task 3: "Compare versions" button on the detail page

**Files:**
- Modify: `static/rack.js:2135-2148` (detail toolbar), `detailAction` (same function Phase 2 modified)

**Interfaces:**
- Consumes: `GET /api/transcripts/{id}/versions` from Task 2; `openCompareModal`, `textDiffHtml` from Phase 2 (unchanged).

- [ ] **Step 1: Add the button, shown only when the transcript has a source or is one**

In `static/rack.js:2140`, change:

```javascript
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="retranscribe" ${t.has_audio ? '' : 'disabled title="No stored audio for this transcript"'}>Re-transcribe</button>
```

to (adding a new button right after it — shown unconditionally; it self-reports "only one version" if there's nothing to compare, same as `openCompareModal`'s existing empty-state toast):

```javascript
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="retranscribe" ${t.has_audio ? '' : 'disabled title="No stored audio for this transcript"'}>Re-transcribe</button>
        <button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="compare-versions">Compare versions</button>
```

- [ ] **Step 2: Wire the button**

In `static/rack.js`, in `detailAction`, add a new branch (placement doesn't matter relative to other branches — add it near the `correction-history` branch added in Phase 2):

```javascript
    if (act === 'compare-versions') {
      await openCompareModal(
        'Compare transcript versions',
        async () => {
          const versions = (await api('/api/transcripts/' + t.id + '/versions')).versions;
          return versions.map(v => ({
            id: v.id,
            optionLabel: (v.provider || '—') + (v.model ? '/' + v.model : '') + ' · ' + timeAgo(v.created_at),
            result: { full_text: v.full_text },
          }));
        },
        result => result.full_text || '',
        textDiffHtml,
      );
      return;
    }
```

Note this always has at least one item (the transcript itself), so `openCompareModal`'s "Nothing to compare yet" toast never fires here — with only one version, both dropdowns default to the same option and the diff pane shows no changes, which is a correct (if unremarkable) result, not an error state.

- [ ] **Step 3: Manual browser verification**

1. Spin up an isolated instance (per Phase 1's Task 3 Step 3 pattern), register, upload a short audio fixture, wait for completion.
2. Click "Compare versions" — confirm the modal opens with exactly one option in each dropdown (the original), and the diff pane shows plain text with no highlights.
3. Click "Re-transcribe", pick a different provider/model, run it, wait for completion, navigate to the new transcript.
4. Click "Compare versions" on the new transcript — confirm both the original and the new version appear as options.
5. Navigate back to the original transcript, click "Compare versions" — confirm it also lists both versions (proving the root-lookup works from either side).
6. Select the original in one dropdown and the rerun in the other — confirm the diff pane highlights word-level differences between the two `full_text` values.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: compare transcript versions across retranscribe reruns"
```

---

## Done criteria

- `pytest -q` passes with 2 new backend tests on top of Phase 2's suite.
- Manual browser check (Task 3, Step 3) confirms version comparison works from both the original and a rerun's detail page.
