# Audio Chunking, Upload Queue & Rate-Limit Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Long recordings that exceed a provider's upload size limit get split into silence-aware chunks, queued through a durable in-process job worker that respects the provider's real rate limits, and reassembled into one transcript — with the upload API and frontend switching from a single blocking call to async job tracking.

**Architecture:** A new `TranscriptionJob` table holds one row per chunk. `POST /api/transcribe` either runs the existing synchronous single-shot path (small files, unchanged) or — over a configurable size threshold — transcodes, silence-aware-splits the audio, creates the chunk job rows, and returns immediately. A background `asyncio` loop (started via FastAPI's `lifespan`) ticks every few seconds, computing each user+provider's real trailing-hour/day audio-second usage from `Transcript`/`TranscriptionJob` history, and dispatches pending chunk jobs up to a concurrency cap without exceeding that budget. Completed chunks store their raw segment results on the job row; once every job for a transcript reaches a terminal state, a merge step stitches segments together (absolute time offsets + overlap dedup) into the parent `Transcript`, runs diarization if requested, and marks the transcript `completed` or `partial`. The frontend polls `GET /api/transcripts/{id}` instead of awaiting one blocking upload call.

**Tech Stack:** FastAPI (`lifespan` context manager for the background task), SQLAlchemy/SQLite (new table + additive columns via a generic `ensure_columns` migration helper — no rename dance needed since none of these are constraint changes), `ffmpeg`/`ffprobe` (silence detection + `-c copy` chunk splitting, no re-encoding), plain `asyncio` (no Celery/Redis/external cron per the spec).

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-01-audio-chunking-and-queue-design.md`. Decisions there (silence-aware boundaries, plain forced mono — no stereo passthrough, in-process queue, usage computed from real history not a separate counter table, async upload UX) are not up for re-litigation during implementation.
- No test suite exists in this repo. Every task's verification is a manual PowerShell/curl sequence against a running `app.py`, matching this codebase's established pattern. Do not introduce a pytest suite.
- Do not implement channel-based diarization, the LLM hotword-correction pass, the local pre-pass, or provider-fallback routing — all explicitly out of scope per the spec.
- Every step that touches `app.py`, `services/transcription.py`, or `services/voice_id.py` must preserve the existing per-request `db: Session = Depends(get_db)` and `current_user: User = Depends(get_current_user)` patterns already in place.
- Groq free-tier numbers to hardcode as the default budget (confirmed live against Groq's docs 2026-07-01): 20 requests/min, 2,000 requests/day, 7,200 audio-seconds/hour, 28,800 audio-seconds/day. File size cap 25MB free / 100MB dev tier.
- `chunk_audio()` and any ffmpeg split must use `-c copy` (stream copy, no re-encode) since the source is already transcoded to the target format — re-encoding a second time would be wasteful and lossy.

---

## File Structure

- **Modify `database/__init__.py`**: add `TranscriptionJob` model; add a generic `ensure_columns(engine, table_name, columns)` migration helper; add `users.settings` (JSON), `transcripts.audio_path` (TEXT), `transcripts.diarize_requested` (Boolean) as additive columns; wire the new helper into `init_db()`.
- **Create `services/settings.py`**: per-user settings defaults + get/update helpers (bitrate, chunk threshold, concurrency).
- **Modify `services/audio_prep.py`**: add a `bitrate_kbps` parameter to `transcode_for_upload`; add `get_audio_duration()`, `detect_silence_midpoints()`, `chunk_audio()`.
- **Create `services/queue.py`**: rate-limit budget calculation, chunk job creation, the background worker tick loop, and transcript reassembly/finalization (including diarization trigger).
- **Modify `services/transcription.py`**: expose a `create_transcript_stub()` helper so the chunked path can create the `Transcript` row without immediately transcribing.
- **Modify `app.py`**: `lifespan` startup for the worker loop; `GET`/`PUT /api/settings`; `transcribe_audio` route branches sync vs. chunked; new `POST /api/transcripts/{id}/retry-failed-chunks`; `_serialize_transcript` gains job-progress fields.
- **Modify `static/index.html`**: `startTx()` becomes fire-and-poll; progress screen shows real chunk-completion counts; transcript detail page gets a "Retry failed sections" action; Settings page gets the four new fields.

---

### Task 1: Data model — `TranscriptionJob` table and additive columns

**Files:**
- Modify: `database/__init__.py` (full file shown below)
- Modify: `app.py:56-58` (the `engine, SessionLocal, migrated_tables = init_db(...)` call site — no signature change, but a new call needs adding right after)

**Interfaces:**
- Produces: `TranscriptionJob` model (`id`, `transcript_id`, `chunk_index`, `start_time`, `end_time`, `audio_path`, `status`, `attempts`, `error`, `result_json`, `created_at`, `updated_at`), `ensure_columns(engine, table_name: str, columns: dict[str, str]) -> None`.
- Consumes: nothing new.

**Why `result_json` (not in the spec's table sketch):** each chunk's raw transcription result (segments) needs to land somewhere before the final merge step runs. Storing it directly on the `Transcript.segments` as each chunk completes would risk two concurrent chunk-completions interleaving their read-modify-write of the same JSON column. Storing each chunk's result on its own `TranscriptionJob` row instead means the merge step is a single read-many/write-once operation once all jobs are terminal — no interleaving possible. This is an implementation detail of Architecture step 3/4 in the spec, not a deviation from it.

**Why `transcripts.audio_path` / `transcripts.diarize_requested`:** the existing synchronous path runs diarization inline, using the local `save_path` variable and the request's `diarize` form field — both of which vanish once the request returns. The chunked path's diarization has to run later, from inside the worker's finalize step, which has no access to that request-scoped state. Persisting the post-transcode (pre-split) audio path and whether diarization was requested lets finalize reproduce that same step.

- [ ] **Step 1: Rewrite `database/__init__.py`**

```python
"""SQLAlchemy models for WhisperDeck."""
import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey,
    JSON, Boolean, UniqueConstraint, create_engine, inspect, text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    password_salt = Column(String(64), nullable=False)
    settings = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    duration_seconds = Column(Float, default=0)
    provider = Column(String(64), default="groq")
    model = Column(String(64), default="whisper-large-v3-turbo")
    language = Column(String(10), default="auto")
    status = Column(String(32), default="pending")  # pending, processing, completed, failed, partial
    full_text = Column(Text, default="")
    segments = Column(JSON, default=list)  # [{start, end, speaker, text}]
    speaker_count = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    audio_path = Column(String(512), nullable=True)  # post-transcode, pre-chunk-split file; used by chunked-path diarization
    diarize_requested = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    summary = relationship("Summary", back_populates="transcript", uselist=False, cascade="all, delete-orphan")
    jobs = relationship("TranscriptionJob", back_populates="transcript", cascade="all, delete-orphan")


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"

    id = Column(Integer, primary_key=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    start_time = Column(Float, nullable=False)  # offset into the full transcript, seconds
    end_time = Column(Float, nullable=False)
    audio_path = Column(String(512), nullable=False)
    status = Column(String(32), default="pending")  # pending, running, completed, failed
    attempts = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    result_json = Column(JSON, nullable=True)  # raw {segments, full_text, language, model} once completed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    transcript = relationship("Transcript", back_populates="jobs")


class Summary(Base):
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=False)
    short_summary = Column(Text, default="")
    key_points = Column(JSON, default=list)
    action_items = Column(JSON, default=list)
    decisions = Column(JSON, default=list)
    model = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    transcript = relationship("Transcript", back_populates="summary")


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_voice_profile_user_name"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String(128), nullable=False)
    embedding = Column(JSON, nullable=True)  # stored as list of floats
    embedding_model = Column(String(64), default="speechbrain/spkrec-ecapa-voxceleb")
    sample_count = Column(Integer, default=0)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_provider_config_user_name"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String(64), nullable=False)  # groq, openai, replicate, local
    display_name = Column(String(128), default="")
    api_key = Column(String(512), default="")
    api_url = Column(String(512), default="")
    default_model = Column(String(64), default="")
    is_active = Column(Boolean, default=True)
    config = Column(JSON, default=dict)


def migrate_schema(engine) -> list[str]:
    """Rename any pre-existing tables that predate per-user scoping, so
    create_all() can recreate them with the new (user_id-aware) schema.

    Returns the list of table names that were migrated — empty on a fresh
    database or one that's already current. Callers use this list to know
    whether backfill_user_id() needs to run.
    """
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    targets = ["provider_configs", "transcripts", "voice_profiles"]
    migrated = []
    for table_name in targets:
        if table_name not in existing_tables:
            continue
        columns = [c["name"] for c in inspector.get_columns(table_name)]
        if "user_id" in columns:
            continue
        migrated.append(table_name)

    if not migrated:
        return []

    with engine.begin() as conn:
        for table_name in migrated:
            conn.execute(text(f"ALTER TABLE {table_name} RENAME TO {table_name}_old"))

    return migrated


def backfill_user_id(engine, migrated_tables: list[str], user_id: int) -> None:
    """Copy rows from the *_old tables (renamed by migrate_schema) into the
    freshly created tables, assigning user_id to every row, then drop the
    old tables. Must run after Base.metadata.create_all() has recreated
    the target tables.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name in migrated_tables:
            old_table = f"{table_name}_old"
            old_columns = [c["name"] for c in inspector.get_columns(old_table)]
            cols = ", ".join(old_columns)
            conn.execute(
                text(f"INSERT INTO {table_name} ({cols}, user_id) SELECT {cols}, :uid FROM {old_table}"),
                {"uid": user_id},
            )
            conn.execute(text(f"DROP TABLE {old_table}"))


def ensure_columns(engine, table_name: str, columns: dict[str, str]) -> None:
    """Add any missing columns to an existing table via plain ALTER TABLE.

    Only safe for additive, unconstrained columns (nullable, no UNIQUE/FK
    changes) — SQLite supports this without a table rebuild. Do NOT use
    this for constraint changes; that needs the rename-and-recreate
    approach in migrate_schema()/backfill_user_id() instead.

    columns maps column name -> SQL type clause, e.g. {"settings": "JSON"}.
    """
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return  # table doesn't exist yet — create_all() will create it with all columns already present
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    with engine.begin() as conn:
        for col_name, sql_type in columns.items():
            if col_name in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {sql_type}"))


def init_db(db_path: str = "data/whisperdesk.db") -> tuple:
    """Initialize the database. Returns (engine, SessionLocal, migrated_tables).

    SessionLocal is a sessionmaker, not a live session — callers create one
    session per request (see app.py's get_db dependency) rather than
    sharing a single session across all concurrent requests.

    migrated_tables is the list from migrate_schema() — non-empty only on
    the first startup against a pre-existing pre-auth database. Callers
    use it to trigger the one-time fallback-user backfill.
    """
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    migrated_tables = migrate_schema(engine)
    Base.metadata.create_all(engine)
    ensure_columns(engine, "users", {"settings": "JSON"})
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN"})
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal, migrated_tables


__all__ = [
    "Base", "User", "Transcript", "Summary", "VoiceProfile", "ProviderConfig", "TranscriptionJob",
    "init_db", "migrate_schema", "backfill_user_id", "ensure_columns",
]
```

- [ ] **Step 2: Verify against a throwaway copy of the real database**

```powershell
cd C:\Claude\whisperdesk
Copy-Item data\whisperdesk.db data\_test_chunking_migrate.db
.venv\Scripts\python.exe -c "
from database import init_db, TranscriptionJob, User, Transcript
engine, SessionLocal, migrated = init_db('data/_test_chunking_migrate.db')
print('migrated tables (expect empty, already-migrated db):', migrated)
db = SessionLocal()
u = db.query(User).first()
print('user settings column readable:', u.settings)
t = db.query(Transcript).first()
print('transcript audio_path column readable:', t.audio_path if t else 'no rows')
print('transcript diarize_requested column readable:', t.diarize_requested if t else 'no rows')
job = TranscriptionJob(transcript_id=t.id if t else 1, chunk_index=0, start_time=0, end_time=10, audio_path='x.mp3')
db.add(job)
db.commit()
print('created job id:', job.id)
"
Remove-Item data\_test_chunking_migrate.db
```
Expected: `migrated tables (expect empty, already-migrated db): []` (this DB already went through the auth migration earlier — no rename needed here since these are additive columns), `user settings column readable: None`, `transcript audio_path column readable: None`, `transcript diarize_requested column readable: None` or `False`, `created job id: 1`.

- [ ] **Step 3: Confirm the app still imports and boots**

```powershell
.venv\Scripts\python.exe -c "import app; print('OK')"
```
Expected: `OK`.

- [ ] **Step 4: Commit**

```powershell
git add database/__init__.py
git commit -m "Add TranscriptionJob table and additive settings/audio_path columns"
```

---

### Task 2: Per-user settings service and API

**Files:**
- Create: `services/settings.py`
- Modify: `app.py` (new `GET`/`PUT /api/settings` routes)

**Interfaces:**
- Produces: `DEFAULT_SETTINGS: dict`, `get_user_settings(db, user_id: int) -> dict`, `update_user_settings(db, user_id: int, updates: dict) -> dict`.
- Consumes: `database.User` (Task 1's `settings` column).

- [ ] **Step 1: Create `services/settings.py`**

```python
"""Per-user tunables for audio prep and the chunking queue.

Stored as a single JSON blob on User.settings (see database/__init__.py) —
no separate table, since this is a small fixed set of scalar values, not
a growing per-item collection like provider configs.
"""
from database import User

DEFAULT_SETTINGS = {
    "bitrate_kbps": 128,
    "chunk_threshold_mb": 20,
    "max_concurrent_chunks": 4,
}


def get_user_settings(db, user_id: int) -> dict:
    """Return this user's settings merged over the defaults — any key the
    user hasn't set yet falls back to DEFAULT_SETTINGS rather than being
    absent, so callers never need their own fallback logic."""
    user = db.query(User).filter(User.id == user_id).first()
    stored = (user.settings or {}) if user else {}
    return {**DEFAULT_SETTINGS, **stored}


def update_user_settings(db, user_id: int, updates: dict) -> dict:
    """Merge updates into the user's stored settings and return the full
    merged-with-defaults settings dict. Unknown keys in updates are
    ignored so a stray frontend field can't pollute the stored JSON."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    current = user.settings or {}
    for key, value in updates.items():
        if key in DEFAULT_SETTINGS:
            current[key] = value
    user.settings = current
    db.commit()
    return get_user_settings(db, user_id)
```

- [ ] **Step 2: Verify the merge/update logic against a throwaway database**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe -c "
from database import init_db
from services.auth import create_user
from services.settings import get_user_settings, update_user_settings, DEFAULT_SETTINGS

engine, SessionLocal, _ = init_db('data/_test_settings.db')
db = SessionLocal()
u = create_user(db, 'settingstest', 'pw')

s = get_user_settings(db, u.id)
assert s == DEFAULT_SETTINGS, s
print('defaults returned when unset: OK')

s2 = update_user_settings(db, u.id, {'bitrate_kbps': 192, 'unknown_key': 'ignored'})
assert s2['bitrate_kbps'] == 192
assert 'unknown_key' not in s2
print('update applied, unknown key ignored: OK')

s3 = get_user_settings(db, u.id)
assert s3['bitrate_kbps'] == 192
assert s3['chunk_threshold_mb'] == DEFAULT_SETTINGS['chunk_threshold_mb']
print('partial update preserves other defaults: OK')
"
Remove-Item data\_test_settings.db
```
Expected: three `OK` lines, no assertion errors.

- [ ] **Step 3: Add `GET`/`PUT /api/settings` routes**

In `app.py`, add near the top imports:
```python
from services.settings import get_user_settings, update_user_settings
```

Add a new section after the `# ── Auth ──` block (before `# ── API Routes ──`):
```python
# ── Settings ──────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_user_settings(db, current_user.id)


@app.put("/api/settings")
async def put_settings(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return update_user_settings(db, current_user.id, data)
```

- [ ] **Step 4: Verify the routes live**

```powershell
cd C:\Claude\whisperdesk
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" -NoNewWindow -RedirectStandardOutput "run_out.log" -RedirectStandardError "run_err.log" -PassThru
Start-Sleep -Seconds 3

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"settingstest2","password":"pw"}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null
Write-Host "defaults:"
(Invoke-WebRequest -Uri http://localhost:9781/api/settings -WebSession $session -UseBasicParsing).Content
Write-Host "after update:"
Invoke-WebRequest -Uri http://localhost:9781/api/settings -Method Put -Body '{"bitrate_kbps":192}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Select-Object -ExpandProperty Content

Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item run_out.log,run_err.log -ErrorAction SilentlyContinue
python -c "
import sqlite3
c = sqlite3.connect('data/whisperdesk.db')
c.execute(\"delete from users where username='settingstest2'\")
c.commit()
"
```
Expected: defaults response is `{"bitrate_kbps":128,"chunk_threshold_mb":20,"max_concurrent_chunks":4}`; after-update response shows `"bitrate_kbps":192` with the other two fields unchanged.

- [ ] **Step 5: Commit**

```powershell
git add services/settings.py app.py
git commit -m "Add per-user settings service and API routes"
```

---

### Task 3: Silence-aware chunking in `services/audio_prep.py`

**Files:**
- Modify: `services/audio_prep.py` (full file shown below)

**Interfaces:**
- Produces: `transcode_for_upload(input_path, output_dir, bitrate_kbps: int = 128) -> str` (bitrate now a parameter, was hardcoded), `get_audio_duration(audio_path: str) -> float`, `detect_silence_midpoints(audio_path: str) -> list[float]`, `chunk_audio(audio_path: str, output_dir: str, target_chunk_bytes: int, overlap_seconds: float = 5.0) -> list[dict]` — each dict is `{"index": int, "path": str, "start_time": float, "end_time": float}`.
- Consumes: nothing new.

**Chunking algorithm:** estimate the file's average bytes/second from its size and duration (accurate since it's already a constant-bitrate MP3), derive a target chunk duration from `target_chunk_bytes`, then walk the silence midpoints found by `detect_silence_midpoints()` picking the one closest to (but not exceeding by more than 20%) each target boundary. If no silence midpoint falls within that window (continuous speech), fall back to a fixed cut at the target duration — the overlap on both sides is what keeps that fallback from losing a word. Every chunk except the first starts `overlap_seconds` before its boundary, and every chunk except the last ends `overlap_seconds` after its boundary; the merge step in Task 5 dedupes the overlapping text.

- [ ] **Step 1: Rewrite `services/audio_prep.py`**

```python
"""Audio preprocessing — normalize uploads before sending to cloud providers.

Video files (mp4, mov, ...) and long recordings can exceed a provider's
upload size limit. Whisper-family models run at 16kHz mono internally
regardless of input format, so transcoding down to that loses nothing the
model would have used anyway, while shrinking the upload substantially and
stripping any video track.

For recordings that are still too large after transcoding, chunk_audio()
splits them at silence boundaries (found via ffmpeg's silencedetect filter)
so each piece fits under the provider's size cap.
"""
import asyncio
import os
import re
import shutil
import subprocess


class AudioPrepError(Exception):
    """Raised when ffmpeg is unavailable or the transcode fails."""
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def transcode_for_upload(input_path: str, output_dir: str, bitrate_kbps: int = 128) -> str:
    """Transcode to 16kHz mono MP3 and return the new file path.

    Streams through ffmpeg rather than decoding into memory, so multi-hour
    recordings don't spike RAM. Does not attempt to handle files that still
    exceed the provider's limit after transcoding — chunk_audio() handles
    that as a separate step.

    bitrate_kbps defaults to 128 (was 64) — sample rate (16kHz) is the real
    ceiling on what Whisper-family models use, since they resample to that
    internally regardless of input; bitrate only governs compression
    artifacts within that 16kHz signal, which matters more for noisy/
    accented audio than the file-size savings of a lower bitrate did once
    chunking removed size pressure as a reason to keep it low.
    """
    if not ffmpeg_available():
        raise AudioPrepError(
            "ffmpeg is not installed or not on PATH. It's required to prepare "
            "audio/video uploads for cloud transcription providers. "
            "See INSTALL.md."
        )

    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base}_16k.mp3")

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "libmp3lame",
        "-b:a", f"{bitrate_kbps}k",
        output_path,
    ]

    def _run():
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AudioPrepError(f"ffmpeg transcode failed: {result.stderr[-2000:]}")
        return output_path

    return await asyncio.to_thread(_run)


def get_audio_duration(audio_path: str) -> float:
    """Return the audio file's duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise AudioPrepError(f"ffprobe failed to read duration: {result.stderr[-500:]}")
    return float(result.stdout.strip())


_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def detect_silence_midpoints(audio_path: str, noise_db: str = "-30dB", min_duration: float = 0.5) -> list[float]:
    """Return timestamps (seconds) at the midpoint of each detected silence
    gap, in order. Cutting a chunk boundary at one of these midpoints keeps
    the cut roughly equidistant from speech on either side.

    A single-pass ffmpeg filter — no decode-to-file, no ML model, adds low
    single-digit seconds even on a multi-hour recording.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-i", audio_path,
            "-af", f"silencedetect=noise={noise_db}:d={min_duration}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    midpoints = []
    for match in _SILENCE_END_RE.finditer(result.stderr):
        silence_end = float(match.group(1))
        silence_duration = float(match.group(2))
        silence_start = silence_end - silence_duration
        midpoints.append((silence_start + silence_end) / 2)
    return midpoints


async def chunk_audio(
    audio_path: str,
    output_dir: str,
    target_chunk_bytes: int,
    overlap_seconds: float = 5.0,
) -> list[dict]:
    """Split audio_path into chunks near target_chunk_bytes each, cutting at
    silence where possible. Returns a list of
    {"index", "path", "start_time", "end_time"} dicts in order.

    Uses -c copy (stream copy) for the split itself since audio_path is
    already transcoded to the target codec/bitrate — re-encoding a second
    time would be wasted work and additional quality loss.
    """
    if not ffmpeg_available():
        raise AudioPrepError("ffmpeg is not installed or not on PATH. See INSTALL.md.")

    total_duration = get_audio_duration(audio_path)
    total_bytes = os.path.getsize(audio_path)
    bytes_per_second = total_bytes / total_duration if total_duration else 0
    if bytes_per_second <= 0:
        raise AudioPrepError("Could not determine audio bitrate for chunking")

    target_duration = target_chunk_bytes / bytes_per_second
    silence_midpoints = detect_silence_midpoints(audio_path)

    # Build cut points: walk forward in target_duration steps, snapping each
    # to the nearest silence midpoint within 20% of the target if one exists.
    cut_points = []
    cursor = target_duration
    tolerance = target_duration * 0.2
    while cursor < total_duration:
        candidates = [m for m in silence_midpoints if abs(m - cursor) <= tolerance]
        cut = min(candidates, key=lambda m: abs(m - cursor)) if candidates else cursor
        cut_points.append(cut)
        cursor = cut + target_duration
    boundaries = [0.0] + cut_points + [total_duration]

    base = os.path.splitext(os.path.basename(audio_path))[0]
    chunks = []

    def _cut_one(index: int, seg_start: float, seg_end: float) -> dict:
        cut_start = max(0.0, seg_start - (overlap_seconds if index > 0 else 0))
        cut_end = min(total_duration, seg_end + (overlap_seconds if seg_end < total_duration else 0))
        chunk_path = os.path.join(output_dir, f"{base}_chunk{index}.mp3")
        cmd = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-ss", str(cut_start),
            "-to", str(cut_end),
            "-c", "copy",
            chunk_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AudioPrepError(f"ffmpeg chunk split failed: {result.stderr[-2000:]}")
        return {"index": index, "path": chunk_path, "start_time": seg_start, "end_time": seg_end}

    def _run_all():
        result = []
        for i in range(len(boundaries) - 1):
            result.append(_cut_one(i, boundaries[i], boundaries[i + 1]))
        return result

    chunks = await asyncio.to_thread(_run_all)
    return chunks
```

- [ ] **Step 2: Verify chunking against a generated long test tone**

```powershell
cd C:\Claude\whisperdesk
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=90" -af "volume=0.02" -c:a libmp3lame -b:a 128k data\uploads\_test_chunk_source.mp3 2>&1 | Out-Null
.venv\Scripts\python.exe -c "
import asyncio
from services.audio_prep import chunk_audio, get_audio_duration, detect_silence_midpoints

async def main():
    dur = get_audio_duration('data/uploads/_test_chunk_source.mp3')
    print('source duration:', round(dur, 1))
    midpoints = detect_silence_midpoints('data/uploads/_test_chunk_source.mp3')
    print('silence midpoints found:', len(midpoints))
    chunks = await chunk_audio('data/uploads/_test_chunk_source.mp3', 'data/uploads', target_chunk_bytes=200_000, overlap_seconds=3.0)
    print('chunk count:', len(chunks))
    for c in chunks:
        print(c['index'], round(c['start_time'],1), round(c['end_time'],1))
    total_span = chunks[-1]['end_time'] - chunks[0]['start_time']
    assert abs(total_span - dur) < 1.0, f'chunk span {total_span} does not cover source duration {dur}'
    print('chunks cover full source duration: OK')

asyncio.run(main())
"
Remove-Item data\uploads\_test_chunk_source.mp3, data\uploads\_test_chunk_source_chunk*.mp3
```
Expected: `source duration: 90.0`, `chunk count:` greater than 1 (a 90s low-volume tone at 128kbps produces roughly 1.4MB, well over the 200KB test threshold so it must split), each chunk's start/end printed in order, and `chunks cover full source duration: OK`.

- [ ] **Step 3: Confirm the app still imports and boots**

```powershell
.venv\Scripts\python.exe -c "import app; print('OK')"
```
Expected: `OK`.

- [ ] **Step 4: Commit**

```powershell
git add services/audio_prep.py
git commit -m "Add silence-aware chunking and configurable bitrate to audio_prep"
```

---

### Task 4: `create_transcript_stub` in `services/transcription.py`

**Files:**
- Modify: `services/transcription.py`

**Interfaces:**
- Produces: `create_transcript_stub(db, user_id: int, filename: str, provider_name: str, model: str, language: str, audio_path: str, diarize_requested: bool, title: str = None) -> Transcript` — creates and commits a `Transcript` row with `status="processing"` without calling any provider.
- Consumes: `database.Transcript` (already imported in this file).

**Why this is its own function, not inlined in `app.py`:** the existing `transcribe()` method creates a `Transcript` row internally as step one, then immediately calls the provider. The chunked path in Task 7 needs that same row-creation logic (same fields, same defaults) but must return before any provider call happens — pulling it into a shared helper keeps the two paths' `Transcript` rows consistent instead of two separate ad-hoc constructions drifting apart over time.

- [ ] **Step 1: Add `create_transcript_stub` to `TranscriptionService`**

In `services/transcription.py`, add this method to the class, right before `async def transcribe(`:

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
    ) -> Transcript:
        """Create a Transcript row in 'processing' status without calling a
        provider — used by the chunked upload path, which enqueues chunk
        jobs instead of transcribing inline. See services/queue.py for how
        those jobs eventually populate full_text/segments/status."""
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
        )
        db.add(transcript)
        db.commit()
        return transcript
