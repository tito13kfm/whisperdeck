"""Unit tests for services/search.py — search_transcripts()."""
import pytest

from database import Transcript, User
from services.search import search_transcripts, search_transcripts_snippets, _MAX_QUERY_CHARS
from sqlalchemy import text


def _make_user(db_session, username="alice"):
    user = User(username=username, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def _make_transcript(db_session, user_id, title="t", filename="f.mp3", *,
                     full_text="", corrected_text=None, segments=None):
    t = Transcript(
        user_id=user_id, title=title, filename=filename,
        status="completed", full_text=full_text,
        corrected_text=corrected_text,
        segments=segments or [],
    )
    db_session.add(t)
    db_session.commit()
    return t


# ── Exact match ───────────────────────────────────────────────────────────

def test_exact_match_in_full_text(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="Sandeep discussed the Claude integration")

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert len(results) == 1
    assert results[0]["transcript_id"] is not None
    assert results[0]["title"] == "t"


def test_exact_match_in_corrected_text(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="hello world",
                     corrected_text="Sandeep discussed Claude integration")

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert len(results) == 1


def test_exact_match_in_segments(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="hello world",
                     segments=[
                         {"speaker": "Alice", "text": "Sandeep joined the call", "start": 0.0, "end": 2.0},
                     ])

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert len(results) == 1


# ── Multi-term AND matching ───────────────────────────────────────────────

def test_multi_term_and_matches_across_columns(db_session):
    """'Sandeep Claude' matches only if BOTH terms appear anywhere."""
    user = _make_user(db_session)
    # Term 1 in full_text, term 2 in segments
    _make_transcript(db_session, user.id,
                     full_text="Sandeep was in the meeting",
                     segments=[
                         {"speaker": "Sandeep", "text": "Claude integration is done", "start": 0.0, "end": 2.0},
                     ])

    results = search_transcripts(db_session, user.id, "Sandeep Claude")
    assert len(results) == 1


def test_multi_term_and_excludes_when_one_missing(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="Sandeep was in the meeting",
                     segments=[
                         {"speaker": "Sandeep", "text": "the integration is done", "start": 0.0, "end": 2.0},
                     ])

    results = search_transcripts(db_session, user.id, "Sandeep Claude")
    assert len(results) == 0


# ── Partial match ─────────────────────────────────────────────────────────

def test_partial_match(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="We discussed the integration approach in detail")

    results = search_transcripts(db_session, user.id, "integr")
    assert len(results) == 1


# ── Case-insensitive ──────────────────────────────────────────────────────

def test_case_insensitive(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="SANDEEP discussed CLAUDE")

    results = search_transcripts(db_session, user.id, "sandeep claude")
    assert len(results) == 1


# ── Multi-transcript results ──────────────────────────────────────────────

def test_multi_transcript_results(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, title="Meeting A", filename="a.mp3",
                     full_text="Sandeep was there")
    _make_transcript(db_session, user.id, title="Meeting B", filename="b.mp3",
                     full_text="Sandeep spoke again")

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert len(results) == 2
    titles = {r["title"] for r in results}
    assert titles == {"Meeting A", "Meeting B"}


# ── No match ──────────────────────────────────────────────────────────────

def test_no_match(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="hello world")

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert results == []


# ── Empty query ───────────────────────────────────────────────────────────

def test_empty_query_returns_empty(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="Sandeep was here")

    assert search_transcripts(db_session, user.id, "") == []
    assert search_transcripts(db_session, user.id, "   ") == []
    assert search_transcripts(db_session, user.id, None) == []


# ── LIKE wildcard escaping ────────────────────────────────────────────────

def test_fts5_percent_is_token_separator(db_session):
    """FTS5 unicode61 tokenizer treats % as a token separator, so searching
    '100%' matches both '100%' and '100' because both produce token '100'."""
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="the result was 100% correct")
    _make_transcript(db_session, user.id, title="other", filename="other.mp3",
                     full_text="the result was 100 correct")  # also matches — same token

    results = search_transcripts(db_session, user.id, "100%")
    assert len(results) == 2  # both tokenize to '100'


def test_fts5_underscore_is_token_separator(db_session):
    """FTS5 unicode61 tokenizer treats _ as a token separator, so 'file_1'
    tokenizes to 'file' + '1'. 'fileA1' tokenizes to 'filea1' and won't match."""
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="open file_1 now")
    _make_transcript(db_session, user.id, title="other", filename="other.mp3",
                     full_text="open fileA1 now")

    results = search_transcripts(db_session, user.id, "file_1")
    assert len(results) == 1


