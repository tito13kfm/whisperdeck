# Issue #67: Diarization Misidentification Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status as of 2026-07-22, end of session:** Phase 0 through Phase 3 (Tasks 0-13) are complete, reviewed (spec compliance + code quality per task, plus a final whole-branch review across Phases 0-3), and pushed to branch `issue-67-diarization` on origin, folded into draft PR #69. Full suite: 316 passed, 2 deselected, no regressions. Whole-branch review: no Critical/Important findings, ready to merge; Minor items noted (no cascade relationship on `relabel_history`, stereo copy stored even when unused, VAD/bleed-filter heuristic blind spots — all documented in the review, none blocking). Known gaps, not yet closed: (1) Task 9 Step 5's real-pyannote runtime verification for live-stereo diarization has NOT been done (this dev machine has no pyannote/torch); (2) Task 13's "Undo relabel" button has not been driven in a real browser/e2e pass. Next up: Phase 4 (per-line confidence signal), starting at Task 14. Phase 5 is contingent (only run if over-splitting persists after Phases 1-2 ship).

**Goal:** Stop diarization from splitting one real speaker across many labels (and merging distinct speakers), add undo for bulk speaker relabels, and surface a per-line confidence signal.

**Architecture:** Five phases. Phase 1 fixes the paths that silently lose the user's `num_speakers` and adds per-run diarization metadata. Phase 2 exploits the live-capture channel layout (mic = left, system audio = right) by storing a stereo copy and diarizing channels separately instead of letting pyannote average them together. Phase 3 adds a `relabel_history` table recording inverse patches for every bulk relabel path, with an undo endpoint and button. Phase 4 computes per-segment assignment confidence from diarization overlap. Phase 5 is a contingent manual repro runbook.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), vanilla JS frontend (`static/rack.js`), pyannote.audio 3.1 (production machine only), soundfile + numpy, ffmpeg via `services/audio_prep.py`, pytest with the existing `client`/`db_session` fixtures.

**Issue:** https://github.com/tito13kfm/WhisperDeck/issues/67 (analysis comment posted 2026-07-22). Related: #55 (voice-match over-collapse, gets undo for free from Phase 3), #38 (voice-ID embedding backend, out of scope here).

---

## Verified facts (do not re-derive, do not doubt these without new evidence)

- Diarization runs on the **full stored audio** at finalize (`services/queue.py:500`), never per chunk. Chunk stitching is not a factor.
- pyannote's `SpeakerDiarization` pipeline constructs `Audio(mono="downmix")` internally and **averages all channels** and resamples any waveform you hand it (verified against pyannote-audio source, `src/pyannote/audio/core/io.py` and `src/pyannote/audio/pipelines/speaker_diarization.py`). Passing a stereo tensor does not crash and does not use the channels; it averages them.
- `Pipeline.instantiate()` with a **partial** params dict keeps unmentioned parameters (verified against pyannote-pipeline source). The existing `instantiate({"clustering": {"threshold": 0.7}})` call at `services/diarization.py:201` only nudges the tuned threshold (0.7046) to 0.7, and threshold is ignored anyway when an exact `num_speakers` is forced. It is dead weight, not the root cause.
- Live captures record **mic to left channel, system audio to right** (`static/rack.js:1441`, `ChannelMerger(2)`), export as webm/opus, and are uploaded through the same `/api/transcribe` path as file uploads with no marker.
- Every webm upload is transcoded to **16 kHz MONO mp3** (`transcode_for_upload`, `services/audio_prep.py:44`, `-ac 1`) because libsndfile cannot read webm. That mono file becomes `transcripts.audio_path` and is what diarization reads. **Stereo is destroyed at upload time today**, not at diarization time.
- pyannote emits `SPEAKER_00`-style labels; the no-ML heuristic fallback emits `Speaker 1`-style labels. "Person N" in the issue is post-rename user vocabulary.
- Local dev machines do NOT have torch/pyannote installed. `tests/` currently: 178 pass, 1 pre-existing failure in the voice_id module (missing torch). Do not chase that failure.

## Traps (read before every task; tasks reference these by number)

- **T1 - JSON columns don't change-track in-place.** `transcript.segments` is a SQLAlchemy JSON column. Always build a NEW list and assign it (`t.segments = new_segments`); mutating the existing list silently fails to persist. Existing tests guard this with `db_session.expire_all()` before re-reading (see `tests/test_speaker_naming.py:68`).
- **T2 - `_finalize_if_done` dirty-write discipline.** In `services/queue.py:500-549` nothing may be assigned to `transcript` attributes before the awaits; there is a long comment explaining why (autoflush would shadow a concurrent /cancel commit). Keep all diarization results in local variables until after the post-await re-fetch at line 547, exactly like the current code does.
- **T3 - rediarize jobs read parameters from the transcript row.** `LlmJob` has no params column; `run_llm_job` reads `transcript.num_speakers` at execution time (`services/llm_jobs.py:330`). Anything the job needs must be persisted on the transcript before enqueueing.
- **T4 - never import torch/pyannote/numpy-heavy deps at module level in services.** `services/diarization.py` imports torch and soundfile INSIDE the method bodies. Keep it that way; module-level imports break every machine without torch (including CI and dev boxes).
- **T5 - tests must not require pyannote.** Monkeypatch `DiarizationService._check_pyannote` (return True/False) and `DiarizationService._run_pyannote_sync` (return canned segments). Never construct a real pyannote pipeline in tests.
- **T6 - Complement Rule (AGENTS.md).** Every guard or behavior change must cover ALL sibling entry points. The sibling sets touched by this plan:
  - Diarization result writers: inline path (`app.py:816-831`), queue finalize (`services/queue.py:500-556`), rediarize job (`services/llm_jobs.py:316-342`).
  - Bulk relabel writers: rename (`app.py:1268-1312`), retag (`app.py:1315-1350`), voice-match apply (`services/llm_jobs.py:389-397`).
  - Stored-file lifecycle: upload transcode (`app.py:704-722`), retranscribe inherit (`app.py:695-699`), delete cleanup (`app.py:979-1003`), the refs helper (`app.py:955-962`), and the storage-review endpoint near `app.py:1107`. Grep `video_path` in `app.py` and mirror its handling for any new file column.
- **T7 - UI text changes break e2e selectors.** Any changed button text, label, or title: grep `tests/` and any e2e directories for the old text and update selectors in the same commit (project CLAUDE.md rule).
- **T8 - do not point diarization at the original webm.** libsndfile cannot open webm ("Format not recognised"); that is the entire reason `transcode_for_upload` exists. The stereo copy in Phase 2 must be FLAC (libsndfile-native).
- **T9 - do not change the stored mono mp3 format or make it stereo.** `chunk_audio` stream-copies mp3, every transcription backend and the audio player consume `audio_path`, and cloud providers expect it. Phase 2 adds a SECOND file (`stereo_audio_path`) instead of altering the existing one.
- **T10 - `combine_with_transcript` zero-overlap fallback.** `best_speaker or seg.get("speaker", "Unknown")` at `services/diarization.py:265` means a transcript segment with no diarization overlap keeps its old speaker. Preserve that behavior in Phase 4's rewrite.
- **T11 - second-best overlap must be a different SPEAKER, not a different turn.** One speaker usually owns several adjacent diarization turns. Confidence margins computed per-turn instead of per-speaker would mark almost every line uncertain. Sum overlap per speaker label first.
- **T12 - rename undo needs a corrected_text before-image.** Renaming A to B when label B already exists makes the text transform non-invertible. Store the full `corrected_text` string before the rename; do not try to reverse the transform.
- **T13 - `finishLiveCapture` clears `CAP.disp` before `loadTape` runs** (`static/rack.js:1541-1552`). Capture `const wasStereo = !!CAP.disp;` as the FIRST line of `finishLiveCapture` or the flag is always false.
- **T14 - git hygiene.** Branch from master (never commit to master), plain conventional commits, no AI-authorship trailers, PR merged only after green CI with branch deletion.
- **T15 - Windows.** Dev shells are PowerShell; paths use backslashes; run tests as `python -m pytest tests -q`.
- **T16 - `_serialize_transcript` is called from list views too.** Any per-transcript extra query added to it multiplies into N+1 on the library screen. Gate new lookups behind an opt-in parameter that only the single-transcript detail route passes.

