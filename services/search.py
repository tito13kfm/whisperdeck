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


# ── Porter stemmer lookup ──────────────────────────────────────────────────
# FTS5's Porter tokenizer stems inflected words to roots (happy/happiness →
# happi). _matches_segment does literal substring matching, which misses
# stemmed matches. Rather than replicate Porter in Python, we use a small
# in-memory FTS5 table to derive the stemmed form of each query term, then
# pass those stems as additional search terms to _matches_segment.
#
# Example: query "happy" → stem "happi" → "happi" IS a substring of
# "happiness", so the segment matches. No heuristic, no false positives.

import sqlite3 as _sqlite3
import threading as _threading

_stem_db = _sqlite3.connect(":memory:", check_same_thread=False)
_stem_db.execute("CREATE TABLE _stems(id INTEGER PRIMARY KEY, token TEXT)")
_stem_db.execute("CREATE VIRTUAL TABLE _stems_fts USING fts5(token, tokenize=porter, "
                 "content=_stems, content_rowid=id)")
_stem_db.execute("CREATE TRIGGER _stems_ai AFTER INSERT ON _stems BEGIN "
                 "INSERT INTO _stems_fts(rowid, token) VALUES(NEW.id, NEW.token); END")
_stem_db.execute("CREATE TRIGGER _stems_ad AFTER DELETE ON _stems BEGIN "
                 "INSERT INTO _stems_fts(_stems_fts, rowid, token) "
                 "VALUES('delete', OLD.id, OLD.token); END")
_stem_db.execute("CREATE VIRTUAL TABLE _stems_vocab USING fts5vocab(_stems_fts, row)")
_stem_lock = _threading.Lock()


def _stem_terms(terms: list[str]) -> set[str]:
    """Return FTS5 Porter-stemmed forms of `terms` by inserting them into a
    small in-memory FTS5 table and reading back the tokens via fts5vocab.
    The result is the set of stems that FTS5 would match against the index."""
    with _stem_lock:
        _stem_db.execute("DELETE FROM _stems")
        for t in terms:
            _stem_db.execute("INSERT INTO _stems(token) VALUES(?)", (t,))
        return {row[0] for row in _stem_db.execute("SELECT DISTINCT term FROM _stems_vocab")}


def _matches_segment(seg: dict, terms: list[str], stems: set[str] | None = None) -> bool:
    """True if any term appears in the segment text (case-insensitive).

    For terms that are Porter stems (identified by `stems`), the match
    additionally requires the containing word to be at most twice the
    stem's length. This prevents short stems like 'cat' (from 'cats')
    from matching unrelated long words like 'concatenate'."""
    text = (seg.get("text") or "").lower()
    words = text.split()
    for term in terms:
        term_lower = term.lower()
        is_stem = stems is not None and term_lower in stems
        if not is_stem:
            if term_lower in text:
                return True
        else:
            for word in words:
                if term_lower in word and len(word) <= len(term_lower) * 2:
                    return True
    return False


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

    # Derive Porter-stemmed forms so _matches_segment can find segments
    # like "happiness" when the query is "happy" (both stem to "happi").
    stems_raw = _stem_terms(terms)
    stems = {s.lower() for s in stems_raw}
    terms = terms + [s for s in stems if s not in terms]

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
            if _matches_segment(seg, terms, stems)
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

    if len(query) > _MAX_QUERY_CHARS:
        return []

    fts5_query = _sanitize_fts5_query(query)
    if not fts5_query or not fts5_query.strip():
        return []

    try:
        rows = db.execute(
            text(
                "SELECT "
                "f.rowid AS transcript_id, "
                "f.rank, "
                "t.title, "
                "t.filename, "
                "t.created_at, "
                "t.full_text, "
                "t.corrected_text, "
                "COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') "
                "FROM json_each(t.segments)), '') AS segment_text, "
                "snippet(transcripts_fts, -1, '<b>', '</b>', '…', 32) AS snippet "
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
    except Exception:
        return []

    results = []
    for r in rows:
        (transcript_id, rank, title, filename, created_at,
         full_text, corrected_text, segment_text, snippet) = r
        snippet_text = snippet or ""

        # Determine match_source: check which column the query terms appear in.
        # Checked in this order since a term can legitimately appear in more
        # than one column (e.g. a corrected transcript keeps its original
        # full_text too) — first match wins.
        terms_lower = [t.lower() for t in query.lower().split() if t]

        def _has_term(text_val):
            text_lower = (text_val or "").lower()
            return any(t in text_lower for t in terms_lower)

        if title and _has_term(title):
            match_source = "title"
        elif _has_term(full_text):
            match_source = "full_text"
        elif _has_term(corrected_text):
            match_source = "corrected_text"
        elif _has_term(segment_text):
            match_source = "segment_text"
        else:
            match_source = "full_text"

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