# ── Per-segment matching in result ────────────────────────────────────────

def test_matching_segments_in_result(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="meeting notes",
                     segments=[
                         {"speaker": "Alice", "text": "hello world", "start": 0.0, "end": 1.0},
                         {"speaker": "Bob", "text": "Sandeep discussed Claude", "start": 1.0, "end": 3.0},
                         {"speaker": "Alice", "text": "Sandeep agreed", "start": 3.0, "end": 4.0},
                     ])

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert len(results) == 1
    assert len(results[0]["matching_segments"]) == 2
    speakers = {s["speaker"] for s in results[0]["matching_segments"]}
    assert speakers == {"Bob", "Alice"}


def test_matching_segments_empty_when_match_in_full_text_only(db_session):
    """If match is only in full_text (not in any segment), matching_segments is empty."""
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="Sandeep was here",
                     segments=[
                         {"speaker": "Alice", "text": "hello world", "start": 0.0, "end": 1.0},
                     ])

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert len(results) == 1
    assert results[0]["matching_segments"] == []


# ── User isolation ────────────────────────────────────────────────────────

def test_user_isolation(db_session):
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")

    _make_transcript(db_session, alice.id, title="Alice's meeting", filename="a.mp3",
                     full_text="Sandeep was here")
    _make_transcript(db_session, bob.id, title="Bob's meeting", filename="b.mp3",
                     full_text="Sandeep was there too")

    results = search_transcripts(db_session, alice.id, "Sandeep")
    assert len(results) == 1
    assert results[0]["title"] == "Alice's meeting"


# ── Query length limit ────────────────────────────────────────────────────

def test_query_over_max_chars_raises(db_session):
    user = _make_user(db_session)
    long_query = "x" * (_MAX_QUERY_CHARS + 1)

    with pytest.raises(ValueError, match="exceeds"):
        search_transcripts(db_session, user.id, long_query)


def test_query_at_max_chars_allowed(db_session):
    user = _make_user(db_session)
    # "Sandeep" padded with spaces to exactly _MAX_QUERY_CHARS; split drops empty
    pad = _MAX_QUERY_CHARS - len("Sandeep")
    query = "Sandeep" + " " * pad

    _make_transcript(db_session, user.id, full_text="Sandeep was here")

    results = search_transcripts(db_session, user.id, query)
    assert len(results) == 1


# ── Edge: NULL corrected_text, empty segments ─────────────────────────────

def test_null_corrected_text_still_matches_full_text(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="Sandeep was here",
                     corrected_text=None)

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert len(results) == 1


def test_processing_transcript_excluded(db_session):
    """Only completed transcripts are searched."""
    user = _make_user(db_session)
    t = Transcript(
        user_id=user.id, title="in progress", filename="p.mp3",
        status="processing", full_text="Sandeep was here",
    )
    db_session.add(t)
    db_session.commit()

    results = search_transcripts(db_session, user.id, "Sandeep")
    assert len(results) == 0


# ── search_transcripts_snippets() tests ────────────────────────────────────

def test_snippets_returns_html_highlight(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="Sandeep discussed the Claude integration")
    results = search_transcripts_snippets(db_session, user.id, "Claude")
    assert len(results) == 1
    assert "<b>Claude</b>" in results[0]["snippet"]
    assert results[0]["title"] == "t"
    assert results[0]["transcript_id"] is not None


def test_snippets_limit(db_session):
    user = _make_user(db_session)
    for i in range(5):
        _make_transcript(db_session, user.id, title=f"Meeting {i}",
                         full_text=f"Sandeep was in meeting {i}")
    results = search_transcripts_snippets(db_session, user.id, "Sandeep", limit=3)
    assert len(results) <= 3


def test_snippets_empty_results(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="hello world")
    results = search_transcripts_snippets(db_session, user.id, "nope")
    assert results == []


def test_snippets_empty_query(db_session):
    user = _make_user(db_session)
    results = search_transcripts_snippets(db_session, user.id, "")
    assert results == []


def test_snippets_match_source_field(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id,
                     full_text="hello world",
                     corrected_text="Sandeep fixed the Claude integration")
    results = search_transcripts_snippets(db_session, user.id, "Claude")
    assert len(results) == 1
    assert results[0]["match_source"] == "corrected_text"


def test_snippets_match_source_full_text(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="hello unique_fulltext_term world")
    results = search_transcripts_snippets(db_session, user.id, "unique_fulltext_term")
    assert len(results) == 1
    assert results[0]["match_source"] == "full_text"