```

- [ ] **Step 2: Verify against a throwaway database**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe -c "
from database import init_db
from services.auth import create_user
from services.transcription import TranscriptionService

engine, SessionLocal, _ = init_db('data/_test_stub.db')
db = SessionLocal()
u = create_user(db, 'stubtest', 'pw')

svc = TranscriptionService('data/_test_stub_uploads')
t = svc.create_transcript_stub(
    db, u.id, filename='meeting.mp3', provider_name='groq', model='whisper-large-v3',
    language='en', audio_path='data/_test_stub_uploads/meeting_16k.mp3', diarize_requested=True,
)
print('id:', t.id, 'status:', t.status, 'audio_path:', t.audio_path, 'diarize_requested:', t.diarize_requested)
"
Remove-Item data\_test_stub.db
Remove-Item data\_test_stub_uploads -Recurse -ErrorAction SilentlyContinue
```
Expected: `id: 1 status: processing audio_path: data/_test_stub_uploads/meeting_16k.mp3 diarize_requested: True`.

- [ ] **Step 3: Commit**

```powershell
git add services/transcription.py
git commit -m "Add create_transcript_stub for the chunked upload path"
```

---

### Task 5: Rate-limit budget and chunk reassembly helpers (`services/queue.py`, part 1)

**Files:**
- Create: `services/queue.py` (this task adds the budget/reassembly half; Task 6 adds the worker loop to the same file)

