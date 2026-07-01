"""
WhisperDeck — FastAPI Application

A modern meeting transcription & voice intelligence application.
Transcribe · Diarize · Summarize · Identify
"""
import os
import json
import datetime
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, Transcript, Summary, VoiceProfile, ProviderConfig
from services.transcription import TranscriptionService
from services.diarization import DiarizationService
from services.voice_id import VoiceIdentificationService
from services.audio_prep import transcode_for_upload, AudioPrepError
from backends import list_providers, get_provider

# ── App Setup ──────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
VOICES_DIR = DATA_DIR / "voices"
DB_PATH = DATA_DIR / "whisperdesk.db"

for d in [DATA_DIR, UPLOAD_DIR, TRANSCRIPT_DIR, VOICES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

engine, db_session = init_db(str(DB_PATH))
transcription_service = TranscriptionService(db_session, str(UPLOAD_DIR))
diarization_service = DiarizationService()
voice_id_service = VoiceIdentificationService(db_session, str(VOICES_DIR))

app = FastAPI(
    title="WhisperDeck",
    version="0.6.0",
    description="Modern meeting transcription & voice intelligence",
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


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_db():
    return db_session


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
async def get_providers():
    """List available providers with their metadata."""
    session = _get_db()
    providers = list_providers()
    # Merge in saved config status
    for p in providers:
        saved = session.query(ProviderConfig).filter(
            ProviderConfig.name == p["id"]
        ).first()
        if saved:
            p["configured"] = bool(saved.api_key)
            p["is_active"] = saved.is_active
        else:
            p["configured"] = False
            p["is_active"] = False
    return providers


@app.get("/api/providers/{name}")
async def get_provider_config(name: str):
    session = _get_db()
    cfg = session.query(ProviderConfig).filter(ProviderConfig.name == name).first()
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
async def update_provider_config(name: str, data: dict = Body(...)):
    session = _get_db()
    cfg = session.query(ProviderConfig).filter(ProviderConfig.name == name).first()
    if not cfg:
        cfg = ProviderConfig(name=name)
        session.add(cfg)

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

    session.commit()
    return {"ok": True, "name": name}


@app.get("/api/providers/{name}/models")
async def list_provider_models(name: str):
    """Fetch available transcription models for a given provider (live if possible)."""
    session = _get_db()
    from backends import get_provider, list_providers

    # Check provider exists
    known = [p["id"] for p in list_providers()]
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")

    # Get saved config
    cfg = session.query(ProviderConfig).filter(ProviderConfig.name == name).first()
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
            "groq": ["whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3"],
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

@app.post("/api/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    provider: str = Form("groq"),
    model: Optional[str] = Form(None),
    language: str = Form("en"),
    temperature: float = Form(0.0),
    diarize: bool = Form(False),
):
    """Upload and transcribe an audio file."""
    session = _get_db()

    # Save uploaded file
    file_ext = os.path.splitext(file.filename or "audio.mp3")[1] or ".mp3"
    safe_name = f"{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{hash(file.filename or 'audio')}{file_ext}"
    save_path = UPLOAD_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

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
    prov_cfg = session.query(ProviderConfig).filter(ProviderConfig.name == provider).first()
    provider_config = {}
    if prov_cfg:
        provider_config = {
            "api_key": prov_cfg.api_key,
            "api_url": prov_cfg.api_url,
            "default_model": prov_cfg.default_model or "",
        }

    try:
        transcript = await transcription_service.transcribe(
            audio_path=str(save_path),
            provider_name=provider,
            provider_config=provider_config,
            title=title or file.filename,
            language=language,
            model=model or provider_config.get("default_model"),
            temperature=temperature,
        )

        # Run diarization if requested
        if diarize and transcript.segments:
            try:
                if diarization_service._check_pyannote():
                    result = await diarization_service.diarize_pyannote(
                        str(save_path), num_speakers=2
                    )
                else:
                    result = await diarization_service.diarize_heuristic(
                        str(save_path),
                        num_speakers=2,
                        segments=transcript.segments,
                    )
                merged = await diarization_service.combine_with_transcript(
                    result, transcript.segments
                )
                transcript.segments = merged
                transcript.speaker_count = result.speaker_count
                session.commit()
            except Exception as e:
                # Non-fatal: diarization enhancement failed. Transcript still
                # succeeds without speaker labels, but log so it's visible.
                print(f"[diarization] non-fatal failure for transcript {transcript.id}: {e}")

        return _serialize_transcript(transcript)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/transcripts")
async def list_transcripts(limit: int = 50, offset: int = 0):
    session = _get_db()
    transcripts = (
        session.query(Transcript)
        .order_by(Transcript.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_serialize_transcript(t) for t in transcripts]


@app.get("/api/transcripts/{transcript_id}")
async def get_transcript(transcript_id: int):
    session = _get_db()
    t = session.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return _serialize_transcript(t)


@app.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: int):
    session = _get_db()
    t = session.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    session.delete(t)
    session.commit()
    return {"ok": True}


@app.patch("/api/transcripts/{transcript_id}")
async def update_transcript(transcript_id: int, data: dict = Body(...)):
    session = _get_db()
    t = session.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if "title" in data:
        t.title = data["title"]
    if "segments" in data:
        t.segments = data["segments"]
    if "full_text" in data:
        t.full_text = data["full_text"]
    t.updated_at = datetime.datetime.utcnow()
    session.commit()
    return _serialize_transcript(t)


# ── Diarization ───────────────────────────────────────────────────────────

@app.post("/api/diarize")
async def diarize_audio(
    file: UploadFile = File(...),
    num_speakers: int = Form(2),
    method: str = Form("heuristic"),
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
            result = await diarization_service.diarize_pyannote(
                str(save_path), num_speakers=num_speakers
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
):
    """Generate an LLM summary of a completed transcript."""
    session = _get_db()

    prov_cfg = session.query(ProviderConfig).filter(ProviderConfig.name == provider).first()
    api_key = prov_cfg.api_key if prov_cfg else ""

    try:
        summary = await transcription_service.summarize(
            transcript_id=transcript_id,
            api_key=api_key,
            provider_name=provider,
            provider_config={"api_key": api_key} if prov_cfg else {},
            model=model,
        )
        return _serialize_summary(summary)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/transcripts/{transcript_id}/summary")
async def get_summary(transcript_id: int):
    session = _get_db()
    summary = session.query(Summary).filter(Summary.transcript_id == transcript_id).first()
    if not summary:
        raise HTTPException(status_code=404, detail="No summary found")
    return _serialize_summary(summary)


# ── Voice Identification Database ─────────────────────────────────────────

@app.get("/api/voices")
async def list_voices():
    """List all enrolled voice profiles."""
    return voice_id_service.list_profiles()


@app.post("/api/voices/enroll")
async def enroll_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    notes: str = Form(""),
):
    """Enroll a new speaker from an audio sample."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"enroll_{name}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        profile = voice_id_service.enroll(name=name, audio_path=str(save_path), notes=notes)
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
):
    """Identify a speaker from an audio sample against enrolled profiles."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"ident_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        matches = voice_id_service.identify(str(save_path), threshold=threshold)
        return {
            "matches": matches,
            "total_profiles": len(voice_id_service.list_profiles()),
            "backend": voice_id_service._backend,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/voices/{profile_id}")
async def delete_voice_profile(profile_id: int):
    ok = voice_id_service.delete_profile(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    return {"ok": True}


# ── Frontend ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the SPA frontend."""
    index_path = BASE_DIR / "static" / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>WhisperDeck</h1><p>Frontend not built yet.</p>")


@app.get("/api/status")
async def full_status():
    """Return comprehensive app status for the frontend dashboard."""
    session = _get_db()
    total = session.query(Transcript).count()
    completed = session.query(Transcript).filter(Transcript.status == "completed").count()
    processing = session.query(Transcript).filter(Transcript.status == "processing").count()
    failed = session.query(Transcript).filter(Transcript.status == "failed").count()
    total_duration = (
        session.query(Transcript.duration_seconds)
        .filter(Transcript.status == "completed")
        .all()
    )
    total_minutes = sum(d[0] for d in total_duration if d[0]) / 60
    voice_count = session.query(VoiceProfile).count()

    # Get active provider
    active_prov = session.query(ProviderConfig).filter(ProviderConfig.is_active == True).first()  # noqa: E712

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