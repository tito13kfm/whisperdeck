"""Cross-transcript search using SQLite FTS5 full-text index (issue #108).

search_transcripts(): FTS5 MATCH identifies matching transcripts; a secondary
Python pass over segments JSON extracts per-segment matches for the assistant.

search_transcripts_snippets(): FTS5 snippet() returns HTML-highlighted excerpts
for the web UI search results panel.
"""
from sqlalchemy import text

from database import Transcript

_MAX_QUERY_CHARS = 500


def _sanitize_fts5_query(query: str) -> str:
    """Wrap each whitespace-separated term in double-quotes for literal FTS5
    matching. Embedded double-quotes are escaped by doubling (SQLite FTS5
    convention). Preserves special characters like ':' that are otherwise
    FTS5 syntax."""
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

    # FTS5 MATCH to find matching transcript IDs, then load ORM objects
    # with user_id and status filters applied in Python (FTS5 is a separate
    # virtual table, not part of the Transcript ORM).
    row = db.execute(
        text(
            "SELECT transcript_id FROM transcripts_fts "
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

    Returns [{transcript_id, title, filename, snippet (HTML with <b> tags),
              rank (float), match_source (str), created_at (str)}].

    Empty query returns []. Results sorted by FTS5 rank (most relevant first).
    """
    query = (query or "").strip()
    if not query:
        return []

    fts5_query = _sanitize_fts5_query(query)

    rows = db.execute(
        text(
            "SELECT "
            "f.transcript_id, "
            "f.rank, "
            "t.title, "
            "t.filename, "
            "t.created_at, "
            "snippet(transcripts_fts, 0, '<b>', '</b>', '…', 32) AS snippet, "
            "CASE "
            "  WHEN transcripts_fts MATCH :q_full THEN 'full_text' "
            "  WHEN transcripts_fts MATCH :q_corrected THEN 'corrected_text' "
            "  WHEN transcripts_fts MATCH :q_segments THEN 'segment_text' "
            "  WHEN transcripts_fts MATCH :q_title THEN 'title' "
            "  ELSE 'full_text' "
            "END AS match_source "
            "FROM transcripts_fts f "
            "JOIN transcripts t ON t.id = f.transcript_id "
            "WHERE transcripts_fts MATCH :q "
            "AND t.user_id = :uid "
            "AND t.status = 'completed' "
            "ORDER BY rank "
            "LIMIT :lim"
        ),
        {
            "q": fts5_query,
            "q_full": f"full_text: {fts5_query}",
            "q_corrected": f"corrected_text: {fts5_query}",
            "q_segments": f"segment_text: {fts5_query}",
            "q_title": f"title: {fts5_query}",
            "uid": user_id,
            "lim": limit,
        },
    ).fetchall()

    return [
        {
            "transcript_id": r[0],
            "rank": r[1],
            "title": r[2],
            "filename": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
            "snippet": r[5],
            "match_source": r[6],
        }
        for r in rows
    ]