---

## Phase 0: Branch and baseline

### Task 0: Create branch, record baseline

**Files:** none modified.

- [x] **Step 1: Branch**

```bash
git checkout master && git pull && git checkout -b issue-67-diarization
```

- [x] **Step 2: Baseline test run**

Run: `python -m pytest tests -q`
Expected: 178 passed, 1 failed (voice_id, missing torch, pre-existing). Record the exact numbers; every later task must not reduce the pass count.

---

## Phase 1: Stop losing the speaker count

### Task 1: Delete the dead clustering-threshold override

**Files:**
- Modify: `services/diarization.py:200-202`

- [x] **Step 1: Delete the lines**

In `diarize_pyannote`'s `_run()`, delete exactly:

```python
            if num_speakers:
                pipeline.instantiate({"clustering": {"threshold": 0.7}})
```

Nothing replaces them. Rationale (verified, see Verified facts): partial instantiate only overrode the tuned threshold, and threshold is unused when an exact count is passed.

- [x] **Step 2: Verify nothing else references instantiate**

Run: `grep -n "instantiate" services/diarization.py`
Expected: no matches.

- [x] **Step 3: Run tests**

Run: `python -m pytest tests -q`
Expected: same counts as baseline.

- [x] **Step 4: Commit**

```bash
git add services/diarization.py
git commit -m "fix: drop dead clustering threshold override in diarize_pyannote"
```

### Task 2: Persist diarization method, unify the queue path through diarize_and_merge

**Files:**
- Modify: `database/__init__.py` (Transcript model + `ensure_columns` in `init_db`)
- Modify: `services/diarization.py` (`diarize_and_merge` return value)
- Modify: `services/queue.py:500-556` (replace inline branch with `diarize_and_merge`)
- Modify: `services/llm_jobs.py:328-335` (3-tuple unpack + method persist)
- Modify: `app.py:819-826` (3-tuple unpack + method persist), `_serialize_transcript` (`app.py:225`)
- Test: `tests/test_diarization_metadata.py` (new)

Traps: T1, T2, T4, T5, T6 (all three diarization writers change together).

- [x] **Step 1: Add the column**

In `database/__init__.py`, add to the `Transcript` model after `num_speakers` (line 51):

```python
    diarization_method = Column(String(32), nullable=True)  # pyannote | heuristic | live_stereo; NULL = never diarized or pre-migration
```

In `init_db`, extend the existing transcripts `ensure_columns` call (line 304) with `"diarization_method": "TEXT"` inside the dict.

- [x] **Step 2: Write the failing tests**

Create `tests/test_diarization_metadata.py`:

```python
"""diarize_and_merge returns and callers persist the diarization method."""
import pytest

from services.diarization import DiarizationService


@pytest.mark.asyncio
async def test_diarize_and_merge_returns_method_heuristic(monkeypatch, tmp_path):
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: False)
    segments = [
        {"start": 0.0, "end": 2.0, "text": "hello"},
        {"start": 4.0, "end": 6.0, "text": "world"},  # 2s gap flips the heuristic speaker
    ]
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "missing.mp3"), num_speakers=2, segments=segments,
    )
    assert method == "heuristic"
    assert count >= 1
    assert all("speaker" in s for s in merged)


@pytest.mark.asyncio
async def test_diarize_and_merge_returns_method_pyannote(monkeypatch, tmp_path):
    from services.diarization import DiarizationResult, DiarizationSegment
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: True)

    async def fake_pyannote(audio_path, num_speakers=None, hf_token=None):
        return DiarizationResult(
            segments=[DiarizationSegment(start=0.0, end=6.0, speaker="SPEAKER_00")],
            speaker_count=1, method="pyannote",
        )

    monkeypatch.setattr(svc, "diarize_pyannote", fake_pyannote)
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "a.mp3"), num_speakers=1,
        segments=[{"start": 0.0, "end": 2.0, "text": "hi"}],
    )
    assert method == "pyannote"
    assert merged[0]["speaker"] == "SPEAKER_00"
```

- [x] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_diarization_metadata.py -q`
Expected: FAIL (ValueError: not enough values to unpack), because `diarize_and_merge` returns a 2-tuple today.

- [x] **Step 4: Change diarize_and_merge to return the method**

In `services/diarization.py`, change the last line of `diarize_and_merge` (line 72) and its docstring return description:

```python
        merged = await self.combine_with_transcript(result, segments)
        return merged, result.speaker_count, result.method
```

Update the docstring sentence to: `Returns (merged_segments, speaker_count, method).`

- [x] **Step 5: Update the three callers (T6)**

`app.py:819` (inline path):

```python
                merged, speaker_count, diarization_method = await diarization_service.diarize_and_merge(
                    str(save_path),
                    num_speakers=num_speakers,
                    segments=transcript.segments,
                    hf_token=user_settings.get("hf_token"),
                )
                transcript.segments = merged
                transcript.speaker_count = speaker_count
                transcript.diarization_method = diarization_method
                db.commit()
```

`services/llm_jobs.py:328` (rediarize job):

```python
                merged, speaker_count, diarization_method = await diarization_service.diarize_and_merge(
                    transcript.audio_path,
                    num_speakers=transcript.num_speakers,
                    segments=transcript.segments or [],
                    hf_token=user_settings.get("hf_token"),
                )
                transcript.segments = merged
                transcript.speaker_count = speaker_count
                transcript.diarization_method = diarization_method
```

(the rest of that block, `updated_at`, `progress_done`, `result_json`, commit, `_finish`, stays as is)

`services/queue.py:517-534` (finalize): replace the whole try/except branch body with one call. The surrounding discipline (T2) stays: `db.rollback()` first, locals only, no writes to `transcript` before the re-fetch. Replace lines 517-536 with:

```python
        diarization_method = None
        try:
            from services.settings import get_user_settings  # local import avoids a module-load cycle with app.py
            user_settings = get_user_settings(db, transcript_user_id)
            # diarize_and_merge picks pyannote when installed, else the
            # pause-gap heuristic (which needs a real count, default 2 inside).
            merged, speaker_count, diarization_method = await diarization_service.diarize_and_merge(
                audio_path, num_speakers=num_speakers, segments=segments,
                hf_token=user_settings.get("hf_token"),
            )
            segments = merged
        except Exception as e:
            print(f"[queue] non-fatal diarization failure for transcript {transcript_id}: {e}")
```

Note: `speaker_count` is already initialized to `None` at line 499; the assignment above only happens on success, matching current behavior. Delete the now-unused `if diarization_service._check_pyannote():` / heuristic branch entirely.

Then in the persistence block (line 551-557), after `transcript.speaker_count = speaker_count`, add:

```python
    if speaker_count is not None:
        transcript.speaker_count = speaker_count
        transcript.diarization_method = diarization_method
```

(i.e. move the method write inside the existing `if speaker_count is not None:` guard; `diarization_method` must also be initialized to `None` alongside `speaker_count = None` at line 499 so the name exists when `diarize_requested` is false.)

- [x] **Step 6: Expose in the API**

In `app.py` `_serialize_transcript` (line 225), add to the returned dict next to `speaker_count`:

```python
        "diarization_method": t.diarization_method,
```

- [x] **Step 7: Run tests**

Run: `python -m pytest tests -q`
Expected: baseline counts plus the 2 new tests passing. `tests/test_posthoc_reprocess.py` exercises the rediarize job; if it fails it is because it stubs `diarize_and_merge` with a 2-tuple: update its stub to return `(merged, count, "pyannote")`.

- [x] **Step 8: Commit**

```bash
git add database/__init__.py services/diarization.py services/queue.py services/llm_jobs.py app.py tests/test_diarization_metadata.py tests/test_posthoc_reprocess.py
git commit -m "feat: persist diarization method, route queue finalize through diarize_and_merge"
```

### Task 3: Re-diarize picker prefills the stored count; detail view shows method

**Files:**
- Modify: `static/rack.js:2960-2971` (picker), `static/rack.js:2614` (Speakers cell)

Traps: T7.

- [x] **Step 1: Prefill the picker**

In `toggleRediarizePicker` (`static/rack.js:2960`), the input currently renders with no value, so running it blank silently wipes `t.num_speakers` (the endpoint at `app.py:1595` persists whatever is sent, by design, see T3). Change the innerHTML block to prefill and explain:

```js
  const t = detailData;
  box.innerHTML = `
    <div class="unit" style="padding:12px 34px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span class="t-unit">Re-diarize</span>
      <input id="rediar-speakers" class="inp" type="number" min="1" max="20" placeholder="auto"
             value="${t && t.num_speakers ? t.num_speakers : ''}"
             title="Number of speakers — clear the field to let pyannote auto-detect (auto-detect tends to over-split)" style="padding:6px 8px;font-size:12px;width:90px">
      <button id="rediar-go" class="btn btn--amber" style="font-size:12px;padding:7px 14px">Run</button>
      <span style="font-size:11px;color:var(--label-dim)">Updates speaker labels in place; re-run correction afterwards if you use the corrected text.</span>
    </div>`;
