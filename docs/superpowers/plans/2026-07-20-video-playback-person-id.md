# Play Source Video During Person Identification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the transcript detail screen, a segment's play button plays the original source video (if the upload had one) starting at that segment's timestamp, instead of only audio — so the user can visually confirm who's speaking during person/speaker identification. Falls back to today's audio-only playback when there's no video.

**Design spec:** `docs/superpowers/specs/2026-07-20-video-playback-person-id-design.md` — read this first for the full rationale, especially why the reference implementation (Tkinter desktop app, ffmpeg-cut-and-open-external-player) isn't reusable and why WhisperDeck needs a real `<video>` + range-seek route instead.

**Suggested sequencing:** Tasks 1-6 (video playback) and Tasks 7-8 (file inventory/cleanup) are independently shippable — 7-8 carry real auth/path-traversal risk that 1-6 don't (client-supplied paths, cross-user permission checks). Consider landing them as two separate PRs so the security-sensitive half gets its own focused review, rather than one large PR where that risk is easy to skim past.

## Global Constraints

- Every new DB-writing function takes `db: Session` as its first parameter (existing project convention).
- `video_path`/`audio_path` file resolution always goes through `os.path.exists` before serving — same pattern as the existing audio route (`app.py:790`).
- Every new/changed route stays scoped by `current_user.id` — no exceptions (matches every existing transcript/voice route).
- Do not touch `services/audio_prep.py:transcode_for_upload` itself — it must keep stripping video for the transcription-facing audio path unchanged; this feature only adds a side-channel that preserves the original file, it doesn't change what gets sent to providers.
- No *automatic* storage-retention policy (age/size-based auto-delete) — cleanup is manual/user-initiated via the file inventory page (Tasks 7-8). This is the one thing still explicitly out of scope per the design spec's Open Tradeoffs section.
- File-deletion behavior (confirmed with the user): deleting a file that's still linked to a transcript never deletes the transcript — only the file is removed and the matching column (`audio_path`/`video_path`) is nulled; transcript text/segments/speakers stay fully intact.
- `POST /api/files/delete` takes a client-supplied path list — every path MUST be resolved to an absolute real path and validated as being inside `UPLOAD_DIR` before any filesystem operation. This is attacker-shaped input (path traversal), not an internal call; do not skip this check to save a step.

---

## File Structure

- **Modify** `services/audio_prep.py` — add `has_video_stream(path: str) -> bool`.
- **Modify** `database/__init__.py` — add `Transcript.video_path` column + `ensure_columns` entry.
- **Modify** `services/transcription.py` — `create_transcript_stub` and `transcribe` accept `video_path: Optional[str] = None` and set it on the `Transcript(...)` constructor.
- **Modify** `app.py` — `_run_transcription_pipeline` detects/carries-forward `video_path`; `_serialize_transcript` gains `has_video`; new `_VIDEO_MIME` table + `GET /api/transcripts/{id}/video` route.
- **Modify** `static/rack.js` — `<video>` element in the detail page template, `segPlay` branches on `t.has_video`, `resetSegAudio` extended, `segmentsHtml` gating updated; new Files page.
- **Modify** `app.py` (again, Task 7) — `GET /api/files`, `POST /api/files/delete`, `delete_transcript` gains file cleanup.
- **Test**: new `tests/test_video_stream_detection.py`, `tests/test_transcribe_local_transcode.py` (extend), new `tests/test_transcript_video.py`, new `tests/test_file_inventory.py`.

---

### Task 1: `has_video_stream()` detection helper

**Files:**
- Modify: `services/audio_prep.py`
- Test: new `tests/test_video_stream_detection.py`

**Interfaces:**
- Produces: `has_video_stream(path: str) -> bool` — ffprobe check for at least one video stream, independent of file extension.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_video_stream_detection.py`:

```python
"""has_video_stream(): ffprobe-based check for a video stream, independent
of file extension — a .mp4 that's actually audio-only (or a misnamed
file) must not falsely report having video."""
import shutil
import subprocess

import pytest

from services.audio_prep import has_video_stream

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _make_video(path):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
         "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )


def _make_audio_only(path):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1",
         "-c:a", "libmp3lame", str(path)],
        check=True, capture_output=True,
    )


def test_detects_video_stream(tmp_path):
    video = tmp_path / "clip.mp4"
    _make_video(video)
    assert has_video_stream(str(video)) is True


def test_audio_only_file_has_no_video_stream(tmp_path):
    audio = tmp_path / "clip.mp3"
    _make_audio_only(audio)
    assert has_video_stream(str(audio)) is False


def test_misnamed_audio_only_mp4_has_no_video_stream(tmp_path):
    """Extension lies — an .mp4 with only an audio stream must still
    report False, since this drives whether we retain/serve it as video."""
    audio = tmp_path / "not_really_video.mp4"
    _make_audio_only(audio)
    assert has_video_stream(str(audio)) is False