def test_snippets_match_source_segment_text(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="base text",
                     segments=[{"speaker": "A", "text": "unique_segment_term here",
                                "start": 0, "end": 1}])
    results = search_transcripts_snippets(db_session, user.id, "unique_segment_term")
    assert len(results) == 1
    assert results[0]["match_source"] == "segment_text"


def test_snippets_has_rank(db_session):
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="Sandeep was here")
    results = search_transcripts_snippets(db_session, user.id, "Sandeep")
    assert len(results) == 1
    assert isinstance(results[0]["rank"], (int, float))


# ── FTS5 trigger sync tests ────────────────────────────────────────────────

def test_fts_trigger_insert_populates_index(db_session):
    """Creating a transcript should populate the FTS index."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="hello world test")
    rows = db_session.execute(
        text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
        {"q": '"hello" AND "world" AND "test"'},
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == t.id


def test_fts_trigger_update_syncs_index(db_session):
    """Updating full_text should add new terms and remove old terms from the FTS index."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="hello world")
    rows = db_session.execute(
        text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
        {"q": '"hello"'},
    ).fetchall()
    assert len(rows) == 1
    t.full_text = "goodbye everyone"
    db_session.commit()
    rows = db_session.execute(
        text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
        {"q": '"goodbye"'},
    ).fetchall()
    assert len(rows) == 1