```

(Only the `value=` attribute and the title text change; keep the rest byte-identical.)

- [x] **Step 2: Show method and auto-detect in the Speakers cell**

At `static/rack.js:2614` replace the Speakers cell content `${t.speaker_count || '—'}` with:

```js
${t.speaker_count || '—'}${t.diarization_method ? ` <span style="font-size:10px;color:var(--label-dim)">${escapeHtml(t.diarization_method)}${t.num_speakers ? '' : ' (auto)'}</span>` : ''}
```

- [x] **Step 3: Selector check (T7)**

Run: `grep -rn "rediar-speakers\|Speakers" tests/` and any e2e directory.
Expected: update any selector that matched the old markup; if no matches, done.

- [x] **Step 4: Runtime check**

Use the project run skill or start the server, open a transcript with a stored count, open the Re-diarize picker, confirm the field shows the count. A green unit run does not prove this layer (project CLAUDE.md).

- [x] **Step 5: Commit**

```bash
git add static/rack.js
git commit -m "fix: prefill re-diarize speaker count so blank runs stop wiping it"
```

---

## Phase 2: Channel-aware diarization for live captures

Design decisions (pinned, do not relitigate during execution):
- A SECOND stored file, 16 kHz stereo FLAC, in a new `transcripts.stereo_audio_path` column. The mono mp3 pipeline is untouched (T9). FLAC because libsndfile reads it natively (T8) and it is lossless at ~1/2 wav size.
- The upload marks itself with a `capture_source=live_stereo` form field set by the frontend only when system audio was actually captured. No new DB column for the flag; `stereo_audio_path IS NOT NULL` is the marker.
- Local user label is `You`. It is renameable like any other label.
- Remote channel gets pyannote with `num_speakers - 1` (None stays None for auto).
- Mic-channel VAD is plain numpy RMS gating, no new dependency.

### Task 4: Stereo transcode helper

**Files:**
- Modify: `services/audio_prep.py` (new function after `transcode_for_upload`)
- Test: `tests/test_audio_prep_stereo.py` (new)

- [x] **Step 1: Write the failing test**

```python
"""transcode_stereo_for_diarization produces a 16 kHz 2-channel FLAC."""
import asyncio
import os
import wave
import struct

import pytest

from services.audio_prep import ffmpeg_available, transcode_stereo_for_diarization

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _write_stereo_wav(path, seconds=1, rate=44100):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<hh", 3000, -3000) for _ in range(rate * seconds)
        )
        w.writeframes(frames)


@pytest.mark.asyncio
async def test_stereo_transcode_keeps_two_channels(tmp_path):
    src = tmp_path / "cap.wav"
    _write_stereo_wav(src)
    out = await transcode_stereo_for_diarization(str(src), str(tmp_path))
    assert out.endswith("_16k_stereo.flac")
    import soundfile as sf
    data, rate = sf.read(out, always_2d=True)
    assert rate == 16000
    assert data.shape[1] == 2
```

Run: `python -m pytest tests/test_audio_prep_stereo.py -q`
Expected: FAIL with ImportError (function does not exist).

- [x] **Step 2: Implement**

Add to `services/audio_prep.py` directly after `transcode_for_upload`:

```python
async def transcode_stereo_for_diarization(input_path: str, output_dir: str) -> str:
    """16 kHz 2-channel FLAC copy of a live-capture recording, kept solely
    for channel-aware diarization (mic = channel 0, system audio = channel 1).
    The mono mp3 from transcode_for_upload stays the transcription source;
    FLAC because libsndfile reads it natively (it cannot open webm)."""
    if not ffmpeg_available():
        raise AudioPrepError(
            "ffmpeg is not installed or not on PATH. It's required to prepare "
            "audio/video uploads for cloud transcription providers. "
            "See INSTALL.md."
        )

    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base}_16k_stereo.flac")

    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", input_path,
        "-vn",
        "-ac", "2",
        "-ar", "16000",
        "-c:a", "flac",
        output_path,
    ]

    def _run():
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AudioPrepError(f"ffmpeg stereo transcode failed: {result.stderr[-2000:]}")
        return output_path

    return await asyncio.to_thread(_run)
```

- [x] **Step 3: Run test to verify it passes**

Run: `python -m pytest tests/test_audio_prep_stereo.py -q`
Expected: PASS (or skip on a machine without ffmpeg; if it skips locally, note that CI must run it).

- [x] **Step 4: Commit**

```bash
git add services/audio_prep.py tests/test_audio_prep_stereo.py
git commit -m "feat: stereo flac transcode for channel-aware diarization"
```

### Task 5: stereo_audio_path column and full file lifecycle

**Files:**
- Modify: `database/__init__.py` (column + ensure_columns)
- Modify: `app.py` (upload plumbing, retranscribe inherit, delete cleanup, refs helper, storage review)

Traps: T6 (lifecycle sibling set), T3.

- [x] **Step 1: Column**

`database/__init__.py`, Transcript model, after `audio_path` (line 48):

```python
    stereo_audio_path = Column(String(512), nullable=True)  # 16 kHz stereo FLAC of a live capture (mic=ch0, system=ch1); NULL for ordinary uploads
```

Extend the transcripts `ensure_columns` dict in `init_db` with `"stereo_audio_path": "TEXT"`.

- [x] **Step 2: Accept and validate the form field**

`app.py`: add `capture_source: Optional[str] = Form(None)` to the `/api/transcribe` route signature (line 853 area) and pass it through to `_run_transcription_pipeline` (add a `capture_source: Optional[str] = None` keyword parameter at line 661 area). At the top of the pipeline body, next to the dictation guard:

```python
    if capture_source not in (None, "live_stereo"):
        capture_source = None  # unknown values from stale clients are ignored, not errors
```

- [x] **Step 3: Produce the stereo copy**

In `_run_transcription_pipeline`, right after the `needs_transcode` block (after line 722), using `raw_path` (captured at line 694, before `save_path` was reassigned):

```python
    stereo_audio_path = None
    if capture_source == "live_stereo":
        try:
            stereo_audio_path = await transcode_stereo_for_diarization(str(raw_path), str(UPLOAD_DIR))
        except AudioPrepError as e:
            # Non-fatal: fall back to mixed-audio diarization rather than
            # failing the whole upload over the enhancement copy.
            print(f"[audio-prep] stereo copy failed, using mixed audio: {e}")
```

Import `transcode_stereo_for_diarization` in the existing `from services.audio_prep import ...` line (`app.py:37`).

Set it on the transcript in BOTH branches (T6): the chunked branch's `create_transcript_stub(...)` call (line 773) gets nothing new in its signature; instead set `transcript.stereo_audio_path = stereo_audio_path` immediately after the stub call, next to the existing `transcript.processed_size_bytes = file_size` pattern, and identically after the inline branch's transcript creation (line 811 area).

- [x] **Step 4: Retranscribe inherits it**

At `app.py:695-699` where `video_path` is inherited from the parent, also inherit:

```python
        stereo_audio_path_inherited = parent.stereo_audio_path if parent else None
