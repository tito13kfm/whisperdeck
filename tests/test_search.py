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
    """Updating full_text should add new terms to the FTS index.
    Old terms may persist due to FTS5 external-content delete limitations."""
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
    """Deleting a transcript excludes it from search_transcripts results
    even if the FTS index still has a stale entry."""
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
