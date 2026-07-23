"""SQLAlchemy models for WhisperDeck."""
import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey,
    JSON, Boolean, UniqueConstraint, create_engine, inspect, text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


def utcnow_naive():
    """Current UTC time as a naive datetime (datetime.utcnow() is deprecated)."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    password_salt = Column(String(64), nullable=False)
    settings = Column(JSON, default=dict)
    is_admin = Column(Boolean, default=False)
    reset_token = Column(String(128), nullable=True)
    reset_token_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    kind = Column(String(16), default="meeting")  # meeting | dictation — drives default diarization, summary prompt, and available reformat actions
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
    video_path = Column(String(512), nullable=True)  # original upload, kept only if it had a video stream — see services/audio_prep.py:has_video_stream
    stereo_audio_path = Column(String(512), nullable=True)  # 16 kHz stereo FLAC of a live capture (mic=ch0, system=ch1); NULL for ordinary uploads
    diarize_requested = Column(Boolean, default=False)
    num_speakers = Column(Integer, nullable=True)  # None = auto-detect (pyannote only; heuristic fallback defaults to 2)
    diarization_method = Column(String(32), nullable=True)  # pyannote | heuristic | live_stereo; NULL = never diarized or pre-migration
    processed_size_bytes = Column(Integer, nullable=True)  # post-transcode size (sum of chunk files if chunked) — NOT the raw upload size
    corrected_text = Column(Text, nullable=True)
    correction_error = Column(Text, nullable=True)
    correction_model = Column(String(128), nullable=True)  # e.g. "groq/llama-3.3-70b-versatile"
    queue_dismissed = Column(Boolean, default=False)  # hides a terminal transcription entry from the Queue screen only
    source_transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=True)  # root transcript this was retranscribed from, for version comparison
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    summary = relationship("Summary", back_populates="transcript", uselist=False, cascade="all, delete-orphan")
    jobs = relationship("TranscriptionJob", back_populates="transcript", cascade="all, delete-orphan")
    # ORM-level cascade is load-bearing: the FK's ondelete="CASCADE" never
    # fires because SQLite's foreign_keys pragma is off (never enabled by
    # this app), and without it deleting a transcript orphans its history —
    # then SQLite's rowid reuse can hand the next transcript the dead one's
    # id, resurrecting a foreign undo entry onto the wrong transcript.
    relabel_history = relationship("RelabelHistory", cascade="all, delete-orphan")


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
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    transcript = relationship("Transcript", back_populates="jobs")


class LlmJob(Base):
    """Background LLM work (correction / summary) against a transcript.
    Powers the Queue screen: status, batch progress, cancel/rerun."""
    __tablename__ = "llm_jobs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(32), nullable=False)  # correction | summary
    status = Column(String(32), default="pending")  # pending, running, completed, failed, cancelled
    attempts = Column(Integer, default=0)  # incremented at claim time in llm_worker_tick — powers auto-retry backoff
    progress_done = Column(Integer, default=0)
    progress_total = Column(Integer, default=0)
    provider = Column(String(64), default="")
    model = Column(String(128), default="")
    error = Column(Text, nullable=True)
    dismissed = Column(Boolean, default=False)  # hides a terminal job from the Queue screen only
    result_json = Column(JSON, nullable=True)  # output snapshot for history/diff — see run_llm_job
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


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


class HotwordEntry(Base):
    __tablename__ = "hotword_entries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    term = Column(String(255), nullable=False)
    source = Column(String(16), default="manual")  # "manual" | "extracted"
    created_at = Column(DateTime, default=utcnow_naive)


class Summary(Base):
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=False)
    short_summary = Column(Text, default="")
    key_points = Column(JSON, default=list)
    action_items = Column(JSON, default=list)
    decisions = Column(JSON, default=list)
    model = Column(String(64), default="")
    provider = Column(String(64), default="")
    created_at = Column(DateTime, default=utcnow_naive)

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
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class VoiceClip(Base):
    """One enrolled audio clip backing a VoiceProfile. A profile's match
    embedding is the mean of its clips' embeddings, recomputed whenever a
    clip is added or removed (see services/voice_id.py)."""
    __tablename__ = "voice_clips"

    id = Column(Integer, primary_key=True)
    voice_profile_id = Column(Integer, ForeignKey("voice_profiles.id", ondelete="CASCADE"), nullable=False)
    audio_path = Column(String(512), nullable=False)
    embedding = Column(JSON, nullable=False)  # this clip's own embedding, list of floats
    embedding_model = Column(String(64), nullable=True)  # backend that produced THIS clip's vector; NULL = pre-migration row
    source_transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)


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


def backfill_llm_job_result_snapshots(SessionLocal, kinds: tuple = ("correction", "summary", "rediarize")) -> None:
    """One-time backfill: completed LlmJob rows that predate result_json have
    no output snapshot. Fill in the latest completed job per (transcript_id,
    kind) from the transcript's current output, so the run-history picker
    isn't empty for pre-existing data. Older, already-superseded completed
    jobs never had their output retained anywhere — they stay snapshot-less
    by design (not a bug: nothing before this feature kept that history).
    Safe to call on every startup — only touches rows still missing a
    snapshot, so it's a no-op once backfilled."""
    from sqlalchemy import func

    db = SessionLocal()
    try:
        latest_ids = (
            db.query(LlmJob.transcript_id, LlmJob.kind, func.max(LlmJob.id).label("max_id"))
            .filter(LlmJob.status == "completed", LlmJob.kind.in_(kinds))
            .group_by(LlmJob.transcript_id, LlmJob.kind)
            .all()
        )
        for transcript_id, kind, max_id in latest_ids:
            job = db.query(LlmJob).filter(LlmJob.id == max_id).first()
            transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
            if not job or not transcript or job.result_json is not None:
                continue
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
        db.commit()
    finally:
        db.close()


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
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER", "processed_size_bytes": "INTEGER", "corrected_text": "TEXT", "correction_error": "TEXT", "correction_model": "TEXT", "queue_dismissed": "BOOLEAN DEFAULT 0", "source_transcript_id": "INTEGER", "video_path": "TEXT", "kind": "TEXT DEFAULT 'meeting'", "diarization_method": "TEXT", "stereo_audio_path": "TEXT"})
    ensure_columns(engine, "llm_jobs", {"dismissed": "BOOLEAN DEFAULT 0", "result_json": "JSON", "attempts": "INTEGER DEFAULT 0"})
    ensure_columns(engine, "summaries", {"provider": "TEXT"})
    ensure_columns(engine, "users", {"is_admin": "BOOLEAN DEFAULT 0", "reset_token": "TEXT", "reset_token_expires_at": "TEXT"})
    ensure_columns(engine, "voice_clips", {"embedding_model": "TEXT"})
    SessionLocal = sessionmaker(bind=engine)
    backfill_llm_job_result_snapshots(SessionLocal)

    # First-user-is-admin migration: if any user exists and no admin exists,
    # promote the earliest-created user to admin.
    _db = SessionLocal()
    try:
        from sqlalchemy import text as _text
        admin_count = _db.query(User).filter(User.is_admin == True).count()  # noqa: E712
        total_users = _db.query(User).count()
        if total_users > 0 and admin_count == 0:
            first_user = _db.query(User).order_by(User.id.asc()).first()
            if first_user:
                first_user.is_admin = True
                _db.commit()
    finally:
        _db.close()

    return engine, SessionLocal, migrated_tables


__all__ = [
    "Base", "User", "Transcript", "Summary", "VoiceProfile", "VoiceClip", "ProviderConfig", "TranscriptionJob", "LlmJob", "RelabelHistory", "HotwordEntry",
    "init_db", "migrate_schema", "backfill_user_id", "ensure_columns", "backfill_llm_job_result_snapshots",
]