```

and prefer it over generating a new one (a retranscribe re-enters the pipeline with the stored mono mp3 as input, so `capture_source` will be None and the original stereo copy must carry forward): where Step 3 initializes `stereo_audio_path = None`, initialize it to the inherited value instead when `source_transcript_id is not None`.

- [x] **Step 5: Delete cleanup and refs (T6)**

- `app.py:958`: change `for field in ("audio_path", "video_path"):` to `("audio_path", "video_path", "stereo_audio_path")`, and extend the `or_(...)` filter at line 955 with `Transcript.stereo_audio_path.isnot(None)`.
- `app.py:987`: change `for path in (t.audio_path, t.video_path):` to include `t.stereo_audio_path`.
- Grep for the storage-review endpoint near `app.py:1107` (`grep -n "video_path" app.py`) and mirror whatever it does for `audio_path`/`video_path` for the new column.

- [x] **Step 6: Serialize**

`_serialize_transcript`: no new key needed by the UI yet; skip (YAGNI).

- [x] **Step 7: Tests**

Existing suite must stay green: `python -m pytest tests -q`. The upload pipeline itself has no isolated unit seam; runtime verification happens in Task 9 Step 5.

- [x] **Step 8: Commit**

```bash
git add database/__init__.py app.py
git commit -m "feat: store stereo flac copy of live captures through full file lifecycle"
```

### Task 6: Frontend tags live-stereo uploads

**Files:**
- Modify: `static/rack.js` (`loadTape`:1278, `ejectTape`:1287, post-job reset:1365, `finishLiveCapture`:1539, `startJob`:1316)

Traps: T13, T7.

- [x] **Step 1: Thread the flag**

```js
function loadTape(file, isLiveStereo = false) {
  S.tapeFile = file;
  S.tapeName = file.name;
  S.tapeLoaded = true;
  S.tapeIsLiveStereo = isLiveStereo;
  S.jobDone = false;
  S.pct = 0;
  syncTranscribe();
}
```

In `ejectTape` (line 1288 area) and the post-job reset (line 1365-1367 area), add `S.tapeIsLiveStereo = false;` next to `S.tapeFile = null;` in both places.

- [x] **Step 2: Capture stereo-ness before CAP is cleared (T13)**

`finishLiveCapture` (line 1539): first line of the function body:

```js
  const wasStereo = !!CAP.disp;
```

and change the `loadTape` call at line 1552 to:

```js
    loadTape(new File([blob], 'live_capture_' + stamp + '.webm', { type: 'audio/webm' }), wasStereo);
```

- [x] **Step 3: Send the field**

In `startJob` (after line 1326):

```js
  if (S.tapeIsLiveStereo) form.append('capture_source', 'live_stereo');
```

- [x] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: tag live stereo captures on upload"
```

### Task 7: Energy VAD and bleed filter primitives

**Files:**
- Modify: `services/diarization.py` (two static methods on `DiarizationService`)
- Test: `tests/test_diarization_stereo.py` (new)

Traps: T4 (numpy imported inside methods).

- [x] **Step 1: Write the failing tests**

```python
"""Channel-aware diarization primitives: energy VAD and bleed filtering."""
import numpy as np
import pytest

from services.diarization import DiarizationService

RATE = 16000


def _tone(seconds, amp=0.5):
    t = np.linspace(0, seconds, int(RATE * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _silence(seconds):
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def test_active_intervals_finds_speech_islands():
    channel = np.concatenate([_silence(1), _tone(2), _silence(2), _tone(1), _silence(1)])
    intervals = DiarizationService._active_intervals(channel, RATE)
    assert len(intervals) == 2
    s0, e0 = intervals[0]
    assert s0 == pytest.approx(1.0, abs=0.1)
    assert e0 == pytest.approx(3.0, abs=0.1)


def test_active_intervals_all_silence_is_empty():
    assert DiarizationService._active_intervals(_silence(3), RATE) == []


def test_active_intervals_merges_short_gaps():
    channel = np.concatenate([_tone(1), _silence(0.3), _tone(1)])
    intervals = DiarizationService._active_intervals(channel, RATE)
    assert len(intervals) == 1


def test_drop_bleed_removes_mic_intervals_dominated_by_system():
    mic = np.concatenate([_tone(1, amp=0.5), _tone(1, amp=0.05)])
    system = np.concatenate([_silence(1), _tone(1, amp=0.5)])
    intervals = [(0.0, 1.0), (1.0, 2.0)]
    kept = DiarizationService._drop_bleed(intervals, mic, system, RATE)
    assert kept == [(0.0, 1.0)]
```

Run: `python -m pytest tests/test_diarization_stereo.py -q`
Expected: FAIL, AttributeError (methods do not exist).

- [x] **Step 2: Implement**

Add to `DiarizationService` (after `combine_with_transcript`):

```python
    @staticmethod
    def _active_intervals(
        channel,
        sample_rate: int,
        frame_ms: int = 30,
        threshold_ratio: float = 4.0,
        min_speech_s: float = 0.25,
        max_gap_s: float = 0.6,
    ) -> list[tuple[float, float]]:
        """Energy VAD over one channel: frames whose RMS exceeds
        threshold_ratio times the noise floor (20th-percentile frame RMS)
        count as speech; speech runs closer than max_gap_s merge; runs
        shorter than min_speech_s drop. Returns [(start_s, end_s), ...]."""
        import numpy as np

        frame = int(sample_rate * frame_ms / 1000)
        n = len(channel) // frame
        if n == 0:
            return []
        rms = np.sqrt((channel[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
        floor = float(np.percentile(rms, 10)) + 1e-8  # verified during Task 7: 20th percentile lands inside the tone region on short-gap clips, inflating the floor above the clip's own peak
        speech = rms > threshold_ratio * floor

        intervals: list[tuple[float, float]] = []
        start = None
        for i, flag in enumerate(speech):
            t = i * frame / sample_rate
            if flag and start is None:
                start = t
            elif not flag and start is not None:
                intervals.append((start, t))
                start = None
        if start is not None:
            intervals.append((start, n * frame / sample_rate))

        merged: list[tuple[float, float]] = []
        for s, e in intervals:
            if merged and s - merged[-1][1] <= max_gap_s:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
        return [(s, e) for s, e in merged if e - s >= min_speech_s]

    @staticmethod
    def _drop_bleed(
        intervals: list[tuple[float, float]],
        mic,
        system,
        sample_rate: int,
        dominance: float = 1.2,
    ) -> list[tuple[float, float]]:
        """Remote voices leak into the mic through speakers. A genuine local
        utterance is louder on the mic channel than on the system channel
        over the same span; bleed is the reverse. Keep mic-dominant spans."""
        import numpy as np

        kept = []
        for s, e in intervals:
            a, b = int(s * sample_rate), int(e * sample_rate)
            rms_mic = float(np.sqrt((mic[a:b] ** 2).mean() + 1e-12))
            rms_sys = float(np.sqrt((system[a:b] ** 2).mean() + 1e-12))
            if rms_mic > rms_sys * dominance:
                kept.append((s, e))
        return kept
```

- [x] **Step 3: Run tests to verify they pass**

Run: `python -m pytest tests/test_diarization_stereo.py -q`
Expected: 4 passed. If the all-silence test fails: the noise floor of a pure-zero signal makes every frame "speech" only if threshold math is wrong; check the `+ 1e-8` lands on `floor`, not on `rms`.

- [x] **Step 4: Commit**

```bash
git add services/diarization.py tests/test_diarization_stereo.py
git commit -m "feat: energy VAD and bleed filter for channel-aware diarization"
```

### Task 8: diarize_live_stereo and the pyannote sync extraction

**Files:**
- Modify: `services/diarization.py` (`_run_pyannote_sync` extraction, `diarize_pyannote` refactor, new `diarize_live_stereo`)
- Test: append to `tests/test_diarization_stereo.py`

Traps: T4, T5, T8.

- [x] **Step 1: Extract the blocking pyannote call**

Add to `DiarizationService`:

```python
    def _run_pyannote_sync(self, waveform, sample_rate: int, num_speakers, hf_token):
        """Blocking pyannote inference on a (channel, time) float32 tensor.
        Callers wrap this in run_in_executor; imports stay inside so machines
        without torch can still import this module."""
        from pyannote.audio import Pipeline

        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token or os.environ.get("HUGGINGFACE_TOKEN", None),
        )
        output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, num_speakers=num_speakers)
        return [
            DiarizationSegment(start=turn.start, end=turn.end, speaker=speaker)
            for turn, _, speaker in output.speaker_diarization.itertracks(yield_label=True)
        ]
```

Refactor `diarize_pyannote`'s `_run()` to use it (keep the existing waveform-loading comment about torchcodec/FFmpeg DLLs):

