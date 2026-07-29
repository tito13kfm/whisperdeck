"""Cross-transcript search using SQLite FTS5 full-text index (issue #108).

search_transcripts(): FTS5 MATCH identifies matching transcript IDs; a secondary
Python pass over segments JSON extracts per-segment matches for the assistant.

search_transcripts_snippets(): FTS5 MATCH with snippet() returns HTML-highlighted
excerpts for the web UI. Uses external-content mode (content='transcripts') so
snippet() reads original text from the content table.
"""
from sqlalchemy import text

from database import Transcript

_MAX_QUERY_CHARS = 500


def _sanitize_fts5_query(query: str) -> str:
    """Wrap each whitespace-separated term in double-quotes for literal FTS5
    matching. Embedded double-quotes are escaped by doubling (SQLite FTS5
    convention)."""
    terms = query.split()
    sanitized = []
    for t in terms:
        escaped = t.replace('"', '""')
        sanitized.append(f'"{escaped}"')
    return " AND ".join(sanitized)


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

    terms = [t for t in query.split() if t]
    if not terms:
        return []

    fts5_query = _sanitize_fts5_query(query)

    row = db.execute(
        text(
            "SELECT rowid FROM transcripts_fts "
            "WHERE transcripts_fts MATCH :q"
        ),
        {"q": fts5_query},
    ).fetchall()

    matching_ids = [r[0] for r in row]
    if not matching_ids:
        return []

    transcripts = (
        db.query(Transcript)
        .filter(
            Transcript.id.in_(matching_ids),
            Transcript.user_id == user_id,
            Transcript.status == "completed",
        )
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


def search_transcripts_snippets(db, user_id: int, query: str, limit: int = 20) -> list[dict]:
    """Search transcripts via FTS5 and return snippet-based results for the web UI.

    Uses FTS5 snippet() with external-content mode, which reads original text
    from the content table for readable, highlighted snippets.

    Returns [{transcript_id, title, filename, snippet (HTML with <b> tags),
              rank (float), match_source (str), created_at (str)}].
    """
    query = (query or "").strip()
    if not query:
        return []

    fts5_query = _sanitize_fts5_query(query)

    rows = db.execute(
        text(
            "SELECT "
            "f.rowid AS transcript_id, "
            "f.rank, "
            "t.title, "
            "t.filename, "
            "t.created_at, "
            "snippet(transcripts_fts, 1, '<b>', '</b>', '…', 32) AS snippet "
            "FROM transcripts_fts f "
            "JOIN transcripts t ON t.id = f.rowid "
            "WHERE transcripts_fts MATCH :q "
            "AND t.user_id = :uid "
            "AND t.status = 'completed' "
            "ORDER BY rank "
            "LIMIT :lim"
        ),
        {"q": fts5_query, "uid": user_id, "lim": limit},
    ).fetchall()

    results = []
    for r in rows:
        transcript_id, rank, title, filename, created_at, snippet = r
        snippet_text = snippet or ""

        # Determine match_source from snippet content or rank context
        match_source = "full_text"
        if title and query.lower() in (title or "").lower():
            match_source = "title"

        results.append({
            "transcript_id": transcript_id,
            "rank": rank,
            "title": title,
            "filename": filename,
            "created_at": str(created_at) if created_at else None,
            "snippet": snippet_text,
            "match_source": match_source,
        })

    return results