def test_fts_trigger_delete_removes_from_search(db_session):
    """Deleting a transcript excludes it from search_transcripts results, and
    drops its entry from the FTS index itself (issue #309).

    The search-level assertion alone is vacuous for the trigger: both search
    paths filter status = 'completed' and JOIN back to transcripts, so they
    return nothing for a deleted row whether or not the index was cleaned.
    The index-level assertions after the delete are what actually exercise
    trg_transcripts_fts_delete."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="hello world")
    rows = db_session.execute(
        text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
        {"q": '"hello"'},
    ).fetchall()
    assert len(rows) == 1
    db_session.delete(t)
    db_session.commit()
    results = search_transcripts(db_session, user.id, "hello")
    assert len(results) == 0
    rows_after = db_session.execute(
        text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
        {"q": '"hello"'},
    ).fetchall()
    assert rows_after == [], "term 'hello' still in the FTS index after delete"
    docsize = db_session.execute(
        text("SELECT COUNT(*) FROM transcripts_fts_docsize WHERE id = :tid"),
        {"tid": t.id},
    ).scalar()
    assert docsize == 0, "FTS index entry survived the transcript delete"


def test_fts_trigger_segment_text_indexed(db_session):
    """Segment text from JSON should be extracted and indexed."""
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="base text",
                     segments=[
                         {"speaker": "A", "text": "Sandeep unique term here", "start": 0, "end": 1},
                     ])
    rows = db_session.execute(
        text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
        {"q": '"unique"'},
    ).fetchall()
    assert len(rows) == 1


# ── API endpoint tests (use client fixture) ────────────────────────────────

def test_api_search_returns_results(db_session, client):
    _make_transcript(db_session, 1, full_text="Sandeep discussed the Claude integration")
    resp = client.get("/api/search?q=Claude&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "total" in data
    assert data["total"] >= 1
    assert len(data["results"]) >= 1
    assert "snippet" in data["results"][0]
    assert "transcript_id" in data["results"][0]


def test_api_search_empty_query_returns_400(client):
    resp = client.get("/api/search?q=")
    assert resp.status_code == 400


def test_api_search_missing_query_returns_422(client):
    resp = client.get("/api/search")
    assert resp.status_code == 422


def test_api_search_query_too_long_returns_400(db_session, client):
    long_q = "x" * 501
    resp = client.get(f"/api/search?q={long_q}")
    assert resp.status_code == 400


def test_api_search_requires_auth():
    from fastapi.testclient import TestClient
    import app as app_module
    tc = TestClient(app_module.app)
    resp = tc.get("/api/search?q=hello")
    assert resp.status_code == 401


def test_api_search_limit_respected(db_session, client):
    for i in range(5):
        _make_transcript(db_session, 1, title=f"Meeting {i}",
                         full_text=f"Sandeep was in meeting {i}")
    resp = client.get("/api/search?q=Sandeep&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) <= 2


def test_api_transcripts_q_filters_by_content(db_session, client):
    _make_transcript(db_session, 1, title="Meeting A", full_text="Sandeep unique word here")
    _make_transcript(db_session, 1, title="Meeting B", full_text="nothing relevant")
    resp = client.get("/api/transcripts?q=unique")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    titles = [t["title"] for t in data]
    assert "Meeting A" in titles


def test_api_transcripts_without_q_returns_all(db_session, client):
    _make_transcript(db_session, 1, title="Meeting A", full_text="hello")
    _make_transcript(db_session, 1, title="Meeting B", full_text="world")
    resp = client.get("/api/transcripts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2


# ── populate_fts() backfill tests ───────────────────────────────────────────


def test_populate_fts_restores_deleted_index(db_session):
    """After wiping the FTS index, populate_fts should restore searchability.
    Exercises both INSERT and UPDATE (trigger) branch."""
    from database import populate_fts
    engine = db_session.get_bind()
    user = _make_user(db_session)

    # t1: set segment_text now → INSERT branch
    t1 = _make_transcript(db_session, user.id, title="with_segs",
                          full_text="alpha beta gamma",
                          segments=[{"speaker": "A", "text": "hello world",
                                     "start": 0, "end": 1}])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE transcripts SET segment_text = 'hello world' WHERE id = :tid"),
            {"tid": t1.id},
        )

    # t2: leave segment_text NULL → UPDATE (trigger) branch
    t2 = _make_transcript(db_session, user.id, title="no_segs",
                          full_text="delta epsilon zeta")

    # Wipe index — all state prepared before this point
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO transcripts_fts(transcripts_fts) VALUES('delete-all')"))

    # Confirm index empty: MATCH yields nothing, docsize is 0
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
            {"q": "alpha OR delta"},
        ).fetchall()
        assert len(rows) == 0
        docsize = conn.execute(
            text("SELECT COUNT(*) FROM transcripts_fts_docsize")
        ).scalar()
        assert docsize == 0

    populate_fts(engine)

    # Both indexed: MATCH finds both, docsize is 2
    with engine.connect() as conn:
        for tid, term in [(t1.id, "alpha"), (t2.id, "delta")]:
            rows = conn.execute(
                text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
                {"q": term},
            ).fetchall()
            assert len(rows) == 1, f"transcript {tid} not found for term '{term}'"
            assert rows[0][0] == tid
        docsize = conn.execute(
            text("SELECT COUNT(*) FROM transcripts_fts_docsize")
        ).scalar()
        assert docsize == 2
        # Backfilling t2 goes through the UPDATE trigger against a row the
        # index does not hold. Before #309 guarded that trigger's delete half,
        # this left the index corrupt: searchable, membership correct, and
        # integrity-check failing from then on.
        conn.execute(text(_INTEGRITY_SQL))


def test_populate_fts_idempotent(db_session):
    """Calling populate_fts on an already-indexed DB must be a no-op
    and must not corrupt the index (integrity-check must pass).

    The backfill path (index wiped, then repopulated) is covered by
    test_populate_fts_restores_deleted_index. The per-row-delete then
    backfill case is covered by the issue #309 cleanup tests below: a
    'delete-all' followed by a trigger-consistent reinsert leaves the index
    clean, idempotent, and safe to DELETE from afterwards. The note that used
    to sit here, saying delete-all corrupts internal state permanently and the
    case was untestable, was wrong on both counts."""
    from database import populate_fts
    engine = db_session.get_bind()
    user = _make_user(db_session)

    _make_transcript(db_session, user.id, full_text="unique term here")

    with engine.connect() as conn:
        first_docsize = conn.execute(
            text("SELECT COUNT(*) FROM transcripts_fts_docsize")
        ).scalar()

    populate_fts(engine)

    with engine.connect() as conn:
        second_docsize = conn.execute(
            text("SELECT COUNT(*) FROM transcripts_fts_docsize")
        ).scalar()
        conn.execute(
            text("INSERT INTO transcripts_fts(transcripts_fts, rank) "
                 "VALUES('integrity-check', 1)")
        )

    assert second_docsize == first_docsize
    assert first_docsize == 1


def test_populate_fts_empty_db_is_noop(db_session):
    """populate_fts on a database with no transcript rows should complete without error."""
    from database import populate_fts
    engine = db_session.get_bind()
    populate_fts(engine)


# ── FTS update trigger dedup tests (issue #206) ─────────────────────────────

def test_fts_update_integrity_check_passes(db_session):
    """After updating full_text, integrity-check with rank=1 must pass.
    The current trigger duplicates rowids, causing the check to fail."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="hello world")
    t.full_text = "goodbye everyone"
    db_session.commit()
    engine = db_session.get_bind()
    with engine.connect() as conn:
        conn.execute(
            text("INSERT INTO transcripts_fts(transcripts_fts, rank) "
                 "VALUES('integrity-check', 1)")
        )