```python
        def _run() -> list[DiarizationSegment]:
            import torch
            import soundfile as sf

            # Load audio ourselves and hand pyannote a waveform tensor rather
            # than a file path — pyannote's built-in decoder requires torchcodec,
            # which needs FFmpeg's shared-library build; Windows installs
            # commonly have the static "full_build" instead, so the decoder
            # fails to load its native DLLs.
            data, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
            waveform = torch.from_numpy(data.T)  # (channel, time)
            return self._run_pyannote_sync(waveform, sample_rate, num_speakers, hf_token)
```

- [x] **Step 2: Write the failing tests**

Append to `tests/test_diarization_stereo.py`:

```python
def _stereo_flac(tmp_path, mic, system):
    import soundfile as sf
    path = tmp_path / "cap_16k_stereo.flac"
    sf.write(str(path), np.stack([mic, system], axis=1), RATE)
    return str(path)


@pytest.mark.asyncio
async def test_live_stereo_mic_becomes_you_and_system_goes_to_pyannote(monkeypatch, tmp_path):
    from services.diarization import DiarizationSegment
    svc = DiarizationService()
    mic = np.concatenate([_tone(2), _silence(3)])
    system = np.concatenate([_silence(2), _tone(3)])
    path = _stereo_flac(tmp_path, mic, system)

    calls = {}

    def fake_sync(waveform, sample_rate, num_speakers, hf_token):
        calls["num_speakers"] = num_speakers
        calls["channels"] = waveform.shape[0]
        return [DiarizationSegment(start=2.0, end=5.0, speaker="SPEAKER_00")]

    monkeypatch.setattr(svc, "_run_pyannote_sync", fake_sync)
    result = await svc.diarize_live_stereo(path, num_speakers=3, hf_token=None)

    assert result.method == "live_stereo"
    assert calls["num_speakers"] == 2  # one fewer: the mic channel accounts for the local user
    assert calls["channels"] == 1  # system channel only, never the stereo pair
    speakers = {s.speaker for s in result.segments}
    assert "You" in speakers and "SPEAKER_00" in speakers


@pytest.mark.asyncio
async def test_live_stereo_silent_system_skips_pyannote(monkeypatch, tmp_path):
    svc = DiarizationService()
    path = _stereo_flac(tmp_path, _tone(2), _silence(2))

    def boom(*a, **k):
        raise AssertionError("pyannote must not run on a silent system channel")

    monkeypatch.setattr(svc, "_run_pyannote_sync", boom)
    result = await svc.diarize_live_stereo(path, num_speakers=2, hf_token=None)
    assert {s.speaker for s in result.segments} == {"You"}


@pytest.mark.asyncio
async def test_live_stereo_rejects_mono_file(tmp_path):
    import soundfile as sf
    path = tmp_path / "mono.flac"
    sf.write(str(path), _tone(1), RATE)
    svc = DiarizationService()
    with pytest.raises(ValueError):
        await svc.diarize_live_stereo(str(path), num_speakers=2, hf_token=None)
```

Run: `python -m pytest tests/test_diarization_stereo.py -q`
Expected: new tests FAIL (no `diarize_live_stereo`).

- [x] **Step 3: Implement diarize_live_stereo**

```python
    async def diarize_live_stereo(
        self,
        stereo_path: str,
        num_speakers: Optional[int] = None,
        hf_token: Optional[str] = None,
    ) -> DiarizationResult:
        """Channel-aware diarization for live captures (mic on channel 0,
        system audio on channel 1, see static/rack.js live capture). The mic
        channel needs no clustering: any speech there is the local user.
        Remote speakers exist only on the system channel, so pyannote runs
        on that channel alone with one fewer expected speaker."""
        import numpy as np
        import soundfile as sf

        data, sample_rate = sf.read(stereo_path, dtype="float32", always_2d=True)
        if data.shape[1] < 2:
            raise ValueError(f"{stereo_path} is not stereo — cannot channel-split")
        mic, system = data[:, 0], data[:, 1]

        mic_intervals = self._drop_bleed(
            self._active_intervals(mic, sample_rate), mic, system, sample_rate
        )
        segments = [
            DiarizationSegment(start=s, end=e, speaker="You") for s, e in mic_intervals
        ]

        remote_count = (num_speakers - 1) if num_speakers else None
        system_active = self._active_intervals(system, sample_rate)
        if remote_count != 0 and system_active:
            import torch

            waveform = torch.from_numpy(np.ascontiguousarray(system[np.newaxis, :]))
            loop = asyncio.get_event_loop()
            remote = await loop.run_in_executor(
                None, self._run_pyannote_sync, waveform, sample_rate, remote_count, hf_token
            )
            segments.extend(remote)

        segments.sort(key=lambda s: s.start)
        speaker_set = set(s.speaker for s in segments)
        return DiarizationResult(
            segments=segments, speaker_count=len(speaker_set), method="live_stereo"
        )
```

- [x] **Step 4: Run tests**

Run: `python -m pytest tests/test_diarization_stereo.py tests -q`
Expected: all stereo tests pass, baseline intact.

- [x] **Step 5: Commit**

```bash
git add services/diarization.py tests/test_diarization_stereo.py
git commit -m "feat: channel-aware diarization for live stereo captures"
```

### Task 9: Wire the stereo path through diarize_and_merge and all callers

**Files:**
- Modify: `services/diarization.py` (`diarize_and_merge` signature)
- Modify: `services/queue.py`, `services/llm_jobs.py`, `app.py` (pass `stereo_audio_path`)
- Test: append to `tests/test_diarization_metadata.py`

Traps: T2, T5, T6.

- [x] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_diarize_and_merge_prefers_live_stereo(monkeypatch, tmp_path):
    from services.diarization import DiarizationResult, DiarizationSegment
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: True)

    async def fake_stereo(stereo_path, num_speakers=None, hf_token=None):
        return DiarizationResult(
            segments=[DiarizationSegment(start=0.0, end=2.0, speaker="You")],
            speaker_count=1, method="live_stereo",
        )

    async def fail_pyannote(*a, **k):
        raise AssertionError("mixed-audio path must not run when a stereo copy exists")

    monkeypatch.setattr(svc, "diarize_live_stereo", fake_stereo)
    monkeypatch.setattr(svc, "diarize_pyannote", fail_pyannote)
    stereo = tmp_path / "s.flac"
    stereo.write_bytes(b"x")  # existence check only; the fake never reads it
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "a.mp3"), num_speakers=2,
        segments=[{"start": 0.0, "end": 1.0, "text": "hi"}],
        stereo_audio_path=str(stereo),
    )
    assert method == "live_stereo"
    assert merged[0]["speaker"] == "You"
```

Run: `python -m pytest tests/test_diarization_metadata.py -q`
Expected: FAIL (unexpected keyword `stereo_audio_path`).

- [x] **Step 2: Extend diarize_and_merge**

```python
    async def diarize_and_merge(
        self,
        audio_path: str,
        num_speakers: Optional[int],
        segments: list[dict],
        hf_token: Optional[str] = None,
        stereo_audio_path: Optional[str] = None,
    ) -> tuple[list[dict], int, str]:
        """Best-available diarization merged onto existing transcript
        segments: channel-aware live-stereo when a stereo copy exists and
        pyannote is installed, else pyannote on the mixed audio, else the
        pause-gap heuristic (which can't auto-detect, so it defaults to 2).
        Returns (merged_segments, speaker_count, method).
        Raises on failure — callers decide whether that's fatal."""
        if stereo_audio_path and os.path.exists(stereo_audio_path) and self._check_pyannote():
            try:
                result = await self.diarize_live_stereo(
                    stereo_audio_path, num_speakers=num_speakers, hf_token=hf_token
                )
            except Exception as e:
                print(f"[diarization] live-stereo path failed ({e}); falling back to mixed audio")
                result = await self.diarize_pyannote(
                    audio_path, num_speakers=num_speakers, hf_token=hf_token
                )
        elif self._check_pyannote():
            result = await self.diarize_pyannote(
                audio_path, num_speakers=num_speakers, hf_token=hf_token
            )
        else:
            result = await self.diarize_heuristic(
                audio_path, num_speakers=num_speakers or 2, segments=segments
            )
        merged = await self.combine_with_transcript(result, segments)
        return merged, result.speaker_count, result.method
