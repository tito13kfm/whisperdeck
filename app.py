"""
WhisperDeck — FastAPI Application

A modern meeting transcription & voice intelligence application.
Transcribe · Diarize · Summarize · Identify
"""
import os
import re
import hashlib
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
from fastapi.responses import Response, JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy import or_, func, case
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database import init_db, backfill_user_id, Transcript, Summary, VoiceNote, VoiceDumpItem, VoiceProfile, VoiceClip, ProviderConfig, User, LlmJob, TranscriptionJob, TranscriptTag, utcnow_naive
from services.auth import (
    get_or_create_fallback_user, create_user, authenticate_user, validate_password,
    verify_password,
    password_min_length, get_user_by_reset_token,
    list_usernames, generate_reset_token, reset_password,
    set_admin_status, get_all_users,
    set_device_token, revoke_device_token, get_user_by_device_token,
    registration_mode, generate_invite_token, get_valid_invite_token,
    consume_invite_token, mark_invite_used,
)
from services.settings import get_user_settings, update_user_settings
from services.transcription import TranscriptionService
from services.diarization import DiarizationService, MissingTokenError, degraded_error_text
from services.voice_id import voice_id_service
from services.audio_prep import transcode_for_upload, AudioPrepError, chunk_audio, get_audio_duration, extract_clips_concat, has_video_stream, transcode_stereo_for_diarization
from services.audio_cleanup import cleanup_audio, filter_hallucinations
from services.queue import (
    create_chunk_jobs, retry_failed_chunks, queue_worker_loop, compute_queue_status,
    cancel_transcript_jobs, resume_cancelled_chunks, reset_stuck_transcription_jobs,
    dismiss_transcript_queue_entry, clear_finished_transcript_queue_entries, get_rate_limit_gauge,
)
from services.hotwords import list_hotwords, add_hotword, delete_hotword
from services.correction import extract_hotwords_from_doc
from services.model_catalog import get_correction_models
from services.llm_jobs import (
    enqueue_llm_job, enqueue_auto_correction, enqueue_auto_classify, enqueue_auto_voice_note, enqueue_auto_voice_dump, enqueue_auto_tagging,
    enqueue_pipeline_classify,
    serialize_llm_job, latest_job,
    cancel_llm_job, rerun_llm_job, llm_worker_loop, reset_stuck_llm_jobs,
    dismiss_llm_job, clear_finished_llm_jobs,
)
from services.classification import effective_kind
from services.relabel import record_relabel, latest_relabel, clear_relabel_history, count_distinct_speakers, USER_ASSIGNED_CONFIDENCE
from backends import list_providers, get_provider, LOCAL_PROVIDERS
from services.security import (
    generate_csrf_token, rotate_csrf_token, validate_csrf_token,
    rate_limiter, encrypt_api_key, decrypt_api_key,
)
from services.search import search_transcripts, search_transcripts_snippets
from services.pricing import get_stt_rate
from services.cost import transcript_cost, provider_cost, estimate_cost

# ── App Setup ──────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
# Support the correctly-spelled WHISPERDECK_DATA_DIR, with a fallback to the
# legacy typo'd WHISPERDESK_DATA_DIR for backward compatibility with existing
# deploy scripts and the portable .bat launcher.  A deprecation warning is
# printed when only the old name is set so operators can migrate.
if os.environ.get("WHISPERDECK_DATA_DIR"):
    DATA_DIR = Path(os.environ["WHISPERDECK_DATA_DIR"])
elif os.environ.get("WHISPERDESK_DATA_DIR"):
    print(
        "[deprecation] WHISPERDESK_DATA_DIR is deprecated — "
        "rename to WHISPERDECK_DATA_DIR (note: DECK, not DESK)"
    )
    DATA_DIR = Path(os.environ["WHISPERDESK_DATA_DIR"])
else:
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

# Capture whether the DB file already exists *before* init_db(), which calls
# Base.metadata.create_all() and creates the file on a fresh install.  The
# data-safety warning should only fire when a pre-existing DB has zero users
# (misconfiguration / wrong DATA_DIR), not on a genuine first run.
_db_preexisted = DB_PATH.exists()
engine, SessionLocal, migrated_tables = init_db(str(DB_PATH))

# ── Data-safety guard: warn loudly if a pre-existing database has zero users.
#    This catches a fresh clone, a misconfigured DATA_DIR, or a database that
#    was silently replaced — all cases where the operator's transcripts may be
#    in a different (still-existing) database file the app isn't pointing at.
#    Skipped on a genuine first install (DB didn't exist before init_db).
_startup_db = SessionLocal()
try:
    _user_count = _startup_db.query(User).count()
    if _user_count == 0 and _db_preexisted:
        print(
            "\n" + "!" * 72 + "\n"
            "  WARNING: The database exists but contains zero user accounts.\n"
            f"  Path: {DB_PATH}\n"
            "  If you recently reinstalled or changed WHISPERDECK_DATA_DIR,\n"
            "  your operator accounts and transcripts may be in a different\n"
            "  location. Register a new account to start fresh, or point\n"
            "  WHISPERDECK_DATA_DIR at the correct data directory.\n" +
            "!" * 72 + "\n"
        )
    # Databases migrated before issue #302 was fixed carry the fallback
    # user with the publicly documented static password. New migrations
    # get a random password; existing installs get this warning on every
    # startup until the password is changed.
    _local_user = _startup_db.query(User).filter(User.username == "local").first()
    if _local_user and verify_password("changeme", _local_user.password_salt, _local_user.password_hash):
        print(
            "\n" + "!" * 72 + "\n"
            "  WARNING: the 'local' user still has the default password\n"
            "  'changeme'. Anyone who can reach this instance can sign in\n"
            "  as it. Log in and change it, or have an admin generate a\n"
            "  reset code from the Service panel. (issue #302)\n" +
            "!" * 72 + "\n"
        )
finally:
    _startup_db.close()

if migrated_tables:
    _migration_db = SessionLocal()
    try:
        _fallback_user, _fallback_password = get_or_create_fallback_user(_migration_db)
        backfill_user_id(engine, migrated_tables, _fallback_user.id)
        if _fallback_password:
            # The plaintext exists only here and in the recovery file
            # below (issue #302). Migration commonly runs headless where
            # stdout is rotated or discarded, and at migration time there
            # is usually no other account that could admin-reset 'local' —
            # without the file a missed log line would strand the entire
            # migrated library. Same trust domain as the SQLite DB beside it.
            _pw_file = DATA_DIR / "LOCAL_USER_PASSWORD.txt"
            _pw_file.write_text(
                "One-time password for the migrated 'local' user: "
                f"{_fallback_password}\n"
                "Log in, change the password, then delete this file.\n"
            )
            print(
                f"[migration] assigned {len(migrated_tables)} pre-existing table(s) "
                f"to fallback user 'local' (one-time password: {_fallback_password} "
                f"— also written to {_pw_file}; change it after logging in, "
                f"then delete that file)"
            )
        else:
            print(
                f"[migration] assigned {len(migrated_tables)} pre-existing table(s) "
                f"to existing fallback user 'local'"
            )
    finally:
        _migration_db.close()

transcription_service = TranscriptionService(str(UPLOAD_DIR))
diarization_service = DiarizationService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    _startup_db = SessionLocal()
    try:
        reset_stuck_transcription_jobs(_startup_db)
        reset_stuck_llm_jobs(_startup_db)
    finally:
        _startup_db.close()
    worker_task = asyncio.create_task(queue_worker_loop(SessionLocal, diarization_service))
    llm_worker_task = asyncio.create_task(llm_worker_loop(SessionLocal, transcription_service, diarization_service))
    yield
    worker_task.cancel()
    llm_worker_task.cancel()


