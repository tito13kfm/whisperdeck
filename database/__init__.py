"""SQLAlchemy models for WhisperDeck."""
import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey,
    JSON, Boolean, UniqueConstraint, create_engine, event, inspect, text
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
    local_device_token_hash = Column(String(128), nullable=True)
    local_device_token_created_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)


class InviteToken(Base):
    """Single-use registration invite (issue #395). The plaintext token is
    shown to the minting admin exactly once; only its SHA-256 is stored,
    same rationale as User.reset_token. Consumed by a compare-and-set
    UPDATE on used_at so two concurrent registrations cannot share one."""
    __tablename__ = "invite_tokens"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(128), unique=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    kind = Column(String(16), default="meeting")  # meeting | dictation | voice_note | voice_dump | auto — drives default diarization, summary prompt, available reformat actions, and whether the voice-note/voice-dump LLM chain fires
    # Studio classification (issue #267/design 2026-08-01-studio-classification-design.md).
    # classification_status: pending | success | uncertain | failed | override.
    # A separate column from `status` above (transcription lifecycle) —
    # same name would collide with an unrelated concept.
    classification_status = Column(String(16), default="override")
    classification_confidence = Column(Float, nullable=True)  # present only when status is success/uncertain
    classification_provenance = Column(JSON, nullable=True)  # {provider, model, schema_version, classified_at} | {legacy_migration: true} | override metadata
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
    diarization_method = Column(String(32), nullable=True)  # pyannote | heuristic | live_stereo | heuristic (pyannote failed) | failed; NULL = never diarized or pre-migration
    processed_size_bytes = Column(Integer, nullable=True)  # post-transcode size (sum of chunk files if chunked) — NOT the raw upload size
    corrected_text = Column(Text, nullable=True)
    correction_error = Column(Text, nullable=True)
    correction_model = Column(String(128), nullable=True)  # e.g. "groq/llama-3.3-70b-versatile"
    context_extraction_error = Column(Text, nullable=True)  # upload-time hotword extraction failure; transcription still runs (issue #310)
    queue_dismissed = Column(Boolean, default=False)  # hides a terminal transcription entry from the Queue screen only
    source_transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=True)  # root transcript this was retranscribed from, for version comparison
    batch_id = Column(String(64), nullable=True, index=True)  # groups transcripts from one bulk upload; NULL for single-file uploads
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    summary = relationship("Summary", back_populates="transcript", uselist=False, cascade="all, delete-orphan")
    voice_note = relationship("VoiceNote", back_populates="transcript", uselist=False, cascade="all, delete-orphan")
    voice_dump_items = relationship("VoiceDumpItem", back_populates="transcript", cascade="all, delete-orphan")
    jobs = relationship("TranscriptionJob", back_populates="transcript", cascade="all, delete-orphan")
    # ORM-level cascade is load-bearing: the FK's ondelete="CASCADE" never
    # fires because SQLite's foreign_keys pragma is off (never enabled by
    # this app), and without it deleting a transcript orphans its children —
    # then SQLite's rowid reuse can hand the next transcript the dead one's
    # id, resurrecting foreign rows onto the wrong transcript.
    relabel_history = relationship("RelabelHistory", cascade="all, delete-orphan")
    llm_jobs = relationship("LlmJob", cascade="all, delete-orphan")
    transcript_tags = relationship("TranscriptTag", cascade="all, delete-orphan")


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
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=True)
    kind = Column(String(32), nullable=False)  # correction | summary | assistant | ...
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