**Interfaces:**
- Produces: `PROVIDER_LIMITS: dict` (per-provider RPM/RPD/ASH/ASD), `compute_audio_seconds_used(db, user_id: int, provider: str, window_seconds: int) -> float`, `has_budget(db, user_id: int, provider: str, additional_seconds: float) -> bool`, `merge_chunk_results(jobs: list[TranscriptionJob]) -> tuple[list[dict], str]` (returns merged segments list and rebuilt full_text).
- Consumes: `database.Transcript`, `database.TranscriptionJob`.

**Overlap dedup approach:** each chunk's segments already carry the chunk's own local timestamps: `merge_chunk_results` first offsets every segment's `start`/`end` by that chunk's `start_time` (from the `TranscriptionJob` row) so all segments share one absolute timeline. Then, for each pair of adjacent chunks, it looks at segments falling inside the known overlap window (the previous chunk's tail, the next chunk's head) and drops duplicates by comparing normalized text — if the next chunk's first segment's text is a near-duplicate (case/whitespace-insensitive substring match) of the previous chunk's last segment's text, the next chunk's copy is dropped, since the previous chunk's version had more surrounding context and is kept.

- [ ] **Step 1: Create `services/queue.py` with budget and reassembly functions**

```python
"""Chunk-upload job queue: rate-limit budget tracking and result reassembly.

The dispatch worker loop lives in this same module — see the bottom half
of this file (queue_worker_tick, queue_worker_loop), added alongside the
functions below.
"""
import datetime
from typing import Optional

from database import Transcript, TranscriptionJob

# Free-tier numbers confirmed live against https://console.groq.com/docs/rate-limits
# on 2026-07-01. Paid/dev tiers raise these — kept here as a dict (not a
# per-user setting) since it's provider capability, not user preference,
# but easy to adjust in code as tiers change.
PROVIDER_LIMITS = {
    "groq": {"rpm": 20, "rpd": 2000, "ash": 7200, "asd": 28800},
}
DEFAULT_LIMITS = {"rpm": 20, "rpd": 2000, "ash": 7200, "asd": 28800}


def compute_audio_seconds_used(db, user_id: int, provider: str, window_seconds: int) -> float:
    """Sum audio-seconds this user has sent to `provider` within the
    trailing `window_seconds`, combining two sources:
      - completed/partial Transcripts (duration_seconds, updated_at) —
        covers the single-shot path and fully-finished chunked transcripts.
      - in-flight TranscriptionJobs (end_time - start_time, updated_at) for
        jobs already dispatched (running or completed) — covers chunked
        transcripts that haven't finished merging yet, which don't have
        Transcript.duration_seconds set until the end.
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=window_seconds)

    transcript_total = (
        db.query(Transcript)
        .filter(
            Transcript.user_id == user_id,
            Transcript.provider == provider,
            Transcript.status.in_(["completed", "partial"]),
            Transcript.updated_at >= cutoff,
        )
        .all()
    )
    transcript_seconds = sum(t.duration_seconds or 0 for t in transcript_total)

    job_rows = (
        db.query(TranscriptionJob)
        .join(Transcript, TranscriptionJob.transcript_id == Transcript.id)
        .filter(
            Transcript.user_id == user_id,
            Transcript.provider == provider,
            TranscriptionJob.status.in_(["running", "completed"]),
            TranscriptionJob.updated_at >= cutoff,
        )
        .all()
    )
    job_seconds = sum((j.end_time - j.start_time) for j in job_rows)

    return transcript_seconds + job_seconds


def has_budget(db, user_id: int, provider: str, additional_seconds: float) -> bool:
    """True if submitting a job of additional_seconds would keep this user
    under both the hourly and daily audio-second budget for provider."""
    limits = PROVIDER_LIMITS.get(provider, DEFAULT_LIMITS)
    used_hour = compute_audio_seconds_used(db, user_id, provider, 3600)
    used_day = compute_audio_seconds_used(db, user_id, provider, 86400)
    return (used_hour + additional_seconds) <= limits["ash"] and (used_day + additional_seconds) <= limits["asd"]


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def merge_chunk_results(jobs: list) -> tuple:
    """Merge completed TranscriptionJob rows (already sorted or not) into
    one absolute-timeline segment list plus rebuilt full_text. Jobs without
    a result_json (failed chunks) are skipped — callers decide separately
    whether that makes the transcript 'completed' or 'partial'.
    """
    ordered = sorted([j for j in jobs if j.result_json], key=lambda j: j.chunk_index)
    merged_segments = []

    for job in ordered:
        raw_segments = job.result_json.get("segments", [])
        offset_segments = [
            {
                "start": s.get("start", 0) + job.start_time,
                "end": s.get("end", 0) + job.start_time,
                "text": s.get("text", ""),
                "speaker": s.get("speaker"),
                "confidence": s.get("confidence"),
            }
            for s in raw_segments
        ]

        if merged_segments and offset_segments:
            prev_tail = _normalize(merged_segments[-1]["text"])
            next_head = _normalize(offset_segments[0]["text"])
            if next_head and (next_head in prev_tail or prev_tail in next_head):
                offset_segments = offset_segments[1:]

        merged_segments.extend(offset_segments)

    full_text = " ".join(s["text"].strip() for s in merged_segments if s["text"].strip())
    return merged_segments, full_text
```

