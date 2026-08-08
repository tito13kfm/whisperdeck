# Investigation: Issue #271 — Classification-aware retranscribe & rediarize

## Target

Issue #271: "Studio pipeline: make retranscribe and rediarize classification-aware"
Design: `docs/superpowers/specs/2026-08-01-studio-classification-design.md`, decision 9.

Parent tracking: #264. Depends on #266 (design), #267 (classification infra), #268 (effective_kind predicates) — all merged.

## Codebase state (post-#268)

### effective_kind() (services/classification.py:23-32)
```python
def effective_kind(transcript) -> str | None:
    if transcript.classification_status in ("success", "override"):
        return transcript.kind
    return None
```
Returns `None` while pending/uncertain/failed — guards can never mistake an unresolved classification for a real kind.

### Database model (database/__init__.py:40-45)
- `classification_status`: String(16), default="override"
- `classification_confidence`: Float, nullable
- `classification_provenance`: JSON, nullable

### Retranscribe endpoint (app.py:2069-2111)

**Line 2109**: `kind=t.kind or "meeting"` — **blindly copies old kind forward**.

Does NOT check `classification_status` at all. Per design decision 9:
- Auto-classified parent (`success`/`uncertain`) → child should **re-classify** (new text may classify differently)
- Override parent (including legacy) → child should **carry forward unchanged**

Current behavior (before fix): all children get `classification_status=None` → column default "override". This is correct for override parents (coincidentally), but WRONG for auto-classified parents (should re-classify).

### _run_transcription_pipeline (app.py:1020-1291)

**Lines 1072-1079** — classification_status decision tree:
```
kind == "auto"           → classification_status = "pending", kind = "meeting"
not is_retranscribe      → classification_status = "override"
is_retranscribe (current) → classification_status = None  # column default
```

Comment at lines 1065-1071 explicitly marks the retranscribe path as #271's territory: "deliberately not stamping classification_status on that path."

**Inline path** (lines 1220-1283): Sets `classification_status` on transcript at lines 1247-1249 (`if classification_status is not None`). Voice-note dispatch at 1269-1281 uses raw `kind` variable (set to "meeting" by the auto→pending transform, so correction fires, as expected).

**Chunked path** (lines 1172-1218): `create_transcript_stub(db, ..., kind=kind)` — does NOT apply `classification_status`. Relies on column default "override". This is a gap: if `classification_status="pending"` from the auto check, the chunked path drops it.

### Chunked finalization (services/queue.py:589-619)

Line 614: `if effective_kind(transcript) == "voice_note"` — already uses `effective_kind()`.

Lines 602-612: Auto-correct / pipeline-classify dispatch. If auto_correct is on, correction completes → triggers `classify_pipeline` via `run_llm_job`'s `classification_status == "pending"` guard. If auto_correct is off, `enqueue_pipeline_classify` fires directly.

**The chunked finalization path works correctly** — IF `classification_status` is set correctly on the transcript row.

### Rediarize endpoint (app.py:2584-2622) — ALREADY CORRECT

Line 2598: `ek = effective_kind(t)` — uses effective_kind, not raw kind.
Lines 2599-2608: Allow-list check: blocks non-meeting kinds + blocks pending/uncertain/failed (returns None → fails `ek != "meeting"`). Server-side enforced.

### Voice-match endpoint (app.py:2625-2651) — ALREADY CORRECT

Line 2638: `ek = effective_kind(t)` — uses effective_kind.
Lines 2639-2647: Same allow-list pattern as rediarize. Server-side enforced.

### PATCH endpoint kind changes (app.py:1999-2027) — ALREADY CORRECT

Lines 2007-2021: `kind="auto"` reverts to auto-classification (sets `classification_status="pending"`, enqueues pipeline classify).
Lines 2022-2027: Explicit kind sets `classification_status="override"`.

### Existing test (test_posthoc_reprocess.py:85-108)

