import json
from unittest.mock import AsyncMock, patch

from database import Transcript, User
from services.correction import (
    _BATCH_OVERLAP_LINES, _batch_lines, correct_transcript,
    extract_hotwords_from_doc,
)
from services.hotwords import list_hotwords


def _make_user_and_transcript(db_session, full_text="we discussed the api rate limiting"):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="completed", full_text=full_text)
    db_session.add(t)
    db_session.commit()
    return user, t


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _chat_completion_response(content: str):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def test_correct_transcript_sets_corrected_text_on_success(db_session):
    user, transcript = _make_user_and_transcript(db_session)

    fake_post = AsyncMock(return_value=_chat_completion_response("We discussed the API rate limiting."))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    db_session.refresh(transcript)
    assert transcript.corrected_text == "We discussed the API rate limiting."
    assert transcript.correction_model == "groq/llama-3.3-70b-versatile"
    assert transcript.correction_error is None


def test_correct_transcript_sets_error_on_failure_without_raising(db_session):
    user, transcript = _make_user_and_transcript(db_session)

    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    db_session.refresh(transcript)
    assert transcript.corrected_text is None
    assert "500" in transcript.correction_error
    # Pins the pre-extraction wording exactly — services/llm_client.py's
    # chat_completion() defaults http_error_label to None so correction
    # keeps its original generic text instead of picking up a feature
    # prefix meant for summarize/reformatting (see llm_client.py docstring).
    assert transcript.correction_error == "LLM API error (500): " + json.dumps({"error": "boom"})


def test_correct_transcript_includes_glossary_in_prompt(db_session):
    from services.hotwords import add_hotword

    user, transcript = _make_user_and_transcript(db_session)
    add_hotword(db_session, user.id, "Groqonomicon")

    fake_post = AsyncMock(return_value=_chat_completion_response("corrected"))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    sent_json = fake_post.call_args.kwargs["json"]
    prompt_text = json.dumps(sent_json)
    assert "Groqonomicon" in prompt_text


def test_extract_hotwords_from_doc_persists_terms(db_session):
    user = User(username="bob", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    fake_post = AsyncMock(
        return_value=_chat_completion_response(json.dumps({"terms": ["Acme Corp", "Q3 rollout"]}))
    )
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        terms = asyncio.run(
            extract_hotwords_from_doc(db_session, user.id, "Agenda: Acme Corp Q3 rollout status", api_key="fake-key")
        )

    assert set(terms) == {"Acme Corp", "Q3 rollout"}
    stored = {h.term for h in list_hotwords(db_session, user.id)}
    assert stored == {"Acme Corp", "Q3 rollout"}
    assert all(h.source == "extracted" for h in list_hotwords(db_session, user.id))


def test_extract_hotwords_from_doc_raises_on_failure(db_session):
    user = User(username="carol", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        import pytest
        with pytest.raises(Exception):
            asyncio.run(
                extract_hotwords_from_doc(db_session, user.id, "some doc text", api_key="fake-key")
            )

    # Function raises before persisting — no hotwords should exist
    assert list_hotwords(db_session, user.id) == []


def test_batch_lines_with_overlap():
    """_batch_lines with overlap=N shares the last N lines of each batch
    with the start of the next batch."""
    lines = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    batches = _batch_lines(lines, budget=20, overlap=3)
    assert len(batches) >= 2
    for idx in range(1, len(batches)):
        assert batches[idx][:3] == batches[idx - 1][-3:]


def test_batch_lines_overlap_never_drops_only_batch():
    """A single batch with fewer lines than overlap must still be emitted."""
    lines = ["a", "b"]
    batches = _batch_lines(lines, budget=100, overlap=4)
    assert batches == [["a", "b"]]


def test_correction_dedup_strips_overlap_lines(db_session):
    """Multi-batch correction strips the overlap from the start of each batch
    2+ output. The strip is proportional to the overlap share of the input
    batch, so fewer output lines (LLM merging) strips fewer lines."""
    segs = [
        {"start": i, "end": i + 1, "speaker": f"Sp {i % 3}", "text": "word " * 60}
        for i in range(30)
    ]
    user, transcript = _make_user_and_transcript(db_session)
    # Override full_text with segments so _transcript_lines uses them.
    transcript.full_text = ""
    transcript.segments = segs
    db_session.commit()

    N = 8  # lines per batch response
    fake_post = AsyncMock(side_effect=[
        _chat_completion_response("\n\n".join(f"batch {i} line {j}" for j in range(N)))
        for i in range(10)])
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))

    db_session.refresh(transcript)
    assert fake_post.await_count > 1
    assert transcript.correction_error is None
    batches = _batch_lines(
        [f"Sp {i % 3}: " + "word " * 60 for i in range(30)],
        overlap=_BATCH_OVERLAP_LINES,
    )
    parts = []
    for i, batch in enumerate(batches):
        lines = [f"batch {i} line {j}" for j in range(N)]
        if i > 0:
            share = _BATCH_OVERLAP_LINES / max(1, len(batch))
            keep_from = min(int(share * N), N)
            lines = lines[keep_from:]
        parts.append("\n\n".join(lines))
    assert transcript.corrected_text == "\n\n".join(parts)


