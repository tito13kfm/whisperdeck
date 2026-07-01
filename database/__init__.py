"""SQLAlchemy models for WhisperDeck."""
import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey,
    JSON, Boolean, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True)
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

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, unique=True)
    embedding = Column(JSON, nullable=True)  # stored as list of floats
    embedding_model = Column(String(64), default="speechbrain/spkrec-ecapa-voxceleb")
    sample_count = Column(Integer, default=0)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)  # groq, openai, replicate, local
    display_name = Column(String(128), default="")
    api_key = Column(String(512), default="")
    api_url = Column(String(512), default="")
    default_model = Column(String(64), default="")
    is_active = Column(Boolean, default=True)
    config = Column(JSON, default=dict)


def init_db(db_path: str = "data/whisperdesk.db") -> tuple:
    """Initialize the database and return engine + session factory.

    Returns a sessionmaker, not a live session — callers create one
    session per request (see app.py's get_db dependency) rather than
    sharing a single session across all concurrent requests.
    """
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal


__all__ = ["Base", "Transcript", "Summary", "VoiceProfile", "ProviderConfig", "init_db"]