"""Persistent per-user hotword glossary — manual entries plus terms
auto-extracted from pasted meeting-context docs (see services/correction.py).
Feeds the post-hoc correction pass, never the transcription-time prompt."""
from sqlalchemy import func

from database import HotwordEntry


def list_hotwords(db, user_id: int) -> list[HotwordEntry]:
    return db.query(HotwordEntry).filter(HotwordEntry.user_id == user_id).all()


def add_hotword(db, user_id: int, term: str, source: str = "manual") -> HotwordEntry:
    """Insert a new glossary term, or return the existing entry if this
    user already has the same term (case-insensitive). The existing
    entry's source is never overwritten by a later dup attempt."""
    term = term.strip()
    existing = (
        db.query(HotwordEntry)
        .filter(HotwordEntry.user_id == user_id)
        .filter(func.lower(HotwordEntry.term) == term.lower())
        .first()
    )
    if existing:
        return existing

    entry = HotwordEntry(user_id=user_id, term=term, source=source)
    db.add(entry)
    db.commit()
    return entry


def delete_hotword(db, user_id: int, hotword_id: int) -> bool:
    entry = (
        db.query(HotwordEntry)
        .filter(HotwordEntry.id == hotword_id, HotwordEntry.user_id == user_id)
        .first()
    )
    if not entry:
        return False
    db.delete(entry)
    db.commit()
    return True