class VoiceNote(Base):
    """Structured output of a voice-note capture (issue #169). One row per
    transcript — a re-run of the chain overwrites the row in place, mirroring
    how `Summary` is the only-summary per transcript. The chain itself
    lives in `LlmJob(kind="voice_note")`; this table is the durable,
    queryable artifact (title/body/structured fields, surfaced in the
    Notes tab on detail and the board page). The job's own `result_json`
    carries the same payload for the run-history view."""
    __tablename__ = "voice_notes"
    __table_args__ = (UniqueConstraint("transcript_id", name="uq_voice_note_transcript"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transcript_id = Column(
        Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False,
    )
    note_type = Column(String(16), nullable=False)  # todo | idea | reminder | journal | general
    title = Column(String(255), default="")
    body = Column(Text, default="")
    structured = Column(JSON, default=dict)  # per-type field bag (e.g. todo: {priority, due_date, items})
    model = Column(String(128), default="")
    provider = Column(String(64), default="")
    created_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    transcript = relationship("Transcript", back_populates="voice_note")


class VoiceDumpItem(Base):
    """One item from a multi-item voice-dump capture (issue #283).
    Many rows per transcript — no unique constraint on transcript_id.
    Structured payload mirrors the VoiceNote shape (title/body/structured)
    but each row is one discrete item (bug, idea, todo, reminder, etc.)
    extracted from a single long dictation."""
    __tablename__ = "voice_dump_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transcript_id = Column(
        Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False,
    )
    source_job_id = Column(Integer, ForeignKey("llm_jobs.id"), nullable=True)  # LlmJob(kind="voice_dump")
    sequence_index = Column(Integer, nullable=False, default=0)
    note_type = Column(String(16), nullable=False)  # bug | idea | todo | reminder (TBD in #284)
    title = Column(String(255), default="")
    body = Column(Text, default="")
    structured = Column(JSON, default=dict)
    model = Column(String(128), default="")
    provider = Column(String(64), default="")
    created_at = Column(DateTime, default=utcnow_naive)
    seen_at = Column(DateTime, nullable=True)  # NULL = unseen; set when user visits Dump Notes board

    transcript = relationship("Transcript", back_populates="voice_dump_items")


class TranscriptTag(Base):
    """One tag (free-form short label) attached to a transcript. Multiple
    rows per transcript are allowed; the LLM tagging job derives 1-5 tags
    per transcript and writes one row per tag. The (transcript_id, tag) pair
    is the natural primary key — no surrogate id needed, and it dedupes
    the same tag across re-tagging runs (a re-run replaces the prior set,
    not appends; see run_llm_job's `tagging` branch). `tag` is stored
    canonicalized (lowercased + trimmed) so the same topic collapses to
    one row across display, dedupe, and filter queries. Issue #171.

    Indexed on `tag` alone so the browse-by-tag query
    (`SELECT transcript_id FROM transcript_tags WHERE tag = ?`) is a
    covering index lookup, not a full scan. The primary key already covers
    the (transcript_id → tags) direction.
    """
    __tablename__ = "transcript_tags"
    transcript_id = Column(
        Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), primary_key=True,
    )
    tag = Column(String(64), primary_key=True)
    created_at = Column(DateTime, default=utcnow_naive)


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


def ensure_nullable_llm_job_transcript_id(engine) -> None:
    """Make transcript_id nullable on existing llm_jobs tables (issue #175).

    SQLite can't alter column constraints, so we rename the old table,
    recreate it with the updated (nullable) schema, copy rows, and drop the
    old copy. On fresh databases create_all() already builds the column as
    nullable — this function is a no-op for those.
    """
    inspector = inspect(engine)
    if "llm_jobs" not in inspector.get_table_names():
        return

    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(llm_jobs)")).fetchall()
        for row in rows:
            if row[1] == "transcript_id" and row[3] == 0:
                return  # already nullable — nothing to do

    # Recreate with nullable transcript_id
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE llm_jobs RENAME TO llm_jobs_old"))
        conn.execute(text(
            "CREATE TABLE llm_jobs ("
            "id INTEGER NOT NULL, "
            "user_id INTEGER NOT NULL, "
            "transcript_id INTEGER, "
            "kind VARCHAR(32) NOT NULL, "
            "status VARCHAR(32), "
            "attempts INTEGER, "
            "progress_done INTEGER, "
            "progress_total INTEGER, "
            "provider VARCHAR(64), "
            "model VARCHAR(128), "
            "error TEXT, "
            "dismissed BOOLEAN, "
            "result_json JSON, "
            "created_at DATETIME, "
            "updated_at DATETIME, "
            "PRIMARY KEY (id), "
            "FOREIGN KEY(user_id) REFERENCES users (id), "
            "FOREIGN KEY(transcript_id) REFERENCES transcripts (id) ON DELETE CASCADE"
            ")"
        ))
        conn.execute(text("INSERT INTO llm_jobs SELECT * FROM llm_jobs_old"))
        conn.execute(text("DROP TABLE llm_jobs_old"))


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


