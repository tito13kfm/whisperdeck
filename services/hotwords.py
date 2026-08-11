"""Persistent per-user hotword glossary — manual entries plus terms
auto-extracted from pasted meeting-context docs (see services/correction.py).
Feeds the post-hoc correction pass and, for gpt-transcribe, transcription-time
keywords context (see backends/openai.py, backends/openrouter.py)."""
from sqlalchemy import func

from database import HotwordEntry


def sanitize_keywords(terms: list[str]) -> list[str]:
    """Sanitize glossary terms before sending as OpenAI keywords.

    OpenAI rejects the entire request if any keyword contains < > CR LF.
    Drop those terms rather than stripping chars (which could produce empty
    or misleading keywords). Also strips whitespace and drops empties.
    """
    out: list[str] = []
    for t in terms:
        if not t:
            continue
        s = t.strip()
        if not s:
            continue
        if any(c in s for c in ("<", ">", "\r", "\n")):
            continue
        out.append(s)
    return out


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