def test_missing_file_returns_false_not_raise(tmp_path):
    assert has_video_stream(str(tmp_path / "nope.mp4")) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_video_stream_detection.py -v`
Expected: `ImportError: cannot import name 'has_video_stream'`.

- [ ] **Step 3: Implement**

In `services/audio_prep.py`, add after `get_audio_duration` (currently ending line 97):

```python
def has_video_stream(path: str) -> bool:
    """True if the file has at least one video stream — used to decide
    whether to retain the original upload for playback, independent of
    file extension (a misnamed or audio-only .mp4 must report False)."""
    try:
        result = subprocess.run(
            [_ffprobe_bin(), "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_video_stream_detection.py -v`
Expected: all pass (skipped if ffmpeg isn't on PATH in the dev environment — matches this repo's existing convention for ffmpeg-dependent tests).

- [ ] **Step 5: Commit**

```bash
git add services/audio_prep.py tests/test_video_stream_detection.py
git commit -m "feat: add has_video_stream ffprobe detection helper"
```

---

### Task 2: `Transcript.video_path` column

**Files:**
- Modify: `database/__init__.py`

**Interfaces:**
- Produces: `Transcript.video_path` column (nullable string), migrated via `ensure_columns`.

- [ ] **Step 1: Add the column**

In `database/__init__.py`, in `class Transcript`, immediately after the existing `audio_path` line (44):

```python
    video_path = Column(String(512), nullable=True)  # original upload, kept only if it had a video stream — see services/audio_prep.py:has_video_stream
```

- [ ] **Step 2: Add the migration entry**

Change line 298:

```python
ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER", "processed_size_bytes": "INTEGER", "corrected_text": "TEXT", "correction_error": "TEXT", "correction_model": "TEXT", "queue_dismissed": "BOOLEAN DEFAULT 0", "source_transcript_id": "INTEGER", "video_path": "TEXT"})
```

- [ ] **Step 3: Verify the column is created**

Run:
```bash
.venv/Scripts/python.exe -c "
from database import init_db, Transcript
from sqlalchemy import inspect
engine, SessionLocal, _ = init_db('file::memory:?cache=shared')
cols = [c['name'] for c in inspect(engine).get_columns('transcripts')]
print('video_path' in cols)
"
```
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add database/__init__.py
git commit -m "feat: add Transcript.video_path column"
```

---

### Task 3: `video_path` plumbed through transcript creation

**Files:**
- Modify: `services/transcription.py`
- Test: `tests/test_transcribe_local_transcode.py` (extend)

**Interfaces:**
- Consumes: `Transcript.video_path` (Task 2).
- Produces: `create_transcript_stub(..., video_path: Optional[str] = None)`, `transcribe(..., video_path: Optional[str] = None)` (via existing `**kwargs` or explicit param — use explicit, matching `create_transcript_stub`'s style) — both set `video_path` on the `Transcript(...)` row they construct.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transcribe_local_transcode.py`:

```python
def test_transcript_stub_persists_video_path(db_session):
    from services.transcription import TranscriptionService
    svc = TranscriptionService()
    t = svc.create_transcript_stub(
        db_session, user_id=1, filename="f.mp4", provider_name="groq", model="",
        language="en", audio_path="/tmp/f_16k.mp3", diarize_requested=False,
        video_path="/tmp/f.mp4",
    )
    assert t.video_path == "/tmp/f.mp4"


def test_transcript_stub_video_path_defaults_none(db_session):
    from services.transcription import TranscriptionService
    svc = TranscriptionService()
    t = svc.create_transcript_stub(
        db_session, user_id=1, filename="f.mp3", provider_name="groq", model="",
        language="en", audio_path="/tmp/f_16k.mp3", diarize_requested=False,
    )
    assert t.video_path is None
```

(Adjust the import path/class name for `TranscriptionService` to match whatever `services/transcription.py` actually names the class — confirm via `grep -n "^class" services/transcription.py` before writing; the design spec references its `create_transcript_stub`/`transcribe` methods but the enclosing class name wasn't pinned down in scouting.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcribe_local_transcode.py -k video_path -v`
Expected: `TypeError: create_transcript_stub() got an unexpected keyword argument 'video_path'`.

- [ ] **Step 3: Implement**

In `services/transcription.py`, `create_transcript_stub` signature (currently line 19-31) gains the new param and passes it through:

```python
    def create_transcript_stub(
        self,
        db,
        user_id: int,
        filename: str,
        provider_name: str,
        model: str,
        language: str,
        audio_path: str,
        diarize_requested: bool,
        title: Optional[str] = None,
        num_speakers: Optional[int] = None,
        video_path: Optional[str] = None,
    ) -> Transcript:
        ...
        transcript = Transcript(
            user_id=user_id,
            title=title or os.path.splitext(filename)[0],
            filename=filename,
            provider=provider_name,
            model=model or "",
            language=language,
            status="processing",
            audio_path=audio_path,
            diarize_requested=diarize_requested,
            num_speakers=num_speakers,
            video_path=video_path,
        )
```

`transcribe` (currently line 56-88) gains the same param, threaded onto its own `Transcript(...)` construction (line 77-86):

```python
    async def transcribe(
        self,
        db,
        user_id: int,
        audio_path: str,
        provider_name: str = "groq",
        provider_config: Optional[dict] = None,
        title: Optional[str] = None,
        language: str = "en",
        model: Optional[str] = None,
        temperature: float = 0.0,
        video_path: Optional[str] = None,
        **kwargs,
    ) -> Transcript:
        ...
        transcript = Transcript(
            user_id=user_id,
            title=title or os.path.splitext(filename)[0],
            filename=filename,
            provider=provider_name,
            model=provider_config.get("default_model", ""),
            language=language,
            status="processing",
            audio_path=audio_path,
            video_path=video_path,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcribe_local_transcode.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/transcription.py tests/test_transcribe_local_transcode.py
git commit -m "feat: thread video_path through transcript creation"
```

---

### Task 4: `_run_transcription_pipeline` detects and carries forward `video_path`

**Files:**
- Modify: `app.py`
- Test: `tests/test_transcribe_local_transcode.py` (extend — add `import os` at the top of this file if not already present; the existing file only imports `io` and `unittest.mock`)

**Interfaces:**
- Consumes: `has_video_stream` (Task 1), `video_path` params (Task 3).
- Produces: fresh video uploads get `video_path` set to the original file; fresh audio-only uploads get `video_path=None`; a retranscribed version (`source_transcript_id` set) inherits its parent's `video_path` without re-probing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transcribe_local_transcode.py`:

```python
def test_video_upload_persists_video_path(client, db_session):
    """audio_path/video_path are always absolute — UPLOAD_DIR is built from
    BASE_DIR = Path(__file__).parent.resolve() (app.py:49), and save_path =
    UPLOAD_DIR / safe_name (app.py:611) inherits that. video_path must be
    the raw upload's own path (with its original .mp4 extension), NOT the
    transcoded output — the mocked transcode returns the input path
    unchanged (`_transcode_mock`, line 5-6), so this also implicitly checks
    that has_video_stream's raw_path capture happens before transcode
    reassigns save_path, not after."""
    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.has_video_stream", return_value=True), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp4", io.BytesIO(b"fake mp4 bytes"), "video/mp4")},
            data={"provider": "moonshine"},
        )
    assert response.status_code == 200
    from database import Transcript
    saved = db_session.query(Transcript).order_by(Transcript.id.desc()).first()
    assert saved.video_path is not None
    assert os.path.isabs(saved.video_path)
    assert saved.video_path.endswith(".mp4")


def test_audio_only_upload_has_no_video_path(client, db_session):
    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.has_video_stream", return_value=False), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
            data={"provider": "moonshine"},
        )
    assert response.status_code == 200
    from database import Transcript
    saved = db_session.query(Transcript).order_by(Transcript.id.desc()).first()
    assert saved.video_path is None


def test_retranscribe_carries_forward_parent_video_path(client, db_session, tmp_path):
    """Parent's stored audio must actually exist on disk and transcode must
    be mocked — _run_transcription_pipeline probes duration and file size
    on the incoming path (app.py:461, app.py:494) before the has_video_stream
    branch even runs, so a missing file or a real ffmpeg call both blow up
    this test for reasons unrelated to what it's testing."""
    from database import Transcript
    parent_audio = tmp_path / "p_16k.mp3"
    parent_audio.write_bytes(b"fake mp3 bytes")
    parent = Transcript(user_id=1, title="p", filename="p.mp4", status="completed",
                        full_text="x", audio_path=str(parent_audio), video_path="/tmp/p.mp4")
    db_session.add(parent)
    db_session.commit()

    fake_transcode = _transcode_mock()
    with patch("app.transcode_for_upload", fake_transcode), \
         patch("app.has_video_stream", return_value=False), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(f"/api/transcripts/{parent.id}/retranscribe",
                                data={"provider": "groq"})
    assert response.status_code == 200
    saved = db_session.query(Transcript).order_by(Transcript.id.desc()).first()
    assert saved.id != parent.id
    assert saved.video_path == "/tmp/p.mp4"
```

Confirmed against `app.py:735-772`: `/retranscribe` (`POST`, `provider` required form field, `audio_path`/`video_path` not accepted from the client) always creates a **new** transcript row and always passes `source_transcript_id=root_id` (where `root_id = t.source_transcript_id or t.id` — so a retranscribe-of-a-retranscribe still points at the original root, not the immediate parent). This means the `else` branch in Task 4's Step 3 (`has_video_stream` re-probe) never runs for retranscribe — it always takes the carry-forward branch. Safe as designed.

`_stub_transcribe` in this file (line 54-59) must be updated to honor `video_path`, since it *replaces* `transcription_service.transcribe` in these tests — the real param-threading from Task 3 never executes under the mock, so without this change every assertion here would silently check the mock's hardcoded output instead of the real code path:

```python
async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed",
                   full_text="hello world", video_path=kwargs.get("video_path"))
    db.add(t)
    db.commit()
    return t
```
(existing 3 tests in this file don't pass `video_path` through their patched calls, so `kwargs.get("video_path")` is `None` for them — no behavior change.)

Note: these tests only exercise the inline `transcribe()` path. The chunked `create_transcript_stub()` path (long video, hosted-chunked or local-chunked branch in `_run_transcription_pipeline`) also receives `video_path=video_path` per Step 3 below but isn't covered by a test here — acceptable gap for this task, flag it if picked up later.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcribe_local_transcode.py -k video -v`
Expected: `has_video_stream` not found on `app` module (no `AttributeError` patch target) or `has_video`/`video_path` missing/wrong in the response.

- [ ] **Step 3: Implement**

In `app.py`, add the import (alongside the existing `transcode_for_upload, AudioPrepError, ...` import, line 31):

```python
from services.audio_prep import transcode_for_upload, AudioPrepError, chunk_audio, get_audio_duration, extract_clips_concat, has_video_stream
```

Add `_VIDEO_MIME` near `_AUDIO_MIME` — deliberately restricted to containers a browser `<video>` tag can actually play. `.mkv`/`.avi`/most `.mov` are NOT included: retaining them as "video" would reproduce the exact "sort of works" problem this feature is meant to fix (file exists, route serves it, but the browser shows a black player with no error). If a wider format is needed later, that's a real follow-up (transcode-on-ingest to mp4), not a silent allowlist expansion:

```python
_VIDEO_MIME = {
    ".mp4": "video/mp4", ".webm": "video/webm",
}
```

In `_run_transcription_pipeline` (`app.py:426`), right before the `needs_transcode` check (before line 460's `try: raw_duration = ...`), capture the raw path and resolve `video_path` — gated on both having a video stream AND being a browser-playable container:

```python
    raw_path = save_path
    if source_transcript_id is not None:
        parent = db.query(Transcript).filter(Transcript.id == source_transcript_id).first()
        video_path = parent.video_path if parent else None
    else:
        playable = raw_path.suffix.lower() in _VIDEO_MIME
        video_path = str(raw_path) if playable and has_video_stream(str(raw_path)) else None
```

A video uploaded in a non-playable container (e.g. `.mkv`) still transcribes normally (audio track is extracted same as always) — it just doesn't get the new video-playback control; segment play falls back to audio-only, i.e. today's behavior, not a regression.

Then pass `video_path=video_path` into both transcript-creation calls:
- `transcription_service.create_transcript_stub(...)` (line 526-537) — add `video_path=video_path,` as a new kwarg.
- `transcription_service.transcribe(...)` (line 550-561) — add `video_path=video_path,` as a new kwarg.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcribe_local_transcode.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_transcribe_local_transcode.py
git commit -m "feat: detect and persist source video path during ingestion"
```

---

### Task 5: `has_video` serialization + `GET /api/transcripts/{id}/video` route

**Files:**
- Modify: `app.py`
- Test: new `tests/test_transcript_video.py`

**Interfaces:**
- Produces: `_serialize_transcript` response gains `"has_video": bool`; `GET /api/transcripts/{transcript_id}/video` streams the file with range support (free via Starlette `FileResponse`, same as the existing audio route).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcript_video.py`:

```python
"""GET /api/transcripts/{id}/video and has_video serialization."""
from database import Transcript


def _video_transcript(db_session, tmp_path, video_path=None):
    t = Transcript(user_id=1, title="t", filename="t.mp4", status="completed",
                   full_text="x", audio_path=str(tmp_path / "a.mp3"), video_path=video_path)
    db_session.add(t)
    db_session.commit()
    return t


def test_has_video_false_when_no_video_path(client, db_session, tmp_path):
    t = _video_transcript(db_session, tmp_path)
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["has_video"] is False


def test_has_video_false_when_file_missing(client, db_session, tmp_path):
    t = _video_transcript(db_session, tmp_path, video_path=str(tmp_path / "gone.mp4"))
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["has_video"] is False


def test_has_video_true_when_file_present(client, db_session, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake mp4 bytes")
    t = _video_transcript(db_session, tmp_path, video_path=str(video))
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["has_video"] is True


def test_video_route_serves_file(client, db_session, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"real mp4 bytes")
    t = _video_transcript(db_session, tmp_path, video_path=str(video))
    r = client.get(f"/api/transcripts/{t.id}/video")
    assert r.status_code == 200
    assert r.content == b"real mp4 bytes"
    assert r.headers["content-type"] == "video/mp4"


def test_video_route_404_when_no_video_path(client, db_session, tmp_path):
    t = _video_transcript(db_session, tmp_path)
    r = client.get(f"/api/transcripts/{t.id}/video")
    assert r.status_code == 404


def test_video_route_404_for_other_users_transcript(client, db_session, tmp_path):
    from database import User
    other = User(username="otheruser", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"bytes")
    t = Transcript(user_id=other.id, title="t", filename="t.mp4", status="completed",
                   full_text="x", video_path=str(video))
    db_session.add(t)
    db_session.commit()
    r = client.get(f"/api/transcripts/{t.id}/video")
    assert r.status_code == 404


def test_video_route_supports_range_requests(client, db_session, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0123456789")
    t = _video_transcript(db_session, tmp_path, video_path=str(video))
    r = client.get(f"/api/transcripts/{t.id}/video", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcript_video.py -v`
Expected: `KeyError: 'has_video'` and 404s (route doesn't exist).

- [ ] **Step 3: Implement**

In `app.py`, `_serialize_transcript` (`app.py:146-186`), add right next to the existing `has_audio` line (177):

```python
        "has_video": bool(t.video_path and os.path.exists(t.video_path)),
```

Add the route right after `get_transcript_audio` (currently ending `app.py:793`):

```python
@app.get("/api/transcripts/{transcript_id}/video")
async def get_transcript_video(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Serve the stored original video — the detail screen's per-line play
    buttons load this once and seek to each segment's start time, same
    pattern as get_transcript_audio."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not (t.video_path and os.path.exists(t.video_path)):
        raise HTTPException(status_code=404, detail="No stored video for this transcript")
    ext = os.path.splitext(t.video_path)[1].lower()
    return FileResponse(t.video_path, media_type=_VIDEO_MIME.get(ext, "video/mp4"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcript_video.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_transcript_video.py
git commit -m "feat: serve source video with has_video flag and range-seek support"
```

---

### Task 6: Frontend — `<video>` element + segment play wired to it

**Files:**
- Modify: `static/rack.js`

**Interfaces:**
- Consumes: `t.has_video`, `GET /api/transcripts/{id}/video` (Task 5).
- Produces: detail page shows a `<video>` element when `has_video`; `segPlay` seeks/plays it for video-sourced transcripts, falls back to the existing `segAudio` behavior otherwise.

- [ ] **Step 1: Add module state and reset handling**

At `rack.js:1682`, alongside the existing declaration:

```js
let segAudio = null, segAudioTid = null, segPlayingBtn = null;
let segVideoTid = null;
```

Extend `resetSegAudio()` (`rack.js:1690-1696`):

```js
function resetSegAudio() {
  if (segAudio) segAudio.pause();
  segAudio = null;
  segAudioTid = null;
  segPlayingBtn = null;
  seedClips = {};
  const v = $('seg-video');
  if (v) v.pause();
  segVideoTid = null;
}
```

- [ ] **Step 2: Add the `<video>` element to the detail page template**

`renderDetail()` (`rack.js:2262`) re-runs on every rename, seed-toggle, and job-poll tick with a changed fingerprint (`scheduleDetailPoll`, `rack.js:1717-1732`) — during person-ID the user is actively renaming/seeding, so re-renders **while a segment is mid-playback are the expected case, not an edge case**. A detached `Audio()` object (like `segAudio`) survives a re-render because it isn't a DOM node; a `<video>` element written into the template is destroyed and rebuilt every time `renderDetail()` runs. Two things in this step exist specifically to survive that:

1. Put `src` directly in the template attribute (not set imperatively in JS after the fact) so a freshly-rebuilt node is immediately pointed at the right URL with no separate "first play sets src" step to miss:

```js
const videoHtml = t.has_video
  ? `<video id="seg-video" controls src="/api/transcripts/${t.id}/video" style="width:100%;max-height:260px;background:#000;border:1px solid var(--inset-edge);border-radius:4px;margin-bottom:12px"></video>`
  : '';
```

Splice `videoHtml` into the returned template string immediately above wherever `detailTabsHtml()`'s output is placed (exact insertion point depends on `renderDetail`'s current template layout — read the function in full before editing to match its existing structure/indentation rather than guessing blind).

2. Because the node (and therefore any listeners attached to it) is rebuilt every render, `segPlayVideo` (Step 4 below) must NOT gate listener attachment behind a "have we seen this transcript id before" check the way `segAudio` does — that check is exactly what would silently no-op after a mid-playback re-render (new node, no listeners, but the guard variable still says "already set up"). Instead gate on a flag stashed on the node itself (`v._wired`), which is automatically absent on a freshly-rebuilt node but present (and skips re-attaching) across repeated clicks on the same node between re-renders — this survives re-render AND avoids stacking duplicate listeners on unchanged nodes. `segVideoTid` is kept separately, only to note which transcript's video is loaded (informational; the src itself is always correct from the template regardless).

- [ ] **Step 3: Update `segmentsHtml` gating**

Change `rack.js:1762` from:
```js
    const controls = !t.has_audio ? '' : `
```
to:
```js
    const controls = !(t.has_audio || t.has_video) ? '' : `
```

- [ ] **Step 4: Branch `segPlay` on `t.has_video`**

Replace `segPlay` (`rack.js:1808-1835`) with a version that branches at the top and factors the shared seek/stop-at-end logic:

```js
function segPlay(btn) {
  const t = detailData;
  const start = parseFloat(btn.dataset.start), end = parseFloat(btn.dataset.end);
  if (t.has_video) return segPlayVideo(btn, t, start, end);
  return segPlayAudio(btn, t, start, end);
}

function segPlayVideo(btn, t, start, end) {
  const v = $('seg-video');
  if (!v) return;
  // Wiring is keyed off the node itself (v._wired), not segVideoTid — the
  // node is rebuilt on every renderDetail() (rename/seed/poll), so a
  // transcript-id-based guard would silently no-op after a mid-playback
  // re-render (new node, no listeners, but the guard variable still says
  // "already set up"). v._wired resets to undefined on a fresh node
  // automatically (it's a new object), so this both survives a re-render
  // AND avoids stacking duplicate listeners across repeated clicks on the
  // SAME node between re-renders — src is already correct from the
  // template (Step 2), so first-wire doesn't need to touch it.
  if (!v._wired) {
    v.addEventListener('timeupdate', () => {
      if (v._stopAt != null && v.currentTime >= v._stopAt) v.pause();
    });
    v.addEventListener('pause', () => {
      if (segPlayingBtn) { segPlayingBtn.textContent = '▶'; segPlayingBtn = null; }
    });
    v._wired = true;
  }
  segVideoTid = t.id;
  if (segPlayingBtn === btn && !v.paused) { v.pause(); return; }
  const seekAndPlay = () => {
    v._stopAt = end;
    v.currentTime = start;
    v.play().catch(err => toast(err.message, 'error'));
  };
  if (v.readyState >= 1) seekAndPlay();
  else v.addEventListener('loadedmetadata', seekAndPlay, { once: true });
  segPlayingBtn = btn;
  btn.textContent = '■';
}

function segPlayAudio(btn, t, start, end) {
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
  }
  if (segPlayingBtn === btn && !segAudio.paused) { segAudio.pause(); return; }
  const seekAndPlay = () => {
    segAudio._stopAt = end;
    segAudio.currentTime = start;
    segAudio.play().catch(err => toast(err.message, 'error'));
  };
  if (segAudio.readyState >= 1) seekAndPlay();
  else segAudio.addEventListener('loadedmetadata', seekAndPlay, { once: true });
  segPlayingBtn = btn;
  btn.textContent = '■';
}
```

Read `rack.js:1808-1835` in full immediately before this edit to confirm the exact current body (it's reproduced from the earlier scouting pass above, but re-read before editing per this project's own verification habits) — `segPlayAudio` above is that same body unchanged, only renamed and factored out so `segPlay` can dispatch.

- [ ] **Step 5: Manual verification (no e2e harness covers per-segment playback today)**

1. Start the dev server, upload a short real mp4 with an audio track, wait for transcription+diarization to complete.
2. Open its detail page — confirm the `<video>` element renders above the segment list.
3. Click a segment's play button — confirm the video seeks to that segment's `start` and stops playing at `end` (matching the existing audio behavior).
4. Click a different segment's play button while one is playing — confirm it stops the first and seeks/plays the new one.
5. Open an audio-only transcript (existing data, or upload a `.wav`) — confirm play buttons still work exactly as before (plain audio, no video element rendered).
6. Retranscribe a video-sourced transcript — confirm the new version's detail page still shows the video element (video_path carried forward per Task 4).

- [ ] **Step 6: Commit**

```bash
git add static/rack.js
git commit -m "feat: play source video at segment timestamp during person identification"
```

---

### Task 7: File inventory + delete route + `delete_transcript` cleanup

**Files:**
- Modify: `app.py`
- Test: new `tests/test_file_inventory.py`

**Interfaces:**
- Produces: `GET /api/files` (linked/orphaned classification, scoped to `current_user`'s own transcripts for "linked"), `POST /api/files/delete` (body `{"paths": [...]}`, path-traversal-guarded, nulls the matching transcript column instead of deleting the transcript), `delete_transcript` now also removes its own `audio_path`/`video_path` files.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_file_inventory.py`:

```python
"""GET /api/files inventory and POST /api/files/delete cleanup — the file
never takes the transcript down with it, only the path/file it targets."""
import os

from database import Transcript, TranscriptionJob, User


def _other_user(db_session):
    u = User(username="otheruser2", password_hash="x", password_salt="y")
    db_session.add(u)
    db_session.commit()
    return u


def test_list_files_classifies_linked_and_orphaned(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    linked_file = tmp_path / "linked.mp3"
    linked_file.write_bytes(b"linked")
    orphan_file = tmp_path / "orphan.mp3"
    orphan_file.write_bytes(b"orphan")

    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed",
                   full_text="x", audio_path=str(linked_file))
    db_session.add(t)
    db_session.commit()

    r = client.get("/api/files")
    assert r.status_code == 200
    body = r.json()
    linked_paths = [f["path"] for f in body["linked"]]
    orphan_paths = [f["path"] for f in body["orphaned"]]
    assert str(linked_file) in linked_paths
    assert str(orphan_file) in orphan_paths
    assert str(linked_file) not in orphan_paths


def test_list_files_excludes_other_users_linked_file_from_both_lists(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    other = _other_user(db_session)
    other_file = tmp_path / "other.mp3"
    other_file.write_bytes(b"other")
    t = Transcript(user_id=other.id, title="t", filename="t.mp3", status="completed",
                   full_text="x", audio_path=str(other_file))
    db_session.add(t)
    db_session.commit()

    r = client.get("/api/files")
    body = r.json()
    all_paths = [f["path"] for f in body["linked"]] + [f["path"] for f in body["orphaned"]]
    assert str(other_file) not in all_paths


def test_list_files_excludes_in_flight_job_chunk_from_orphaned(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    chunk_file = tmp_path / "chunk_0.mp3"
    chunk_file.write_bytes(b"chunk")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="processing", full_text="")
    db_session.add(t)
    db_session.commit()
    job = TranscriptionJob(transcript_id=t.id, chunk_index=0, audio_path=str(chunk_file), status="running")
    db_session.add(job)
    db_session.commit()

    r = client.get("/api/files")
    orphan_paths = [f["path"] for f in r.json()["orphaned"]]
    assert str(chunk_file) not in orphan_paths


def test_delete_rejects_path_outside_upload_dir(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"do not delete me")

    r = client.post("/api/files/delete", json={"paths": [str(outside)]})
    assert r.status_code == 400
    assert outside.exists()


def test_delete_linked_file_nulls_column_keeps_transcript(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    linked_file = tmp_path / "linked.mp3"
    linked_file.write_bytes(b"linked")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="keep me", filename="t.mp3", status="completed",
                   full_text="full transcript text", audio_path=str(linked_file))
    db_session.add(t)
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(linked_file)]})
    assert r.status_code == 200
    assert str(linked_file) in r.json()["deleted"]
    assert not linked_file.exists()

    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t2 is not None
    assert t2.title == "keep me"
    assert t2.full_text == "full transcript text"
    assert t2.audio_path is None


def test_delete_skips_other_users_linked_file(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    other = _other_user(db_session)
    other_file = tmp_path / "other.mp3"
    other_file.write_bytes(b"other")
    t = Transcript(user_id=other.id, title="t", filename="t.mp3", status="completed",
                   full_text="x", audio_path=str(other_file))
    db_session.add(t)
    db_session.commit()

    r = client.post("/api/files/delete", json={"paths": [str(other_file)]})
    assert r.status_code == 200
    body = r.json()
    assert any(s["path"] == str(other_file) and s["reason"] == "forbidden" for s in body["skipped"])
    assert other_file.exists()


def test_delete_orphan_removes_outright(client, db_session, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path)
    orphan_file = tmp_path / "orphan.mp3"
    orphan_file.write_bytes(b"orphan")

    r = client.post("/api/files/delete", json={"paths": [str(orphan_file)]})
    assert r.status_code == 200
    assert str(orphan_file) in r.json()["deleted"]
    assert not orphan_file.exists()


def test_delete_transcript_removes_its_media_files(client, db_session, tmp_path):
    from database import Transcript as _T
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"a")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = _T(user_id=user.id, title="t", filename="t.mp4", status="completed",
           full_text="x", audio_path=str(audio), video_path=str(video))
    db_session.add(t)
    db_session.commit()

    r = client.delete(f"/api/transcripts/{t.id}")
    assert r.status_code == 200
    assert not audio.exists()
    assert not video.exists()
```

Check the exact `TranscriptionJob` constructor field names (`chunk_index` used above is a guess — confirm against `database/__init__.py`'s actual columns, currently only scouted down to `audio_path`/`status`/`attempts` at lines 68-71; read the full class before finalizing this test) and adjust.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_inventory.py -v`
Expected: 404s (routes don't exist yet); `test_delete_transcript_removes_its_media_files` fails because the files still exist after delete.

- [ ] **Step 3: Implement**

In `app.py`, add near the top-level route section (grouped with the other transcript-adjacent routes, e.g. right before or after the voice-clip routes):

```python
def _resolve_under_upload_dir(path_str: str) -> Optional[str]:
    """Path-traversal guard: resolve to a real absolute path and confirm
    it's inside UPLOAD_DIR before any filesystem operation touches it.
    Returns None if the path escapes UPLOAD_DIR or doesn't exist."""
    try:
        real = os.path.realpath(path_str)
        upload_real = os.path.realpath(str(UPLOAD_DIR))
        if os.path.commonpath([real, upload_real]) != upload_real:
            return None
        return real
    except (OSError, ValueError):
        return None


@app.get("/api/files")
async def list_files(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    in_flight_paths = {
        os.path.realpath(j.audio_path)
        for j in db.query(TranscriptionJob).filter(TranscriptionJob.status.in_(["pending", "running"])).all()
        if j.audio_path
    }
    linked_by_path = {}
    for t in db.query(Transcript).filter(Transcript.user_id == current_user.id).all():
        for field in ("audio_path", "video_path"):
            p = getattr(t, field)
            if p and os.path.exists(p):
                linked_by_path[os.path.realpath(p)] = (t, field)
    # Any transcript row (any user) that references a path excludes it from
    # "orphaned" even if it belongs to another user — it's just excluded
    # from this response entirely in that case (not shown as linked OR orphaned).
    all_referenced_paths = set(linked_by_path.keys())
    for t in db.query(Transcript).filter(Transcript.user_id != current_user.id).all():
        for field in ("audio_path", "video_path"):
            p = getattr(t, field)
            if p:
                all_referenced_paths.add(os.path.realpath(p))

    linked, orphaned = [], []
    total_linked, total_orphaned = 0, 0
    for name in os.listdir(UPLOAD_DIR):
        full = os.path.join(str(UPLOAD_DIR), name)
        if not os.path.isfile(full):
            continue  # confirmed: chunk_audio is called as chunk_audio(str(save_path), str(UPLOAD_DIR), ...) (app.py:522) — chunks land flat in UPLOAD_DIR, no subdirectory, so this listdir() does see them
        real = os.path.realpath(full)
        size = os.path.getsize(full)
        mtime = datetime.datetime.utcfromtimestamp(os.path.getmtime(full)).isoformat()
        if real in linked_by_path:
            t, field = linked_by_path[real]
            linked.append({"transcript_id": t.id, "transcript_title": t.title, "field": field,
                            "path": full, "size_bytes": size, "modified_at": mtime})
            total_linked += size
        elif real in in_flight_paths or real in all_referenced_paths:
            continue  # in-flight chunk, or belongs to another user — excluded entirely
        else:
            orphaned.append({"path": full, "size_bytes": size, "modified_at": mtime})
            total_orphaned += size
    return {"linked": linked, "orphaned": orphaned,
            "total_linked_bytes": total_linked, "total_orphaned_bytes": total_orphaned}


@app.post("/api/files/delete")
async def delete_files(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    paths = data.get("paths") or []
    in_flight_paths = {
        os.path.realpath(j.audio_path)
        for j in db.query(TranscriptionJob).filter(TranscriptionJob.status.in_(["pending", "running"])).all()
        if j.audio_path
    }
    deleted, skipped = [], []
    freed_bytes = 0
    for raw_path in paths:
        real = _resolve_under_upload_dir(raw_path)
        if real is None:
            raise HTTPException(status_code=400, detail=f"Path not allowed: {raw_path}")
        if real in in_flight_paths:
            skipped.append({"path": raw_path, "reason": "in_use"})
            continue
        owner_match = None
        foreign_match = False
        for t in db.query(Transcript).filter(
            (Transcript.audio_path == raw_path) | (Transcript.video_path == raw_path)
            | (Transcript.audio_path == real) | (Transcript.video_path == real)
        ).all():
            if t.user_id == current_user.id:
                owner_match = t
            else:
                foreign_match = True
        if foreign_match and not owner_match:
            skipped.append({"path": raw_path, "reason": "forbidden"})
            continue
        try:
            size = os.path.getsize(real)  # captured before removal — gone from disk afterward
            os.remove(real)
        except OSError:
            skipped.append({"path": raw_path, "reason": "remove_failed"})
            continue
        if owner_match:
            if owner_match.audio_path in (raw_path, real):
                owner_match.audio_path = None
            if owner_match.video_path in (raw_path, real):
                owner_match.video_path = None
            db.commit()
        deleted.append(raw_path)
        freed_bytes += size
    return {"deleted": deleted, "skipped": skipped, "freed_bytes": freed_bytes}
```

(`freed_bytes = 0` initialized alongside `deleted, skipped = [], []` at the top of the function — size must be captured before `os.remove`, not after, since the file is gone from disk afterward and `os.path.getsize` would raise.)

Add `TranscriptionJob` to the existing `database` import line in `app.py` if not already imported (check first — `LlmJob` and others are already imported near the top; add `TranscriptionJob` alongside if missing).

In `delete_transcript` (`app.py:668-677`), add file cleanup before the DB delete:

```python
@app.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    for path in (t.audio_path, t.video_path):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    db.delete(t)
    db.commit()
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_inventory.py tests/test_transcript_video.py tests/test_speaker_naming.py -v`
Expected: all pass (last two files re-run to confirm the `delete_transcript` change doesn't break existing delete tests, if any check `os.path.exists(...)` differently).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_file_inventory.py
git commit -m "feat: file inventory, manual cleanup, and delete_transcript media cleanup"
```

---

### Task 8: Frontend — Files page

**Files:**
- Modify: `static/rack.js`

**Interfaces:**
- Consumes: `GET /api/files`, `POST /api/files/delete` (Task 7).
- Produces: a new page (e.g. `#page-files`) reachable from the nav, listing Linked and Orphaned files with per-row checkboxes, select-all, delete-selected, and byte totals.

- [ ] **Step 1: Add the page and nav entry**

Follow this codebase's existing page-registration pattern (find how `#page-voices` is wired — nav button, `S.page` routing, a `renderFilesPage()` function analogous to `renderVoicesPage`) rather than inventing a new pattern. Read the Voice roster page's registration end-to-end first (nav entry, route dispatch, render function, data-load call) and mirror its structure exactly for consistency.

- [ ] **Step 2: Implement `renderFilesPage()`**

```js
async function renderFilesPage() {
  let data;
  try {
    data = await api('/api/files');
  } catch (e) { toast(e.message, 'error'); return; }

  const fmtBytes = (n) => n > 1e9 ? (n / 1e9).toFixed(2) + ' GB' : (n / 1e6).toFixed(1) + ' MB';
  const row = (f, group) => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--seg-edge)">
      <input type="checkbox" data-file-select="${group}" data-path="${escapeHtml(f.path)}">
      <div style="flex:1;min-width:0;font-family:var(--f-mono);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${f.transcript_title ? escapeHtml(f.transcript_title) + ' (' + f.field + ')' : escapeHtml(f.path)}
      </div>
      <div style="font-family:var(--f-mono);font-size:11px;color:var(--label-dim)">${fmtBytes(f.size_bytes)}</div>
    </div>`;

  $('page-files').innerHTML = `
    <h3>Linked (${fmtBytes(data.total_linked_bytes)})</h3>
    <button data-files-select-all="linked">Select all</button>
    <button data-files-delete="linked">Delete selected</button>
    ${data.linked.map(f => row(f, 'linked')).join('')}
    <h3>Orphaned (${fmtBytes(data.total_orphaned_bytes)})</h3>
    <button data-files-select-all="orphaned">Select all</button>
    <button data-files-delete="orphaned">Delete selected</button>
    ${data.orphaned.map(f => row(f, 'orphaned')).join('')}
  `;
}

async function deleteSelectedFiles(group) {
  const paths = [...document.querySelectorAll(`[data-file-select="${group}"]:checked`)].map(el => el.dataset.path);
  if (!paths.length) return;
  if (!confirm(`Delete ${paths.length} file(s)? This cannot be undone.`)) return;
  try {
    const r = await api('/api/files/delete', { method: 'POST', body: JSON.stringify({ paths }) });
    toast(`Deleted ${r.deleted.length}, skipped ${r.skipped.length}`, 'info');
    renderFilesPage();
  } catch (e) { toast(e.message, 'error'); }
}
```

Match the exact markup style (buttons, headers, retro rack aesthetic — LEDs, `var(--f-cond)`, etc.) to the rest of `rack.js` rather than the plain HTML sketched above; this is a content/structure sketch, not final styling. Wire `data-files-select-all`/`data-files-delete` click handlers into whatever central click-delegation function this page uses (check whether `detailBodyClick`-style delegation is global or per-page before deciding where to add the handler).

- [ ] **Step 3: Manual verification**

1. Upload a video, let it complete, note its transcript title.
2. Manually drop an extra untracked file into the upload directory (or delete a transcript first via Task 7 — its own media is now cleaned up automatically, so instead: upload a file, cancel processing, or directly copy a stray file into the uploads folder for the test) to create a genuine orphan.
3. Open the Files page — confirm the video shows under Linked with its transcript title, the stray file shows under Orphaned.
4. Select and delete the orphan — confirm it disappears from disk and from the list; confirm the Linked file is untouched.
5. Select and delete the Linked file — confirm the transcript still opens and shows its text, but `has_audio`/`has_video` are now false and playback controls are gone.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: Files page for viewing and cleaning up stored media"
```

---

## Deferred / explicitly not in this plan

- Any automatic storage-retention policy (age/size-based auto-expiry) — cleanup via the Files page (Tasks 7-8) is manual/user-initiated only.
- Voice-clip file cleanup — already handled by the existing `remove_clip`/clip-delete route; not part of this plan's inventory scope.