def classification_columns_were_absent(engine) -> bool:
    """Must be called right after create_all() and before ensure_columns()
    adds classification_status/_confidence/_provenance to "transcripts" —
    that's the only moment column absence is observable. The result feeds
    backfill_legacy_classification() later, once SessionLocal exists.
    Every other startup ORM query (backfill_llm_job_result_snapshots,
    populate_fts, etc.) runs after ensure_columns has already added these
    columns, so none of them ever see a pre-#267 schema."""
    inspector = inspect(engine)
    if "transcripts" not in inspector.get_table_names():
        return False  # fresh DB — create_all() already built the column
    existing = {c["name"] for c in inspector.get_columns("transcripts")}
    return "classification_provenance" not in existing


def backfill_legacy_classification(SessionLocal, was_absent: bool) -> int:
    """One-time migration for issue #267 (design decision 7): every transcript
    that existed before classification_provenance was added becomes a
    permanent manual-override record — status=override, no confidence,
    provenance={legacy_migration: true}. None are retroactively classified.

    `was_absent` (from classification_columns_were_absent(), captured before
    ensure_columns() ran) is what makes this one-time: a null provenance on
    a later restart can also mean "created after this shipped, not yet
    classified" (once #268 introduces the 'auto' kind sentinel), which must
    never be relabeled as legacy.
    """
    if not was_absent:
        return 0
    db = SessionLocal()
    try:
        count = (
            db.query(Transcript)
            .update(
                {"classification_status": "override", "classification_provenance": {"legacy_migration": True}},
                synchronize_session=False,
            )
        )
        db.commit()
        return count
    finally:
        db.close()


def populate_fts(engine) -> None:
    """Backfill transcripts_fts and the segment_text column from existing data.

    Finds completed transcripts whose FTS index entries are missing (via
    the _docsize shadow table — external-content mode's reliable membership
    indicator). Computes segment_text from JSON segments, writes it to the
    content table, and inserts the FTS index row. Only one FTS entry per
    transcript: when segment_text was NULL the UPDATE trigger indexes the
    row; when segment_text already exists, an explicit INSERT is used.
    Idempotent — rows already indexed are excluded by the anti-join.
    """
    transcript_table = "transcripts"
    inspector = inspect(engine)
    if transcript_table not in inspector.get_table_names():
        return
    if "transcripts_fts" not in inspector.get_table_names():
        return

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.id, t.title, t.full_text, t.corrected_text, "
                "       t.segments, t.segment_text "
                "FROM transcripts t "
                "WHERE t.status = 'completed' "
                "  AND NOT EXISTS (SELECT 1 FROM transcripts_fts_docsize d WHERE d.id = t.id)"
            )
        ).fetchall()

    if not rows:
        return

    import json

    with engine.begin() as conn:
        for t_id, title, full_text, corrected_text, segments, existing_st in rows:
            segment_text = ""
            if segments:
                try:
                    segs = json.loads(segments) if isinstance(segments, str) else segments
                    segment_text = " ".join(s.get("text", "") for s in segs if isinstance(s, dict))
                except (json.JSONDecodeError, TypeError):
                    pass

            if not existing_st:
                # The one purpose segment_text serves (issue #191): this write
                # is a lever, not data. Any UPDATE on the row fires
                # trg_transcripts_fts_update, whose insert half indexes the row
                # with segment terms derived from the segments JSON — the value
                # written here is never what gets indexed, and stays NULL on
                # every row the ORM creates.
                conn.execute(
                    text("UPDATE transcripts SET segment_text = :st WHERE id = :tid"),
                    {"st": segment_text, "tid": t_id},
                )
            else:
                # segment_text already set, insert FTS row directly
                conn.execute(
                    text(
                        "INSERT INTO transcripts_fts "
                        "(rowid, title, full_text, corrected_text, segment_text) "
                        "VALUES (:tid, :title, :ft, :ct, :st)"
                    ),
                    {"tid": t_id, "title": title or "", "ft": full_text or "",
                     "ct": corrected_text or "", "st": segment_text},
                )