```

(`os` is already imported at module top of `services/diarization.py`; verify with `grep -n "^import os" services/diarization.py` and add if missing.)

- [x] **Step 3: Pass it from all three callers (T6)**

- `services/queue.py`: next to `audio_path = transcript.audio_path` (line 515 area, BEFORE the awaits, T2) add `stereo_audio_path = transcript.stereo_audio_path`, and pass `stereo_audio_path=stereo_audio_path` in the `diarize_and_merge` call from Task 2.
- `services/llm_jobs.py` rediarize block: pass `stereo_audio_path=transcript.stereo_audio_path`.
- `app.py` inline path: pass `stereo_audio_path=transcript.stereo_audio_path` (set in Task 5 before this call runs; verify ordering: the stub/creation sets it before the diarize call at line 817).

- [x] **Step 4: Run tests**

Run: `python -m pytest tests -q`
Expected: baseline + all new tests green.

- [ ] **Step 5: Runtime verification (mandatory, unit-green is not enough) — NOT YET DONE, real gap**

On a machine with pyannote (production box): record a short live capture with system audio playing a video with a distinct voice, speak over it, transcribe with diarize on and speakers = 2. Expect exactly two labels: `You` on your lines, `SPEAKER_00` on the video's lines, and `diarization_method = "live_stereo"` in the transcript detail response. On the dev machine, at minimum drive an upload with `capture_source=live_stereo` and confirm the flac appears in uploads and `stereo_audio_path` is set (heuristic fallback will label it; that is expected without pyannote).

**Status as of end of Phase 2 (2026-07-22):** this dev machine has neither pyannote nor torch installed, so this step could not be performed. Everything up through the ffmpeg stereo transcode has been verified for real (`tests/test_audio_prep_stereo.py` runs actual ffmpeg, not mocked). The `diarize_live_stereo` pyannote-inference path has ONLY been exercised against monkeypatched unit tests — never against real pyannote output on real audio. This is the single biggest unverified assumption going into Phase 3+. Do this on the production/pyannote-equipped box before relying on live-stereo diarization in practice, and before assuming Phase 5's contingent repro runbook won't be needed.

- [x] **Step 6: Commit**

```bash
git add services/diarization.py services/queue.py services/llm_jobs.py app.py tests/test_diarization_metadata.py
git commit -m "feat: route live captures through channel-aware diarization"
```

---

## Phase 3: Undo for bulk relabels (issue item 2)

### Task 10: RelabelHistory model and record helper

**Files:**
- Modify: `database/__init__.py` (new model)
- Create: `services/relabel.py`
- Test: `tests/test_relabel_undo.py` (new)

Traps: T1, T12.

- [x] **Step 1: Model**

Add to `database/__init__.py` after `LlmJob`:

```python
class RelabelHistory(Base):
    """Inverse patch for one bulk speaker-relabel action (rename, retag,
    voice-match apply), newest-last. POST /relabel-undo pops the newest.
    Capped per transcript in services/relabel.py; no schema-level cap."""
    __tablename__ = "relabel_history"

    id = Column(Integer, primary_key=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(32), nullable=False)  # rename | retag | voice_match
    inverse = Column(JSON, nullable=False)  # {"segments": [{"index": i, "speaker": old}], "corrected_text": str|None}
    description = Column(String(255), default="")
    created_at = Column(DateTime, default=utcnow_naive)
```

New table: `Base.metadata.create_all` in `init_db` creates it automatically; no `ensure_columns` entry needed.

- [x] **Step 2: Failing tests for the helper**

Create `tests/test_relabel_undo.py`:

```python
"""Relabel history: record helper, pruning, undo endpoint."""
import pytest

from database import RelabelHistory, Transcript, User


def _test_user(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _transcript(db_session, **overrides):
    user = _test_user(db_session)
    fields = dict(
        user_id=user.id, title="mtg", filename="mtg.mp3", status="completed",
        full_text="hello there general",
        segments=[
            {"start": 0.0, "end": 2.0, "text": "hello there", "speaker": "SPEAKER_00"},
            {"start": 2.0, "end": 4.0, "text": "general kenobi", "speaker": "SPEAKER_01"},
            {"start": 4.0, "end": 6.0, "text": "you are bold", "speaker": "SPEAKER_00"},
        ],
    )
    fields.update(overrides)
    t = Transcript(**fields)
    db_session.add(t)
    db_session.commit()
    return t


def test_record_relabel_stores_inverse_and_prunes(db_session):
    from services.relabel import record_relabel, MAX_HISTORY
    t = _transcript(db_session)
    for n in range(MAX_HISTORY + 5):
        record_relabel(db_session, t, "retag", [(0, f"OLD_{n}")], description=f"run {n}")
        db_session.commit()
    rows = (db_session.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == t.id)
            .order_by(RelabelHistory.id).all())
    assert len(rows) == MAX_HISTORY
    assert rows[-1].description == f"run {MAX_HISTORY + 4}"
    assert rows[-1].inverse["segments"] == [{"index": 0, "speaker": f"OLD_{MAX_HISTORY + 4}"}]
```

Run: `python -m pytest tests/test_relabel_undo.py -q`
Expected: FAIL (no `services.relabel`).

- [x] **Step 3: Implement the helper**

Create `services/relabel.py`:

```python
"""Inverse-patch recording for bulk speaker relabels, powering undo.

Call record_relabel in the same transaction as the relabel itself, BEFORE
the commit, so the history entry and the new labels land (or roll back)
together."""
from database import RelabelHistory

MAX_HISTORY = 20


def record_relabel(db, transcript, kind: str, changed: list[tuple[int, str]],
                   corrected_text_before: str | None = None, description: str = "") -> None:
    """changed: [(segment_index, old_speaker), ...] for every segment the
    action rewrote. corrected_text_before: full before-image when the action
    also rewrites corrected_text (renames); None otherwise. Renames are not
    invertible by reverse transform (renaming A to an already-present B
    merges them), hence the before-image."""
    if not changed:
        return
    db.add(RelabelHistory(
        transcript_id=transcript.id,
        kind=kind,
        inverse={
            "segments": [{"index": i, "speaker": old} for i, old in changed],
            "corrected_text": corrected_text_before,
        },
        description=description[:255],
    ))
    stale = (
        db.query(RelabelHistory.id)
        .filter(RelabelHistory.transcript_id == transcript.id)
        .order_by(RelabelHistory.id.desc())
        .offset(MAX_HISTORY - 1)  # the row added above is pending, not yet counted
        .all()
    )
    stale_ids = [row_id for (row_id,) in stale]
    if stale_ids:
        db.query(RelabelHistory).filter(RelabelHistory.id.in_(stale_ids)).delete(
            synchronize_session=False
        )
