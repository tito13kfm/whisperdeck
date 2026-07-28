"""Unit tests for services/search.py — search_transcripts()."""
import pytest

from database import Transcript, User
from services.search import search_transcripts, _MAX_QUERY_CHARS


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

def test_like_wildcard_percent_is_literal(db_session):
    """User typing '100%' should search for literal '100%', not wildcard."""
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="the result was 100% correct")
    _make_transcript(db_session, user.id, title="other", filename="other.mp3",
                     full_text="the result was 100 correct")  # no % sign

    results = search_transcripts(db_session, user.id, "100%")
    assert len(results) == 1


def test_like_wildcard_underscore_is_literal(db_session):
    """User typing 'file_1' should search for literal 'file_1', not 'fileX1'."""
    user = _make_user(db_session)
    _make_transcript(db_session, user.id, full_text="open file_1 now")
    _make_transcript(db_session, user.id, title="other", filename="other.mp3",
                     full_text="open fileA1 now")  # _ would match A if not escaped

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