- [ ] **Step 2: Verify budget calculation against a throwaway database**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe -c "
import datetime
from database import init_db, Transcript, TranscriptionJob
from services.auth import create_user
from services.queue import compute_audio_seconds_used, has_budget

engine, SessionLocal, _ = init_db('data/_test_queue_budget.db')
db = SessionLocal()
u = create_user(db, 'budgettest', 'pw')

t = Transcript(user_id=u.id, title='x', filename='x.mp3', provider='groq', status='completed', duration_seconds=6000.0)
db.add(t)
db.commit()

used = compute_audio_seconds_used(db, u.id, 'groq', 3600)
print('used (should be 6000):', used)
print('has_budget for 2000 more (6000+2000=8000 > 7200 ash cap, expect False):', has_budget(db, u.id, 'groq', 2000))
print('has_budget for 500 more (6000+500=6500 <= 7200, expect True):', has_budget(db, u.id, 'groq', 500))
"
Remove-Item data\_test_queue_budget.db
```
Expected: `used (should be 6000): 6000.0`, `has_budget for 2000 more...: False`, `has_budget for 500 more...: True`.

- [ ] **Step 3: Verify merge/dedup logic**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe -c "
from services.queue import merge_chunk_results

class FakeJob:
    def __init__(self, chunk_index, start_time, result_json):
        self.chunk_index = chunk_index
        self.start_time = start_time
        self.result_json = result_json

j0 = FakeJob(0, 0.0, {'segments': [
    {'start': 0.0, 'end': 5.0, 'text': 'hello everyone welcome to the meeting'},
    {'start': 5.0, 'end': 9.0, 'text': 'today we will discuss the budget'},
]})
j1 = FakeJob(1, 8.0, {'segments': [
    {'start': 0.0, 'end': 1.0, 'text': 'today we will discuss the budget'},  # overlap duplicate
    {'start': 1.0, 'end': 6.0, 'text': 'and the timeline for next quarter'},
]})

segments, full_text = merge_chunk_results([j1, j0])  # deliberately out of order
print('segment count (expect 3, duplicate dropped):', len(segments))
for s in segments:
    print(round(s['start'],1), round(s['end'],1), s['text'])
print('full_text:', full_text)
"
```
Expected: `segment count (expect 3, duplicate dropped): 3`, three segments with strictly increasing absolute `start` times (0.0, 5.0, 9.0), and the duplicated "today we will discuss the budget" text appears only once in `full_text`.