app = FastAPI(
    title="WhisperDeck",
    version="0.8.0",
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

# CSRF-safe methods never mutate state, so they're exempt; every other
# /api/* request must carry a token matching the one issued by
# GET /api/csrf-token for its session — including /api/login and
# /api/register, since the SPA always fetches a token (anonymous session)
# before ever showing the login form (see rack.js checkAuth()).
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def enforce_csrf(request: Request, call_next):
    if request.method not in _CSRF_SAFE_METHODS and request.url.path.startswith("/api/"):
        # A request bearing a device's Authorization: Bearer token is not
        # cookie/session-authenticated, so it isn't CSRF-exploitable -- a
        # cross-origin page can't attach an Authorization header on the
        # victim's behalf the way it can rely on an ambient cookie. Skip
        # the CSRF check only for the one route that actually honors a
        # bearer token for auth (/api/transcribe); every other /api/*
        # route ignores the header entirely, so exempting them here would
        # only widen the CSRF-skip surface for no reason. Whether the
        # token is actually valid is decided downstream by whichever auth
        # dependency the route uses (still 401s on a bad or unhonored token).
        has_bearer = (
            request.url.path == "/api/transcribe"
            and (request.headers.get("authorization") or "").lower().startswith("bearer ")
        )
        if not has_bearer:
            csrf = request.headers.get("x-csrf-token") or ""
            if not validate_csrf_token(request.session, csrf):
                # This literal string is matched by the client retry logic in rack.js api().
                # Keep the two in sync.
                return JSONResponse(status_code=403, content={"detail": "Invalid or missing CSRF token"})
    return await call_next(request)


async def static_cache_headers(request: Request, call_next):
    """Add Cache-Control headers for static assets and index.html (issue #140).

    /static/*  → Cache-Control: public, max-age=3600 (browser caches for 1 hour)
    GET /      → Cache-Control: no-cache (browser revalidates every time, allows 304)
    /sw.js     → Cache-Control: no-cache (issue #146, so deploys reach clients
                 promptly instead of the browser pinning a stale worker script)
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif request.method == "GET" and request.url.path in ("/", "/sw.js"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Starlette's add_middleware() prepends to the stack, so the *last* middleware
# added here runs *first* on each request — enforce_csrf must therefore be
# registered before SessionMiddleware so that, at request time, Session runs
# first (populating request.session) and enforce_csrf runs second (reading
# it). Registering these two in the other order raises "SessionMiddleware
# must be installed to access request.session" even though it is.
app.add_middleware(BaseHTTPMiddleware, dispatch=enforce_csrf)
# CSRF posture (issue #36, supersedes the SameSite-only posture from #32):
# every session-cookie-authenticated mutation validates X-CSRF-Token via
# enforce_csrf above, independent of browser SameSite behavior. Pin lax
# explicitly anyway so a Starlette default change can't silently weaken the
# defense-in-depth layer.
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")
# Static asset caching (issue #140): add Cache-Control headers after the
# response is produced but before GZip compression.
app.add_middleware(BaseHTTPMiddleware, dispatch=static_cache_headers)
# GZip compression for responses > 500 bytes. Added last (outermost in
# middleware stack) so it compresses the final response after all other
# middleware has processed it.
app.add_middleware(GZipMiddleware, minimum_size=500)


# WAL mode serializes writers: two connections both trying to write take
# turns, and the loser waits out PRAGMA busy_timeout (5000ms,
# database/__init__.py) before SQLAlchemy raises OperationalError wrapping
# sqlite3's "database is locked". That UPDATE never took the lock, so the
# write did not happen -- retrying is safe, not just harmless. An audit for
# issue #391 found ~27 write endpoints that would otherwise let this escape
# as a bare 500, so this is one handler covering all of them instead of a
# try/except bolted onto each. Non-"is locked" OperationalErrors (disk I/O
# errors, etc.) are re-raised and still surface as 500s.
@app.exception_handler(OperationalError)
async def handle_db_locked(request: Request, exc: OperationalError):
    if "is locked" not in str(exc.orig or exc):
        raise exc
    return JSONResponse(
        status_code=409,
        content={"detail": "The database is busy with another write; please retry."},
        headers={"Retry-After": "1"},
    )


# ── Helpers ────────────────────────────────────────────────────────────────

def get_db():
    """Per-request DB session — one session per request, closed when the
    request finishes, instead of one shared session for the whole app."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _resolve_session_user(request: Request, db: Session) -> User | None:
    """Look up the user for the current session, clearing a stale session
    (one whose user_id no longer maps to a User row) as a side effect."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.session.clear()
        return None
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = _resolve_session_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


def _resolve_device_token_user(request: Request, db: Session) -> User | None:
    """Look up the user for a device bearer token, if the request carries
    one. Kept separate from _resolve_session_user because this path is
    only trusted on routes that explicitly opt in via
    get_current_user_or_device below, not on get_current_user itself."""
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[len("bearer "):].strip()
    return get_user_by_device_token(db, token)


def get_current_user_or_device(request: Request, db: Session = Depends(get_db)) -> User:
    """Auth dependency for the one route that must also accept a device's
    bearer token. Session cookie is tried first so a logged-in browser tab
    is unaffected; the bearer token is the fallback for a headless caller
    with no cookie jar. Deliberately not the default get_current_user,
    since every other route keeps session-only auth."""
    user = _resolve_session_user(request, db)
    if user:
        request.state.device_authenticated = False
        return user
    user = _resolve_device_token_user(request, db)
    if user:
        request.state.device_authenticated = True
        return user
    raise HTTPException(status_code=401, detail="Not logged in")


# `rediarize` is in services.llm_jobs.VALID_KINDS but the serializer
# doesn't consume it; keeping it out of the batch filter avoids fetching
# rows that would just be discarded.
_SERIALIZED_JOB_KINDS = (
    "correction", "summary", "voice_match",
    "format_markdown", "format_email", "format_coding_prompt", "classify_intent",
    "voice_note", "voice_dump", "tagging", "assistant", "classify_pipeline",
)


def _batch_latest_jobs(db: Session, transcript_ids: list[int]) -> dict[tuple[int, str], LlmJob]:
    """Return the latest LlmJob row per (transcript_id, kind). Missing
    pairs (no row exists for that transcript+kind) are simply absent from
    the dict; callers use `jobs_map.get((tid, kind))`."""
    if not transcript_ids:
        return {}
    max_id_subq = (
        db.query(
            LlmJob.transcript_id.label("tid"),
            LlmJob.kind.label("kind"),
            func.max(LlmJob.id).label("max_id"),
        )
        .filter(
            LlmJob.transcript_id.in_(transcript_ids),
            LlmJob.kind.in_(_SERIALIZED_JOB_KINDS),
        )
        .group_by(LlmJob.transcript_id, LlmJob.kind)
        .subquery()
    )
    jobs = (
        db.query(LlmJob)
        .join(max_id_subq, LlmJob.id == max_id_subq.c.max_id)
        .all()
    )
    return {(j.transcript_id, j.kind): j for j in jobs}


def _serialize_transcript(db: Session, t: Transcript, *, jobs_map: dict[tuple[int, str], LlmJob], include_relabel: bool = False) -> dict:
    jobs = t.jobs or []
    job_progress = None
    if jobs:
        job_progress = {
            "total": len(jobs),
            "completed": sum(1 for j in jobs if j.status == "completed"),
            "failed": sum(1 for j in jobs if j.status == "failed"),
        }
    data = {
        "id": t.id,
        "source_transcript_id": t.source_transcript_id,
        "batch_id": t.batch_id or None,
        "kind": t.kind or "meeting",
        "classification_status": t.classification_status or "override",
        "classification_confidence": t.classification_confidence,
        "classification_provenance": t.classification_provenance,
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
        "diarization_method": t.diarization_method,
        "num_speakers": t.num_speakers,
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
        "has_video": bool(t.video_path and os.path.exists(t.video_path)),
        "job_progress": job_progress,
        "processed_size_bytes": t.processed_size_bytes,
        "queue_status": compute_queue_status(db, t),
        "correction_job": serialize_llm_job(jobs_map[(t.id, "correction")]) if (t.id, "correction") in jobs_map else None,
        "summary_job": serialize_llm_job(jobs_map[(t.id, "summary")]) if (t.id, "summary") in jobs_map else None,
        # include_result: the voice-match similarity summary is small and the
        # detail view renders it after the job finishes (issue #311). No other
        # kind opts in — their result_json holds whole documents.
        "voice_match_job": serialize_llm_job(jobs_map[(t.id, "voice_match")], include_result=True) if (t.id, "voice_match") in jobs_map else None,
        "classify_pipeline_job": serialize_llm_job(jobs_map[(t.id, "classify_pipeline")]) if (t.id, "classify_pipeline") in jobs_map else None,
        "cost": transcript_cost(db, t),
        "tags": _tags_for_transcript(db, t.id),
        **_dictation_job_fields(jobs_map, t),
    }
    if include_relabel:
        last = latest_relabel(db, t.id)
        data["last_relabel"] = (
            {"kind": last.kind, "description": last.description} if last else None
        )
    return data


def _dictation_job_fields(jobs_map: dict[tuple[int, str], LlmJob], t: Transcript) -> dict:
    """format_*_job / classify_intent_job / classify_intent_hint /
    voice_note_job / tagging_job — gated on effective_kind() (design
    decision 11), but every field is always present (null for kinds that
    can never have that job). The shape is uniform across all kinds so the
    frontend doesn't have to switch on kind to read the response
    (test_all_kinds_have_same_job_field_names pins this). `tagging_job` is
    uniform because tagging runs on every kind, not just one."""
    tagging_job = jobs_map.get((t.id, "tagging"))
    kind = effective_kind(t)
    if kind == "dictation":
        classify_job = jobs_map.get((t.id, "classify_intent"))
        return {
            "format_markdown_job": serialize_llm_job(jobs_map[(t.id, "format_markdown")]) if (t.id, "format_markdown") in jobs_map else None,
            "format_email_job": serialize_llm_job(jobs_map[(t.id, "format_email")]) if (t.id, "format_email") in jobs_map else None,
            "format_coding_prompt_job": serialize_llm_job(jobs_map[(t.id, "format_coding_prompt")]) if (t.id, "format_coding_prompt") in jobs_map else None,
            "classify_intent_job": serialize_llm_job(classify_job) if classify_job else None,
            # Auto-computed suggestion — a UI hint for which format button to
            # highlight, not a gate on any of them.
            "classify_intent_hint": (
                classify_job.result_json.get("format")
                if classify_job and classify_job.status == "completed" and classify_job.result_json
                else None
            ),
            "voice_note_job": None,
            "voice_dump_job": None,
            "tagging_job": serialize_llm_job(tagging_job) if tagging_job else None,
        }
    if kind == "voice_note":
        vn_job = jobs_map.get((t.id, "voice_note"))
        return {
            "format_markdown_job": None, "format_email_job": None, "format_coding_prompt_job": None,
            "classify_intent_job": None, "classify_intent_hint": None,
            "voice_note_job": serialize_llm_job(vn_job) if vn_job else None,
            "voice_dump_job": None,
            "tagging_job": serialize_llm_job(tagging_job) if tagging_job else None,
        }
    if kind == "voice_dump":
        vd_job = jobs_map.get((t.id, "voice_dump"))
        return {
            "format_markdown_job": None, "format_email_job": None, "format_coding_prompt_job": None,
            "classify_intent_job": None, "classify_intent_hint": None,
            "voice_note_job": None,
            "voice_dump_job": serialize_llm_job(vd_job) if vd_job else None,
            "tagging_job": serialize_llm_job(tagging_job) if tagging_job else None,
        }
    return {
        "format_markdown_job": None, "format_email_job": None, "format_coding_prompt_job": None,
        "classify_intent_job": None, "classify_intent_hint": None,
        "voice_note_job": None,
        "voice_dump_job": None,
        "tagging_job": serialize_llm_job(tagging_job) if tagging_job else None,
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
        "provider": s.provider,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ── Auth ──────────────────────────────────────────────────────────────────

@app.get("/api/csrf-token")
async def csrf_token(request: Request):
    """Return the session's CSRF token for the X-CSRF-Token header.
    Generates one on first call per session; subsequent calls return the same token.
    Login and register rotate the token for session-fixation protection.
    The frontend caches it and re-fetches on auth state changes."""
    token = generate_csrf_token(request.session)
    return {"token": token}


@app.post("/api/register")
async def register(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else None
    # Skip rate limiting for TestClient (no real client IP) so tests don't
    # share a single bucket across the whole suite.
    if client_ip and not rate_limiter.check(f"register:{client_ip}", max_requests=5, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many registration attempts — try again later")
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    # Registration gate (issue #395). Server-side by design — hiding the
    # register form in the SPA is chrome, this check is the contract.
    mode = registration_mode(db)
    invite_token = (data.get("invite_token") or "").strip()
    if mode == "closed":
        raise HTTPException(status_code=403, detail="Registration is closed")
    if mode == "invite":
        if not invite_token:
            raise HTTPException(status_code=400, detail="An invite token is required to register")
        # Validity peek BEFORE username/password errors, mirroring the
        # reset-password ordering convention.
        if not get_valid_invite_token(db, invite_token):
            raise HTTPException(status_code=400, detail="Invalid or expired invite token")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    ok, reason = validate_password(password)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    if mode == "invite":
        # CAS-consume immediately before create_user; a loser between the
        # peek above and here gets the same message. Uncommitted until
        # create_user's commit finalizes both atomically.
        if not consume_invite_token(db, invite_token):
            raise HTTPException(status_code=400, detail="Invalid or expired invite token")
    try:
        user = create_user(db, username, password)
    except IntegrityError:
        # Concurrent registrations can both pass the SELECT-then-INSERT
        # check above; the loser's commit hits users.username UNIQUE.
        # Rollback to keep the session usable, then return the same 400
        # the synchronous path returns. Issue #125. The rollback also
        # un-consumes the invite token above — load-bearing, keep them in
        # the same transaction.
        db.rollback()
        raise HTTPException(status_code=400, detail="Username already taken")
    if mode == "invite":
        mark_invite_used(db, invite_token, user.id)
    request.session["user_id"] = user.id
    rotate_csrf_token(request.session)
    return {"ok": True, "username": user.username}


@app.post("/api/login")
async def login(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else None
    if client_ip and not rate_limiter.check(f"login:{client_ip}", max_requests=10, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too many login attempts — try again later")
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    # Per-account failure throttle (issue #124), keyed on (username, client):
    # - failures only, so successful logins never consume a slot;
    # - peek() before authenticating, so a full bucket 429s even for the
    #   correct password (no validity oracle);
    # - the username is hashed into the key so attacker-chosen input cannot
    #   control key size;
    # - scoped by client IP: a pure per-username key would let anyone lock
    #   an arbitrary account out of login indefinitely with a trickle of
    #   wrong passwords (usernames are enumerable by design) — scoping it
    #   means an attacker only ever fills their own bucket. Cross-IP
    #   distributed guessing remains bounded by the IP bucket above plus
    #   PBKDF2 cost per attempt.
    # Applies under TestClient too (protects the account, not the client);
    # tests rely on the existing bucket-clearing fixtures.
    user_key = None
    if username:
        user_digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:32]
        user_key = f"login-user:{user_digest}:{client_ip or 'local'}"
        if not rate_limiter.peek(user_key, max_requests=5, window_seconds=300):
            raise HTTPException(status_code=429, detail="Too many failed attempts for this account — try again later")
    user = authenticate_user(db, username, password)
    if not user:
        if user_key:
            rate_limiter.record(user_key)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    request.session["user_id"] = user.id
    rotate_csrf_token(request.session)
    return {"ok": True, "username": user.username}


@app.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(current_user: User = Depends(get_current_user)):
    return {"username": current_user.username, "is_admin": bool(current_user.is_admin)}


# ── /api/bootstrap ─────────────────────────────────────────────────────────


def _build_status_payload(db: Session, current_user: User) -> dict:
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
    voice_note_count = db.query(VoiceNote).filter(VoiceNote.user_id == current_user.id).count()
    voice_dump_unseen = db.query(VoiceDumpItem).filter(
        VoiceDumpItem.user_id == current_user.id, VoiceDumpItem.seen_at == None  # noqa: E711
    ).count()

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
        "voice_notes": voice_note_count,
        "voice_dump_unseen": voice_dump_unseen,
        "diarization_available": diarization_service._check_pyannote(),
        "voice_id_backend": voice_id_service._backend,
        "backend_name": voice_id_service.backend_name,
    }

def _tags_for_transcripts(db: Session, transcript_ids: list[int]) -> dict[int, list[str]]:
    """Batch-load tag strings for a list of transcripts. Returns a dict
    keyed by transcript_id, value is the tag list in insertion order
    (the tagging job writes in prompt-order, no need to sort). Used by
    the list view to avoid the N+1 trap of one query per row."""
    if not transcript_ids:
        return {}
    rows = (
        db.query(TranscriptTag)
        .filter(TranscriptTag.transcript_id.in_(transcript_ids))
        .all()
    )
    out: dict[int, list[str]] = {tid: [] for tid in transcript_ids}
    for row in rows:
        out.setdefault(row.transcript_id, []).append(row.tag)
    return out


def _tags_for_transcript(db: Session, transcript_id: int) -> list[str]:
    """Single-transcript variant for the detail serializer. The detail
    view serializes one row at a time, so a per-row query is fine and
    avoids threading a tags map through the call chain."""
    rows = (
        db.query(TranscriptTag.tag)
        .filter(TranscriptTag.transcript_id == transcript_id)
        .order_by(TranscriptTag.created_at.asc())
        .all()
    )
    return [r[0] for r in rows]


def _serialize_transcript_summary(db: Session, t: Transcript, tags: list[str] | None = None,
                                  cost_map: dict[int, float] | None = None) -> dict:
    """Lightweight transcript payload for list/dashboard views. Omits
    full_text, segments, corrected_text, and per-kind LLM job details —
    every field retained here is consumed by at least one frontend row
    renderer (statusView / transcriptPct / transcriptMeta / bankDetailFields).

    cost_map, when provided, supplies a pre-computed STT cost per transcript_id
    so that list views carry a cost value with zero additional DB queries."""
    jobs = t.jobs or []
    job_progress = None
    if jobs:
        job_progress = {
            "total": len(jobs),
            "completed": sum(1 for j in jobs if j.status == "completed"),
            "failed": sum(1 for j in jobs if j.status == "failed"),
        }
    data = {
        "id": t.id,
        "batch_id": t.batch_id or None,
        "kind": t.kind or "meeting",
        "title": t.title,
        "filename": t.filename,
        "status": t.status,
        "duration_seconds": t.duration_seconds,
        "provider": t.provider,
        "model": t.model,
        "language": t.language,
        "speaker_count": t.speaker_count,
        "diarize_requested": t.diarize_requested,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "queue_status": compute_queue_status(db, t),
        "job_progress": job_progress,
        "cost": cost_map.get(t.id) if cost_map else None,
        "tags": tags if tags is not None else [],
    }
    return data


def _batch_stt_costs(transcripts: list[Transcript]) -> dict[int, float]:
    """Compute STT cost for a batch of transcripts from already-loaded fields.
    Zero additional DB queries — rates come from the in-memory dict in pricing.py."""
    costs: dict[int, float] = {}
    for t in transcripts:
        if not t.duration_seconds or not t.provider:
            costs[t.id] = 0.0
            continue
        rate_info = get_stt_rate(t.provider, t.model or "")
        costs[t.id] = rate_info["rate_per_minute"] * (t.duration_seconds / 60.0)
    return costs


def _build_recent_transcripts(db: Session, current_user: User, limit: int, offset: int = 0, query: str | None = None, batch_id: str | None = None) -> list:
    if query:
        search_results = search_transcripts(db, current_user.id, query)
        matching_ids = [r["transcript_id"] for r in search_results]
        if not matching_ids:
            return []
        q = db.query(Transcript).filter(Transcript.id.in_(matching_ids))
        if batch_id:
            q = q.filter(Transcript.batch_id == batch_id)
        transcripts = q.all()
        id_order = {tid: i for i, tid in enumerate(matching_ids)}
        transcripts.sort(key=lambda t: id_order.get(t.id, len(matching_ids)))
        paged = transcripts[offset:offset + limit]
        tags_map = _tags_for_transcripts(db, [t.id for t in paged])
        cost_map = _batch_stt_costs(paged)
        return [_serialize_transcript_summary(db, t, tags=tags_map.get(t.id, []), cost_map=cost_map) for t in paged]

    q = (
        db.query(Transcript)
        .filter(Transcript.user_id == current_user.id)
    )
    if batch_id:
        q = q.filter(Transcript.batch_id == batch_id)
    transcripts = (
        q.order_by(Transcript.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    tags_map = _tags_for_transcripts(db, [t.id for t in transcripts])
    cost_map = _batch_stt_costs(transcripts)
    return [_serialize_transcript_summary(db, t, tags=tags_map.get(t.id, []), cost_map=cost_map) for t in transcripts]


def _build_jobs_payload(db: Session, current_user: User, limit: int) -> dict:
    llm = (
        db.query(LlmJob)
        .filter(LlmJob.user_id == current_user.id, LlmJob.dismissed.is_(False))
        .order_by(LlmJob.id.desc())
        .limit(limit)
        .all()
    )
    transcripts = (
        db.query(Transcript)
        .filter(Transcript.user_id == current_user.id, Transcript.queue_dismissed.is_(False))
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
    return {
        "jobs": entries[:limit],
        "active": active,
        "rate_limit_gauge": get_rate_limit_gauge(db, current_user.id, "groq"),
    }


@app.get("/api/bootstrap")
async def bootstrap(request: Request, db: Session = Depends(get_db)):
    """One-shot boot payload for the frontend's initial dashboard render.

    Returns the CSRF token, the current user (or null if signed out), the
    registration mode (issue #395, top-level so anonymous callers get it),
    and every piece of data the Monitor page needs on first paint: full
    status, the five most recent transcripts, and the active jobs. Cuts the
    boot waterfall from 4-5 sequential requests to one (issue #143).
    Resolves the user from the session manually so the unauthenticated path
    returns a clean shape instead of 401.
    """
    csrf = generate_csrf_token(request.session)

    user_payload = None
    status_payload = None
    recents_payload: list = []
    jobs_payload = {"jobs": [], "active": 0}

    user = _resolve_session_user(request, db)
    if user:
        user_payload = {"username": user.username, "is_admin": bool(user.is_admin)}
        status_payload = _build_status_payload(db, user)
        recents_payload = _build_recent_transcripts(db, user, limit=5)
        jobs_payload = _build_jobs_payload(db, user, limit=20)
        from services.settings import get_user_settings
        settings_payload = get_user_settings(db, user.id)
    else:
        settings_payload = None

    return {
        "csrf_token": csrf,
        "user": user_payload,
        "status": status_payload,
        "recent_transcripts": recents_payload,
        "jobs": jobs_payload,
        "settings": settings_payload,
        # Top-level (not inside settings, which is None when signed out):
        # the anonymous login screen derives register-form visibility from
        # this, the same state the server enforces (issue #395).
        "registration_mode": registration_mode(db),
    }


@app.get("/favicon.ico")
async def favicon():
    """Return 204 for missing favicon to silence browser console noise."""
    return Response(status_code=204)


# ── Account Recovery ──────────────────────────────────────────────────────


@app.post("/api/forgot-username")
async def forgot_username(request: Request, db: Session = Depends(get_db)):
    """Self-service: return every registered username so the user can
    identify their own account. Rate-limited to prevent enumeration per
    client IP, but usernames aren't secrets in a self-hosted app."""
    client_ip = request.client.host if request.client else None
    if client_ip and not rate_limiter.check(f"forgot-username:{client_ip}", max_requests=5, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many requests — try again later")
    return {"usernames": list_usernames(db)}


@app.post("/api/forgot-password")
async def forgot_password(request: Request, data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Admin-only: generate a one-time reset token for any user.
    The token is returned directly (no email) — the admin shares it with
    the affected user out-of-band."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only administrators can generate password reset tokens")
    client_ip = request.client.host if request.client else None
    if client_ip and not rate_limiter.check(f"forgot-password:{client_ip}", max_requests=10, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")
    target_username = (data.get("username") or "").strip()
    if not target_username:
        raise HTTPException(status_code=400, detail="username is required")
    token = generate_reset_token(db, current_user, target_username)
    if token is None:
        raise HTTPException(status_code=404, detail="User not found")
    expires_at = db.query(User).filter(User.username == target_username).first().reset_token_expires_at
    return {"reset_token": token, "expires_at": expires_at.isoformat() if expires_at else None}


@app.post("/api/reset-password")
async def reset_password_route(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    """Reset a password using a valid one-time token. Auto-logs in on success."""
    client_ip = request.client.host if request.client else None
    if client_ip and not rate_limiter.check(f"reset-password:{client_ip}", max_requests=5, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")
    token = (data.get("token") or "").strip()
    new_password = data.get("new_password") or ""
    if not token or not new_password:
        raise HTTPException(status_code=400, detail="token and new_password are required")
    # Check token validity BEFORE password policy so a bad token + weak
    # password reports the token error, not the password error.
    if not get_user_by_reset_token(db, token):
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    ok, reason = validate_password(new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    user = reset_password(db, token, new_password)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    request.session["user_id"] = user.id
    rotate_csrf_token(request.session)
    return {"ok": True, "username": user.username}


# ── Admin User Management ─────────────────────────────────────────────────


@app.get("/api/admin/users")
async def admin_users(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Admin-only: list all users with admin status."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return get_all_users(db)


@app.post("/api/admin/promote")
async def admin_promote(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Admin-only: grant admin status to another user."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    target_username = (data.get("username") or "").strip()
    if not target_username:
        raise HTTPException(status_code=400, detail="username is required")
    target = set_admin_status(db, current_user, target_username, is_admin=True)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True, "username": target.username, "is_admin": bool(target.is_admin)}


@app.post("/api/admin/invites")
async def admin_create_invite(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Admin-only: mint a single-use registration invite token (issue #395).
    The plaintext is returned once, same convention as /api/forgot-password;
    only its hash is stored."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    client_ip = request.client.host if request.client else None
    if client_ip and not rate_limiter.check(f"invite-mint:{client_ip}", max_requests=10, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")
    minted = generate_invite_token(db, current_user)
    if minted is None:
        raise HTTPException(status_code=403, detail="Administrator access required")
    token, expires_at = minted
    return {"invite_token": token, "expires_at": expires_at.isoformat()}


@app.post("/api/admin/demote")
async def admin_demote(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Admin-only: revoke admin status from another user (cannot self-demote)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    target_username = (data.get("username") or "").strip()
    if not target_username:
        raise HTTPException(status_code=400, detail="username is required")
    target = set_admin_status(db, current_user, target_username, is_admin=False)
    if not target:
        raise HTTPException(status_code=400, detail="Cannot demote yourself or user not found")
    return {"ok": True, "username": target.username, "is_admin": bool(target.is_admin)}


# ── Settings ──────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_user_settings(db, current_user.id)


@app.put("/api/settings")
async def put_settings(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return update_user_settings(db, current_user.id, data)


@app.post("/api/settings/device-token")
async def generate_device_token_route(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate (or regenerate) this user's device bearer token. Returns
    the plaintext token once; only its hash is ever stored."""
    token = set_device_token(db, current_user)
    return {"token": token, "created_at": current_user.local_device_token_created_at.isoformat()}


@app.get("/api/settings/device-token")
async def device_token_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {
        "has_token": bool(current_user.local_device_token_hash),
        "created_at": current_user.local_device_token_created_at.isoformat() if current_user.local_device_token_created_at else None,
    }


@app.delete("/api/settings/device-token")
async def revoke_device_token_route(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    revoke_device_token(db, current_user)
    return {"ok": True}


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
        "version": "0.8.0",
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
        if not p["needs_key"]:
            # Local providers: "no key needed" isn't "ready" — the optional
            # package (faster-whisper, moonshine-voice) might not be
            # installed. Probe check_health instead of trusting needs_key,
            # so the badge doesn't lie. Hosted providers skip this: their
            # health check would be a network call on every page load.
            try:
                health = await get_provider(p["id"], {}).check_health()
                p["configured"] = bool(health.get("ok"))
            except Exception:
                p["configured"] = False
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

    if "api_key" in data:
        if data["api_key"] and not data["api_key"].startswith("••••"):
            cfg.api_key = encrypt_api_key(data["api_key"], SESSION_SECRET)
        elif not data["api_key"]:
            cfg.api_key = ""
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
            "builtin": ["tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large-v3-turbo"],
            "moonshine": ["tiny", "tiny-streaming", "base", "small-streaming", "medium-streaming"],
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
    auto_correct: Optional[bool] = None,
    num_speakers: Optional[int],
    source_transcript_id: Optional[int] = None,
    batch_id: Optional[str] = None,
    kind: str = "meeting",
    capture_source: Optional[str] = None,
) -> dict:
    """Everything after the source audio is on disk: transcode decision,
    chunk-vs-inline branch, inline diarization, auto-correct enqueue.
    Shared by /api/transcribe (fresh upload) and
    /api/transcripts/{id}/retranscribe (stored audio_path).

    source_transcript_id is set on the transcript row before its first
    commit in both branches — not patched on afterward — so a version-chain
    link is never left missing by an exception raised later in the same
    request (chunk-job creation, diarization, auto-correct enqueue).

    Dictation transcripts are always single-speaker by definition (the
    summarize prompt and reformatting features assume it) — diarize is
    forced off here, server-side, rather than trusting the client to have
    sent diarize=false. Enforced once at this convergence point so it can't
    be bypassed by calling either entry point directly."""
    # "auto" (design decision 11) defers kind to the pipeline classifier —
    # store a placeholder kind (never read while classification_status is
    # "pending", see effective_kind()) and leave classification_status at
    # its "pending" starting point. An explicit kind is a manual override,
    # recorded as such rather than left to the column default so callers of
    # this function never need to know the default happens to agree.
    #
    # This function is also the retranscribe entry point (source_transcript_id
    # is set only there). Since #271, retranscribe may pass kind="auto" when
    # the source transcript was auto-classified (success/uncertain/failed);
    # that hits the if kind=="auto" branch above and gets
    # classification_status="pending" for re-classification. When retranscribe
    # passes an explicit kind (override/legacy source), it falls through to
    # classification_status=None here — column default "override" — carrying
    # the user's explicit choice forward unchanged (design decision 9).
    # Non-retranscribe callers always pass an explicit kind and land
    # classification_status="override" above.
    is_retranscribe = source_transcript_id is not None
    if kind == "auto":
        classification_status = "pending"
        kind = "meeting"
    elif not is_retranscribe:
        classification_status = "override"
    else:
        classification_status = None  # leave column default; #271's territory
    if kind in ("dictation", "voice_note", "voice_dump"):
        diarize = False
    if capture_source not in (None, "live_stereo"):
        capture_source = None  # unknown values from stale clients are ignored, not errors
    user_settings = get_user_settings(db, current_user.id)

    # Normalize for cloud upload: strips video track, downsamples to 16kHz
    # mono (all Whisper providers resample to this internally anyway). Fixes
    # "file too large" errors on video uploads and long recordings. Builtin
    # runs locally with no upload limit, so skip the extra transcode there —
    # unless the container is one libsndfile can't open (browser live capture
    # produces webm/opus), in which case local providers need the ffmpeg
    # pass too or soundfile fails with "Format not recognised".
    local_readable_exts = {".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"}

    # Capture the raw upload's path/extension before the needs_transcode
    # branch below reassigns save_path to a transcoded (audio-only) output.
    # A retranscribe inherits its parent's video_path without re-probing —
    # the stored audio_path already went through this decision once.
    raw_path = save_path
    if source_transcript_id is not None:
        parent = db.query(Transcript).filter(
            Transcript.id == source_transcript_id, Transcript.user_id == current_user.id
        ).first()
        video_path = parent.video_path if parent else None
        # A retranscribe re-enters the pipeline with the stored mono mp3 as
        # input, so capture_source will be None and no new stereo copy would
        # be produced below — carry the parent's forward instead.
        stereo_audio_path_inherited = parent.stereo_audio_path if parent else None
    else:
        playable = raw_path.suffix.lower() in _VIDEO_MIME
        video_path = str(raw_path) if playable and has_video_stream(str(raw_path)) else None
        stereo_audio_path_inherited = None

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

    # Audio cleanup stage (issue #270): opt-in loudnorm/denoise/highpass chain.
    # Runs after transcode so the filters see the normalized 16kHz mono input.
    # Non-fatal: any filter failure falls back to the unprocessed audio — the
    # pipeline proceeds regardless.
    try:
        cleanup_result = await cleanup_audio(str(save_path), str(UPLOAD_DIR), user_settings)
        save_path = Path(cleanup_result.audio_path)
    except Exception as e:
        print(f"[audio-cleanup] non-fatal failure: {e}")

    stereo_audio_path = stereo_audio_path_inherited
    if capture_source == "live_stereo":
        try:
            stereo_audio_path = await transcode_stereo_for_diarization(str(raw_path), str(UPLOAD_DIR))
        except Exception as e:
            # Non-fatal: fall back to mixed-audio diarization rather than
            # failing the whole upload over the enhancement copy.
            print(f"[audio-prep] stereo copy failed, using mixed audio: {e}")

    stereo_persisted = False

    def _discard_stereo_copy():
        # A failure below, before the path is persisted onto a transcript
        # row, would otherwise strand the FLAC as an orphan in the upload
        # dir. Only remove a copy created by THIS request (an inherited
        # path belongs to the source transcript of a re-transcribe) and
        # only while no committed row references it yet.
        if (
            not stereo_persisted
            and stereo_audio_path
            and stereo_audio_path != stereo_audio_path_inherited
        ):
            try:
                os.remove(stereo_audio_path)
            except OSError:
                pass

    # Get provider config — decrypts API key transparently
    from services.settings import _decrypt_key_if_needed
    prov_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == provider,
    ).first()
    provider_config = {}
    if prov_cfg:
        raw_key = prov_cfg.api_key or ""
        decrypted_key = _decrypt_key_if_needed(raw_key, SESSION_SECRET)
        provider_config = {
            "api_key": decrypted_key,
            "api_url": prov_cfg.api_url or "",
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
            _discard_stereo_copy()
            raise HTTPException(status_code=500, detail=str(e))
        except Exception:
            _discard_stereo_copy()
            raise

        if not chunks:
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
                video_path=video_path,
                kind=kind,
            )
            transcript.segments = []
            transcript.full_text = ""
            transcript.status = "completed"
            transcript.processed_size_bytes = file_size
            transcript.source_transcript_id = source_transcript_id
            transcript.batch_id = batch_id
            transcript.kind = kind
            if classification_status is not None:
                transcript.classification_status = classification_status
            transcript.num_speakers = num_speakers
            transcript.stereo_audio_path = stereo_audio_path
            transcript.duration_seconds = duration_seconds
            db.commit()
            stereo_persisted = True
            return _serialize_transcript(db, transcript, jobs_map={})

        try:
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
                video_path=video_path,
                kind=kind,
            )
            # Real processed size, not the raw upload size — the sum of all
            # chunk files, since that's what actually gets sent to the provider.
            transcript.processed_size_bytes = sum(os.path.getsize(c["path"]) for c in chunks)
            transcript.stereo_audio_path = stereo_audio_path
            # Known now from the ffprobe above — lets the UI say "48-min
            # recording" before the first chunk lands.
            transcript.duration_seconds = duration_seconds
            transcript.source_transcript_id = source_transcript_id
            transcript.batch_id = batch_id
            if classification_status is not None:
                transcript.classification_status = classification_status
            db.commit()
        except Exception:
            _discard_stereo_copy()
            raise
        stereo_persisted = True
        create_chunk_jobs(db, transcript.id, chunks)
        return _serialize_transcript(db, transcript, jobs_map=_batch_latest_jobs(db, [transcript.id]))

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
            video_path=video_path,
            vad_filter=user_settings.get("cleanup_vad_enabled", True),
            vad_threshold=user_settings.get("cleanup_vad_threshold", 0.5),
            vad_min_silence_duration_ms=user_settings.get("cleanup_vad_min_silence_ms", 100),
        )
        transcript.processed_size_bytes = file_size
        transcript.source_transcript_id = source_transcript_id
        transcript.batch_id = batch_id
        transcript.kind = kind
        if classification_status is not None:
            transcript.classification_status = classification_status
        transcript.num_speakers = num_speakers
        transcript.stereo_audio_path = stereo_audio_path
        db.commit()
        stereo_persisted = True

        # Post-hoc hallucination filter (issue #270): run after transcription
        # but before diarization so hallucinated segments don't get speaker
        # labels assigned. Builtin-only (requires faster-whisper confidence
        # and no_speech_prob fields).
        if user_settings.get("cleanup_hallu_enabled") and transcript.segments:
            transcript.segments = filter_hallucinations(
                transcript.segments,
                rep_window=user_settings.get("cleanup_hallu_rep_window", 3),
                logprob_cutoff=user_settings.get("cleanup_hallu_logprob_cutoff", -2.0),
                no_speech_cutoff=user_settings.get("cleanup_hallu_no_speech_cutoff", 0.6),
            )
            transcript.full_text = " ".join(s["text"] for s in transcript.segments if s.get("text")).strip()

        # Run diarization if requested
        if diarize and transcript.segments:
            try:
                merged, speaker_count, diarization_method, diarization_warning = await diarization_service.diarize_and_merge(
                    str(save_path),
                    num_speakers=num_speakers,
                    segments=transcript.segments,
                    hf_token=user_settings.get("hf_token"),
                    stereo_audio_path=transcript.stereo_audio_path,
                )
                transcript.segments = merged
                transcript.speaker_count = count_distinct_speakers(merged)
                transcript.diarization_method = diarization_method
                if diarization_warning:
                    # pyannote failed but the heuristic rescued the run
                    # (issue #121): labels exist, status stays completed,
                    # but the reason must not vanish.
                    transcript.error = degraded_error_text(diarization_warning)
                db.commit()
            except Exception as e:
                # Only reachable when the heuristic tier also failed —
                # the issue-#120 hard-failure surfacing, unchanged.
                import traceback
                traceback.print_exc()
                transcript.error = f"Diarization failed: {e}"
                transcript.diarization_method = "failed"
                if transcript.status == "completed":
                    transcript.status = "partial"
                db.commit()

        # Post-hoc correction pass — queued as a background LlmJob (visible
        # on the Queue screen) instead of blocking this response. Runs
        # unconditionally of kind now (design decision 11): classification
        # needs corrected text (decision 2) even for a not-yet-classified
        # 'auto' transcript, and voice_note no longer opts out. Only the
        # auto_correct user setting gates it. enqueue_auto_classify and the
        # voice-note-chain enqueue below both no-op on the wrong kind via
        # effective_kind(), so calling them unconditionally is safe.
        if auto_correct is None:
            auto_correct = user_settings.get("auto_correct", True)
        if auto_correct:
            enqueue_auto_correction(db, transcript, user_settings)
        else:
            # No correction pass means correction-completion (the usual
            # classify_pipeline trigger, services/llm_jobs.py's "correction"
            # branch) never fires — trigger classification directly instead,
            # or an auto-kind transcript would stay pending forever
            # (issue #268 comment 2's gap). No-ops via its own status guard
            # when kind was explicitly chosen (classification_status is
            # already 'override', never 'pending').
            enqueue_pipeline_classify(db, transcript, user_settings)
        enqueue_auto_classify(db, transcript, user_settings)
        if effective_kind(transcript) == "voice_note":
            enqueue_auto_voice_note(db, transcript, user_settings)
        if effective_kind(transcript) == "voice_dump":
            enqueue_auto_voice_dump(db, transcript, user_settings)
        # Tagging fires for every kind — keep this site in lockstep
        # with services/queue.py:_finalize_if_done (issue #171).
        enqueue_auto_tagging(db, transcript, user_settings)

        return _serialize_transcript(db, transcript, jobs_map=_batch_latest_jobs(db, [transcript.id]))

    except Exception as e:
        # transcribe() marked its row failed without ever persisting
        # stereo_audio_path (that happens post-success), so the FLAC is
        # unreferenced — remove it rather than leaving an orphan.
        _discard_stereo_copy()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    provider: str = Form("moonshine"),
    model: Optional[str] = Form(None),
    language: str = Form("en"),
    temperature: float = Form(0.0),
    diarize: bool = Form(False),
    auto_correct: Optional[bool] = Form(None),
    num_speakers: Optional[int] = Form(None),
    context_doc: Optional[str] = Form(None),
    kind: str = Form("meeting"),
    capture_source: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_device),
):
    """Upload and transcribe an audio file."""
    if kind not in ("meeting", "dictation", "voice_note", "voice_dump", "auto"):
        raise HTTPException(status_code=400, detail="kind must be 'meeting', 'dictation', 'voice_note', 'voice_dump', or 'auto'")
    is_device_call = getattr(request.state, "device_authenticated", False)
    if is_device_call and not rate_limiter.check(f"device-upload:{current_user.id}", max_requests=30, window_seconds=3600):
        raise HTTPException(status_code=429, detail="Too many device uploads, try again later")
    # Save uploaded file
    file_ext = os.path.splitext(file.filename or "audio.mp3")[1] or ".mp3"
    safe_name = f"{utcnow_naive().strftime('%Y%m%d_%H%M%S')}_{hash(file.filename or 'audio')}{file_ext}"
    save_path = UPLOAD_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    if context_doc and context_doc.strip():
        from services.settings import resolve_provider_key, KEYLESS_PROVIDERS
        user_settings = get_user_settings(db, current_user.id)
        extraction_provider = user_settings.get("correction_provider", "groq")
        extraction_key, extraction_cfg = resolve_provider_key(db, current_user.id, extraction_provider)
        if extraction_key or extraction_provider in KEYLESS_PROVIDERS:
            try:
                await extract_hotwords_from_doc(
                    db, current_user.id, context_doc, api_key=extraction_key,
                    provider_name=extraction_provider, provider_config=extraction_cfg,
                )
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
        auto_correct=auto_correct,
        num_speakers=num_speakers,
        kind=kind,
        capture_source=capture_source,
    )


@app.post("/api/bulk-transcribe")
async def bulk_transcribe(
    files: list[UploadFile] = File(default=[]),
    settings: str = Form(...),
    file_settings: str = Form("[]"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload and transcribe multiple audio files in one batch."""
    import secrets as _secrets

    # Validate at least one file
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    # Parse settings JSON
    try:
        global_settings = json.loads(settings)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="settings must be valid JSON")

    # Parse optional per-file overrides
    try:
        per_file_overrides: list[dict] = json.loads(file_settings)
        if not isinstance(per_file_overrides, list):
            raise HTTPException(status_code=400, detail="file_settings must be a JSON array")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="file_settings must be valid JSON")

    # Per-file overrides get the same validation as the global settings below —
    # otherwise an invalid kind/provider in file_settings lands unchecked on
    # the transcript row instead of failing the request up front.
    for idx, override in enumerate(per_file_overrides):
        if not isinstance(override, dict):
            raise HTTPException(status_code=400, detail=f"file_settings[{idx}] must be an object")
        if "kind" in override and override["kind"] not in ("meeting", "dictation", "voice_note", "voice_dump", "auto"):
            raise HTTPException(
                status_code=400,
                detail=f"file_settings[{idx}].kind must be 'meeting', 'dictation', 'voice_note', 'voice_dump', or 'auto'",
            )
        if "provider" in override:
            try:
                get_provider(override["provider"], {})
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"file_settings[{idx}].provider is unknown: {override['provider']}",
                )

    # Validate kind
    kind = global_settings.get("kind", "meeting")
    if kind not in ("meeting", "dictation", "voice_note", "voice_dump", "auto"):
        raise HTTPException(status_code=400, detail="kind must be 'meeting', 'dictation', 'voice_note', 'voice_dump', or 'auto'")

    # Validate provider
    provider = global_settings.get("provider", "moonshine")
    try:
        get_provider(provider, {})
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Reject local providers with combined file size > 500 MB
    if provider in LOCAL_PROVIDERS:
        total_bytes = 0
        for f in files:
            content = await f.read()
            total_bytes += len(content)
            await f.seek(0)  # rewind for later use
        if total_bytes > 500 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"Combined file size ({total_bytes / (1024*1024):.0f} MB) exceeds 500 MB limit for local provider '{provider}'",
            )

    # Generate batch_id
    batch_id = f"{utcnow_naive().strftime('%Y%m%d_%H%M%S')}_{_secrets.token_hex(3)}"

    # Pull defaults from user settings, fall back to global_settings
    user_settings = get_user_settings(db, current_user.id)
    bulk_defaults = user_settings.get("bulk_defaults", {})
    model = global_settings.get("model") or bulk_defaults.get("model", "")
    language = global_settings.get("language") or bulk_defaults.get("language", "auto")
    diarize = global_settings.get("diarize", bulk_defaults.get("diarize", False))
    auto_correct = global_settings.get("auto_correct", bulk_defaults.get("auto_correct", True))
    num_speakers = global_settings.get("num_speakers", bulk_defaults.get("num_speakers"))

    transcripts = []
    errors = []
    all_failed = True

    for i, f in enumerate(files):
        # Merge per-file overrides (if any) over global settings
        override = per_file_overrides[i] if i < len(per_file_overrides) else {}
        file_kind = override.get("kind", kind)
        file_provider = override.get("provider", provider)
        file_model = override.get("model", model)
        file_language = override.get("language", language)
        file_diarize = override.get("diarize", diarize)
        file_auto_correct = override.get("auto_correct", auto_correct)
        file_num_speakers = override.get("num_speakers", num_speakers)
        file_title = override.get("title", None)

        file_ext = os.path.splitext(f.filename or "audio.mp3")[1] or ".mp3"
        safe_name = f"{utcnow_naive().strftime('%Y%m%d_%H%M%S')}_{hash(f.filename or 'audio')}{file_ext}"
        save_path = UPLOAD_DIR / safe_name
        content = await f.read()
        with open(save_path, "wb") as fh:
            fh.write(content)

        try:
            result = await _run_transcription_pipeline(
                db, current_user, save_path,
                filename=f.filename or "audio.mp3",
                title=file_title,
                provider=file_provider,
                model=file_model,
                language=file_language,
                temperature=0.0,
                diarize=file_diarize,
                auto_correct=file_auto_correct,
                num_speakers=file_num_speakers,
                kind=file_kind,
                batch_id=batch_id,
            )
            transcripts.append(result)
            all_failed = False
        except HTTPException as e:
            # _run_transcription_pipeline wraps runtime failures (bad codec,
            # transcode errors) in HTTPException too, not just validation
            # errors — all validation happens above, before this loop starts,
            # so any HTTPException reaching here is a per-file runtime
            # failure and must be treated as a partial failure like any
            # other exception, not used to abort the whole batch.
            errors.append({"index": i, "filename": f.filename or f"file_{i}", "error": str(e.detail)})
        except Exception as e:
            errors.append({"index": i, "filename": f.filename or f"file_{i}", "error": str(e)})

    if all_failed and not transcripts:
        raise HTTPException(status_code=500, detail="All files failed to transcribe")

    response = {"batch_id": batch_id, "transcripts": transcripts}
    if errors:
        response["errors"] = errors
    return response


# ── Batch management API ────────────────────────────────────────────────────


@app.get("/api/batches")
async def list_batches(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List batches for the current user, newest first. Each batch entry
    includes aggregate status counts, total duration, and a first_title."""
    rows = (
        db.query(
            Transcript.batch_id,
            func.count(Transcript.id).label("total"),
            func.sum(case((Transcript.status == "completed", 1), else_=0)).label("completed"),
            func.sum(case((Transcript.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((Transcript.status == "partial", 1), else_=0)).label("partial"),
            func.sum(case((Transcript.status == "pending", 1), else_=0)).label("pending"),
            func.sum(case((Transcript.status == "processing", 1), else_=0)).label("processing"),
            func.sum(case((Transcript.status == "cancelled", 1), else_=0)).label("cancelled"),
            func.coalesce(func.sum(Transcript.duration_seconds), 0).label("total_duration_seconds"),
            func.min(Transcript.created_at).label("created_at"),
        )
        .filter(
            Transcript.user_id == current_user.id,
            Transcript.batch_id.isnot(None),
        )
        .group_by(Transcript.batch_id)
        .order_by(func.min(Transcript.created_at).desc(), Transcript.batch_id)
        .offset(offset)
        .limit(limit)
        .all()
    )

    if not rows:
        return {"batches": []}

    batch_ids = [row.batch_id for row in rows]
    # Look up first_title: lowest-id transcript per batch, scoped to
    # current user (batch_ids themselves are user-scoped from the main
    # query, but a batch_id collision with another user would leak their
    # title unless both queries filter by user_id).
    first_transcripts = (
        db.query(Transcript.batch_id, func.min(Transcript.id).label("min_id"))
        .filter(
            Transcript.batch_id.in_(batch_ids),
            Transcript.user_id == current_user.id,
        )
        .group_by(Transcript.batch_id)
        .subquery()
    )
    title_rows = (
        db.query(Transcript.batch_id, Transcript.title)
        .join(first_transcripts, Transcript.id == first_transcripts.c.min_id)
        .all()
    )
    titles = {row.batch_id: row.title for row in title_rows}

    batches = []
    for row in rows:
        batches.append({
            "batch_id": row.batch_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "total": row.total,
            "completed": row.completed or 0,
            "failed": row.failed or 0,
            "partial": row.partial or 0,
            "pending": row.pending or 0,
            "processing": row.processing or 0,
            "cancelled": row.cancelled or 0,
            "total_duration_seconds": float(row.total_duration_seconds or 0),
            "first_title": titles.get(row.batch_id),
        })
    return {"batches": batches}


@app.get("/api/batches/{batch_id}")
async def get_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return full detail for one batch including all transcripts."""
    transcripts = (
        db.query(Transcript)
        .filter(
            Transcript.batch_id == batch_id,
            Transcript.user_id == current_user.id,
        )
        .order_by(Transcript.id)
        .all()
    )
    if not transcripts:
        raise HTTPException(status_code=404, detail="Batch not found")

    status_counts = {
        "completed": sum(1 for t in transcripts if t.status == "completed"),
        "failed": sum(1 for t in transcripts if t.status == "failed"),
        "partial": sum(1 for t in transcripts if t.status == "partial"),
        "pending": sum(1 for t in transcripts if t.status == "pending"),
        "processing": sum(1 for t in transcripts if t.status == "processing"),
        "cancelled": sum(1 for t in transcripts if t.status == "cancelled"),
    }
    jobs_map = _batch_latest_jobs(db, [t.id for t in transcripts])

    return {
        "batch_id": batch_id,
        "created_at": transcripts[0].created_at.isoformat() if transcripts[0].created_at else None,
        "total": len(transcripts),
        "status_counts": status_counts,
        "total_duration_seconds": sum(t.duration_seconds or 0 for t in transcripts),
        "transcripts": [_serialize_transcript(db, t, jobs_map=jobs_map) for t in transcripts],
    }


@app.post("/api/batches/{batch_id}/cancel")
async def cancel_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel all active (pending/processing) transcripts in a batch."""
    transcripts = (
        db.query(Transcript)
        .filter(
            Transcript.batch_id == batch_id,
            Transcript.user_id == current_user.id,
        )
        .all()
    )
    if not transcripts:
        raise HTTPException(status_code=404, detail="Batch not found")

    cancelled = 0
    already_terminal = 0
    errors = []

    for t in transcripts:
        if t.status in ("pending", "processing"):
            try:
                cancel_transcript_jobs(db, t.id)
                cancelled += 1
            except Exception as e:
                errors.append({"transcript_id": t.id, "error": str(e)})
                db.rollback()
        else:
            already_terminal += 1

    response = {
        "batch_id": batch_id,
        "cancelled": cancelled,
        "already_terminal": already_terminal,
    }
    if errors:
        response["errors"] = errors
    return response


@app.get("/api/search")
async def search_transcripts_endpoint(
    q: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """FTS5 full-text search across transcript content (issue #108).
    Returns ranked snippet results for the Tape Library search bar."""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    if len(q) > 500:
        raise HTTPException(status_code=400, detail="Query exceeds 500 characters")
    results = search_transcripts_snippets(db, current_user.id, q.strip(), limit=limit)
    return {"results": results, "total": len(results)}


@app.get("/api/transcripts")
async def list_transcripts(
    limit: int = 50, offset: int = 0, q: str | None = Query(None),
    batch_id: str | None = Query(None),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    if q and q.strip():
        if len(q.strip()) > 500:
            raise HTTPException(status_code=400, detail="Query exceeds 500 characters")
        return _build_recent_transcripts(db, current_user, limit=limit, offset=offset, query=q.strip(), batch_id=batch_id)
    return _build_recent_transcripts(db, current_user, limit=limit, offset=offset, batch_id=batch_id)


@app.get("/api/transcripts/{transcript_id}")
async def get_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return _serialize_transcript(db, t, jobs_map=_batch_latest_jobs(db, [t.id]), include_relabel=True)


# Job states whose chunk files are still needed: pending/running are
# actively in flight, failed chunks get auto-retried after a backoff window
# (and manually via "Retry failed sections"), and cancelled chunks are
# resumable. Only completed jobs' files are truly dead.
_LIVE_JOB_STATUSES = ("pending", "running", "failed", "cancelled")


def _live_job_paths(db: Session) -> set:
    rows = (
        db.query(TranscriptionJob.audio_path)
        .filter(TranscriptionJob.status.in_(_LIVE_JOB_STATUSES))
        .all()
    )
    return {os.path.realpath(audio_path) for (audio_path,) in rows if audio_path}


def _transcript_refs_by_realpath(db: Session) -> dict:
    """Map realpath -> [(transcript, field)] over every user's transcripts.
    Reference checks compare resolved paths, not raw strings — a textual
    variant of the same file (redundant separators, '.' or '..' segments)
    stored in the DB would slip past a string-equality check and let the
    file be deleted out from under the transcript that references it.

    Filtered at the query level to rows that actually hold a path — a
    transcript with all three columns NULL can never match a realpath lookup,
    so excluding it up front (rather than after loading the full row)
    keeps the table scan cheap as it grows. Full Transcript ORM objects
    are still returned (not a column projection): delete_files mutates
    and commits these same instances after removing a file."""
    refs = {}
    rows = db.query(Transcript).filter(
        or_(Transcript.audio_path.isnot(None), Transcript.video_path.isnot(None), Transcript.stereo_audio_path.isnot(None))
    ).all()
    for t in rows:
        for field in ("audio_path", "video_path", "stereo_audio_path"):
            p = getattr(t, field)
            if p:
                refs.setdefault(os.path.realpath(p), []).append((t, field))
    return refs


def _transcript_pipeline_can_resume(db: Session, t: Transcript) -> bool:
    """True if this transcript can (re-)enter the transcription pipeline —
    still processing, or holding any retryable/resumable chunk job. Its
    audio_path (the pre-chunk source file) is read again at finalize for
    chunked-path diarization, so deleting the file for such a transcript
    would silently drop speaker labels on the eventual retry/resume."""
    if t.status == "processing":
        return True
    return db.query(TranscriptionJob).filter(
        TranscriptionJob.transcript_id == t.id,
        TranscriptionJob.status.in_(_LIVE_JOB_STATUSES),
    ).first() is not None


@app.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    refs = _transcript_refs_by_realpath(db)
    for path in (t.audio_path, t.video_path, t.stereo_audio_path):
        if path and os.path.exists(path):
            # Retranscribe carries video_path/audio_path/stereo_audio_path forward verbatim, so
            # sibling transcripts (same or different user) can reference the
            # exact same file. Removing it here would break their playback.
            real = os.path.realpath(path)
            still_referenced = any(other.id != t.id for other, _ in refs.get(real, []))
            if still_referenced:
                continue
            try:
                os.remove(path)
            except OSError as e:
                logging.warning(f" OSError removing transcript file {path!r}: {e} ")
                pass
    # Chunk files live on TranscriptionJob.audio_path, not on Transcript.
    # The cascade deletes the rows but not the files — clean them here.
    chunk_jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t.id).all()
    if chunk_jobs:
        other_job_reals = set()
        for j in db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id != t.id, TranscriptionJob.audio_path.isnot(None)).all():
            try:
                other_job_reals.add(os.path.realpath(j.audio_path))
            except (OSError, ValueError):
                continue
        other_transcript_reals = set()
        for other in db.query(Transcript).filter(
            Transcript.id != t.id,
            ((Transcript.audio_path.isnot(None)) | (Transcript.video_path.isnot(None)) | (Transcript.stereo_audio_path.isnot(None))),
        ).all():
            for field in ("audio_path", "video_path", "stereo_audio_path"):
                path = getattr(other, field)
                if not path:
                    continue
                try:
                    other_transcript_reals.add(os.path.realpath(path))
                except (OSError, ValueError):
                    continue
        for job in chunk_jobs:
            if not job.audio_path or not os.path.exists(job.audio_path):
                continue
            try:
                real = os.path.realpath(job.audio_path)
            except (OSError, ValueError):
                continue
            if real in other_job_reals or real in other_transcript_reals:
                continue
            try:
                os.remove(job.audio_path)
            except OSError as e:
                logging.warning(f" OSError removing chunk file {job.audio_path!r}: {e} ")
                pass
    db.delete(t)
    db.commit()
    return {"ok": True}


def _resolve_upload_name(name: str) -> Optional[str]:
    """Resolve a bare filename to its real path inside UPLOAD_DIR. The client
    never sees a server-side absolute path (list_files only returns
    basenames) — a path separator or '..' component means the input isn't a
    bare name at all, rejected up front. realpath is still checked for
    containment afterward as defense in depth against a symlink placed
    directly in UPLOAD_DIR. UPLOAD_DIR is flat (chunk_audio and the upload
    endpoints never create subdirectories), so a basename is always a
    unique, sufficient identifier for a file inside it."""
    if not name or os.sep in name or (os.altsep and os.altsep in name) or name in (".", ".."):
        return None
    try:
        real = os.path.realpath(os.path.join(str(UPLOAD_DIR), name))
        upload_real = os.path.realpath(str(UPLOAD_DIR))
        # Strictly inside — UPLOAD_DIR itself is not a deletable target.
        if real == upload_real or os.path.commonpath([real, upload_real]) != upload_real:
            return None
        return real
    except (OSError, ValueError, TypeError):
        return None


@app.get("/api/files")
async def list_files(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    live_job_paths = _live_job_paths(db)
    refs = _transcript_refs_by_realpath(db)

    linked, orphaned = [], []
    total_linked, total_orphaned = 0, 0
    for name in os.listdir(UPLOAD_DIR):
        full = os.path.join(str(UPLOAD_DIR), name)
        if not os.path.isfile(full):
            continue  # confirmed: chunk_audio writes chunks flat into UPLOAD_DIR, no subdirectory
        real = os.path.realpath(full)
        try:
            size = os.path.getsize(full)
            # Naive-UTC isoformat (no +00:00 suffix), matching created_at and
            # the rest of the app (utcnow_naive) — the frontend's timeAgo()
            # appends 'Z' itself and produces Invalid Date on "...+00:00Z".
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(full), datetime.UTC).replace(tzinfo=None).isoformat()
        except OSError:
            continue  # vanished between listdir and stat (job cleanup, concurrent delete)
        own_refs = [(t, f) for t, f in refs.get(real, []) if t.user_id == current_user.id]
        if own_refs:
            # A shared path (e.g. a retranscribe chain) has multiple entries
            # here — emit one linked row per referencing transcript so the
            # dependency is visible, at the cost of counting the file's size
            # once per reference in total_linked_bytes.
            for t, field in own_refs:
                linked.append({"transcript_id": t.id, "transcript_title": t.title, "field": field,
                                "name": name, "size_bytes": size, "modified_at": mtime})
                total_linked += size
        elif real in live_job_paths or real in refs:
            # Live chunk (pending/running/failed/cancelled job), or referenced
            # only by another user's transcript — excluded from the response
            # entirely (shown as neither linked nor orphaned).
            continue
        elif current_user.is_admin:
            # UPLOAD_DIR is shared across all users, and a truly orphaned
            # file (no Transcript/TranscriptionJob row at all) carries no
            # owner anywhere in the schema — unlike linked files, which stay
            # scoped by own_refs above. Until upload-time ownership is
            # tracked, only an admin (who already has cross-user visibility
            # elsewhere in the app) sees the orphan list.
            orphaned.append({"name": name, "size_bytes": size, "modified_at": mtime})
            total_orphaned += size
    linked.sort(key=lambda x: x["modified_at"])
    orphaned.sort(key=lambda x: x["modified_at"])

    return {"linked": linked, "orphaned": orphaned,
            "total_linked_bytes": total_linked, "total_orphaned_bytes": total_orphaned}


@app.post("/api/files/delete")
async def delete_files(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    names = data.get("names") or []
    if not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names must be a list of strings")
    # Validate every name up front — a bad entry anywhere in the batch aborts
    # the whole request with no side effects, rather than partially deleting
    # earlier entries before hitting the bad one.
    resolved = []
    for raw_name in names:
        if not isinstance(raw_name, str):
            raise HTTPException(status_code=400, detail="names must be a list of strings")
        real = _resolve_upload_name(raw_name)
        if real is None:
            raise HTTPException(status_code=400, detail=f"Name not allowed: {raw_name}")
        resolved.append((raw_name, real))

    live_job_paths = _live_job_paths(db)
    refs = _transcript_refs_by_realpath(db)
    deleted, skipped = [], []
    freed_bytes = 0
    for raw_name, real in resolved:
        if real in live_job_paths:
            skipped.append({"name": raw_name, "reason": "in_use"})
            continue
        entries = refs.get(real, [])
        if len({t.id for t, _ in entries}) > 1:
            # 2+ transcripts reference this file — a same-user retranscribe
            # chain, a cross-user collision, or both. Either way, deleting
            # would silently break playback for at least one other
            # transcript, so skip regardless of who owns which row.
            skipped.append({"name": raw_name, "reason": "shared"})
            continue
        if entries:
            m = entries[0][0]
            if m.user_id != current_user.id:
                skipped.append({"name": raw_name, "reason": "not_found_or_forbidden"})
                continue
            if _transcript_pipeline_can_resume(db, m):
                skipped.append({"name": raw_name, "reason": "in_use"})
                continue
        elif not current_user.is_admin:
            # No transcript reference at all — a true orphan with no
            # recorded owner. Same admin-only rule as the listing endpoint
            # (list_files): a non-admin can't prove this file is theirs, so
            # it can't be treated as deletable by them either. Reuse the
            # "not_found_or_forbidden" reason rather than a distinct one, so
            # a non-admin probing arbitrary UPLOAD_DIR filenames can't use
            # the response to tell an orphan apart from someone else's file.
            skipped.append({"name": raw_name, "reason": "not_found_or_forbidden"})
            continue
        try:
            size = os.path.getsize(real)  # captured before removal — gone from disk afterward
            os.remove(real)
        except OSError:
            skipped.append({"name": raw_name, "reason": "remove_failed"})
            continue
        # entries all belong to one transcript here — null every field of
        # its that pointed at this file (audio_path, video_path, and
        # stereo_audio_path can in principle all reference the same path).
        for t, field in entries:
            setattr(t, field, None)
        if entries:
            db.commit()
        deleted.append(raw_name)
        freed_bytes += size
    return {"deleted": deleted, "skipped": skipped, "freed_bytes": freed_bytes}


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
        clear_relabel_history(db, t.id)
        t.segments = data["segments"]
        # Client-supplied replacement: the labels it carries are now the only
        # record of who speaks, so the cached count has to follow them.
        t.speaker_count = count_distinct_speakers(t.segments)
    if "full_text" in data:
        t.full_text = data["full_text"]
    if "kind" in data:
        if data["kind"] not in ("meeting", "dictation", "voice_note", "voice_dump", "auto"):
            raise HTTPException(status_code=400, detail="kind must be 'meeting', 'dictation', 'voice_note', 'voice_dump', or 'auto'")
        # The pipeline reads kind mid-job (dictation skips diarization), so a
        # flip during processing would diarize later chunks differently than
        # earlier ones. Only allow changing kind on settled transcripts.
        if data["kind"] != t.kind and t.status == "processing":
            raise HTTPException(status_code=409, detail="Cannot change mode while transcription is running")
        if data["kind"] == "auto":
            # Revert to auto-classification: store placeholder kind + pending
            # status, same as _run_transcription_pipeline for a fresh 'auto'
            # recording. Enqueue classification directly — correction
            # already completed on this settled transcript, so the usual
            # correction-completion trigger won't fire (issue #269 gap).
            t.kind = "meeting"
            t.classification_status = "pending"
            t.classification_confidence = None
            t.classification_provenance = None
            t.updated_at = utcnow_naive()
            db.commit()
            user_settings = get_user_settings(db, current_user.id)
            enqueue_pipeline_classify(db, t, user_settings)
            return _serialize_transcript(db, t, jobs_map=_batch_latest_jobs(db, [t.id]))
        else:
            t.kind = data["kind"]
            # Explicitly picking a kind is a manual override (design decision 5)
            # — must supersede any classification in flight (pending/uncertain/
            # failed), even if the value happens to match the placeholder kind.
            t.classification_status = "override"
    t.updated_at = utcnow_naive()
    db.commit()
    return _serialize_transcript(db, t, jobs_map=_batch_latest_jobs(db, [t.id]))


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
    root_id = t.source_transcript_id or t.id
    user_settings = get_user_settings(db, current_user.id)
    retranscribe_auto_correct = user_settings.get("auto_correct", True)
    # Design decision 9 (#271): auto-classified transcripts re-classify against
    # new corrected text (may legitimately classify differently). Overrides
    # (including legacy-migrated) carry forward unchanged — a user's explicit
    # choice is never silently discarded by a re-run.
    source_status = t.classification_status or "override"  # column default is "override"
    if source_status in ("success", "uncertain", "failed"):
        # Re-classify: pass "auto" so _run_transcription_pipeline sets
        # classification_status="pending" and the correction-completion
        # trigger enqueues classify_pipeline against the new text.
        # Includes "failed" per Oracle review: a failed classification is
        # an auto-intent transcript that should get a fresh attempt, not
        # be silently converted to an override (acceptance: "failures are
        # visible and retryable").
        retranscribe_kind = "auto"
    else:
        # Override, pending, or unknown — carry the existing kind forward.
        retranscribe_kind = t.kind or "meeting"
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
        source_transcript_id=root_id,
        kind=retranscribe_kind,
        auto_correct=retranscribe_auto_correct,
    )


_AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".flac": "audio/flac", ".webm": "audio/webm", ".m4a": "audio/mp4",
}

# Deliberately restricted to containers a browser <video> tag can actually
# play. .mkv/.avi/most .mov are NOT included: retaining them as "video"
# would reproduce the exact "sort of works" problem this feature exists to
# fix (file exists, route serves it, but the browser shows a black player
# with no error, since it can't decode the container).
_VIDEO_MIME = {
    ".mp4": "video/mp4", ".webm": "video/webm",
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

    entry = record_relabel(
        db, t, "rename", changed,
        corrected_text_before=t.corrected_text if t.corrected_text else None,
        description=f"rename {old} to {new} ({renamed} lines)",
    )
    t.segments = new_segments
    # Renaming A to a name already present elsewhere merges the two, so the
    # distinct-label count can drop by one here.
    t.speaker_count = count_distinct_speakers(new_segments)

    if t.corrected_text:
        # Line-anchored: only rewrite the 'Old Name: ' prefix at the start
        # of a line — the same string inside sentence text must not change.
        prefix = f"{old}: "
        t.corrected_text = "\n".join(
            (new + line[len(old):]) if line.startswith(prefix) else line
            for line in t.corrected_text.splitlines()
        )
    if entry is not None and entry.inverse.get("corrected_text") is not None:
        # After-image stamp: undo restores the before-image only while
        # corrected_text still equals what this rename produced. A JSON
        # column doesn't change-track in-place edits — assign a new dict.
        entry.inverse = {**entry.inverse, "corrected_text_after": t.corrected_text}

    t.updated_at = utcnow_naive()
    db.commit()
    return {"renamed": renamed, "transcript": _serialize_transcript(db, t, jobs_map=_batch_latest_jobs(db, [t.id]), include_relabel=True)}


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
    changed = [
        (i, segments[i].get("speaker") or "", segments[i].get("speaker_confidence"))
        for i in sorted(index_set)
    ]
    record_relabel(db, t, "retag", changed,
                   description=f"retag {len(index_set)} lines to {speaker}")
    # A retag is the user overriding the diarizer, so the diarizer's
    # confidence in the label it lost no longer describes the line. Stamp the
    # user-assigned sentinel instead of leaving the stale value, which kept
    # the "?" uncertainty marker on lines the user just corrected (issue
    # #305). The old value travels in the inverse patch above so undo can
    # bring it back.
    new_segments = [
        {**seg, "speaker": speaker, "speaker_confidence": USER_ASSIGNED_CONFIDENCE}
        if i in index_set else seg
        for i, seg in enumerate(segments)
    ]
    t.segments = new_segments
    # A by-index retag can fold lines into an existing label (count drops) or
    # introduce a label the transcript did not have before (count rises).
    t.speaker_count = count_distinct_speakers(new_segments)
    t.updated_at = utcnow_naive()
    db.commit()
    return {"retagged": len(index_set), "transcript": _serialize_transcript(db, t, jobs_map=_batch_latest_jobs(db, [t.id]), include_relabel=True)}


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
    entry = latest_relabel(db, t.id)
    if not entry:
        raise HTTPException(status_code=404, detail="Nothing to undo")

    segments = list(t.segments or [])
    for patch in entry.inverse.get("segments", []):
        i = patch.get("index")
        if isinstance(i, int) and 0 <= i < len(segments):
            restored = {**segments[i], "speaker": patch.get("speaker") or ""}
            # Retag patches carry the pre-retag confidence (None when the
            # segment never had one, behaviorally the same to the UI); rows
            # recorded before issue #305 lack the key entirely and must leave
            # confidence untouched.
            if "speaker_confidence" in patch:
                restored["speaker_confidence"] = patch["speaker_confidence"]
            segments[i] = restored
    t.segments = segments
    # Restoring the old labels restores the old label set, so the count has to
    # come back with them.
    t.speaker_count = count_distinct_speakers(segments)
    if (
        entry.inverse.get("corrected_text") is not None
        and entry.inverse.get("corrected_text_after") == t.corrected_text
    ):
        # Restore the before-image only if corrected_text is still exactly
        # what the rename produced. If a correction pass ran in between,
        # the snapshot is stale — reverting it would silently discard the
        # newer LLM output, which is worth more than the label prefix.
        t.corrected_text = entry.inverse["corrected_text"]
    undone_kind, undone_desc = entry.kind, entry.description
    db.delete(entry)
    t.updated_at = utcnow_naive()
    db.commit()
    return {"undone": undone_kind, "description": undone_desc,
            "transcript": _serialize_transcript(db, t, jobs_map=_batch_latest_jobs(db, [t.id]), include_relabel=True)}


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
        permanent_path = VOICES_DIR / f"clip_{profile.id}_{utcnow_naive().strftime('%Y%m%d_%H%M%S%f')}.wav"
        shutil.copyfile(sample_path, permanent_path)
        user_settings = get_user_settings(db, current_user.id)
        clip = voice_id_service.add_clip(db, profile.id, current_user.id, str(permanent_path),
                                          source_transcript_id=t.id,
                                          hf_token=user_settings.get("hf_token"))
        db.refresh(profile)
        return {
            "id": profile.id,
            "name": profile.name,
            "sample_count": profile.sample_count,
            "embedding_model": profile.embedding_model,
            "notes": profile.notes,
            "clip_id": clip.id,
            "warning": voice_id_service.degraded_model_warning(clip.embedding_model),
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
    safe_name = f"diar_{utcnow_naive().strftime('%Y%m%d_%H%M%S')}{file_ext}"
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
    except MissingTokenError as e:
        # Explicit pyannote opt-in without a token is user-actionable:
        # 400 with the settings-pointing message, not a generic 500
        # (issue #119). The pipeline paths degrade to the heuristic
        # instead — this endpoint bypasses diarize_and_merge on purpose.
        raise HTTPException(status_code=400, detail=str(e))
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
    if effective_kind(t) == "voice_note":
        raise HTTPException(status_code=400, detail="Voice notes have their own structured summary — see the Notes tab; the meeting-style summary doesn't apply")
    if t.status != "completed":
        raise HTTPException(status_code=400, detail=f"Transcript {transcript_id} is not completed")
    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS
    api_key, _ = resolve_provider_key(db, current_user.id, provider)
    if provider not in KEYLESS_PROVIDERS and not api_key:
        raise HTTPException(status_code=400, detail=f"No {provider} API key saved — add one in the service panel")

    job = enqueue_llm_job(db, current_user.id, transcript_id, "summary", provider, model)
    return {"job": serialize_llm_job(job)}


# ── Reformatting (dictation) ────────────────────────────────────────────────

_FORMAT_TARGET_KINDS = {
    "markdown": "format_markdown",
    "email": "format_email",
    "coding_prompt": "format_coding_prompt",
}


@app.post("/api/transcripts/{transcript_id}/format/{target}")
async def format_transcript(
    transcript_id: int,
    target: str,
    provider: str = Form("local_llm"),
    model: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue a reformat of a completed dictation transcript into `target`
    ('markdown' | 'email' | 'coding_prompt') — watch it on the Queue screen
    or fetch history via GET /api/transcripts/{id}/runs/{kind}."""
    kind = _FORMAT_TARGET_KINDS.get(target)
    if not kind:
        raise HTTPException(status_code=400, detail=f"Unknown format target '{target}' — use markdown, email, or coding_prompt")
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    ek = effective_kind(t)
    if ek != "dictation":
        if ek == "voice_note":
            raise HTTPException(status_code=400, detail="Voice notes have their own structured view — see the Notes tab or Voice notes board; reformatting doesn't apply")
        raise HTTPException(status_code=400, detail="Reformatting is only available for dictation transcripts")
    if t.status != "completed":
        raise HTTPException(status_code=400, detail=f"Transcript {transcript_id} is not completed")
    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS
    api_key, _ = resolve_provider_key(db, current_user.id, provider)
    if provider not in KEYLESS_PROVIDERS and not api_key:
        raise HTTPException(status_code=400, detail=f"No {provider} API key saved — add one in the service panel")

    job = enqueue_llm_job(db, current_user.id, transcript_id, kind, provider, model)
    return {"job": serialize_llm_job(job)}


@app.post("/api/transcripts/{transcript_id}/export-markdown")
async def export_markdown(
    transcript_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Write a clean Markdown export of a completed transcript to the user's
    configured export_directory. No LLM call — assembles the document from
    stored segments + the summary row. Returns {ok, path} on success.
    """
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if t.status != "completed":
        raise HTTPException(status_code=400, detail="Transcript is not ready for export")

    from services.settings import get_user_settings
    settings = get_user_settings(db, current_user.id)
    export_dir = (settings.get("export_directory") or "").strip()
    if not export_dir:
        raise HTTPException(status_code=400, detail="Export directory not configured — set it in Settings")
    if not os.path.isdir(export_dir):
        raise HTTPException(status_code=500, detail=f"Export directory does not exist: {export_dir}")

    probe = os.path.join(export_dir, f".wd-export-probe-{os.getpid()}-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}")
    try:
        with open(probe, "w") as fp:
            fp.write("ok")
        os.remove(probe)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Export directory is not writable: {export_dir} ({e})")

    from services.reformatting import build_export_markdown
    summary_data = None
    if t.summary is not None:
        summary_data = {
            "short_summary": t.summary.short_summary or "",
            "key_points": list(t.summary.key_points or []),
            "action_items": list(t.summary.action_items or []),
            "decisions": list(t.summary.decisions or []),
        }
    md = build_export_markdown(t, summary_data)

    safe_title = re.sub(r"[\\/:*?\"<>|]", "-", (t.title or "").strip())
    safe_title = re.sub(r"\s+", " ", safe_title).strip() or "transcript"
    date_str = (t.created_at or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).strftime("%Y-%m-%d")
    filename = f"{safe_title}-{date_str}.md"
    full_path = os.path.join(export_dir, filename)

    try:
        with open(full_path, "w", encoding="utf-8") as fp:
            fp.write(md)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")

    return {"ok": True, "path": full_path}


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

    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS
    api_key, provider_config = resolve_provider_key(db, current_user.id, provider)
    if provider not in KEYLESS_PROVIDERS and not api_key:
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
    ek = effective_kind(t)
    if ek != "meeting":
        # Allow-list, not a blocklist (design decision 8): pending/uncertain/
        # failed must block here too, unlike summary/reformat above — a
        # missing or unconfident classification can never silently unlock
        # re-diarize.
        if ek == "voice_note":
            raise HTTPException(status_code=400, detail="Voice notes are single-speaker — re-diarize doesn't apply")
        if ek == "dictation":
            raise HTTPException(status_code=400, detail="Dictation transcripts are single-speaker — re-diarize doesn't apply")
        raise HTTPException(status_code=400, detail="Re-diarize isn't available yet — classification hasn't completed")
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


@app.post("/api/transcripts/{transcript_id}/voice-match")
async def voice_match_transcript(
    transcript_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue a background pass that relabels segments using the voice
    roster — no re-clustering, just matching against enrolled voices."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    ek = effective_kind(t)
    if ek != "meeting":
        # Allow-list, not a blocklist (design decision 8) — same reasoning
        # as rediarize above, minus the diarization pre-pass condition
        # (voice-match doesn't depend on diarization method).
        if ek == "voice_note":
            raise HTTPException(status_code=400, detail="Voice notes are single-speaker — voice matching doesn't apply")
        if ek == "dictation":
            raise HTTPException(status_code=400, detail="Dictation transcripts are single-speaker — voice matching doesn't apply")
        raise HTTPException(status_code=400, detail="Voice matching isn't available yet — classification hasn't completed")
    if not (t.audio_path and os.path.exists(t.audio_path)):
        raise HTTPException(status_code=400, detail="No stored audio for this transcript")
    job = enqueue_llm_job(db, current_user.id, transcript_id, "voice_match", "", "")
    return {"job": serialize_llm_job(job)}


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
    if kind not in ("correction", "summary", "rediarize", "format_markdown", "format_email", "format_coding_prompt", "classify_intent", "voice_note", "voice_dump"):
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


def _serialize_voice_note(n: VoiceNote) -> dict:
    if not n:
        return None
    return {
        "id": n.id,
        "transcript_id": n.transcript_id,
        "note_type": n.note_type,
        "title": n.title or "",
        "body": n.body or "",
        "structured": n.structured or {},
        "model": n.model or "",
        "provider": n.provider or "",
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _serialize_voice_dump_item(item) -> dict:
    if not item:
        return None
    return {
        "id": item.id,
        "transcript_id": item.transcript_id,
        "source_job_id": item.source_job_id,
        "sequence_index": item.sequence_index,
        "note_type": item.note_type,
        "title": item.title or "",
        "body": item.body or "",
        "structured": item.structured or {},
        "model": item.model or "",
        "provider": item.provider or "",
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "seen_at": item.seen_at.isoformat() if item.seen_at else None,
    }


@app.get("/api/transcripts/{transcript_id}/voice-note")
async def get_transcript_voice_note(
    transcript_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch the latest VoiceNote row for this transcript (one per
    transcript, in-place update on re-run). Returns null when no chain
    has completed yet — the frontend renders the Notes tab as an
    empty-state with a "still processing" message in that case."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    note = (
        db.query(VoiceNote)
        .filter(VoiceNote.transcript_id == transcript_id)
        .order_by(VoiceNote.id.desc())
        .first()
    )
    return {"voice_note": _serialize_voice_note(note)}


@app.get("/api/voice-notes")
async def list_voice_notes(
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List this user's voice notes, most recent first. Each row
    includes the source transcript's title and duration so the board
    page can render a card without a follow-up fetch per card."""
    rows = (
        db.query(VoiceNote, Transcript)
        .join(Transcript, VoiceNote.transcript_id == Transcript.id)
        .filter(VoiceNote.user_id == current_user.id)
        .order_by(VoiceNote.created_at.desc(), VoiceNote.id.desc())
        .limit(limit)
        .all()
    )
    return {"voice_notes": [
        {
            **_serialize_voice_note(n),
            "transcript_title": t.title or "",
            "transcript_duration_seconds": t.duration_seconds or 0,
            "transcript_status": t.status,
        }
        for n, t in rows
    ]}


@app.delete("/api/voice-notes/{voice_note_id}")
async def delete_voice_note(
    voice_note_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a single voice-note row. The underlying transcript is
    untouched — the note is a derived artifact, the transcript is the
    user's source recording. Allows the user to discard a bad chain
    output without losing the source audio/text."""
    n = (
        db.query(VoiceNote)
        .filter(VoiceNote.id == voice_note_id, VoiceNote.user_id == current_user.id)
        .first()
    )
    if not n:
        raise HTTPException(status_code=404, detail="Voice note not found")
    db.delete(n)
    db.commit()
    return {"deleted": voice_note_id}


@app.post("/api/transcripts/{transcript_id}/voice-note/rerun")
async def rerun_voice_note_chain(
    transcript_id: int,
    provider: str = Form("groq"),
    model: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """(Re)run the voice-note LLM chain against a completed voice-note
    transcript. Used by the Notes tab's "Rerun chain" button — the user
    might want a fresh classification if the first attempt was wrong,
    or to try a different LLM. Pre-fails with a clear message when no
    key is saved, mirroring the other LLM-job rerun routes."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if effective_kind(t) != "voice_note":
        raise HTTPException(status_code=400, detail="Voice-note chain only applies to voice_note transcripts")
    if t.status not in ("completed", "partial"):
        raise HTTPException(status_code=400, detail=f"Transcript {transcript_id} is not completed")
    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS
    api_key, _ = resolve_provider_key(db, current_user.id, provider)
    if provider not in KEYLESS_PROVIDERS and not api_key:
        raise HTTPException(status_code=400, detail=f"No {provider} API key saved — add one in the service panel")
    job = enqueue_llm_job(db, current_user.id, transcript_id, "voice_note", provider, model)
    return {"job": serialize_llm_job(job)}


@app.post("/api/transcripts/{transcript_id}/voice-dump/rerun")
async def rerun_voice_dump_chain(
    transcript_id: int,
    provider: str = Form("groq"),
    model: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """(Re)run the voice-dump LLM chain against a completed voice-dump
    transcript. Pre-fails with a clear message when no key is saved,
    mirroring voice-note/rerun."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if effective_kind(t) != "voice_dump":
        raise HTTPException(status_code=400, detail="Voice-dump chain only applies to voice_dump transcripts")
    if t.status not in ("completed", "partial"):
        raise HTTPException(status_code=400, detail=f"Transcript {transcript_id} is not completed")
    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS
    api_key, _ = resolve_provider_key(db, current_user.id, provider)
    if provider not in KEYLESS_PROVIDERS and not api_key:
        raise HTTPException(status_code=400, detail=f"No {provider} API key saved — add one in the service panel")
    job = enqueue_llm_job(db, current_user.id, transcript_id, "voice_dump", provider, model)
    return {"job": serialize_llm_job(job)}


@app.post("/api/transcripts/{transcript_id}/voice-dump/save-draft")
async def save_voice_dump_draft(
    transcript_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save an edited item list back into the voice_dump job's result_json.
    The client sends the full (possibly edited) item list; we replace
    result_json['items'] with it. Only touches the draft, does not create
    VoiceDumpItem rows."""
    items = await request.json()
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    job = latest_job(db, transcript_id, "voice_dump")
    if not job:
        raise HTTPException(status_code=404, detail="No voice_dump job found for this transcript")
    if not job.result_json:
        job.result_json = {}
    job.result_json = {**job.result_json, "items": items}
    db.commit()
    return {"items": items}


@app.post("/api/transcripts/{transcript_id}/voice-dump/finalize")
async def finalize_voice_dump(
    transcript_id: int,
    request: Request,
    items: list[dict] = Body(..., embed=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Finalize voice-dump items. Accepts the (possibly edited) item list,
    discards any item with discarded=True, and inserts VoiceDumpItem rows
    for the rest. Does not delete the job or its result_json."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")

    kept = [it for it in items if not it.get("discarded", False)]

    source_job_id = None
    source_job = latest_job(db, transcript_id, "voice_dump")
    if source_job:
        source_job_id = source_job.id

    created = []
    for idx, item in enumerate(kept):
        vdi = VoiceDumpItem(
            user_id=current_user.id,
            transcript_id=transcript_id,
            source_job_id=source_job_id,
            sequence_index=idx,
            note_type=item.get("type", "general"),
            title=item.get("title", ""),
            body=item.get("body", ""),
            structured=item.get("structured", {}),
            model=item.get("model", ""),
            provider=item.get("provider", ""),
        )
        db.add(vdi)
        created.append(vdi)
    db.commit()

    # Refresh to get assigned ids
    for vdi in created:
        db.refresh(vdi)

    return {"items": [_serialize_voice_dump_item(vdi) for vdi in created]}


@app.get("/api/transcripts/{transcript_id}/voice-dump-items")
async def get_transcript_voice_dump_items(
    transcript_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """All finalized VoiceDumpItem rows for one transcript."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")

    items = (
        db.query(VoiceDumpItem)
        .filter(VoiceDumpItem.transcript_id == transcript_id)
        .order_by(VoiceDumpItem.sequence_index)
        .all()
    )
    return {"items": [_serialize_voice_dump_item(it) for it in items]}


@app.get("/api/voice-dump-items")
async def list_voice_dump_items(
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List this user's voice dump items across all transcripts, most
    recent first. Each row includes the source transcript's title and
    duration for card rendering."""
    rows = (
        db.query(VoiceDumpItem, Transcript)
        .join(Transcript, VoiceDumpItem.transcript_id == Transcript.id)
        .filter(VoiceDumpItem.user_id == current_user.id)
        .order_by(VoiceDumpItem.created_at.desc(), VoiceDumpItem.id.desc())
        .limit(limit)
        .all()
    )
    return {"items": [
        {
            **_serialize_voice_dump_item(item),
            "transcript_title": t.title or "",
            "transcript_duration_seconds": t.duration_seconds or 0,
            "transcript_status": t.status,
        }
        for item, t in rows
    ]}


@app.post("/api/voice-dump-items/mark-seen")
async def mark_voice_dump_items_seen(
    ids: list[int] = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark the listed voice-dump items as seen for the current user.
    Called when the Dump Notes board loads, so the nav badge reflects
    only items created after that visit."""
    if not ids:
        return {"marked_seen": 0}
    updated = (
        db.query(VoiceDumpItem)
        .filter(
            VoiceDumpItem.id.in_(ids),
            VoiceDumpItem.user_id == current_user.id,
            VoiceDumpItem.seen_at == None,  # noqa: E711
        )
        .update({VoiceDumpItem.seen_at: utcnow_naive()}, synchronize_session=False)
    )
    db.commit()
    return {"marked_seen": updated}


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
            "id": v.id, "provider": v.provider, "model": v.model, "status": v.status,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "full_text": v.full_text,
        }
        for v in versions
    ]}


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
    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS
    user_settings = get_user_settings(db, current_user.id)
    extraction_provider = user_settings.get("correction_provider", "groq")
    extraction_key, extraction_cfg = resolve_provider_key(db, current_user.id, extraction_provider)
    if not extraction_key and extraction_provider not in KEYLESS_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Context extraction needs a {extraction_provider.capitalize()} API key (service panel)",
        )
    try:
        terms = await extract_hotwords_from_doc(
            db, current_user.id, context_doc, api_key=extraction_key,
            provider_name=extraction_provider, provider_config=extraction_cfg,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Context extraction failed: {e}",
        )
    return {"terms": terms}


@app.get("/api/correction-models/{provider}")
async def correction_models(provider: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Curated, cost-aware model shortlist for the correction/summary pickers.
    OpenRouter entries are validated against its live catalog with pricing.
    For local_llm, fetches live models from the configured endpoint."""
    local_llm_api_url = None
    local_llm_api_key = None
    if provider == "local_llm":
        cfg = db.query(ProviderConfig).filter(
            ProviderConfig.user_id == current_user.id,
            ProviderConfig.name == "local_llm",
        ).first()
        if cfg:
            local_llm_api_url = cfg.api_url
            local_llm_api_key = cfg.api_key
    return {"provider": provider, "models": await get_correction_models(provider, local_llm_api_url, local_llm_api_key)}


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
        "batch_id": t.batch_id,
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
    return _build_jobs_payload(db, current_user, limit=limit)


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


@app.post("/api/jobs/{job_id}/dismiss")
async def dismiss_job(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Hide one terminal Queue entry — either kind of id shape (see list_jobs)."""
    try:
        if job_id.startswith("transcription-"):
            t = dismiss_transcript_queue_entry(db, current_user.id, int(job_id.removeprefix("transcription-")))
            return {"ok": True, "job": _transcription_queue_entry(db, t)}
        job = dismiss_llm_job(db, current_user.id, int(job_id))
        return {"ok": True, "job": serialize_llm_job(job)}
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jobs/clear")
async def clear_finished_jobs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Bulk-hide every terminal Queue entry (both kinds) for this user."""
    cleared = clear_finished_llm_jobs(db, current_user.id) + clear_finished_transcript_queue_entries(db, current_user.id)
    return {"ok": True, "cleared": cleared}


# ── Assistant ────────────────────────────────────────────────────────────

@app.post("/api/assistant")
async def assistant_request(
    request: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enqueue an assistant job: interpret a natural language request and
    execute an action plan (search, summarize, save)."""
    request = (request or "").strip()
    if not request:
        raise HTTPException(status_code=400, detail="Request cannot be empty")
    if len(request) > 2000:
        raise HTTPException(status_code=400, detail="Request must be under 2000 characters")

    user_settings = get_user_settings(db, current_user.id)
    provider = user_settings.get("correction_provider", "local_llm")
    model = user_settings.get("correction_model", "gpt-oss-20b-mxfp4-GGUF")

    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS
    api_key, _ = resolve_provider_key(db, current_user.id, provider)
    if provider not in KEYLESS_PROVIDERS and not api_key:
        raise HTTPException(status_code=400, detail=f"No {provider} API key saved")

    job = enqueue_llm_job(db, current_user.id, None, "assistant", provider, model)
    job.result_json = {"user_request": request}
    db.commit()
    return {"job": serialize_llm_job(job)}


@app.get("/api/assistant/result/{job_id}")
async def assistant_result(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll for an assistant job's progress or completed result."""
    job = db.query(LlmJob).filter(
        LlmJob.id == job_id, LlmJob.user_id == current_user.id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    response = serialize_llm_job(job)
    if job.status in ("completed", "failed", "cancelled"):
        response["result"] = job.result_json
    return response


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
    safe_name = f"enroll_{name}_{utcnow_naive().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        user_settings = get_user_settings(db, current_user.id)
        profile = voice_id_service.enroll(db, current_user.id, name=name, audio_path=str(save_path),
                                           notes=notes, hf_token=user_settings.get("hf_token"))
        return {
            "id": profile.id,
            "name": profile.name,
            "sample_count": profile.sample_count,
            "embedding_model": profile.embedding_model,
            "notes": profile.notes,
            # Non-null when the clip silently landed on the MFCC fallback, so a
            # degraded enrollment doesn't stay invisible (issue #109).
            "warning": voice_id_service.degraded_model_warning(profile.embedding_model),
        }
    except ValueError as e:
        # enroll() raises ValueError for the cases the caller can act on: no
        # backend, extraction failed, a clip that would be unmatchable against
        # the roster. Those are 400s, not server faults. Matches the add-clip
        # route, which already did this.
        try:
            os.remove(save_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(e))
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
    safe_name = f"ident_{utcnow_naive().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        user_settings = get_user_settings(db, current_user.id)
        outcome = voice_id_service.identify_detailed(db, current_user.id, str(save_path),
                                                     threshold=threshold,
                                                     hf_token=user_settings.get("hf_token"))
        return {
            "matches": outcome["matches"],
            "total_profiles": len(voice_id_service.list_profiles(db, current_user.id)),
            "backend": voice_id_service._backend,
            # An empty match list on its own can't say whether nobody matched or
            # nothing could be compared at all (issue #109).
            "probe_model": outcome["probe_model"],
            "degraded": outcome["degraded"],
            "skipped_model_mismatch": outcome["skipped_model_mismatch"],
            "warning": outcome["warning"],
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
    safe_name = f"clip_{profile_id}_{utcnow_naive().strftime('%Y%m%d_%H%M%S%f')}{file_ext}"
    save_path = VOICES_DIR / safe_name
    with open(save_path, "wb") as f:
        f.write(await file.read())

    try:
        user_settings = get_user_settings(db, current_user.id)
        clip = voice_id_service.add_clip(db, profile_id, current_user.id, str(save_path),
                                          hf_token=user_settings.get("hf_token"))
        return {"id": clip.id, "voice_profile_id": clip.voice_profile_id,
                "created_at": clip.created_at.isoformat() if clip.created_at else None,
                "warning": voice_id_service.degraded_model_warning(clip.embedding_model)}
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

# The password-min-length replace is intentionally per-request: tests
# assert that PASSWORD_MIN_LENGTH changes are visible on the next GET,
# so caching the full transformed HTML would break that contract.
_INDEX_HTML_FALLBACK = "<h1>WhisperDeck</h1><p>Frontend not built yet.</p>"
_index_html_cache: Optional[str] = None


def _load_index_html() -> str:
    global _index_html_cache
    if _index_html_cache is None:
        index_path = BASE_DIR / "static" / "index.html"
        if index_path.exists():
            _index_html_cache = index_path.read_text(encoding="utf-8")
        else:
            _index_html_cache = _INDEX_HTML_FALLBACK
    return _index_html_cache


@app.get("/", response_class=HTMLResponse)
async def index():
    body = _load_index_html()
    if body is _INDEX_HTML_FALLBACK:
        return HTMLResponse(body)
    return HTMLResponse(
        body.replace(
            '<meta name="wd-password-min-length" content="8">',
            f'<meta name="wd-password-min-length" content="{password_min_length()}">',
        )
    )


# First-party precached assets whose content decides the service worker's
# cache identity. The precached fonts are immutable, so they are excluded.
SW_FINGERPRINT_ASSETS = ("rack.min.js", "rack.min.css", "index.html")

# Matches the hand-maintained `const CACHE_VERSION = 'vN';` line in static/sw.js.
SW_CACHE_VERSION_RE = re.compile(r"(const CACHE_VERSION = ')([^']*)(')")


def sw_build_fingerprint() -> str:
    """Short content hash of the first-party assets the service worker
    precaches.

    The worker's fetch handler is cache-first for static assets, and its
    `activate` step only deletes caches whose name differs from the current
    CACHE_NAME. So the served bundle is pinned until CACHE_VERSION changes.
    Relying on a human to bump that literal on every bundle change does not
    work: 17 commits changed static/rack.min.js between the last bump and
    the one in this change, each shipping a bundle existing clients could
    not see. Deriving part of the version from asset content removes the
    manual step instead of documenting it harder.
    """
    static_dir = BASE_DIR / "static"
    digest = hashlib.sha256()
    for name in SW_FINGERPRINT_ASSETS:
        try:
            digest.update((static_dir / name).read_bytes())
        except OSError:
            # A missing asset must still produce a stable, distinct hash
            # rather than raising while serving the worker.
            digest.update(b"\0missing:" + name.encode() + b"\0")
        digest.update(b"\0")
    return digest.hexdigest()[:12]


@app.get("/sw.js")
async def service_worker():
    """Serve the service worker from root path so its scope covers the entire
    origin.  Serving from /static/sw.js would limit scope to /static/ only,
    making it impossible to intercept /api/* or /.

    The CACHE_VERSION literal is suffixed with a build fingerprint on the way
    out (see sw_build_fingerprint), so a changed bundle always yields a
    changed worker script. The browser byte-compares the worker on each
    no-cache fetch, so a changed script installs, re-precaches under the new
    cache name, and purges the old one."""
    sw_path = BASE_DIR / "static" / "sw.js"
    if not sw_path.exists():
        return Response(status_code=404)
    source = sw_path.read_text(encoding="utf-8")
    body, substitutions = SW_CACHE_VERSION_RE.subn(
        lambda m: m.group(1) + m.group(2) + "-" + sw_build_fingerprint() + m.group(3),
        source,
        count=1,
    )
    if substitutions != 1:
        # sw.js's declaration was reformatted out of recognition. Serve it
        # unmodified rather than mangled; tests/test_service_worker.py asserts
        # the substitution happens, so this cannot pass CI unnoticed.
        body = source
    response = Response(content=body, media_type="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/api/status")
async def full_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Return comprehensive app status for the frontend dashboard."""
    return _build_status_payload(db, current_user)


# ── Cost Analytics ─────────────────────────────────────────────────────


@app.get("/api/costs")
async def api_costs_overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Per-provider cost totals for the current month plus lifetime totals and rate-limit gauge.
    Monthly window: trailing 30 days from now."""
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    month_start = now - datetime.timedelta(days=30)
    providers = db.query(Transcript.provider).filter(
        Transcript.user_id == current_user.id,
        Transcript.provider.isnot(None),
    ).distinct().all()
    provider_costs = {}
    lifetime_total = 0.0
    monthly_total = 0.0
    for (p,) in providers:
        if not p:
            continue
        pc = provider_cost(db, current_user.id, p, month_start)
        gauge = get_rate_limit_gauge(db, current_user.id, p)
        pc["used_today_seconds"] = gauge["used_seconds"]
        pc["limit_today_seconds"] = gauge["limit_seconds"]
        pc["used_today_cost"] = gauge["used_cost"]
        pc["limit_today_cost"] = gauge["limit_cost"]
        pc["resets_in_seconds"] = gauge["resets_in_seconds"]
        provider_costs[p] = pc
        monthly_total += pc.get("total_cost", 0.0)

    epoch = datetime.datetime(2020, 1, 1)
    for (p,) in providers:
        if not p:
            continue
        lt = provider_cost(db, current_user.id, p, epoch)
        lifetime_total += lt.get("total_cost", 0.0)

    primary_gauge = get_rate_limit_gauge(db, current_user.id, "groq")

    return {
        "providers": provider_costs,
        "monthly_total": round(monthly_total, 4),
        "lifetime_total": round(lifetime_total, 4),
        "rate_limit_gauge": primary_gauge,
    }


@app.get("/api/transcripts/{transcript_id}/cost")
async def api_transcript_cost(transcript_id: int, db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    """Cost breakdown for a single transcript."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id,
        Transcript.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript_cost(db, t)


@app.post("/api/costs/estimate")
async def api_cost_estimate(data: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Pre-submit STT cost estimate. Accepts {provider, model, duration_seconds}."""
    provider = (data.get("provider") or "").strip()
    model = (data.get("model") or "").strip()
    duration = data.get("duration_seconds")
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    if duration is None or not isinstance(duration, (int, float)):
        raise HTTPException(status_code=400, detail="duration_seconds is required and must be a number")
    if duration < 0:
        raise HTTPException(status_code=400, detail="duration_seconds must be non-negative")
    return estimate_cost(provider, model, float(duration))


# ── Serve Static Files ───────────────────────────────────────────────────

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 46)
    print("         WhisperDeck v0.8")
    print("  Transcribe - Diarize - Summarize - Identify")
    print("=" * 46)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 9781)), reload=False)