def test_correction_dedup_keeps_new_content_when_overlap_merges(db_session, monkeypatch):
    """Regression: when the LLM merges the overlap region into fewer output
    lines, proportional dedup must not strip content beyond the overlap.
    A one-line overlap followed by a new line keeps the new line."""
    user, transcript = _make_user_and_transcript(db_session)
    transcript.full_text = ""
    transcript.segments = [
        {"start": i, "end": i + 1, "speaker": f"Sp{i}", "text": f"line {i}"}
        for i in range(8)
    ]
    db_session.commit()

    def fake_batch_lines(lines, budget=6000, overlap=0):
        return [lines[:8], lines[4:8] + ["unique new content"]]
    monkeypatch.setattr("services.correction._batch_lines", fake_batch_lines)

    fake_post = AsyncMock(side_effect=[
        _chat_completion_response("\n\n".join(f"out {j}" for j in range(5))),
        _chat_completion_response("merged overlap\n\nunique new content"),
    ])
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))

    db_session.refresh(transcript)
    assert "unique new content" in transcript.corrected_text
    assert "merged overlap" not in transcript.corrected_text


def test_correction_dedup_single_newline_separator_keeps_content(db_session, monkeypatch):
    """Regression: output that separates lines with single '\n' instead of the
    instructed blank line must not be stripped wholesale."""
    user, transcript = _make_user_and_transcript(db_session)
    transcript.full_text = ""
    transcript.segments = [
        {"start": i, "end": i + 1, "speaker": f"Sp{i}", "text": f"line {i}"}
        for i in range(8)
    ]
    db_session.commit()

    def fake_batch_lines(lines, budget=6000, overlap=0):
        return [lines[:8], lines[4:8] + ["new line here"]]
    monkeypatch.setattr("services.correction._batch_lines", fake_batch_lines)

    fake_post = AsyncMock(side_effect=[
        _chat_completion_response("\n".join(f"out {j}" for j in range(5))),
        _chat_completion_response("merged\nnew line here"),
    ])
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))

    db_session.refresh(transcript)
    assert "new line here" in transcript.corrected_text
    assert transcript.correction_error is None


def test_correction_dedup_keeps_content_when_actual_overlap_is_short(db_session, monkeypatch):
    """Regression: when a batch has fewer actual overlap lines than
    _BATCH_OVERLAP_LINES (a huge single line forces a 1-line batch), the dedup
    must scale to the actual overlap count, not the nominal one."""
    user, transcript = _make_user_and_transcript(db_session)
    transcript.full_text = ""
    transcript.segments = [
        {"start": 0, "end": 1, "speaker": "A", "text": "x" * 7000},
        {"start": 1, "end": 2, "speaker": "B", "text": "second segment"},
    ]
    db_session.commit()

    def fake_batch_lines(lines, budget=6000, overlap=0):
        return [lines[:1], lines[:1] + lines[1:]]
    monkeypatch.setattr("services.correction._batch_lines", fake_batch_lines)

    fake_post = AsyncMock(side_effect=[
        _chat_completion_response("huge corrected"),
        _chat_completion_response("merged-overlap\n\nsecond segment corrected"),
    ])
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))

    db_session.refresh(transcript)
    assert "second segment corrected" in transcript.corrected_text
    assert transcript.correction_error is None


def test_correction_single_batch_no_dedup(db_session):
    """Single-batch transcripts should not apply overlap dedup."""
    user, transcript = _make_user_and_transcript(db_session)
    fake_post = AsyncMock(return_value=_chat_completion_response("Correction works."))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="k"))

    db_session.refresh(transcript)
    assert transcript.corrected_text == "Correction works."
    assert fake_post.await_count == 1
    assert transcript.correction_error is None


def test_batch_lines_overlap_does_not_drop_final_batch():
    """When segments are long enough that batch_size < overlap, the final
    batch must still be emitted even though len(current) <= overlap."""
    lines = ["x" * 4000, "y" * 4000]
    batches = _batch_lines(lines, budget=6000, overlap=4)
    # Both original lines must appear (overlap may duplicate them; that's fine)
    all_lines = [ln for b in batches for ln in b]
    assert "x" * 4000 in all_lines
    assert "y" * 4000 in all_lines
