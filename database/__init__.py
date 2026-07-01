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
    status = Column(String(32), default="pending")  # pending, processing, completed, failed
    full_text = Column(Text, default="")
    segments = Column(JSON, default=list)  # [{start, end, speaker, text}]
    speaker_count = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    summary = relationship("Summary", back_populates="transcript", uselist=False, cascade="all, delete-orphan")


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
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal, migrated_tables


__all__ = [
    "Base", "User", "Transcript", "Summary", "VoiceProfile", "ProviderConfig",
    "init_db", "migrate_schema", "backfill_user_id",
]