def test_fts_update_old_terms_removed(db_session):
    """After updating full_text, old terms must not match via FTS5 MATCH.
    Duplicate rowids currently keep old terms searchable."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="hello world")
    t.full_text = "goodbye everyone"
    db_session.commit()
    engine = db_session.get_bind()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
            {"q": '"hello"'},
        ).fetchall()
        assert len(rows) == 0, "old term 'hello' should no longer match after update"


def test_fts_update_idempotent(db_session):
    """Double update must not duplicate the index entry — docsize stays 1."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="hello world")
    t.full_text = "goodbye everyone"
    db_session.commit()
    t.full_text = "final text here"
    db_session.commit()
    engine = db_session.get_bind()
    with engine.connect() as conn:
        docsize = conn.execute(
            text("SELECT COUNT(*) FROM transcripts_fts_docsize")
        ).scalar()
        assert docsize == 1, f"expected 1 docsize entry, got {docsize}"
        conn.execute(
            text("INSERT INTO transcripts_fts(transcripts_fts, rank) "
                 "VALUES('integrity-check', 1)")
        )


def test_fts_update_old_segment_terms_removed(db_session):
    """After updating segments, old segment-only terms must not match.
    The delete command uses computed OLD.segments (not OLD.segment_text
    which is NULL for ORM-created rows). Does not run integrity-check:
    the content-table segment_text column is NULL while FTS index has
    non-empty tokens (pre-existing issue, not introduced by this fix)."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="base text",
                         segments=[
                             {"speaker": "A", "text": "old segment unique term",
                              "start": 0, "end": 1},
                         ])
    t.segments = [
        {"speaker": "A", "text": "new segment different term",
         "start": 0, "end": 1},
    ]
    db_session.commit()
    engine = db_session.get_bind()
    with engine.connect() as conn:
        rows_old = conn.execute(
            text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
            {"q": '"unique"'},
        ).fetchall()
        assert len(rows_old) == 0, "old segment term 'unique' should not match after update"
        rows_new = conn.execute(
            text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
            {"q": '"different"'},
        ).fetchall()
        assert len(rows_new) == 1


def test_fts_update_trigger_migrates_existing_database(tmp_path):
    """A database created before the #206 fix already has a trigger named
    trg_transcripts_fts_update with the old (insert-only) body. Re-running
    init_db() on that existing file -- exactly what happens on every app
    restart -- must replace it with the corrected body. CREATE TRIGGER IF
    NOT EXISTS would see the old trigger already present and silently skip
    creating the fix, leaving every upgraded install broken."""
    from sqlalchemy import create_engine
    from database import init_db

    db_path = tmp_path / "existing.db"
    engine, _, _ = init_db(str(db_path))
    engine.dispose()

    # Simulate a pre-fix database: replace the (already-corrected) trigger
    # with the old, buggy insert-only body.
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.connect() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_update"))
        conn.execute(text(
            "CREATE TRIGGER trg_transcripts_fts_update "
            "AFTER UPDATE ON transcripts BEGIN "
            "INSERT INTO transcripts_fts(rowid, title, full_text, corrected_text, segment_text) "
            "VALUES ("
            "NEW.id, NEW.title, NEW.full_text, NEW.corrected_text, "
            "COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') FROM json_each(NEW.segments)), '')"
            "); END"
        ))
        conn.commit()
    old_engine.dispose()

    # Re-run init_db on the SAME file, as the app does on every startup.
    engine2, SessionLocal2, _ = init_db(str(db_path))
    db = SessionLocal2()
    try:
        user = _make_user(db)
        t = _make_transcript(db, user.id, full_text="hello world")
        t.full_text = "goodbye everyone"
        db.commit()
        with engine2.connect() as conn:
            rows = conn.execute(
                text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
                {"q": '"hello"'},
            ).fetchall()
            assert len(rows) == 0, (
                "old term 'hello' still matches after update on a database "
                "that pre-existed the fix -- the trigger migration did not run"
            )
    finally:
        db.close()
        engine2.dispose()


# ── FTS delete trigger and orphan cleanup (issue #309) ──────────────────────

_INTEGRITY_SQL = ("INSERT INTO transcripts_fts(transcripts_fts, rank) "
                  "VALUES('integrity-check', 1)")


def _match_ids(conn, query):
    """Rowids the FTS index itself matches, sorted. Sorted because FTS5 does
    not promise an order without ORDER BY and every assertion below is about
    set membership."""
    rows = conn.execute(
        text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
        {"q": query},
    ).fetchall()
    return sorted(row[0] for row in rows)


def _docsize_ids(conn):
    """Index membership. COUNT(*) on transcripts_fts would count the content
    table, not the index, so the shadow table is the real artifact."""
    return [row[0] for row in conn.execute(
        text("SELECT id FROM transcripts_fts_docsize ORDER BY id")
    ).fetchall()]


def _orphan_ids(conn):
    return [row[0] for row in conn.execute(text(
        "SELECT d.id FROM transcripts_fts_docsize d "
        "WHERE NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.id = d.id) "
        "ORDER BY d.id"
    )).fetchall()]


def test_fts_trigger_delete_removes_segment_terms(db_session):
    """The delete has to carry segment text derived from OLD.segments. If it
    passed OLD.segment_text, which is NULL on every ORM-created row, the
    segment-only terms would stay in the index after the delete."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="base text",
                         segments=[{"speaker": "A", "text": "segonlyterm here",
                                    "start": 0, "end": 1}])
    engine = db_session.get_bind()
    with engine.connect() as conn:
        assert _match_ids(conn, '"segonlyterm"') == [t.id]
    db_session.delete(t)
    db_session.commit()
    with engine.connect() as conn:
        assert _match_ids(conn, '"segonlyterm"') == []
        assert _docsize_ids(conn) == []