- [ ] **Step 4: Commit**

```powershell
git add services/queue.py
git commit -m "Add rate-limit budget tracking and chunk-merge helpers"
```

---

### Task 6: Background worker loop (`services/queue.py`, part 2)

**Files:**
- Modify: `services/queue.py` (append the worker loop below the functions from Task 5)

**Interfaces:**
- Consumes: `has_budget`, `merge_chunk_results` (Task 5), `backends.get_provider`, `backends.base.ProviderError`, `database.ProviderConfig`, `services.diarization.DiarizationService`.
- Produces: `create_chunk_jobs(db, transcript_id: int, chunks: list[dict]) -> None`, `retry_failed_chunks(db, transcript_id: int) -> int` (returns count re-queued), `queue_worker_tick(SessionLocal, diarization_service) -> None`, `queue_worker_loop(SessionLocal, diarization_service, interval_seconds: float = 5.0) -> None` (the `async def` entrypoint `app.py`'s lifespan will run as a background task).

**Retry/backoff:** a failed job becomes eligible for redispatch once `MAX_ATTEMPTS` (3) hasn't been reached and enough time has passed since its last update — `min(60, 5 * 2**attempts)` seconds. This is computed inline from `updated_at` rather than a stored "next eligible" column, since the worker already re-evaluates every tick.

**Finalization:** once every job for a transcript is `completed` or has exhausted retries as `failed`, the tick merges results (Task 5), sets `full_text`/`segments`, sets `status` to `completed` (no failed jobs) or `partial` (at least one), runs diarization if `Transcript.diarize_requested` and segments exist (mirroring the existing inline diarization block in `app.py`'s sync path), and sets `duration_seconds` from the last chunk's `end_time`.

- [ ] **Step 1: Append job creation, retry, and the worker loop to `services/queue.py`**

```python
import asyncio

from backends import get_provider, ProviderError
from database import ProviderConfig

MAX_ATTEMPTS = 3


def create_chunk_jobs(db, transcript_id: int, chunks: list) -> None:
    """Insert one pending TranscriptionJob per chunk dict (as returned by
    services.audio_prep.chunk_audio)."""
    for chunk in chunks:
        db.add(TranscriptionJob(
            transcript_id=transcript_id,
            chunk_index=chunk["index"],
            start_time=chunk["start_time"],
            end_time=chunk["end_time"],
            audio_path=chunk["path"],
        ))
    db.commit()


def retry_failed_chunks(db, transcript_id: int) -> int:
    """Reset every permanently-failed job for this transcript back to
    pending so the worker picks it up again. Returns how many were reset."""
    failed = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "failed")
        .all()
    )
    for job in failed:
        job.status = "pending"
        job.attempts = 0
        job.error = None
    if failed:
        transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
        if transcript:
            transcript.status = "processing"
        db.commit()
    return len(failed)


def _retry_eligible(job) -> bool:
    if job.attempts >= MAX_ATTEMPTS:
        return False
    backoff = min(60, 5 * (2 ** job.attempts))
    elapsed = (datetime.datetime.utcnow() - job.updated_at).total_seconds()
    return elapsed >= backoff


async def _run_chunk_job(db, job, provider_config: dict, provider_name: str) -> None:
    job.status = "running"
    job.attempts += 1
    db.commit()
    try:
        provider = get_provider(provider_name, provider_config)
        result = await provider.transcribe(job.audio_path, language="en", temperature=0.0)
        job.result_json = {
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text, "speaker": s.speaker, "confidence": s.confidence}
                for s in result.segments
            ],
            "full_text": result.full_text,
            "language": result.language,
            "model": result.model,
        }
        job.status = "completed"
        job.error = None
    except (ProviderError, Exception) as e:
        # Always land on "failed", never straight back to "pending" — the
        # tick's own _retry_eligible + backoff pass (below) is what
        # resurrects a job to "pending" once its backoff window has
        # elapsed. Setting "pending" here directly would skip that check
        # and let a job that fails immediately get redispatched on the
        # very next tick (~5s later), hammering the provider on repeated
        # failures instead of backing off. Once attempts reaches
        # MAX_ATTEMPTS, _retry_eligible permanently refuses to resurrect
        # it — that's what makes "failed" terminal.
        job.status = "failed"
        job.error = str(e)
    db.commit()


async def _finalize_if_done(db, transcript_id: int, diarization_service) -> None:
    jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == transcript_id).all()
    if not jobs or any(j.status in ("pending", "running") for j in jobs):
        return  # still work outstanding

    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        return

    segments, full_text = merge_chunk_results(jobs)
    transcript.segments = segments
    transcript.full_text = full_text
    transcript.duration_seconds = max((j.end_time for j in jobs), default=0)
    transcript.status = "partial" if any(j.status == "failed" for j in jobs) else "completed"
    transcript.updated_at = datetime.datetime.utcnow()

    if transcript.diarize_requested and segments and transcript.audio_path:
        try:
            if diarization_service._check_pyannote():
                result = await diarization_service.diarize_pyannote(transcript.audio_path, num_speakers=2)
            else:
                result = await diarization_service.diarize_heuristic(
                    transcript.audio_path, num_speakers=2, segments=segments,
                )
            merged = await diarization_service.combine_with_transcript(result, segments)
            transcript.segments = merged
            transcript.speaker_count = result.speaker_count
        except Exception as e:
            print(f"[queue] non-fatal diarization failure for transcript {transcript_id}: {e}")

    db.commit()


async def queue_worker_tick(SessionLocal, diarization_service) -> None:
    """One pass: retry-eligible failed jobs become pending, then dispatch
    pending jobs (grouped by user+provider) up to that user's concurrency
    setting, skipping any dispatch that would exceed rate-limit budget."""
    db = SessionLocal()
    try:
        from services.settings import get_user_settings  # local import avoids a module-load cycle with app.py

        pending_or_retry = (
            db.query(TranscriptionJob)
            .filter(TranscriptionJob.status.in_(["pending", "failed"]))
            .all()
        )
        for job in pending_or_retry:
            if job.status == "failed" and _retry_eligible(job):
                job.status = "pending"
                job.error = None
        db.commit()

        pending = db.query(TranscriptionJob).filter(TranscriptionJob.status == "pending").all()
        by_transcript = {}
        for job in pending:
            by_transcript.setdefault(job.transcript_id, []).append(job)

        for transcript_id, jobs in by_transcript.items():
            transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
            if not transcript:
                continue
            settings = get_user_settings(db, transcript.user_id)
            concurrency_cap = settings["max_concurrent_chunks"]

            already_running = (
                db.query(TranscriptionJob)
                .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "running")
                .count()
            )
            slots = max(0, concurrency_cap - already_running)
            if slots == 0:
                continue

            prov_cfg = (
                db.query(ProviderConfig)
                .filter(ProviderConfig.user_id == transcript.user_id, ProviderConfig.name == transcript.provider)
                .first()
            )
            provider_config = {
                "api_key": prov_cfg.api_key if prov_cfg else "",
                "api_url": prov_cfg.api_url if prov_cfg else "",
                "default_model": (prov_cfg.default_model if prov_cfg else "") or transcript.model,
            }

            jobs.sort(key=lambda j: j.chunk_index)
            dispatched = []
            for job in jobs[:slots]:
                job_duration = job.end_time - job.start_time
                if not has_budget(db, transcript.user_id, transcript.provider, job_duration):
                    break  # over budget — leave remaining jobs pending for a later tick
                dispatched.append(job)

            if dispatched:
                await asyncio.gather(*[
                    _run_chunk_job(db, job, provider_config, transcript.provider) for job in dispatched
                ])

            await _finalize_if_done(db, transcript_id, diarization_service)
    finally:
        db.close()


async def queue_worker_loop(SessionLocal, diarization_service, interval_seconds: float = 5.0) -> None:
    """Runs forever (until cancelled) — call via asyncio.create_task from
    app.py's lifespan startup."""
    while True:
        try:
            await queue_worker_tick(SessionLocal, diarization_service)
        except Exception as e:
            print(f"[queue] worker tick failed: {e}")
        await asyncio.sleep(interval_seconds)
```

- [ ] **Step 2: Verify a full chunked job cycle end-to-end against a throwaway database and a real Groq key**

```powershell
cd C:\Claude\whisperdesk
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=6" -c:a libmp3lame -b:a 128k data\uploads\_test_worker_a.mp3 2>&1 | Out-Null
ffmpeg -y -f lavfi -i "sine=frequency=880:duration=6" -c:a libmp3lame -b:a 128k data\uploads\_test_worker_b.mp3 2>&1 | Out-Null
.venv\Scripts\python.exe -c "
import asyncio
from database import init_db
from services.auth import create_user
from services.transcription import TranscriptionService
from services.diarization import DiarizationService
from services.queue import create_chunk_jobs, queue_worker_tick

async def main():
    engine, SessionLocal, _ = init_db('data/_test_worker.db')
    db = SessionLocal()
    u = create_user(db, 'workertest', 'pw')

    from database import ProviderConfig
    import os
    key = os.environ.get('GROQ_API_KEY', '')
    db.add(ProviderConfig(user_id=u.id, name='groq', api_key=key))
    db.commit()

    svc = TranscriptionService('data/uploads')
    t = svc.create_transcript_stub(
        db, u.id, filename='test.mp3', provider_name='groq', model='whisper-large-v3',
        language='en', audio_path='data/uploads/_test_worker_a.mp3', diarize_requested=False,
    )
    create_chunk_jobs(db, t.id, [
        {'index': 0, 'path': 'data/uploads/_test_worker_a.mp3', 'start_time': 0.0, 'end_time': 6.0},
        {'index': 1, 'path': 'data/uploads/_test_worker_b.mp3', 'start_time': 6.0, 'end_time': 12.0},
    ])

    diar = DiarizationService()
    for _ in range(3):
        await queue_worker_tick(SessionLocal, diar)

    db2 = SessionLocal()
    from database import Transcript
    result = db2.query(Transcript).filter(Transcript.id == t.id).first()
    print('status:', result.status)
    print('duration_seconds:', result.duration_seconds)
    print('segment count:', len(result.segments or []))

asyncio.run(main())
"
Remove-Item data\_test_worker.db, data\uploads\_test_worker_a.mp3, data\uploads\_test_worker_b.mp3 -ErrorAction SilentlyContinue
```
Set `$env:GROQ_API_KEY` to your real key before running (`$env:GROQ_API_KEY = "gsk_..."`). Expected: `status: completed` (or `partial` if the tone audio produces no transcribable speech from Whisper's perspective — either is acceptable proof the pipeline ran; check for a Python exception, which is the real failure signal), `duration_seconds: 12.0`, segment count printed without error.

- [ ] **Step 3: Confirm the app still imports and boots**

```powershell
.venv\Scripts\python.exe -c "import app; print('OK')"
```
Expected: `OK`.

- [ ] **Step 4: Commit**

```powershell
git add services/queue.py
git commit -m "Add background worker loop for chunk dispatch and finalization"
```

---

### Task 7: Wire the worker into `app.py`; branch `transcribe_audio`; add retry endpoint

**Files:**
- Modify: `app.py`

**Interfaces:**
- Consumes: `services.queue.queue_worker_loop`, `create_chunk_jobs`, `retry_failed_chunks` (Task 6); `services.audio_prep.chunk_audio` (Task 3); `services.transcription.TranscriptionService.create_transcript_stub` (Task 4); `services.settings.get_user_settings` (Task 2).
- Produces: `POST /api/transcripts/{id}/retry-failed-chunks`; `_serialize_transcript` gains `job_progress` field consumed by the frontend in Task 8.

- [ ] **Step 1: Switch `app = FastAPI(...)` to use a `lifespan` context manager**

In `app.py`, add near the top imports:
```python
import asyncio
from contextlib import asynccontextmanager
```

Add:
```python
from services.queue import create_chunk_jobs, retry_failed_chunks, queue_worker_loop
from services.audio_prep import chunk_audio
```

Find:
```python
app = FastAPI(
    title="WhisperDeck",
    version="0.6.0",
    description="Modern meeting transcription & voice intelligence",
)
```
Change to:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(queue_worker_loop(SessionLocal, diarization_service))
    yield
    worker_task.cancel()


app = FastAPI(
    title="WhisperDeck",
    version="0.6.0",
    description="Modern meeting transcription & voice intelligence",
    lifespan=lifespan,
)
```

- [ ] **Step 2: Branch `transcribe_audio` on the chunk-threshold setting**

Find (the section right after transcoding, before the provider-config lookup):
```python
    # Normalize for cloud upload: strips video track, downsamples to 16kHz
    # mono (all Whisper providers resample to this internally anyway). Fixes
    # "file too large" errors on video uploads and long recordings. Builtin
    # runs locally with no upload limit, so skip the extra transcode there.
    if provider != "builtin":
        try:
            save_path = Path(await transcode_for_upload(str(save_path), str(UPLOAD_DIR)))
        except AudioPrepError as e:
            raise HTTPException(status_code=500, detail=str(e))

    # Get provider config
    prov_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == provider,
    ).first()
    provider_config = {}
    if prov_cfg:
        provider_config = {
            "api_key": prov_cfg.api_key,
            "api_url": prov_cfg.api_url,
            "default_model": prov_cfg.default_model or "",
        }

    try:
        transcript = await transcription_service.transcribe(
            db,
            current_user.id,
            audio_path=str(save_path),
            provider_name=provider,
            provider_config=provider_config,
            title=title or file.filename,
            language=language,
            model=model or provider_config.get("default_model"),
            temperature=temperature,
        )
```
Change to:
```python
    user_settings = get_user_settings(db, current_user.id)

    # Normalize for cloud upload: strips video track, downsamples to 16kHz
    # mono (all Whisper providers resample to this internally anyway). Fixes
    # "file too large" errors on video uploads and long recordings. Builtin
    # runs locally with no upload limit, so skip the extra transcode there.
    if provider != "builtin":
        try:
            save_path = Path(await transcode_for_upload(
                str(save_path), str(UPLOAD_DIR), bitrate_kbps=user_settings["bitrate_kbps"]
            ))
        except AudioPrepError as e:
            raise HTTPException(status_code=500, detail=str(e))

    # Get provider config
    prov_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == provider,
    ).first()
    provider_config = {}
    if prov_cfg:
        provider_config = {
            "api_key": prov_cfg.api_key,
            "api_url": prov_cfg.api_url,
            "default_model": prov_cfg.default_model or "",
        }

    threshold_bytes = user_settings["chunk_threshold_mb"] * 1024 * 1024
    file_size = os.path.getsize(save_path)

    if provider != "builtin" and file_size > threshold_bytes:
        # Over the size threshold: split into chunks and hand off to the
        # background worker instead of transcribing inline. The request
        # returns as soon as jobs are queued — see services/queue.py for
        # dispatch/finalization and static/index.html's polling loop for
        # how the frontend picks up the result.
        try:
            chunks = await chunk_audio(str(save_path), str(UPLOAD_DIR), target_chunk_bytes=threshold_bytes)
        except AudioPrepError as e:
            raise HTTPException(status_code=500, detail=str(e))

        transcript = transcription_service.create_transcript_stub(
            db,
            current_user.id,
            filename=file.filename or "audio.mp3",
            provider_name=provider,
            model=model or provider_config.get("default_model") or "",
            language=language,
            audio_path=str(save_path),
            diarize_requested=diarize,
            title=title or file.filename,
        )
        create_chunk_jobs(db, transcript.id, chunks)
        return _serialize_transcript(transcript)

    try:
        transcript = await transcription_service.transcribe(
            db,
            current_user.id,
            audio_path=str(save_path),
            provider_name=provider,
            provider_config=provider_config,
            title=title or file.filename,
            language=language,
            model=model or provider_config.get("default_model"),
            temperature=temperature,
        )
```

Add the import for `get_user_settings` if not already present from Task 2 (it should already be there — confirm the line `from services.settings import get_user_settings, update_user_settings` exists near the top).

- [ ] **Step 3: Add `job_progress` to `_serialize_transcript`**

Find:
```python
def _serialize_transcript(t: Transcript) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "filename": t.filename,
        "duration_seconds": t.duration_seconds,
        "provider": t.provider,
        "model": t.model,
        "language": t.language,
        "status": t.status,
        "full_text": t.full_text,
        "segments": t.segments or [],
        "speaker_count": t.speaker_count,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "has_summary": t.summary is not None,
    }
