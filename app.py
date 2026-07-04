"""
WhisperDeck — FastAPI Application

A modern meeting transcription & voice intelligence application.
Transcribe · Diarize · Summarize · Identify
"""
import os
import json
import datetime
import shutil
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import secrets

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Body, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from database import init_db, backfill_user_id, Transcript, Summary, VoiceProfile, VoiceClip, ProviderConfig, User, LlmJob
from services.auth import get_or_create_fallback_user, create_user, authenticate_user
from services.settings import get_user_settings, update_user_settings
from services.transcription import TranscriptionService
from services.diarization import DiarizationService
from services.voice_id import voice_id_service
from services.audio_prep import transcode_for_upload, AudioPrepError, chunk_audio, get_audio_duration, extract_clips_concat
from services.queue import create_chunk_jobs, retry_failed_chunks, queue_worker_loop, compute_queue_status, cancel_transcript_jobs, resume_cancelled_chunks
from services.hotwords import list_hotwords, add_hotword, delete_hotword
from services.correction import extract_hotwords_from_doc
from services.model_catalog import get_correction_models
from services.llm_jobs import (
    enqueue_llm_job, enqueue_auto_correction, serialize_llm_job, latest_job,
    cancel_llm_job, rerun_llm_job, llm_worker_loop,
)
from backends import list_providers, get_provider, LOCAL_PROVIDERS

# ── App Setup ──────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
VOICES_DIR = DATA_DIR / "voices"
DB_PATH = DATA_DIR / "whisperdesk.db"