def test_fts_trigger_delete_leaves_sibling_rows_indexed(db_session):
    """Deleting one of two rows must remove only that row's terms."""
    user = _make_user(db_session)
    keep = _make_transcript(db_session, user.id, title="keep",
                            full_text="alpha shared")
    drop = _make_transcript(db_session, user.id, title="drop",
                            full_text="beta shared")
    engine = db_session.get_bind()
    db_session.delete(drop)
    db_session.commit()
    with engine.connect() as conn:
        assert _docsize_ids(conn) == [keep.id]
        assert _match_ids(conn, '"shared"') == [keep.id]
        assert _match_ids(conn, '"beta"') == []
        conn.execute(text(_INTEGRITY_SQL))


def test_fts_trigger_delete_of_never_indexed_row_keeps_index_valid(db_session):
    """A pre-FTS row with status != 'completed' is skipped by populate_fts() and
    so is absent from the index for the whole life of the install. Deleting one
    must not corrupt the index.

    This is what the trigger's WHEN EXISTS guard on transcripts_fts_docsize
    buys. Without it the trigger issues 'delete' for a rowid the index does not
    hold, and integrity-check fails from then on, even though the content table
    and the index now agree on membership and every search still looks right.

    The unindexed row is built by dropping the INSERT trigger, which is the same
    end state as a row that predates FTS. integrity-check is asserted only after
    the delete: while that row exists in the content table and not in the index,
    the check fails on the setup itself, which says nothing about the trigger."""
    user = _make_user(db_session)
    engine = db_session.get_bind()
    indexed = _make_transcript(db_session, user.id, title="indexed",
                               full_text="alpha unique")
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_insert"))
    unindexed = _make_transcript(db_session, user.id, title="unindexed",
                                 full_text="beta unique")
    with engine.connect() as conn:
        assert _docsize_ids(conn) == [indexed.id]
    db_session.delete(unindexed)
    db_session.commit()
    with engine.connect() as conn:
        assert _docsize_ids(conn) == [indexed.id]
        assert _match_ids(conn, '"alpha"') == [indexed.id]
        conn.execute(text(_INTEGRITY_SQL))


def test_fts_trigger_update_of_never_indexed_row_keeps_index_valid(db_session):
    """Sibling of the test above, on the UPDATE trigger. The issue named only
    the missing DELETE trigger, but the UPDATE trigger's delete half had the
    same unguarded 'delete' and corrupted the index the same way for a row that
    was never indexed.

    This is not a hypothetical path: populate_fts() indexes a pre-FTS row by
    UPDATEing it, so every backfill ran the unguarded delete half against an
    unindexed rowid. Fixed here by guarding that statement with WHERE EXISTS,
    on the statement rather than the trigger, because the insert half below it
    still has to run or the backfill would index nothing."""
    user = _make_user(db_session)
    engine = db_session.get_bind()
    indexed = _make_transcript(db_session, user.id, title="indexed",
                               full_text="alpha unique")
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_insert"))
    unindexed = _make_transcript(db_session, user.id, title="unindexed",
                                 full_text="beta unique")
    unindexed.full_text = "gamma unique"
    db_session.commit()
    with engine.connect() as conn:
        # The insert half still ran, so the row is now indexed exactly once,
        # under its new terms only.
        assert _docsize_ids(conn) == sorted([indexed.id, unindexed.id])
        assert _match_ids(conn, '"gamma"') == [unindexed.id]
        assert _match_ids(conn, '"beta"') == []
        assert _match_ids(conn, '"alpha"') == [indexed.id]
        conn.execute(text(_INTEGRITY_SQL))