_FTS_REINDEX_CHUNK = 500


def cleanup_fts_orphans(engine) -> int:
    """Drop FTS index entries whose transcripts row no longer exists.

    Returns the number of orphaned entries removed. Databases created before
    the AFTER DELETE trigger (issue #309) kept the index entry of every deleted
    transcript forever. Search results were unaffected because both search
    paths JOIN back to transcripts, but the orphans inflate the index and skew
    FTS5 ranking, since term frequencies still count the deleted documents.
    Existing installs do not self-heal, so this runs once per startup and is a
    no-op as soon as there is nothing to clean.

    Deliberately NOT `INSERT INTO transcripts_fts(transcripts_fts)
    VALUES('rebuild')`, which is the obvious repair and corrupts the database.
    A rebuild re-reads the content table, so it indexes the literal
    segment_text column, while all three triggers index a value derived from
    the segments JSON. Two things follow: every row loses its segment terms,
    and the index stops agreeing with what the triggers believe is indexed, so
    the next trigger-issued 'delete' carries derived segment text against an
    index entry holding NULL. Those values must match in external-content mode.
    They do not, and the next DELETE fails with "database disk image is
    malformed". `integrity-check` returns OK right after the rebuild, so that
    corruption is latent and a post-cleanup integrity-check would pass.

    What runs instead: capture the current index membership, 'delete-all', then
    reinsert using the same derived expression the triggers use. Membership has
    to be captured rather than recomputed, because it is not expressible as a
    predicate. The INSERT trigger indexes every row regardless of status while
    populate_fts() only backfilled `status = 'completed'` rows, so an existing
    index holds "all post-FTS rows plus completed pre-FTS rows". Reindexing
    "all rows" would pull previously unindexed rows in and skew the very term
    frequencies this cleanup exists to correct.

    Everything runs on one connection inside one transaction. That is load
    bearing: `engine.begin()` rolls the whole sequence back if a reinsert
    fails, where a wipe committed separately from its reinsert would leave the
    index empty.
    """
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "transcripts" not in table_names or "transcripts_fts" not in table_names:
        return 0

    # Identical to the expression in all three triggers, aliased to `t`.
    segment_text_sql = (
        "COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') "
        "FROM json_each(t.segments)), '')"
    )

    with engine.begin() as conn:
        orphan_count = conn.execute(text(
            "SELECT COUNT(*) FROM transcripts_fts_docsize d "
            "WHERE NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.id = d.id)"
        )).scalar()
        if not orphan_count:
            return 0

        keep = [row[0] for row in conn.execute(text(
            "SELECT d.id FROM transcripts_fts_docsize d "
            "JOIN transcripts t ON t.id = d.id"
        )).fetchall()]

        conn.execute(text("INSERT INTO transcripts_fts(transcripts_fts) VALUES('delete-all')"))

        # title, full_text and corrected_text are selected raw, deliberately
        # not coalesced to '': the triggers pass them raw as well, and the
        # values supplied to a later 'delete' have to be the ones that were
        # indexed. Chunked to stay under SQLite's bound-parameter limit, which
        # is 999 on older builds.
        for start in range(0, len(keep), _FTS_REINDEX_CHUNK):
            chunk = keep[start:start + _FTS_REINDEX_CHUNK]
            placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
            conn.execute(
                text(
                    "INSERT INTO transcripts_fts "
                    "(rowid, title, full_text, corrected_text, segment_text) "
                    "SELECT t.id, t.title, t.full_text, t.corrected_text, "
                    f"{segment_text_sql} "
                    f"FROM transcripts t WHERE t.id IN ({placeholders})"
                ),
                {f"id{i}": tid for i, tid in enumerate(chunk)},
            )

    return orphan_count