for d in [DATA_DIR, UPLOAD_DIR, TRANSCRIPT_DIR, VOICES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

SESSION_SECRET_PATH = DATA_DIR / ".session_secret"

# Local recordings longer than this run through the chunk-job pipeline
# (real progress + cancel/resume) instead of one opaque blocking call.
LOCAL_CHUNK_SECONDS = 300

if SESSION_SECRET_PATH.exists():
    SESSION_SECRET = SESSION_SECRET_PATH.read_text().strip()
else:
    SESSION_SECRET = secrets.token_hex(32)
    SESSION_SECRET_PATH.write_text(SESSION_SECRET)

engine, SessionLocal, migrated_tables = init_db(str(DB_PATH))

if migrated_tables:
    _migration_db = SessionLocal()
    try:
        _fallback_user = get_or_create_fallback_user(_migration_db)
        backfill_user_id(engine, migrated_tables, _fallback_user.id)
        print(
            f"[migration] assigned {len(migrated_tables)} pre-existing table(s) "
            f"to fallback user 'local' (password: changeme — change it after logging in)"
        )
    finally:
        _migration_db.close()

transcription_service = TranscriptionService(str(UPLOAD_DIR))
diarization_service = DiarizationService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(queue_worker_loop(SessionLocal, diarization_service))
    llm_worker_task = asyncio.create_task(llm_worker_loop(SessionLocal, transcription_service, diarization_service))
    yield
    worker_task.cancel()
    llm_worker_task.cancel()


app = FastAPI(
    title="WhisperDeck",
    version="0.6.0",
    description="Modern meeting transcription & voice intelligence",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # allow_credentials=True + allow_origins=["*"] is a spec-invalid combo
    # (browsers reject it) and unnecessary here — the SPA sends no
    # cookies/auth headers, API keys stay server-side.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)


# ── Helpers ────────────────────────────────────────────────────────────────

def get_db():
    """Per-request DB session — one session per request, closed when the
    request finishes, instead of one shared session for the whole app."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


def _serialize_transcript(db: Session, t: Transcript) -> dict:
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
        "corrected_text": t.corrected_text,
        "correction_error": t.correction_error,
        "correction_model": t.correction_model,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "has_summary": t.summary is not None,
        # Gates the post-hoc re-transcribe/re-diarize buttons: needs the
        # stored source file, not just a path that once existed.
        "has_audio": bool(t.audio_path and os.path.exists(t.audio_path)),
        "job_progress": job_progress,
        "processed_size_bytes": t.processed_size_bytes,
        "queue_status": compute_queue_status(db, t),
        # latest LLM job per kind — the detail tabs render live progress
        # ("correction running — section X of Y") straight from these.
        "correction_job": serialize_llm_job(cj) if (cj := latest_job(db, t.id, "correction")) else None,
        "summary_job": serialize_llm_job(sj) if (sj := latest_job(db, t.id, "summary")) else None,
    }


def _serialize_summary(s: Summary) -> dict:
    if not s:
        return None
    return {
        "id": s.id,
        "transcript_id": s.transcript_id,
        "short_summary": s.short_summary,
        "key_points": s.key_points or [],
        "action_items": s.action_items or [],
        "decisions": s.decisions or [],
        "model": s.model,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ── Auth ──────────────────────────────────────────────────────────────────

@app.post("/api/register")
async def register(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    user = create_user(db, username, password)
    request.session["user_id"] = user.id
    return {"ok": True, "username": user.username}


@app.post("/api/login")
async def login(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = authenticate_user(db, username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    request.session["user_id"] = user.id
    return {"ok": True, "username": user.username}


@app.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(current_user: User = Depends(get_current_user)):
    return {"username": current_user.username}


# ── Settings ──────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_user_settings(db, current_user.id)


@app.put("/api/settings")
async def put_settings(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return update_user_settings(db, current_user.id, data)


# ── Hotword Glossary ─────────────────────────────────────────────────────

def _serialize_hotword(h) -> dict:
    return {
        "id": h.id,
        "term": h.term,
        "source": h.source,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


@app.get("/api/hotwords")
async def get_hotwords(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [_serialize_hotword(h) for h in list_hotwords(db, current_user.id)]


@app.post("/api/hotwords")
async def create_hotword(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    term = (data.get("term") or "").strip()
    if not term:
        raise HTTPException(status_code=400, detail="term is required")
    entry = add_hotword(db, current_user.id, term)
    return _serialize_hotword(entry)


@app.delete("/api/hotwords/{hotword_id}")
async def remove_hotword(hotword_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    deleted = delete_hotword(db, current_user.id, hotword_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Hotword not found")
    return {"ok": True}


# ── API Routes ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "app": "WhisperDeck",
        "version": "0.6.0",
        "diarization_backend": diarization_service._check_pyannote(),
        "voice_id_backend": voice_id_service._backend,
    }


# ── Providers ─────────────────────────────────────────────────────────────

@app.get("/api/providers")
async def get_providers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List available providers with their metadata."""
    providers = list_providers()
    # Merge in saved config status
    for p in providers:
        saved = db.query(ProviderConfig).filter(
            ProviderConfig.user_id == current_user.id,
            ProviderConfig.name == p["id"],
        ).first()
        if saved:
            p["configured"] = bool(saved.api_key)
            p["is_active"] = saved.is_active
        else:
            p["configured"] = False
            p["is_active"] = False
    return providers


@app.get("/api/providers/{name}")
async def get_provider_config(name: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == name,
    ).first()
    if not cfg:
        return {"name": name, "api_key": "", "api_url": "", "default_model": "", "is_active": False}
    return {
        "name": cfg.name,
        "display_name": cfg.display_name,
        "api_key": ("••••" + cfg.api_key[-4:] if len(cfg.api_key) > 8 else "••••••••") if cfg.api_key else "",
        "api_url": cfg.api_url,
        "default_model": cfg.default_model,
        "is_active": cfg.is_active,
        "_has_key": bool(cfg.api_key),
    }


@app.put("/api/providers/{name}")
async def update_provider_config(name: str, data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == name,
    ).first()
    if not cfg:
        cfg = ProviderConfig(name=name, user_id=current_user.id)
        db.add(cfg)

    if "api_key" in data and data["api_key"] and not data["api_key"].startswith("••••"):
        cfg.api_key = data["api_key"]
    if "api_url" in data:
        cfg.api_url = data["api_url"]
    if "default_model" in data:
        cfg.default_model = data["default_model"]
    if "is_active" in data:
        cfg.is_active = data["is_active"]
    if "display_name" in data:
        cfg.display_name = data["display_name"]

    db.commit()
    return {"ok": True, "name": name}


@app.get("/api/providers/{name}/models")
async def list_provider_models(name: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Fetch available transcription models for a given provider (live if possible)."""
    from backends import get_provider, list_providers

    # Check provider exists
    known = [p["id"] for p in list_providers()]
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")

    # Get saved config
    cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == name,
    ).first()
    prov_config = {}
    if cfg:
        prov_config = {
            "api_key": cfg.api_key or "",
            "api_url": cfg.api_url or "",
            "default_model": cfg.default_model or "",
        }

    # Also grab site_url/site_name for OpenRouter
    prov_config["site_url"] = prov_config.get("site_url", "")
    prov_config["site_name"] = "WhisperDeck"

    try:
        provider = get_provider(name, prov_config)
        models = await provider.list_models()
        return {"provider": name, "models": models, "live": True}
    except Exception as e:
        # Return defaults on failure
        default_map = {
            "groq": ["whisper-large-v3-flash", "whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3"],
            "openai": ["whisper-1"],
            "replicate": ["varunp2k/whisper-large-v3-turbo", "openai/whisper"],
            "local": ["whisper-large-v3-turbo", "whisper-large-v3"],
            "openrouter": ["openai/whisper-1", "deepgram/whisper-large-v3-turbo"],
        }
        return {
            "provider": name,
            "models": default_map.get(name, ["whisper-large-v3-turbo"]),
            "live": False,
            "error": str(e),
        }


# ── Transcription ─────────────────────────────────────────────────────────

async def _run_transcription_pipeline(
    db: Session,
    current_user: User,
    save_path: Path,
    *,
    filename: str,
    title: Optional[str],
    provider: str,
    model: Optional[str],
    language: str,
    temperature: float,
    diarize: bool,
    num_speakers: Optional[int],
) -> dict:
    """Everything after the source audio is on disk: transcode decision,
    chunk-vs-inline branch, inline diarization, auto-correct enqueue.
    Shared by /api/transcribe (fresh upload) and
    /api/transcripts/{id}/retranscribe (stored audio_path)."""
    user_settings = get_user_settings(db, current_user.id)

    # Normalize for cloud upload: strips video track, downsamples to 16kHz
    # mono (all Whisper providers resample to this internally anyway). Fixes
    # "file too large" errors on video uploads and long recordings. Builtin
    # runs locally with no upload limit, so skip the extra transcode there —
    # unless the container is one libsndfile can't open (browser live capture
    # produces webm/opus), in which case local providers need the ffmpeg
    # pass too or soundfile fails with "Format not recognised".
    local_readable_exts = {".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"}
    try:
        raw_duration = get_audio_duration(str(save_path))
    except Exception:
        raw_duration = 0.0
    needs_transcode = (
        provider not in LOCAL_PROVIDERS
        or save_path.suffix.lower() not in local_readable_exts
        # Long local recordings go through the chunk pipeline below, and
        # chunk_audio stream-copies — it needs the transcoded mp3, not raw
        # PCM (splitting a WAV into .mp3 chunk files fails in the muxer).
        or raw_duration > LOCAL_CHUNK_SECONDS
    )
    if needs_transcode:
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

    try:
        # Re-probe post-transcode (video tracks stripped, codec changed);
        # falls back to the raw probe from above.
        duration_seconds = get_audio_duration(str(save_path)) or raw_duration
    except Exception:
        duration_seconds = raw_duration

    # Hosted providers chunk to stay under upload/rate limits. Local providers
    # chunk long recordings purely for observability and control: each chunk is
    # a TranscriptionJob, which is what gives the UI real percent progress and
    # makes cancel/resume work. Short local files keep the inline path — chunk
    # overhead is pointless for a voice memo. Model context is not harmed:
    # Moonshine VAD-splits internally (trained on 10-55s segments) and Whisper
    # works in 30s windows; chunk seams reuse the same silence-aware split +
    # overlap dedupe as the hosted path.
    hosted_chunked = provider not in LOCAL_PROVIDERS and file_size > threshold_bytes
    local_chunked = provider in LOCAL_PROVIDERS and duration_seconds > LOCAL_CHUNK_SECONDS

    if hosted_chunked or local_chunked:
        if local_chunked:
            # ~LOCAL_CHUNK_SECONDS of audio per chunk, derived from this
            # file's actual byte rate.
            target_chunk_bytes = int(file_size / duration_seconds * LOCAL_CHUNK_SECONDS)
        else:
            target_chunk_bytes = threshold_bytes
        try:
            chunks = await chunk_audio(str(save_path), str(UPLOAD_DIR), target_chunk_bytes=target_chunk_bytes)
        except AudioPrepError as e:
            raise HTTPException(status_code=500, detail=str(e))

        transcript = transcription_service.create_transcript_stub(
            db,
            current_user.id,
            filename=filename,
            provider_name=provider,
            model=model or provider_config.get("default_model") or "",
            language=language,
            audio_path=str(save_path),
            diarize_requested=diarize,
            title=title or filename,
            num_speakers=num_speakers,
        )
        # Real processed size, not the raw upload size — the sum of all
        # chunk files, since that's what actually gets sent to the provider.
        transcript.processed_size_bytes = sum(os.path.getsize(c["path"]) for c in chunks)
        # Known now from the ffprobe above — lets the UI say "48-min
        # recording" before the first chunk lands.
        transcript.duration_seconds = duration_seconds
        db.commit()
        create_chunk_jobs(db, transcript.id, chunks)
        return _serialize_transcript(db, transcript)

    try:
        transcript = await transcription_service.transcribe(
            db,
            current_user.id,
            audio_path=str(save_path),
            provider_name=provider,
            provider_config=provider_config,
            title=title or filename,
            language=language,
            model=model or provider_config.get("default_model"),
            temperature=temperature,
        )
        transcript.processed_size_bytes = file_size
        db.commit()

        # Run diarization if requested
        if diarize and transcript.segments:
            try:
                merged, speaker_count = await diarization_service.diarize_and_merge(
                    str(save_path),
                    num_speakers=num_speakers,
                    segments=transcript.segments,
                    hf_token=user_settings.get("hf_token"),
                )
                transcript.segments = merged
                transcript.speaker_count = speaker_count
                db.commit()
            except Exception as e:
                # Non-fatal: diarization enhancement failed. Transcript still
                # succeeds without speaker labels, but log so it's visible.
                print(f"[diarization] non-fatal failure for transcript {transcript.id}: {e}")

        # Post-hoc correction pass — queued as a background LlmJob (visible
        # on the Queue screen) instead of blocking this response.
        if user_settings.get("auto_correct", True):
            enqueue_auto_correction(db, transcript, user_settings)

        return _serialize_transcript(db, transcript)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    provider: str = Form("groq"),
    model: Optional[str] = Form(None),
    language: str = Form("en"),
    temperature: float = Form(0.0),
    diarize: bool = Form(False),
    num_speakers: Optional[int] = Form(None),
    context_doc: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload and transcribe an audio file."""
    # Save uploaded file
    file_ext = os.path.splitext(file.filename or "audio.mp3")[1] or ".mp3"
    safe_name = f"{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{hash(file.filename or 'audio')}{file_ext}"
    save_path = UPLOAD_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    if context_doc and context_doc.strip():
        groq_cfg = db.query(ProviderConfig).filter(
            ProviderConfig.user_id == current_user.id,
            ProviderConfig.name == "groq",
        ).first()
        if groq_cfg and groq_cfg.api_key:
            try:
                await extract_hotwords_from_doc(db, current_user.id, context_doc, api_key=groq_cfg.api_key)
            except Exception as e:
                # Non-fatal: glossary-building side effect, never blocks transcription.
                print(f"[correction] non-fatal hotword extraction failure: {e}")

    return await _run_transcription_pipeline(
        db, current_user, save_path,
        filename=file.filename or "audio.mp3",
        title=title,
        provider=provider,
        model=model,
        language=language,
        temperature=temperature,
        diarize=diarize,
        num_speakers=num_speakers,
    )


@app.get("/api/transcripts")
async def list_transcripts(limit: int = 50, offset: int = 0, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    transcripts = (
        db.query(Transcript)
        .filter(Transcript.user_id == current_user.id)
        .order_by(Transcript.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_serialize_transcript(db, t) for t in transcripts]


@app.get("/api/transcripts/{transcript_id}")
async def get_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return _serialize_transcript(db, t)


@app.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.patch("/api/transcripts/{transcript_id}")
async def update_transcript(transcript_id: int, data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if "title" in data:
        t.title = data["title"]
    if "segments" in data:
        t.segments = data["segments"]
    if "full_text" in data:
        t.full_text = data["full_text"]
    t.updated_at = datetime.datetime.utcnow()
    db.commit()
    return _serialize_transcript(db, t)


@app.post("/api/transcripts/{transcript_id}/retry-failed-chunks")
async def retry_transcript_chunks(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    count = retry_failed_chunks(db, transcript_id)
    return {"ok": True, "retried": count}


@app.post("/api/transcripts/{transcript_id}/cancel")
async def cancel_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if t.status != "processing":
        raise HTTPException(status_code=400, detail=f"Cannot cancel a transcript with status '{t.status}'")
    count = cancel_transcript_jobs(db, transcript_id)
    return {"ok": True, "cancelled": count}


@app.post("/api/transcripts/{transcript_id}/resume")
async def resume_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if t.status != "cancelled":
        raise HTTPException(status_code=400, detail=f"Cannot resume a transcript with status '{t.status}'")
    count = resume_cancelled_chunks(db, transcript_id)
    return {"ok": True, "resumed": count}


@app.post("/api/transcripts/{transcript_id}/retranscribe")
async def retranscribe_transcript(
    transcript_id: int,
    provider: str = Form(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    diarize: Optional[bool] = Form(None),
    num_speakers: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-run transcription on the stored audio with a different
    provider/model. Creates a NEW transcript row (the original is kept for
    side-by-side comparison); unspecified options default to the source
    transcript's values."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not (t.audio_path and os.path.exists(t.audio_path)):
        raise HTTPException(
            status_code=400,
            detail="No stored audio for this transcript — it predates audio retention or the file was removed",
        )
    return await _run_transcription_pipeline(
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


_AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".flac": "audio/flac", ".webm": "audio/webm", ".m4a": "audio/mp4",
}


@app.get("/api/transcripts/{transcript_id}/audio")
async def get_transcript_audio(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Serve the stored source audio — the detail screen's per-line play
    buttons load this once and seek to each segment's start time."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not (t.audio_path and os.path.exists(t.audio_path)):
        raise HTTPException(status_code=404, detail="No stored audio for this transcript")
    ext = os.path.splitext(t.audio_path)[1].lower()
    return FileResponse(t.audio_path, media_type=_AUDIO_MIME.get(ext, "audio/mpeg"))


@app.post("/api/transcripts/{transcript_id}/speakers/rename")
async def rename_transcript_speaker(
    transcript_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename a diarization speaker label across the whole transcript —
    every matching segment, plus the 'Speaker: text' line prefixes in
    corrected_text so the two views stay in agreement."""
    old = (data.get("from") or "").strip()
    new = (data.get("to") or "").strip()
    if not old or not new:
        raise HTTPException(status_code=400, detail="Both 'from' and 'to' names are required")
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")

    # New list, not in-place mutation — SQLAlchemy doesn't change-track
    # in-place edits on a JSON column.
    renamed = 0
    new_segments = []
    for seg in t.segments or []:
        if (seg.get("speaker") or "") == old:
            seg = {**seg, "speaker": new}
            renamed += 1
        new_segments.append(seg)
    if renamed == 0:
        raise HTTPException(status_code=400, detail=f"No segments have speaker '{old}'")
    t.segments = new_segments

    if t.corrected_text:
        # Line-anchored: only rewrite the 'Old Name: ' prefix at the start
        # of a line — the same string inside sentence text must not change.
        prefix = f"{old}: "
        t.corrected_text = "\n".join(
            (new + line[len(old):]) if line.startswith(prefix) else line
            for line in t.corrected_text.splitlines()
        )

    t.updated_at = datetime.datetime.utcnow()
    db.commit()
    return {"renamed": renamed, "transcript": _serialize_transcript(db, t)}


@app.post("/api/transcripts/{transcript_id}/segments/retag")
async def retag_transcript_segments(
    transcript_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fix a chunk of mis-diarized lines by index, without touching other
    segments that happen to share the same (correct) original label.
    corrected_text is intentionally left untouched — there is no reliable
    line-to-segment-index mapping once the LLM has reworded/merged lines."""
    indices = data.get("indices") or []
    speaker = (data.get("speaker") or "").strip()
    if not indices or not speaker:
        raise HTTPException(status_code=400, detail="'indices' (non-empty) and 'speaker' are required")

    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")

    segments = t.segments or []
    for i in indices:
        if not isinstance(i, int) or i < 0 or i >= len(segments):
            raise HTTPException(status_code=400, detail=f"Segment index {i} is out of range")

    index_set = set(indices)
    new_segments = [
        {**seg, "speaker": speaker} if i in index_set else seg
        for i, seg in enumerate(segments)
    ]
    t.segments = new_segments
    t.updated_at = datetime.datetime.utcnow()
    db.commit()
    return {"retagged": len(index_set), "transcript": _serialize_transcript(db, t)}


@app.post("/api/transcripts/{transcript_id}/enroll-speaker")
async def enroll_speaker_from_transcript(
    transcript_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enroll a voice profile from transcript lines flagged as seeds.
    The clip time ranges are cut from the stored audio, concatenated into
    one sample, and added as a clip on the named profile (creating it if
    needed) — the profile's embedding is the mean of all its clips, so
    repeated calls append and average rather than overwrite."""
    name = (data.get("name") or "").strip()
    clips = data.get("clips") or []
    if not name:
        raise HTTPException(status_code=400, detail="Speaker name is required")
    if not clips or len(clips) > 10:
        raise HTTPException(status_code=400, detail="Flag between 1 and 10 clips to seed a voice")
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not (t.audio_path and os.path.exists(t.audio_path)):
        raise HTTPException(status_code=404, detail="No stored audio for this transcript")

    try:
        sample_path = await extract_clips_concat(t.audio_path, clips, str(UPLOAD_DIR))
    except (AudioPrepError, KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Could not extract seed clips: {e}")
    profile_created_here = False
    permanent_path = None
    try:
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == current_user.id, VoiceProfile.name == name
        ).first()
        if not profile:
            profile = VoiceProfile(
                user_id=current_user.id, name=name, embedding=None,
                embedding_model=voice_id_service.backend_name, sample_count=0,
                notes=f"Seeded from transcript {t.id}",
            )
            db.add(profile)
            db.commit()
            profile_created_here = True
        permanent_path = VOICES_DIR / f"clip_{profile.id}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S%f')}.wav"
        shutil.copyfile(sample_path, permanent_path)
        clip = voice_id_service.add_clip(db, profile.id, current_user.id, str(permanent_path),
                                          source_transcript_id=t.id)
        db.refresh(profile)
        return {
            "id": profile.id,
            "name": profile.name,
            "sample_count": profile.sample_count,
            "embedding_model": profile.embedding_model,
            "notes": profile.notes,
            "clip_id": clip.id,
        }
    except ValueError as e:
        if permanent_path is not None:
            try:
                os.remove(permanent_path)
            except OSError:
                pass
        if profile_created_here:
            db.delete(profile)
            db.commit()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            os.remove(sample_path)
        except OSError:
            pass


# ── Diarization ───────────────────────────────────────────────────────────

@app.post("/api/diarize")
async def diarize_audio(
    file: UploadFile = File(...),
    num_speakers: int = Form(2),
    method: str = Form("heuristic"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run speaker diarization on an audio file."""
    file_ext = os.path.splitext(file.filename or "audio.mp3")[1] or ".mp3"
    safe_name = f"diar_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = UPLOAD_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        if method == "pyannote" and diarization_service._check_pyannote():
            user_settings = get_user_settings(db, current_user.id)
            result = await diarization_service.diarize_pyannote(
                str(save_path), num_speakers=num_speakers, hf_token=user_settings.get("hf_token")
            )
        else:
            result = await diarization_service.diarize_heuristic(
                str(save_path), num_speakers=num_speakers
            )

        return {
            "segments": [
                {"start": s.start, "end": s.end, "speaker": s.speaker, "text": s.text}
                for s in result.segments
            ],
            "speaker_count": result.speaker_count,
            "method": result.method,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Summarization ────────────────────────────────────────────────────────

@app.post("/api/transcripts/{transcript_id}/summarize")
async def summarize_transcript(
    transcript_id: int,
    provider: str = Form("groq"),
    model: str = Form("llama-3.3-70b-versatile"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue an LLM summary of a completed transcript (returns the job —
    watch it on the Queue screen or poll the transcript)."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if t.status != "completed":
        raise HTTPException(status_code=400, detail=f"Transcript {transcript_id} is not completed")
    from services.settings import resolve_provider_key
    api_key, _ = resolve_provider_key(db, current_user.id, provider)
    if provider != "local" and not api_key:
        raise HTTPException(status_code=400, detail=f"No {provider} API key saved — add one in the service panel")

    job = enqueue_llm_job(db, current_user.id, transcript_id, "summary", provider, model)
    return {"job": serialize_llm_job(job)}


@app.post("/api/transcripts/{transcript_id}/correct")
async def correct_transcript_route(
    transcript_id: int,
    provider: str = Form("groq"),
    model: str = Form("llama-3.3-70b-versatile"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually (re)run the correction pass, e.g. to try a different
    provider/model against the same raw full_text."""
    transcript = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if transcript.status not in ("completed", "partial"):
        raise HTTPException(status_code=400, detail=f"Transcript {transcript_id} is not completed")

    from services.settings import resolve_provider_key
    api_key, provider_config = resolve_provider_key(db, current_user.id, provider)
    if provider != "local" and not api_key:
        raise HTTPException(status_code=400, detail=f"No {provider} API key saved — add one in the service panel")

    job = enqueue_llm_job(db, current_user.id, transcript_id, "correction", provider, model)
    return {"job": serialize_llm_job(job)}


@app.post("/api/transcripts/{transcript_id}/rediarize")
async def rediarize_transcript(
    transcript_id: int,
    num_speakers: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue an in-place re-diarization of the stored audio — speaker labels
    are merged onto the existing segments. num_speakers=None lets pyannote
    auto-detect. Runs as a background job (watch the Queue screen)."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if t.status not in ("completed", "partial"):
        raise HTTPException(status_code=400, detail=f"Transcript {transcript_id} is not completed")
    if not (t.audio_path and os.path.exists(t.audio_path)):
        raise HTTPException(
            status_code=400,
            detail="No stored audio for this transcript — it predates audio retention or the file was removed",
        )
    # The job reads its parameters from the transcript row (LlmJob has no
    # params column), so persist the requested count before enqueueing.
    t.num_speakers = num_speakers
    t.diarize_requested = True
    db.commit()
    job = enqueue_llm_job(db, current_user.id, transcript_id, "rediarize", "", "")
    return {"job": serialize_llm_job(job)}


@app.post("/api/transcripts/{transcript_id}/context")
async def add_transcript_context(
    transcript_id: int,
    context_doc: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paste a meeting-context doc after the fact — extracts names/jargon
    into the hotword glossary so a correction re-run can apply them.
    Unlike the upload-time path (a silent side effect), this is an explicit
    user action, so a missing key is an error the user can act on."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not context_doc.strip():
        raise HTTPException(status_code=400, detail="Context document is empty")
    groq_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == "groq",
    ).first()
    if not (groq_cfg and groq_cfg.api_key):
        raise HTTPException(status_code=400, detail="Context extraction needs a Groq API key (service panel)")
    terms = await extract_hotwords_from_doc(
        db, current_user.id, context_doc, api_key=groq_cfg.api_key
    )
    return {"terms": terms}


@app.get("/api/correction-models/{provider}")
async def correction_models(provider: str, current_user: User = Depends(get_current_user)):
    """Curated, cost-aware model shortlist for the correction/summary pickers.
    OpenRouter entries are validated against its live catalog with pricing."""
    return {"provider": provider, "models": await get_correction_models(provider)}


# ── Job queue (unified: transcription + LLM jobs) ─────────────────────────

def _transcription_queue_entry(db, t: Transcript) -> dict:
    """Normalize a transcript's chunk pipeline into the shared job shape."""
    qs = compute_queue_status(db, t)
    jobs = t.jobs or []
    done = sum(1 for j in jobs if j.status == "completed")
    total = len(jobs)
    if t.status == "processing":
        status = {"transcribing": "running", "rate_limited": "waiting"}.get(
            (qs or {}).get("state"), "queued")
    else:
        status = t.status  # completed / failed / partial / cancelled
    return {
        "id": f"transcription-{t.id}",
        "kind": "transcription",
        "transcript_id": t.id,
        "title": t.title or t.filename,
        "status": status,
        "progress": {"done": done, "total": total},
        "provider": t.provider,
        "model": t.model,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@app.get("/api/jobs")
async def list_jobs(limit: int = Query(50, le=200), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Master queue: newest-first LLM jobs + transcription pipelines that
    are active or ran through the chunk queue."""
    llm = (
        db.query(LlmJob)
        .filter(LlmJob.user_id == current_user.id)
        .order_by(LlmJob.id.desc())
        .limit(limit)
        .all()
    )
    transcripts = (
        db.query(Transcript)
        .filter(Transcript.user_id == current_user.id)
        .order_by(Transcript.created_at.desc())
        .limit(limit)
        .all()
    )
    titles = {t.id: (t.title or t.filename) for t in transcripts}
    missing = [j.transcript_id for j in llm if j.transcript_id not in titles]
    if missing:
        for t in db.query(Transcript).filter(Transcript.id.in_(missing)).all():
            titles[t.id] = t.title or t.filename

    entries = []
    for t in transcripts:
        if t.status == "processing" or t.jobs:
            entries.append(_transcription_queue_entry(db, t))
    for j in llm:
        e = serialize_llm_job(j)
        e["title"] = titles.get(j.transcript_id, f"Transcript {j.transcript_id}")
        entries.append(e)

    entries.sort(key=lambda e: e["created_at"] or "", reverse=True)
    active = sum(1 for e in entries if e["status"] in ("pending", "running", "queued", "waiting"))
    return {"jobs": entries[:limit], "active": active}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        job = cancel_llm_job(db, current_user.id, job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "job": serialize_llm_job(job)}


@app.post("/api/jobs/{job_id}/rerun")
async def rerun_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        job = rerun_llm_job(db, current_user.id, job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "job": serialize_llm_job(job)}


@app.get("/api/transcripts/{transcript_id}/summary")
async def get_summary(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    summary = db.query(Summary).filter(Summary.transcript_id == transcript_id).first()
    if not summary:
        raise HTTPException(status_code=404, detail="No summary found")
    return _serialize_summary(summary)


# ── Voice Identification Database ─────────────────────────────────────────

@app.get("/api/voices")
async def list_voices(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List all enrolled voice profiles."""
    return voice_id_service.list_profiles(db, current_user.id)


@app.post("/api/voices/enroll")
async def enroll_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enroll a new speaker from an audio sample."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"enroll_{name}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        profile = voice_id_service.enroll(db, current_user.id, name=name, audio_path=str(save_path), notes=notes)
        return {
            "id": profile.id,
            "name": profile.name,
            "sample_count": profile.sample_count,
            "embedding_model": profile.embedding_model,
            "notes": profile.notes,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/voices/identify")
async def identify_speaker(
    file: UploadFile = File(...),
    threshold: float = Form(0.65),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Identify a speaker from an audio sample against enrolled profiles."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"ident_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        matches = voice_id_service.identify(db, current_user.id, str(save_path), threshold=threshold)
        return {
            "matches": matches,
            "total_profiles": len(voice_id_service.list_profiles(db, current_user.id)),
            "backend": voice_id_service._backend,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/voices/{profile_id}")
async def delete_voice_profile(profile_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ok = voice_id_service.delete_profile(db, current_user.id, profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    return {"ok": True}


@app.post("/api/voices/{profile_id}/clips")
async def add_voice_clip(
    profile_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add one clip to an existing roster profile — recomputes the
    profile's match embedding as the mean of all its clips."""
    file_ext = os.path.splitext(file.filename or "clip.wav")[1] or ".wav"
    safe_name = f"clip_{profile_id}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S%f')}{file_ext}"
    save_path = VOICES_DIR / safe_name
    with open(save_path, "wb") as f:
        f.write(await file.read())

    try:
        clip = voice_id_service.add_clip(db, profile_id, current_user.id, str(save_path))
        return {"id": clip.id, "voice_profile_id": clip.voice_profile_id,
                "created_at": clip.created_at.isoformat() if clip.created_at else None}
    except ValueError as e:
        try:
            os.remove(save_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/voices/{profile_id}/clips/{clip_id}")
async def delete_voice_clip(
    profile_id: int, clip_id: int,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    ok = voice_id_service.remove_clip(db, profile_id, current_user.id, clip_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Clip not found")
    return {"ok": True}


@app.get("/api/voices/{profile_id}/clips/{clip_id}/audio")
async def get_voice_clip_audio(
    profile_id: int, clip_id: int,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    profile = db.query(VoiceProfile).filter(
        VoiceProfile.id == profile_id, VoiceProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    clip = db.query(VoiceClip).filter(
        VoiceClip.id == clip_id, VoiceClip.voice_profile_id == profile.id
    ).first()
    if not clip or not os.path.exists(clip.audio_path):
        raise HTTPException(status_code=404, detail="Clip audio not found")
    ext = os.path.splitext(clip.audio_path)[1].lower()
    return FileResponse(clip.audio_path, media_type=_AUDIO_MIME.get(ext, "audio/wav"))


# ── Frontend ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the SPA frontend."""
    index_path = BASE_DIR / "static" / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>WhisperDeck</h1><p>Frontend not built yet.</p>")


@app.get("/api/status")
async def full_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Return comprehensive app status for the frontend dashboard."""
    total = db.query(Transcript).filter(Transcript.user_id == current_user.id).count()
    completed = db.query(Transcript).filter(
        Transcript.user_id == current_user.id, Transcript.status == "completed"
    ).count()
    processing = db.query(Transcript).filter(
        Transcript.user_id == current_user.id, Transcript.status == "processing"
    ).count()
    failed = db.query(Transcript).filter(
        Transcript.user_id == current_user.id, Transcript.status == "failed"
    ).count()
    total_duration = (
        db.query(Transcript.duration_seconds)
        .filter(Transcript.user_id == current_user.id, Transcript.status == "completed")
        .all()
    )
    total_minutes = sum(d[0] for d in total_duration if d[0]) / 60
    voice_count = db.query(VoiceProfile).filter(VoiceProfile.user_id == current_user.id).count()

    # Get active provider
    active_prov = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id, ProviderConfig.is_active == True  # noqa: E712
    ).first()

    return {
        "total_transcripts": total,
        "completed": completed,
        "processing": processing,
        "failed": failed,
        "total_minutes": round(total_minutes, 1),
        "voice_profiles": voice_count,
        "diarization_available": diarization_service._check_pyannote(),
        "voice_id_backend": voice_id_service._backend,
        "backend_name": voice_id_service.backend_name,
    }


# ── Serve Static Files ───────────────────────────────────────────────────

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 46)
    print("         WhisperDeck v0.6")
    print("  Transcribe - Diarize - Summarize - Identify")
    print("=" * 46)
    uvicorn.run("app:app", host="0.0.0.0", port=9781, reload=False)