def test_cleanup_fts_orphans_removes_orphan_and_is_idempotent(db_session):
    """Broken state: a database that deleted transcripts before #309 added the
    delete trigger, so the index still holds their entries. Built by dropping
    the trigger and then deleting a row, which is exactly what those installs
    did.

    The segment-term assertion after the repair is the one that fails if the
    cleanup is ever changed to 'rebuild': a rebuild indexes the literal
    segment_text column, which is NULL, so every row silently loses its
    segment terms."""
    from database import cleanup_fts_orphans
    engine = db_session.get_bind()
    user = _make_user(db_session)

    keep = _make_transcript(db_session, user.id, title="keep",
                            full_text="alpha shared",
                            segments=[{"speaker": "A", "text": "segkeepterm",
                                       "start": 0, "end": 1}])
    orphan = _make_transcript(db_session, user.id, title="orphan",
                              full_text="beta shared")
    orphan_id = orphan.id

    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_delete"))
    db_session.delete(orphan)
    db_session.commit()

    with engine.connect() as conn:
        assert _orphan_ids(conn) == [orphan_id]
        assert _match_ids(conn, '"beta"') == [orphan_id]

    assert cleanup_fts_orphans(engine) == 1

    with engine.connect() as conn:
        assert _orphan_ids(conn) == []
        assert _docsize_ids(conn) == [keep.id]
        assert _match_ids(conn, '"beta"') == []
        assert _match_ids(conn, '"shared"') == [keep.id]
        assert _match_ids(conn, '"segkeepterm"') == [keep.id]

    assert cleanup_fts_orphans(engine) == 0

    with engine.connect() as conn:
        assert _docsize_ids(conn) == [keep.id]
        assert _match_ids(conn, '"segkeepterm"') == [keep.id]


def test_cleanup_fts_orphans_noop_when_no_orphans(db_session):
    """Nothing to clean: the detect query gates the rest, so a clean database
    never wipes and rebuilds its index on startup.

    Asserting the searchable state cannot show that, because an unconditional
    wipe-and-reindex ends up searchable too. Neither can comparing the raw
    transcripts_fts_data blocks: for a one-document index, delete-all plus a
    reinsert of the same row produces byte-identical blocks, so a fingerprint
    passes whether the wipe ran or not. The only assertion that actually
    distinguishes the two is that the 'delete-all' statement never reaches the
    database, so this watches the statements the function emits."""
    from sqlalchemy import event
    from database import cleanup_fts_orphans
    engine = db_session.get_bind()
    user = _make_user(db_session)
    t = _make_transcript(db_session, user.id, full_text="alpha text",
                         segments=[{"speaker": "A", "text": "segkeepterm",
                                    "start": 0, "end": 1}])

    emitted = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        emitted.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        assert cleanup_fts_orphans(engine) == 0
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert emitted, "no statements recorded, the listener did not attach"
    wipes = [s for s in emitted if "delete-all" in s]
    assert wipes == [], f"index was wiped on a database with no orphans: {wipes}"

    with engine.connect() as conn:
        assert _docsize_ids(conn) == [t.id]
        assert _match_ids(conn, '"segkeepterm"') == [t.id]


def test_cleanup_fts_orphans_does_not_index_previously_unindexed_rows(db_session):
    """Membership is captured, not recomputed. An install's index holds "all
    post-FTS rows plus completed pre-FTS rows", which is not expressible as a
    predicate, so reindexing "all rows" would pull in rows that were never
    indexed and skew the term frequencies this cleanup exists to correct."""
    from database import cleanup_fts_orphans
    engine = db_session.get_bind()
    user = _make_user(db_session)
    keep = _make_transcript(db_session, user.id, title="keep",
                            full_text="alpha text")
    orphan = _make_transcript(db_session, user.id, title="orphan",
                              full_text="beta text")
    orphan_id = orphan.id

    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_insert"))
    _make_transcript(db_session, user.id, title="never", full_text="gamma text")
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_delete"))
    db_session.delete(orphan)
    db_session.commit()

    with engine.connect() as conn:
        assert _orphan_ids(conn) == [orphan_id]

    assert cleanup_fts_orphans(engine) == 1

    with engine.connect() as conn:
        assert _docsize_ids(conn) == [keep.id]
        assert _match_ids(conn, '"alpha"') == [keep.id]
        assert _match_ids(conn, '"gamma"') == []