```
Change to:
```python
def _serialize_transcript(t: Transcript) -> dict:
    jobs = t.jobs or []
    job_progress = None
    if jobs:
        job_progress = {
            "total": len(jobs),
            "completed": sum(1 for j in jobs if j.status == "completed"),
            "failed": sum(1 for j in jobs if j.status == "failed"),
        }
    return {
        "id": t.id,
        "title": t.title,
        "filename": t.filename,
        "duration_seconds": t.duration_seconds,
        "provider": t.provider,
        "model": t.model,
        "language": t.language,
        "status": t.status,
        "full_text": t.full_text,
        "segments": t.segments or [],
        "speaker_count": t.speaker_count,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "has_summary": t.summary is not None,
        "job_progress": job_progress,
    }
```

- [ ] **Step 4: Add the retry-failed-chunks route**

Add right after the `update_transcript` route (before `# ── Diarization ──`):
```python
@app.post("/api/transcripts/{transcript_id}/retry-failed-chunks")
async def retry_transcript_chunks(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    count = retry_failed_chunks(db, transcript_id)
    return {"ok": True, "retried": count}
```

- [ ] **Step 5: Verify the chunked path end-to-end via a running server**

```powershell
cd C:\Claude\whisperdesk
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=40" -af "volume=0.02" -c:a libmp3lame -b:a 192k test_long.mp3 2>&1 | Out-Null
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" -NoNewWindow -RedirectStandardOutput "run_out.log" -RedirectStandardError "run_err.log" -PassThru
Start-Sleep -Seconds 3

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"chunktest","password":"pw"}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/settings -Method Put -Body '{"chunk_threshold_mb":1}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/providers/groq -Method Put -Body "{\"api_key\":\"$env:GROQ_API_KEY\"}" -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null

$form = @{ file = Get-Item "test_long.mp3"; provider = "groq"; language = "en"; model = "whisper-large-v3" }
$r = Invoke-WebRequest -Uri http://localhost:9781/api/transcribe -Method Post -Form $form -WebSession $session -UseBasicParsing
$body = $r.Content | ConvertFrom-Json
Write-Host "immediate response status (expect processing):" $body.status
Write-Host "job_progress:" ($body.job_progress | ConvertTo-Json -Compress)
$tid = $body.id

Start-Sleep -Seconds 15
$final = (Invoke-WebRequest -Uri "http://localhost:9781/api/transcripts/$tid" -WebSession $session -UseBasicParsing).Content | ConvertFrom-Json
Write-Host "status after wait:" $final.status
Write-Host "job_progress after wait:" ($final.job_progress | ConvertTo-Json -Compress)

Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item run_out.log,run_err.log,test_long.mp3 -ErrorAction SilentlyContinue
python -c "
import sqlite3
c = sqlite3.connect('data/whisperdesk.db')
c.execute(\"delete from users where username='chunktest'\")
c.commit()
"
Get-ChildItem data\uploads | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-5) } | Remove-Item
```
Set `$env:GROQ_API_KEY` first. Expected: immediate response `status: processing` with `job_progress` showing `total` > 1 and `completed: 0`; after the 15s wait, `status` has become `completed` (or `partial`) with `job_progress.completed` equal to (or close to) `total`.

