"""Cross-transcript search across full_text, corrected_text, and segments JSON.

Splits the query into whitespace-delimited terms, ANDs them with escaped LIKE,
then does a secondary Python pass over segments JSON for per-segment matching.
"""
import json
import re

from sqlalchemy import and_, or_

from database import Transcript

_MAX_QUERY_CHARS = 500


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so user-input `%` and `_` are literal."""
    return term.replace("%", "\\%").replace("_", "\\_")


def _split_terms(query: str) -> list[str]:
    """Split on whitespace, drop empty strings."""
    return [t for t in query.split() if t]


def _matches_segment(seg: dict, terms: list[str]) -> bool:
    """True if any term appears in the segment text (case-insensitive)."""
    text = (seg.get("text") or "").lower()
    return any(term.lower() in text for term in terms)


def search_transcripts(db, user_id: int, query: str) -> list[dict]:
    """Search all of a user's transcripts for matching terms.

    Returns a list of dicts, each representing one matching transcript:
        [{transcript_id, title, filename,
          matching_segments: [{speaker, text, start, end}]}]

    Query over 500 chars raises ValueError (caller returns 400).
    Empty query returns [].
    """
    query = (query or "").strip()

    if not query:
        return []

    if len(query) > _MAX_QUERY_CHARS:
        raise ValueError(f"Query exceeds {_MAX_QUERY_CHARS} characters")

    terms = _split_terms(query)
    if not terms:
        return []

    escaped_terms = [_escape_like(t) for t in terms]

    # Build AND-ed LIKE clauses: full_text LIKE '%term%' AND full_text LIKE '%other%'
    # Search across full_text, corrected_text, and the raw segments JSON column.
    # Use ESCAPE '\\' so user-input % and _ don't act as wildcards.
    like_clauses = []
    for term in escaped_terms:
        like_clauses.append(
            or_(
                Transcript.full_text.like(f"%{term}%", escape="\\"),
                Transcript.corrected_text.like(f"%{term}%", escape="\\"),
                # segments is stored as JSON text — the raw column contains the
                # JSON string, so LIKE on the column catches term matches in
                # segment text without a separate Python parse for each row.
                Transcript.segments.like(f"%{term}%", escape="\\"),
            )
        )

    transcripts = (
        db.query(Transcript)
        .filter(Transcript.user_id == user_id)
        .filter(and_(*like_clauses))
        .filter(Transcript.status == "completed")  # only finished transcripts have reliable text
        .all()
    )

    results = []
    for t in transcripts:
        segments = t.segments or []
        matching_segments = [
            {
                "speaker": seg.get("speaker", ""),
                "text": seg.get("text", ""),
                "start": seg.get("start"),
                "end": seg.get("end"),
            }
            for seg in segments
            if _matches_segment(seg, terms)
        ]
        results.append({
            "transcript_id": t.id,
            "title": t.title,
            "filename": t.filename,
            "matching_segments": matching_segments,
        })

    return results