def init_db(db_path: str = "data/whisperdesk.db") -> tuple:
    """Initialize the database. Returns (engine, SessionLocal, migrated_tables).

    SessionLocal is a sessionmaker, not a live session — callers create one
    session per request (see app.py's get_db dependency) rather than
    sharing a single session across all concurrent requests.

    migrated_tables is the list from migrate_schema() — non-empty only on
    the first startup against a pre-existing pre-auth database. Callers
    use it to trigger the one-time fallback-user backfill.
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_size=10,
        max_overflow=20,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        # WAL removes reader/writer blocking (issue #66: worker ticks and
        # per-request sessions were exhausting the default 15-connection
        # pool during bursty HTTP traffic). Must run as a PRAGMA on the raw
        # connection, before any transaction — journal_mode=WAL can't be
        # set via connect_args or inside a BEGIN.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    migrated_tables = migrate_schema(engine)
    Base.metadata.create_all(engine)
    # Must be captured here — right after create_all(), right before
    # ensure_columns() adds these same columns below. Any later check would
    # always see them present and never detect a genuinely pre-#267 database.
    _classification_cols_were_absent = classification_columns_were_absent(engine)
    ensure_columns(engine, "users", {"settings": "JSON"})
    # About "segment_text" in the list below (issue #191): it is a backfill-only
    # trigger lever, not a content column, and it stays on purpose.
    #
    # It has no ORM attribute, so every row the app creates leaves it NULL. The
    # segment terms in the FTS index are computed from the segments JSON by all
    # three triggers (group_concat over json_each), never read from this column,
    # so the FTS index deliberately disagrees with the content table here. That
    # divergence is why an `INSERT INTO transcripts_fts VALUES('rebuild')` would
    # corrupt the database — see cleanup_fts_orphans' docstring.
    #
    # Its one job is in populate_fts(): writing it is the only UPDATE that fires
    # trg_transcripts_fts_update for a pre-#108 row that has no index entry yet.
    # Dropping the column would take that lever away and leave old installs
    # unsearchable. Never read it for content — it is NULL on anything recent.
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER", "processed_size_bytes": "INTEGER", "corrected_text": "TEXT", "correction_error": "TEXT", "correction_model": "TEXT", "context_extraction_error": "TEXT", "queue_dismissed": "BOOLEAN DEFAULT 0", "source_transcript_id": "INTEGER", "batch_id": "TEXT", "video_path": "TEXT", "kind": "TEXT DEFAULT 'meeting'", "diarization_method": "TEXT", "stereo_audio_path": "TEXT", "segment_text": "TEXT", "classification_status": "TEXT DEFAULT 'override'", "classification_confidence": "REAL", "classification_provenance": "JSON"})
    ensure_columns(engine, "llm_jobs", {"dismissed": "BOOLEAN DEFAULT 0", "result_json": "JSON", "attempts": "INTEGER DEFAULT 0"})
    ensure_nullable_llm_job_transcript_id(engine)
    ensure_columns(engine, "summaries", {"provider": "TEXT"})
    ensure_columns(engine, "users", {"is_admin": "BOOLEAN DEFAULT 0", "reset_token": "TEXT", "reset_token_expires_at": "TEXT"})
    ensure_columns(engine, "users", {"local_device_token_hash": "TEXT", "local_device_token_created_at": "TEXT"})
    ensure_columns(engine, "voice_clips", {"embedding_model": "TEXT"})
    # Capture whether the seen_at column existed right before ensure_columns adds it,
    # so the backfill runs exactly once — not on every startup (issue #374).
    _vd_seen_at_was_absent = (
        "voice_dump_items" in inspect(engine).get_table_names()
        and "seen_at" not in {c["name"] for c in inspect(engine).get_columns("voice_dump_items")}
    )
    ensure_columns(engine, "voice_dump_items", {"seen_at": "DATETIME"})
    if _vd_seen_at_was_absent:
        # Backfill existing dump-note rows as seen so deploy day doesn't spike
        # the badge with every historical item.
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE voice_dump_items SET seen_at = datetime('now')"
            ))
    # Brand-new table for issue #171; create_all() above handles fresh DBs,
    # this idempotent CREATE handles DBs that pre-date the model.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS transcript_tags ("
            "transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE, "
            "tag VARCHAR(64) NOT NULL, "
            "created_at DATETIME, "
            "PRIMARY KEY (transcript_id, tag)"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_transcript_tags_tag ON transcript_tags (tag)"
        ))
        # Brand-new table for issue #395, same fresh-vs-predating split as
        # transcript_tags above.
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS invite_tokens ("
            "id INTEGER PRIMARY KEY, "
            "token_hash VARCHAR(128) NOT NULL UNIQUE, "
            "created_by INTEGER REFERENCES users(id), "
            "created_at DATETIME, "
            "expires_at DATETIME NOT NULL, "
            "used_at DATETIME, "
            "used_by INTEGER REFERENCES users(id)"
            ")"
        ))
        # FTS5 full-text search over transcript content (issue #108).
        # FTS5 full-text search over transcript content (issue #108).
        # External-content mode with content='transcripts': FTS5 reads
        # original text from the transcripts table for snippet(). A
        # denormalized segment_text column (added via ensure_columns below)
        # holds the concatenated segment text so FTS5 can index it.
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5("
            "title,"
            "full_text,"
            "corrected_text,"
            "segment_text,"
            "content='transcripts',"
            "content_rowid='id',"
            "tokenize='porter unicode61'"
            ")"
        ))
        # Trigger: AFTER INSERT — sync FTS index.
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS trg_transcripts_fts_insert "
            "AFTER INSERT ON transcripts BEGIN "
            "INSERT INTO transcripts_fts(rowid, title, full_text, corrected_text, segment_text) "
            "VALUES ("
            "NEW.id, NEW.title, NEW.full_text, NEW.corrected_text, "
            "COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') FROM json_each(NEW.segments)), '')"
            "); END"
        ))
        # Trigger: AFTER UPDATE deletes the old FTS row then inserts the new
        # one, keeping one entry per rowid. The delete is routed through
        # INSERT INTO ... VALUES('delete', ...) — the FTS5 external-content
        # delete command — and must include every column the table defines
        # (title, full_text, corrected_text, segment_text), not just rowid.
        # segment_text in the delete is computed from OLD.segments (mirroring
        # the INSERT trigger) because the column itself is often NULL.
        # Note: non-MATCH queries on this table are answered from the
        # content table; use transcripts_fts_docsize for index membership
        # checks.
        # DROP + unconditional CREATE (not IF NOT EXISTS): this trigger's body
        # changed to fix #206 (stale FTS entries after UPDATE), and again to add
        # the membership guard below (#309). Any database created before either
        # fix already has a trigger named trg_transcripts_fts_update — IF NOT
        # EXISTS would see it and skip creating the corrected body, silently
        # leaving old databases broken.
        # The delete half is guarded on transcripts_fts_docsize because issuing
        # 'delete' for a rowid the index does not hold corrupts the index:
        # integrity-check fails afterwards even once the content table and the
        # index agree on membership again. Rows that were never indexed are
        # ordinary here, not hypothetical — populate_fts() below UPDATEs exactly
        # those rows to get them indexed, which is what fires this trigger for
        # them. The guard has to sit on the statement (INSERT ... SELECT ...
        # WHERE EXISTS) rather than on the trigger (WHEN ...), because the
        # insert half underneath must still run for an unindexed row: skipping
        # the whole body would leave the backfill with nothing to index.
        conn.execute(text("DROP TRIGGER IF EXISTS trg_transcripts_fts_update"))
        conn.execute(text(
            "CREATE TRIGGER trg_transcripts_fts_update "
            "AFTER UPDATE ON transcripts BEGIN "
            "INSERT INTO transcripts_fts(transcripts_fts, rowid, title, full_text, corrected_text, segment_text) "
            "SELECT 'delete', OLD.id, OLD.title, OLD.full_text, OLD.corrected_text, "
            "COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') FROM json_each(OLD.segments)), '') "
            "WHERE EXISTS (SELECT 1 FROM transcripts_fts_docsize WHERE id = OLD.id); "
            "INSERT INTO transcripts_fts(rowid, title, full_text, corrected_text, segment_text) "
            "VALUES ("
            "NEW.id, NEW.title, NEW.full_text, NEW.corrected_text, "
            "COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') FROM json_each(NEW.segments)), '')"
            "); END"
        ))
        # Trigger: AFTER DELETE — drop the row's terms from the FTS index
        # (issue #108 shipped without one, issue #309). Mirrors the delete half
        # of the UPDATE trigger above exactly, segment_text included: it is
        # computed from OLD.segments, not read from OLD.segment_text, because
        # that column is NULL on every ORM-created row and the values supplied
        # to an external-content 'delete' must match what was indexed.
        # IF NOT EXISTS is correct here, unlike the UPDATE trigger's
        # unconditional DROP + CREATE. That DROP exists because #206 changed
        # the body of a trigger old databases already had; no database has ever
        # had a trigger named trg_transcripts_fts_delete, so there is no stale
        # body to displace.
        # Guarded on transcripts_fts_docsize for the same reason as the UPDATE
        # trigger's delete half: a 'delete' for a rowid the index does not hold
        # corrupts the index. Deleting a row that was never indexed is a real
        # case, not a hypothetical one — a pre-FTS row with status != 'completed'
        # is skipped by populate_fts() and so is absent from the index for the
        # whole life of the install. Here the guard sits on the trigger (WHEN)
        # rather than on the statement, because the whole body is conditional.
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS trg_transcripts_fts_delete "
            "AFTER DELETE ON transcripts "
            "WHEN EXISTS (SELECT 1 FROM transcripts_fts_docsize WHERE id = OLD.id) "
            "BEGIN "
            "INSERT INTO transcripts_fts(transcripts_fts, rowid, title, full_text, corrected_text, segment_text) "
            "VALUES('delete', OLD.id, OLD.title, OLD.full_text, OLD.corrected_text, "
            "COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') FROM json_each(OLD.segments)), '')); "
            "END"
        ))
    # Before populate_fts(), so the anti-join there sees the corrected index
    # and the reindex below has fewer rows to rewrite. Either order reaches the
    # same final state; this one is cheaper.
    cleanup_fts_orphans(engine)
    populate_fts(engine)
    SessionLocal = sessionmaker(bind=engine)
    backfill_llm_job_result_snapshots(SessionLocal)
    backfill_legacy_classification(SessionLocal, _classification_cols_were_absent)

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
    "Base", "User", "Transcript", "Summary", "VoiceNote", "VoiceProfile", "VoiceClip", "ProviderConfig", "TranscriptionJob", "LlmJob", "RelabelHistory", "HotwordEntry", "TranscriptTag",
    "init_db", "migrate_schema", "backfill_user_id", "ensure_columns", "ensure_nullable_llm_job_transcript_id", "backfill_llm_job_result_snapshots",
    "backfill_legacy_classification", "classification_columns_were_absent",
]