`test_retranscribe_child_classification_status_not_forced_by_268`:
- Sets parent `classification_status="success"` (auto-classified)
- Does retranscribe
- Asserts `child.classification_status == "override"` (current column-default behavior)

**This test MUST change**: per design decision 9, auto-classified parents should trigger re-classification on the child → child should get `classification_status="pending"`, NOT "override".

## Sibling sweep

### Callers of _run_transcription_pipeline
1. **transcribe_audio** (app.py:1337-1350) — fresh upload. Already correct (passes user-chosen kind, non-retranscribe → `classification_status="override"`).
2. **retranscribe_transcript** (app.py:2098-2111) — **NEEDS FIX**: blindly copies `t.kind`.
3. **bulk_transcribe** (app.py lines below 1350, not shown) — fresh upload via batch. Same as transcribe_audio.
4. No other callers found.

### Other retranscribe-like endpoints
- **retry-failed-chunks** (app.py:2033+) — retries chunk jobs, does not create new transcripts. Out of scope.
- No other endpoints create new transcripts from stored audio.

### Other classification_status mutation sites
- **PATCH /api/transcripts/{id}** (app.py:1999-2027) — already handles "auto" + override correctly.
- **LlmJob runner** (services/llm_jobs.py:454,463-465) — sets failed/success/uncertain on the classify_pipeline job.
- **Migration** (database/__init__.py:420-452) — legacy-migration sets "override".
- No other mutation sites found.

### Voice-note / correction dispatch
- **Inline** (app.py:1269-1281): uses raw `kind` variable (set to "meeting" by auto check, correct for classification-pending).
- **Chunked** (services/queue.py:594-619): uses `effective_kind()` now, correct.
- No direct `classification_status` branch in either dispatch path — they gate on `kind`/`effective_kind()`, not `classification_status` directly.

## What the issue's plan items actually need

| Plan item | Status | Notes |
|---|---|---|
| 1. Retranscribe re-classification semantics | NEEDS WORK | `retranscribe_transcript` at line 2109 must route by `classification_status` |
| 2. Carry provenance without stale state | NEEDS WORK | Column defaults handle this; provenance flows via `classification_status` routing |
| 3. Apply effective_kind to rediarize/voice-match | ALREADY DONE | #268 applied these. Both endpoints already use `effective_kind()` |
| 4. Chunked vs inline parity | NEEDS WORK | Chunked path drops `classification_status`; inline path applies it |
| 5. Behavior when classification unavailable | NEEDS WORK | Pending/failed → no kind confirmed → fallback: carry forward existing kind as override |
| 6. Idempotency/retry protection | LOW RISK | Existing job dispatch guards (status check in `enqueue_pipeline_classify`) prevent duplicates |

## Fix summary

1. **app.py:retranscribe_transcript** (line 2109): Replace `kind=t.kind or "meeting"` with classification_status-aware routing:
   - `success`/`uncertain` → `kind="auto"` (triggers re-classification via existing pipeline logic)
   - `override` (including legacy default) → `kind=t.kind or "meeting"` (carry forward)
   - `pending`/`failed` → `kind=t.kind or "meeting"` (safe fallback, unsettled state on a completed parent shouldn't happen)

2. **app.py:_run_transcription_pipeline chunked path** (after `create_transcript_stub`, before `db.commit()`): Apply `classification_status` if not None, matching inline path's lines 1247-1249.

3. **Tests**: 
   - Update `test_retranscribe_child_classification_status_not_forced_by_268` — auto-classified parent → child should get `pending`, not `override`
   - Add: retranscribe of override parent → child gets same kind + "override" status
   - Add: retranscribe chain classification provenance (override carries forward correctly across a chain)
   - Add: auto-classified parent → child triggers re-classification (verify classification_status on child)

### Worktree paths
- Main checkout: `C:/Claude/whisperdesk`
- This worktree: `C:/Claude/whisperdesk-sisyphus-271` (branch `issue-271-sisyphus`)