- [ ] **Step 6: Verify the small-file synchronous path is unaffected**

The threshold check only triggers chunking when the transcoded file exceeds `chunk_threshold_mb` — confirm a short file well under that stays on the old synchronous path (immediate full response, no `job_progress`):

```powershell
cd C:\Claude\whisperdesk
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=2" -c:a libmp3lame test_short.mp3 2>&1 | Out-Null
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" -NoNewWindow -RedirectStandardOutput "run_out.log" -RedirectStandardError "run_err.log" -PassThru
Start-Sleep -Seconds 3

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"synctest","password":"pw"}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/providers/groq -Method Put -Body "{\"api_key\":\"$env:GROQ_API_KEY\"}" -ContentType "application/json" -WebSession $session -UseBasicParsing | Out-Null

$form = @{ file = Get-Item "test_short.mp3"; provider = "groq"; language = "en"; model = "whisper-large-v3" }
$r = Invoke-WebRequest -Uri http://localhost:9781/api/transcribe -Method Post -Form $form -WebSession $session -UseBasicParsing
$body = $r.Content | ConvertFrom-Json
Write-Host "status (expect completed/failed, not processing):" $body.status
Write-Host "job_progress (expect null):" $body.job_progress

Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item run_out.log,run_err.log,test_short.mp3 -ErrorAction SilentlyContinue
python -c "
import sqlite3
c = sqlite3.connect('data/whisperdesk.db')
c.execute(\"delete from users where username='synctest'\")
c.commit()
"
```
Expected: `status` is immediately `completed` (or `failed` if the tone audio isn't transcribable speech — either proves the request didn't return `processing`) and `job_progress` is `null`/empty, confirming the old blocking single-shot path still runs unchanged for files under the threshold.

- [ ] **Step 7: Commit**

```powershell
git add app.py
git commit -m "Wire chunked upload path, background worker, and retry-failed-chunks endpoint"
```

---

### Task 8: Frontend — polling-based upload, progress UI, retry action

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `GET /api/transcripts/{id}` (now includes `job_progress`), `POST /api/transcripts/{id}/retry-failed-chunks` (Task 7).

- [ ] **Step 1: Replace `startTx()`'s single blocking call with fire-and-poll**

