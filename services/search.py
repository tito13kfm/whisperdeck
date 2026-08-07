"""Cross-transcript search using SQLite FTS5 full-text index (issue #108).

search_transcripts(): FTS5 MATCH identifies matching transcript IDs; a second
FTS5 pass over the segment texts (same porter unicode61 tokenizer as the main
index) identifies per-segment matches for the assistant (issue #192).

search_transcripts_snippets(): FTS5 MATCH with snippet() returns HTML-highlighted
excerpts for the web UI. Uses external-content mode (content='transcripts') so
snippet() reads original text from the content table.
"""
import sqlite3

from sqlalchemy import text

from database import Transcript

_MAX_QUERY_CHARS = 500


def _quote_fts5_term(term: str) -> str:
    """Double-quote a term for literal FTS5 matching. Embedded double-quotes
    are escaped by doubling (SQLite FTS5 convention)."""
    return '"' + term.replace('"', '""') + '"'


def _sanitize_fts5_query(query: str) -> str:
    """Wrap each whitespace-separated term in double-quotes and join with AND."""
    return " AND ".join(_quote_fts5_term(t) for t in query.split())


def _fts_match_indices(texts: list[str], terms: list[str]) -> set[int]:
    """Return the indices of `texts` that FTS5 matches for any of `terms`,
    using the same tokenizer as transcripts_fts (porter unicode61).

    This is the issue #192 fix. The main index matches with Porter stemming
    (happy/happiness both stem to happi), and any Python-side approximation
    of that (substring checks, shared prefixes, length gates) has false
    positives: happen/happy, happens/happy, concatenate/cats, runner/run.
    Instead of approximating, index the candidate texts into a throwaway
    in-memory FTS5 table and let FTS5 decide, so this pass agrees with the
    transcript-level MATCH by construction.

    Terms are OR-ed: a text matches if it contains any query term. That
    mirrors the previous per-segment behavior (transcript-level matching
    remains AND across terms via _sanitize_fts5_query).
    """
    if not texts or not terms:
        return set()
    match_query = " OR ".join(_quote_fts5_term(t) for t in terms)
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE texts_fts "
            "USING fts5(body, tokenize='porter unicode61')"
        )
        conn.executemany(
            "INSERT INTO texts_fts(rowid, body) VALUES (?, ?)",
            list(enumerate(texts)),
        )
        rows = conn.execute(
            "SELECT rowid FROM texts_fts WHERE texts_fts MATCH ?",
            (match_query,),
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _matches_segment(seg: dict, terms: list[str]) -> bool:
    """True if the segment text matches any term under FTS5 porter matching."""
    return 0 in _fts_match_indices([seg.get("text") or ""], terms)


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
        hits = _fts_match_indices(
            [seg.get("text") or "" for seg in segments], terms
        )
        matching_segments = [
            {
                "speaker": seg.get("speaker", ""),
                "text": seg.get("text", ""),
                "start": seg.get("start"),
                "end": seg.get("end"),
            }
            for i, seg in enumerate(segments)
            if i in hits
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

    terms = [t for t in query.split() if t]

    results = []
    for r in rows:
        (transcript_id, rank, title, filename, created_at,
         full_text, corrected_text, segment_text, snippet) = r
        snippet_text = snippet or ""

        # Determine match_source with the same FTS5 porter matching used for
        # segments, so a stemmed match (query "happy", column text
        # "happiness") is attributed to the right column. Checked in this
        # order since a term can legitimately appear in more than one column
        # (e.g. a corrected transcript keeps its original full_text too) —
        # first match wins.
        columns = [
            ("title", title),
            ("full_text", full_text),
            ("corrected_text", corrected_text),
            ("segment_text", segment_text),
        ]
        hits = _fts_match_indices([val or "" for _, val in columns], terms)
        match_source = next(
            (name for i, (name, _) in enumerate(columns) if i in hits),
            "full_text",
        )

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