def test_cleanup_fts_orphans_reindexes_across_chunks(db_session, monkeypatch):
    """The reinsert is chunked to stay under SQLite's bound-parameter limit.
    Force a chunk size of 2 against 3 surviving rows so the loop runs more than
    once and nothing is dropped at the boundary."""
    import database
    from database import cleanup_fts_orphans
    monkeypatch.setattr(database, "_FTS_REINDEX_CHUNK", 2)
    engine = db_session.get_bind()
    user = _make_user(db_session)
    kept = [_make_transcript(db_session, user.id, title=f"k{i}",
                             full_text=f"term{i} shared") for i in range(3)]
    orphan = _make_transcript(db_session, user.id, title="orphan",
                              full_text="beta shared")
    orphan_id = orphan.id

    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_delete"))
    db_session.delete(orphan)
    db_session.commit()

    with engine.connect() as conn:
        assert _orphan_ids(conn) == [orphan_id]

    assert cleanup_fts_orphans(engine) == 1

    kept_ids = sorted(t.id for t in kept)
    with engine.connect() as conn:
        assert _docsize_ids(conn) == kept_ids
        assert _match_ids(conn, '"shared"') == kept_ids
        for i, t in enumerate(kept):
            assert _match_ids(conn, f'"term{i}"') == [t.id]


def test_cleanup_fts_orphans_without_fts_tables_is_noop(db_session, tmp_path):
    """Two zero-cardinality cases.

    A database with the FTS schema but no transcript rows reports 0. And a
    database with no transcripts table at all, which is what an engine pointed
    at a file init_db() has never run against looks like, must return 0 rather
    than raise: without the table-existence check the detect query fails with
    "no such table: transcripts_fts_docsize"."""
    from sqlalchemy import create_engine
    from database import cleanup_fts_orphans

    assert cleanup_fts_orphans(db_session.get_bind()) == 0

    bare = create_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    try:
        assert cleanup_fts_orphans(bare) == 0
    finally:
        bare.dispose()


def test_init_db_cleans_pre_309_orphans_on_restart(tmp_path):
    """End-to-end upgrade path. A database created before #309 has no delete
    trigger and accumulates orphaned index entries. Re-running init_db(), which
    is what every app restart does, must add the trigger, drop the orphan, keep
    the survivor's terms including its segment terms, and leave an index that a
    later real DELETE applies to cleanly.

    That last step is the guard against the latent corruption a 'rebuild'
    cleanup would have introduced: integrity-check passes right after a
    rebuild, and the next DELETE fails with "database disk image is
    malformed"."""
    from database import init_db

    db_path = tmp_path / "pre309.db"
    engine, SessionLocal, _ = init_db(str(db_path))
    db = SessionLocal()
    try:
        user = _make_user(db)
        keep = _make_transcript(db, user.id, title="keep",
                                full_text="alpha text",
                                segments=[{"speaker": "A", "text": "segkeepterm",
                                           "start": 0, "end": 1}])
        orphan = _make_transcript(db, user.id, title="orphan",
                                  full_text="beta text")
        keep_id, orphan_id = keep.id, orphan.id
    finally:
        db.close()

    # Simulate the pre-#309 state: no delete trigger, and a transcript deleted
    # while it was absent.
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_delete"))
        conn.execute(text("DELETE FROM transcripts WHERE id = :tid"),
                     {"tid": orphan_id})
    with engine.connect() as conn:
        assert _orphan_ids(conn) == [orphan_id]
    engine.dispose()

    engine2, SessionLocal2, _ = init_db(str(db_path))
    db2 = SessionLocal2()
    try:
        with engine2.connect() as conn:
            assert _orphan_ids(conn) == []
            assert _docsize_ids(conn) == [keep_id]
            assert _match_ids(conn, '"segkeepterm"') == [keep_id]
            assert _match_ids(conn, '"beta"') == []

        surviving = db2.query(Transcript).filter(Transcript.id == keep_id).one()
        db2.delete(surviving)
        db2.commit()

        with engine2.connect() as conn:
            assert _docsize_ids(conn) == []
            assert _match_ids(conn, '"alpha"') == []
    finally:
        db2.close()
        engine2.dispose()