```

Note the `MAX_HISTORY - 1`: the freshly added entry is in the session but not in query results until flush; the offset accounts for it. If the prune test fails off-by-one, check autoflush behavior first (the `db.query` triggers a flush, making the new row visible, in which case use `MAX_HISTORY` instead; the test pins the correct behavior either way).

- [x] **Step 4: Run the test, adjust the offset if the flush assumption was wrong**

Run: `python -m pytest tests/test_relabel_undo.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add database/__init__.py services/relabel.py tests/test_relabel_undo.py
git commit -m "feat: relabel history table and inverse-patch recorder"
```

### Task 11: Record in rename/retag, add undo endpoint, expose last entry

**Files:**
- Modify: `app.py` (rename `:1268`, retag `:1315`, new undo endpoint, `_serialize_transcript`)
- Test: append to `tests/test_relabel_undo.py`

Traps: T1, T6, T12, T16.

- [x] **Step 1: Failing endpoint tests**

```python
def test_rename_then_undo_restores_segments_and_corrected_text(client, db_session):
    t = _transcript(db_session, corrected_text="SPEAKER_00: hello there\n\nSPEAKER_01: general kenobi")
    r = client.post(f"/api/transcripts/{t.id}/speakers/rename",
                    json={"from": "SPEAKER_00", "to": "Alice"})
    assert r.status_code == 200

    r = client.post(f"/api/transcripts/{t.id}/relabel-undo")
    assert r.status_code == 200
    assert r.json()["undone"] == "rename"

    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert [s["speaker"] for s in t2.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert t2.corrected_text.startswith("SPEAKER_00: hello there")


def test_retag_then_undo(client, db_session):
    t = _transcript(db_session)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0, 2], "speaker": "Bob"})
    assert r.status_code == 200
    r = client.post(f"/api/transcripts/{t.id}/relabel-undo")
    assert r.status_code == 200
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert [s["speaker"] for s in t2.segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


def test_undo_with_no_history_is_404(client, db_session):
    t = _transcript(db_session)
    assert client.post(f"/api/transcripts/{t.id}/relabel-undo").status_code == 404


def test_two_undos_walk_back_two_actions(client, db_session):
    t = _transcript(db_session)
    client.post(f"/api/transcripts/{t.id}/segments/retag", json={"indices": [0], "speaker": "A"})
    client.post(f"/api/transcripts/{t.id}/segments/retag", json={"indices": [0], "speaker": "B"})
    client.post(f"/api/transcripts/{t.id}/relabel-undo")
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t2.segments[0]["speaker"] == "A"
    client.post(f"/api/transcripts/{t.id}/relabel-undo")
    db_session.expire_all()
    t3 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t3.segments[0]["speaker"] == "SPEAKER_00"
```

Run: `python -m pytest tests/test_relabel_undo.py -q`
Expected: new tests FAIL (404 on the undo route, no recording).

- [x] **Step 2: Record in the rename endpoint**

In `rename_transcript_speaker` (`app.py:1290` area), collect old values and record. The loop becomes:

```python
    renamed = 0
    changed = []
    new_segments = []
    for idx, seg in enumerate(t.segments or []):
        if (seg.get("speaker") or "") == old:
            changed.append((idx, seg.get("speaker") or ""))
            seg = {**seg, "speaker": new}
            renamed += 1
        new_segments.append(seg)
    if renamed == 0:
        raise HTTPException(status_code=400, detail=f"No segments have speaker '{old}'")

    from services.relabel import record_relabel
    record_relabel(
        db, t, "rename", changed,
        corrected_text_before=t.corrected_text if t.corrected_text else None,
        description=f"rename {old} to {new} ({renamed} lines)",
    )
    t.segments = new_segments
```

(The corrected_text rewrite below it stays unchanged; the before-image was captured above it.)

- [x] **Step 3: Record in the retag endpoint**

In `retag_transcript_segments` (`app.py:1342` area), before building `new_segments`:

```python
    index_set = set(indices)
    changed = [(i, segments[i].get("speaker") or "") for i in sorted(index_set)]
    from services.relabel import record_relabel
    record_relabel(db, t, "retag", changed,
                   description=f"retag {len(index_set)} lines to {speaker}")
```

- [x] **Step 4: Undo endpoint**

Add after the retag endpoint:

```python
@app.post("/api/transcripts/{transcript_id}/relabel-undo")
async def undo_last_relabel(
    transcript_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revert the most recent bulk relabel (rename / retag / voice match) by
    applying its stored inverse patch. Per-line manual edits are not bulk
    actions and are not tracked here."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    entry = (
        db.query(RelabelHistory)
        .filter(RelabelHistory.transcript_id == t.id)
        .order_by(RelabelHistory.id.desc())
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Nothing to undo")

    segments = list(t.segments or [])
    for patch in entry.inverse.get("segments", []):
        i = patch.get("index")
        if isinstance(i, int) and 0 <= i < len(segments):
            segments[i] = {**segments[i], "speaker": patch.get("speaker") or ""}
    t.segments = segments
    if entry.inverse.get("corrected_text") is not None:
        t.corrected_text = entry.inverse["corrected_text"]
    undone_kind, undone_desc = entry.kind, entry.description
    db.delete(entry)
    t.updated_at = utcnow_naive()
    db.commit()
    return {"undone": undone_kind, "description": undone_desc,
            "transcript": _serialize_transcript(db, t)}
```

Import `RelabelHistory` in `app.py`'s existing `from database import ...` line.

- [x] **Step 5: Expose the last entry to the UI without an N+1 (T16)**

Give `_serialize_transcript` an opt-in keyword:

```python
def _serialize_transcript(db: Session, t: Transcript, include_relabel: bool = False) -> dict:
```

and inside, at the end before returning, when `include_relabel`:

```python
    if include_relabel:
        last = (
            db.query(RelabelHistory)
            .filter(RelabelHistory.transcript_id == t.id)
            .order_by(RelabelHistory.id.desc())
            .first()
        )
        data["last_relabel"] = (
            {"kind": last.kind, "description": last.description} if last else None
        )
```

(adapt the variable name to whatever the function's return dict is called). Pass `include_relabel=True` from the single-transcript detail route and from the undo/rename/retag endpoints' responses only; find them with `grep -n "_serialize_transcript(db, t)" app.py` and change only the detail-view and relabel-related call sites, not list views.

- [x] **Step 6: Run tests**

Run: `python -m pytest tests/test_relabel_undo.py tests/test_speaker_naming.py -q`
Expected: all pass (speaker_naming still green proves rename behavior unchanged).

- [x] **Step 7: Commit**

```bash
git add app.py tests/test_relabel_undo.py
git commit -m "feat: undo endpoint for bulk speaker relabels"
```

### Task 12: Record voice-match relabels

**Files:**
- Modify: `services/llm_jobs.py:365-397`
- Test: append to `tests/test_relabel_undo.py`

Traps: T1, T6.

- [x] **Step 1: Failing test**

The voice-match job harness already exists in `tests/test_voice_match_job.py` (it stubs `extract_clips_concat` and `voice_id_service.identify`, builds a transcript with `SPEAKER_00`/`SPEAKER_01` segments, then awaits `run_llm_job`). Do this exactly:

1. Open `tests/test_voice_match_job.py` and locate its first test where a match IS applied (the one asserting a segment's speaker becomes the profile name, near line 53-81).
2. Copy that test's entire arrange/act section (fixtures, monkeypatches, job creation, `run_llm_job` call) into a new `test_voice_match_records_relabel_history` in `tests/test_relabel_undo.py`, unchanged.
3. Replace its assertions with:

```python
    rows = db_session.query(RelabelHistory).filter(RelabelHistory.kind == "voice_match").all()
    assert len(rows) == 1
    assert rows[0].inverse["segments"][0]["speaker"] == "SPEAKER_00"
```

(add `RelabelHistory` to the imports at the top of `tests/test_relabel_undo.py` if Step 2 of Task 10 did not already).

Run: `python -m pytest tests/test_relabel_undo.py -q`
Expected: FAIL (no history row).

- [x] **Step 2: Implement**

In the voice_match block of `services/llm_jobs.py`, thread a `changed` list:

```python
            skipped = 0
            changed = []
            new_segments = list(segments)
```

inside the loop where a match lands (line 389):

```python
                    if matches:
                        changed.append((i, seg.get("speaker") or ""))
                        new_segments[i] = {**seg, "speaker": matches[0]["name"]}
```

and before `transcript.segments = new_segments` (line 395):

```python
            if changed:
                from services.relabel import record_relabel
                record_relabel(db, transcript, "voice_match", changed,
                               description=f"voice match relabeled {len(changed)} lines")
```

- [x] **Step 3: Run tests**

Run: `python -m pytest tests/test_relabel_undo.py tests/test_voice_match_job.py -q`
Expected: PASS.

- [x] **Step 4: Commit**

```bash
git add services/llm_jobs.py tests/test_relabel_undo.py
git commit -m "feat: voice-match relabels are undoable"
```

### Task 13: Undo button in the transcript detail UI

**Files:**
- Modify: `static/rack.js` (actions row near `:2594`, act dispatcher near `:2778`)

Traps: T7.

- [x] **Step 1: Button**

In the detail actions row (next to the Re-diarize button block at `static/rack.js:2594`), add inside the non-dictation template section:

```js
        ${t.last_relabel ? `<button class="btn" style="font-size:12px;padding:7px 14px;border-color:var(--inset-edge)" data-dact="relabel-undo" title="${escapeHtml(t.last_relabel.description || '')}">Undo relabel</button>` : ''}
```

- [x] **Step 2: Handler**

In the same click dispatcher that handles `rediarize-history` (`static/rack.js:2778` area), add:

```js
    if (act === 'relabel-undo') {
      try {
        const res = await api('/api/transcripts/' + t.id + '/relabel-undo', { method: 'POST' });
        toast('Undid ' + (res.description || res.undone), 'info');
        await loadTranscriptDetail(t.id);
      } catch (e) { toast(e.message, 'error'); }
      return;
    }
```

(match the exact `withBusy`/`api` idiom of the neighboring handlers; if neighbors wrap in `withBusy(e.currentTarget, async () => ...)`, do the same.)

- [x] **Step 3: Selector check (T7)**

`grep -rn "Undo relabel\|relabel-undo" tests/` plus e2e dirs; add/update selectors as needed.

- [x] **Step 4: Runtime check**

Drive it: rename a speaker in the UI, confirm the Undo button appears with the action description in its tooltip, click it, confirm labels revert and the button disappears (history empty) or shows the previous action.

- [x] **Step 5: Commit**

```bash
git add static/rack.js
git commit -m "feat: undo-relabel button on transcript detail"
```

---

## Phase 4: Per-line confidence signal (issue item 3)

### Task 14: Overlap-based confidence in combine_with_transcript

**Files:**
- Modify: `services/diarization.py:235-267`
- Test: `tests/test_diarization_confidence.py` (new)

Traps: T10, T11.

- [ ] **Step 1: Failing tests**

```python
"""combine_with_transcript: per-speaker overlap totals and confidence."""
import pytest

from services.diarization import DiarizationResult, DiarizationSegment, DiarizationService


def _result(segs):
    return DiarizationResult(segments=segs, speaker_count=len({s.speaker for s in segs}), method="pyannote")


@pytest.mark.asyncio
async def test_uncontested_full_overlap_is_high_confidence():
    svc = DiarizationService()
    merged = await svc.combine_with_transcript(
        _result([DiarizationSegment(start=0.0, end=5.0, speaker="A")]),
        [{"start": 1.0, "end": 2.0, "text": "x"}],
    )
    assert merged[0]["speaker"] == "A"
    assert merged[0]["speaker_confidence"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_contested_split_overlap_is_low_confidence():
    svc = DiarizationService()
    merged = await svc.combine_with_transcript(
        _result([
            DiarizationSegment(start=0.0, end=1.1, speaker="A"),
            DiarizationSegment(start=1.1, end=2.0, speaker="B"),
        ]),
        [{"start": 0.0, "end": 2.0, "text": "x"}],
    )
    assert merged[0]["speaker"] == "A"  # 1.1s beats 0.9s
    assert merged[0]["speaker_confidence"] < 0.5


@pytest.mark.asyncio
async def test_adjacent_turns_of_same_speaker_are_not_competition():
    svc = DiarizationService()
    merged = await svc.combine_with_transcript(
        _result([
            DiarizationSegment(start=0.0, end=1.0, speaker="A"),
            DiarizationSegment(start=1.0, end=2.0, speaker="A"),
        ]),
        [{"start": 0.0, "end": 2.0, "text": "x"}],
    )
    assert merged[0]["speaker"] == "A"
    assert merged[0]["speaker_confidence"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_zero_overlap_keeps_prior_speaker_with_zero_confidence():
    svc = DiarizationService()
    merged = await svc.combine_with_transcript(
        _result([DiarizationSegment(start=10.0, end=11.0, speaker="A")]),
        [{"start": 0.0, "end": 2.0, "text": "x", "speaker": "KEEP_ME"}],
    )
    assert merged[0]["speaker"] == "KEEP_ME"
    assert merged[0]["speaker_confidence"] == 0.0
```

Run: `python -m pytest tests/test_diarization_confidence.py -q`
Expected: FAIL (KeyError speaker_confidence).

- [ ] **Step 2: Rewrite combine_with_transcript**

Replace the body (keep signature and the empty-diarization early return):

```python
        if not diarization.segments:
            return transcript_segments

        merged = []
        for seg in transcript_segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            duration = max(seg_end - seg_start, 1e-6)

            # Total overlap per SPEAKER, not per turn: one speaker usually
            # owns several adjacent diarization turns, and treating those as
            # competing would mark nearly every line uncertain.
            per_speaker: dict[str, float] = {}
            for dseg in diarization.segments:
                overlap = max(0.0, min(seg_end, dseg.end) - max(seg_start, dseg.start))
                if overlap > 0:
                    per_speaker[dseg.speaker] = per_speaker.get(dseg.speaker, 0.0) + overlap

            if per_speaker:
                ranked = sorted(per_speaker.items(), key=lambda kv: kv[1], reverse=True)
                best_speaker, best_total = ranked[0]
                second_total = ranked[1][1] if len(ranked) > 1 else 0.0
                coverage = min(best_total / duration, 1.0)
                margin = (best_total - second_total) / best_total  # 1.0 when uncontested
                confidence = round(coverage * margin, 3)
            else:
                best_speaker, confidence = None, 0.0

            merged.append({
                **seg,
                "speaker": best_speaker or seg.get("speaker", "Unknown"),
                "speaker_confidence": confidence,
            })

        return merged
```

Behavior note (deliberate, tested): assignment now uses max TOTAL overlap per speaker instead of max single turn; a speaker holding two short turns inside one Whisper segment now beats a speaker holding one slightly longer turn. This is an accuracy improvement, not an accident.

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_diarization_confidence.py tests -q`
Expected: new tests pass; if an existing combine test asserts the exact dict shape, update it to tolerate the added `speaker_confidence` key.

- [ ] **Step 4: Commit**

```bash
git add services/diarization.py tests/test_diarization_confidence.py
git commit -m "feat: per-segment speaker confidence from diarization overlap"
```

### Task 15: Surface confidence in the UI

**Files:**
- Modify: `static/rack.js:1960-1987` (segment row), `static/rack.js:2614` (Speakers cell)

Traps: T7.

- [ ] **Step 1: Line marker**

In the segment renderer (`static/rack.js:1960`), after the `speakerLabel` const (line 1973), add:

```js
    const lowConf = sg.speaker_confidence != null && sg.speaker_confidence < 0.5;
```

and in the speaker row div (line 1980-1983), after `${speakerLabel}`:

```js
          ${lowConf ? '<span title="Low-confidence speaker assignment — the diarizer was unsure here" style="font-family:var(--f-mono);font-size:10px;color:var(--nixie);cursor:help">?</span>' : ''}
```

- [ ] **Step 2: Header count**

In the Speakers cell (extended in Task 3), append:

```js
${(() => { const u = (t.segments || []).filter(s => s.speaker_confidence != null && s.speaker_confidence < 0.5).length; return u ? ` <span style="font-size:10px;color:var(--nixie)" title="Lines where the speaker assignment is uncertain">${u} uncertain</span>` : ''; })()}
```

- [ ] **Step 3: Selector check (T7) and runtime check**

Grep tests/e2e for Speakers-cell markup; then drive a diarized transcript in the browser and confirm markers render and old transcripts (no `speaker_confidence` key) render without markers or errors.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: mark low-confidence speaker assignments in transcript view"
```

---

## Phase 5 (contingent): repro runbook on the pyannote machine

Not tasks; run only if over-splitting persists after Phases 1 and 2 are deployed.

1. Pick one known-bad recording. Note `diarization_method`, `num_speakers`, `speaker_count` from the transcript detail (all persisted after Phase 1).
2. Script: load the stored audio, call `diarize_pyannote` directly with the same count, dump raw turns (start, end, label) BEFORE `combine_with_transcript`. If raw turns look right but the transcript is wrong, the bug is in combine (granularity); if raw turns are wrong, it is clustering input quality.
3. Matrix: {stored mono mp3, stereo flac right channel} x {count set, count blank}. Compare label counts and cross-label purity by listening to 3 samples per label.
4. File findings on #67 with the matrix table.

---

## Final verification (before PR)

- [ ] `python -m pytest tests -q`: baseline pass count + all new tests, same single pre-existing voice_id failure.
- [ ] Drive each changed UI surface in a real browser (re-diarize picker prefill, undo button, confidence markers).
- [ ] `git log --oneline master..HEAD` reads as a clean conventional-commit sequence, no AI-authorship trailers (T14).
- [ ] Open PR referencing #67 and #55 (undo), wait for green CI, merge with branch deletion.