Find:
```javascript
  try {
    document.getElementById('progTitle').textContent = 'Uploading audio...';
    document.getElementById('progDesc').textContent = (selectedFile.size / (1024*1024)).toFixed(1) + ' MB';

    const r = await fetch(API + '/api/transcribe', { method: 'POST', body: form });
    if (!r.ok) {
      const err = await r.text();
      throw new Error(err);
    }

    // Upload complete
    setStageDone('stg-upload');
    setStageActive('stg-transcribe');
    document.getElementById('progTitle').textContent = 'Transcribing with Whisper...';

    const data = await r.json();

    setStageDone('stg-transcribe');

    if (document.getElementById('txDiarize').value === 'true') {
      setStageActive('stg-diarize');
      document.getElementById('progTitle').textContent = 'Running speaker diarization...';
      await new Promise(r => setTimeout(r, 600));
      setStageDone('stg-diarize');
    } else {
      setStageDone('stg-diarize');
    }

    setStageActive('stg-done');
    document.getElementById('progTitle').textContent = 'Transcription complete!';
    document.getElementById('progDesc').textContent = data.segments ? data.segments.length + ' segments · ' + data.provider : '';
    document.getElementById('progCircle').style.strokeDashoffset = '0';
    document.getElementById('progCircle').style.stroke = 'var(--success)';
    document.getElementById('progCheck').style.display = 'block';

    toast('Transcription complete!', 'success');
    selectedFile = null;
    document.getElementById('txStartBtn').disabled = true;

    setTimeout(() => navigate('detail', data.id), 1200);
  } catch (e) {
    toast('Transcription failed: ' + (e.message || e), 'error');
    document.getElementById('progTitle').textContent = 'Transcription failed';
    document.getElementById('progDesc').textContent = e.message || 'Unknown error';
  }
```
Change to:
```javascript
  try {
    document.getElementById('progTitle').textContent = 'Uploading audio...';
    document.getElementById('progDesc').textContent = (selectedFile.size / (1024*1024)).toFixed(1) + ' MB';

    const r = await fetch(API + '/api/transcribe', { method: 'POST', body: form });
    if (!r.ok) {
      const err = await r.text();
      throw new Error(err);
    }

    setStageDone('stg-upload');
    setStageActive('stg-transcribe');
    document.getElementById('progTitle').textContent = 'Transcribing with Whisper...';

    const initial = await r.json();
    const finalData = await pollTranscript(initial.id);

    setStageDone('stg-transcribe');

    if (document.getElementById('txDiarize').value === 'true') {
      setStageActive('stg-diarize');
      document.getElementById('progTitle').textContent = 'Running speaker diarization...';
      await new Promise(r => setTimeout(r, 600));
      setStageDone('stg-diarize');
    } else {
      setStageDone('stg-diarize');
    }

    setStageActive('stg-done');
    const failedMsg = finalData.status === 'partial' ? ' (some sections failed — retry from the transcript page)' : '';
    document.getElementById('progTitle').textContent = finalData.status === 'partial' ? 'Transcription partially complete' : 'Transcription complete!';
    document.getElementById('progDesc').textContent = (finalData.segments ? finalData.segments.length + ' segments · ' + finalData.provider : '') + failedMsg;
    document.getElementById('progCircle').style.strokeDashoffset = '0';
    document.getElementById('progCircle').style.stroke = finalData.status === 'partial' ? 'var(--warning)' : 'var(--success)';
    document.getElementById('progCheck').style.display = 'block';

    toast(finalData.status === 'partial' ? 'Transcription partially complete' : 'Transcription complete!', finalData.status === 'partial' ? 'error' : 'success');
    selectedFile = null;
    document.getElementById('txStartBtn').disabled = true;

    setTimeout(() => navigate('detail', finalData.id), 1200);
  } catch (e) {
    toast('Transcription failed: ' + (e.message || e), 'error');
    document.getElementById('progTitle').textContent = 'Transcription failed';
    document.getElementById('progDesc').textContent = e.message || 'Unknown error';
  }
```

- [ ] **Step 2: Add the `pollTranscript` helper**

Add right after the `startTx` function closes (find `function toggleAdv() {` and add the new function immediately before it):
```javascript
async function pollTranscript(id) {
  while (true) {
    const r = await fetch(API + '/api/transcripts/' + id);
    if (!r.ok) throw new Error('Lost track of transcript ' + id);
    const data = await r.json();
    if (data.job_progress) {
      const p = data.job_progress;
      document.getElementById('progDesc').textContent = p.completed + ' of ' + p.total + ' sections done';
    }
    if (['completed', 'failed', 'partial'].includes(data.status)) {
      if (data.status === 'failed') throw new Error(data.error || 'Transcription failed');
      return data;
    }
    await new Promise(res => setTimeout(res, 2000));
  }
}
```

- [ ] **Step 3: Add a "Retry failed sections" button to the transcript detail page**

Find the detail-page rendering function — search for where `status === 'failed'` or similar status-conditional UI is built for the detail view (look for `function renderDetail` or similar in the script — since the exact detail-page markup wasn't fully read during planning, the implementer should grep `renderDetail\|txDetail\|detail-page` in `static/index.html` to find the right insertion point, and add this button conditionally when `transcript.status === 'partial'`):

```javascript
if (transcript.status === 'partial') {
  // insert near the other detail-page action buttons
  html += `<button class="btn btn-sm" onclick="retryFailedChunks(${transcript.id})">Retry failed sections</button>`;
}
```

And add the handler function near `logout()`:
```javascript
async function retryFailedChunks(id) {
  try {
    const r = await fetch(API + '/api/transcripts/' + id + '/retry-failed-chunks', { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const body = await r.json();
    toast('Retrying ' + body.retried + ' failed section(s)...', 'success');
    setTimeout(() => navigate('detail', id), 500);
  } catch (e) {
    toast('Retry failed: ' + (e.message || e), 'error');
  }
}
```

- [ ] **Step 4: Verify in a real browser**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe app.py
```
Open `http://localhost:9781`, log in, upload a recording long enough to exceed the chunk threshold (or lower the threshold in Settings first, once Task 9 adds that UI — until then, set it via `PUT /api/settings` as in Task 7's verification). Confirm:
1. The progress screen shows "N of M sections done" updating as chunks complete, instead of jumping straight to done.
2. On completion, the detail page loads normally with the full merged transcript.
3. If a chunk is deliberately made to fail (e.g. temporarily corrupt one chunk's provider config mid-run), the transcript reaches `partial` and a "Retry failed sections" button appears; clicking it and waiting shows the transcript reach `completed`.

- [ ] **Step 5: Commit**

```powershell
git add static/index.html
git commit -m "Switch upload flow to polling; add chunk progress and retry-failed-chunks UI"
```

---

### Task 9: Settings page UI for the four new tunables

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `GET`/`PUT /api/settings` (Task 2).

- [ ] **Step 1: Add a Settings card for audio/chunking tunables**

Find the `set-card` for "Account" added in the per-user-auth work (`<h4>Account</h4>`), and add a new card immediately before it:
```html
        <div class="set-card">
          <h4>Audio & Chunking</h4>
          <div class="cfg-f" style="margin-bottom:10px">
            <label>Bitrate (kbps)</label>
            <input type="number" id="setBitrate" style="width:100%;padding:7px 10px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-family:var(--font);outline:none">
          </div>
          <div class="cfg-f" style="margin-bottom:10px">
            <label>Chunk size threshold (MB)</label>
            <input type="number" id="setChunkThreshold" style="width:100%;padding:7px 10px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-family:var(--font);outline:none">
          </div>
          <div class="cfg-f" style="margin-bottom:16px">
            <label>Max concurrent chunk uploads</label>
            <input type="number" id="setMaxConcurrent" style="width:100%;padding:7px 10px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-family:var(--font);outline:none">
          </div>
          <button class="btn btn-sm btn-primary" onclick="saveAudioSettings()">Save</button>
        </div>
```

- [ ] **Step 2: Add load/save JS functions**

Add near `checkAuth()`:
```javascript
async function loadAudioSettings() {
  try {
    const r = await fetch(API + '/api/settings');
    if (!r.ok) return;
    const s = await r.json();
    document.getElementById('setBitrate').value = s.bitrate_kbps;
    document.getElementById('setChunkThreshold').value = s.chunk_threshold_mb;
    document.getElementById('setMaxConcurrent').value = s.max_concurrent_chunks;
  } catch (e) {}
}

async function saveAudioSettings() {
  try {
    const body = {
      bitrate_kbps: parseInt(document.getElementById('setBitrate').value, 10),
      chunk_threshold_mb: parseInt(document.getElementById('setChunkThreshold').value, 10),
      max_concurrent_chunks: parseInt(document.getElementById('setMaxConcurrent').value, 10),
    };
    const r = await fetch(API + '/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());
    toast('Settings saved', 'success');
  } catch (e) {
    toast('Failed to save settings: ' + (e.message || e), 'error');
  }
}
```

Find where the Settings page becomes visible (search for the navigation handler that shows the settings page, e.g. inside `navigate(page)` where `page === 'settings'`) and add a call to `loadAudioSettings()` there, alongside whatever existing settings-load logic runs when that page opens.

- [ ] **Step 3: Verify in a real browser**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe app.py
```
Open `http://localhost:9781`, log in, go to Settings. Confirm the three fields show the current values (128 / 20 / 4 by default), change them, click Save, confirm the toast, reload the page, navigate back to Settings, and confirm the changed values persisted.

- [ ] **Step 4: Commit**

```powershell
git add static/index.html
git commit -m "Add Settings UI for bitrate, chunk threshold, and concurrency"
```

---

## Post-implementation note

Task 8 Step 3 (the detail-page retry button) asks the implementer to locate the detail-rendering function by grep rather than giving an exact line number — the detail-page markup wasn't read in full during planning. This is the one step in this plan without a fully pre-verified insertion point; treat it as the first thing to double-check if that step's diff looks off, and confirm the button only renders when `transcript.status === 'partial'` (not on every transcript) before moving on.
