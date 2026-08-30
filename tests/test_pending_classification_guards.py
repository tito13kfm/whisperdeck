"""Design decision 8/11: capability guards must branch on effective_kind(),
not raw Transcript.kind, so an unconfirmed classification (pending,
uncertain, or failed) can never silently unlock/lock the wrong thing.
Summary is the one exception that stays AVAILABLE across all three unconfirmed
states (only blocked once confirmed voice_note); reformat, rediarize,
voice-match, and the voice-note rerun all stay BLOCKED across all three (the
stricter, safety-relevant set) -- issue #268's acceptance criterion:
'classification failure cannot silently enable an unsafe capability'."""
from unittest.mock import AsyncMock, patch

import pytest

from database import LlmJob, Transcript, User
from services.audio_prep import DiarizationEligibility


# The three classification_status values design decision 6/8 treats as "not
# yet confirmed" -- none of them may ever resolve to a real kind.
UNCONFIRMED_STATUSES = ["pending", "uncertain", "failed"]


def _testuser(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _transcript(db_session, **overrides):
    user = _testuser(db_session)
    fields = dict(
        user_id=user.id, title="t", filename="t.mp3", status="completed",
        full_text="hello world", segments=[], kind="meeting",
        classification_status="pending",
    )
    fields.update(overrides)
    t = Transcript(**fields)
    db_session.add(t)
    db_session.commit()
    return t


def _transcript_with_audio(db_session, tmp_path, **overrides):
    audio = tmp_path / "stored.mp3"
    audio.write_bytes(b"fake audio")
    return _transcript(db_session, audio_path=str(audio), **overrides)


@pytest.mark.parametrize("status", UNCONFIRMED_STATUSES)
def test_summary_available_across_unconfirmed_states(client, db_session, status):
    """Decision 8's asymmetry: summary is not kind-dependent enough to
    justify blocking a user who just wants a meeting-style summary while
    classification is pending, uncertain, or failed."""
    t = _transcript(db_session, classification_status=status)
    r = client.post(f"/api/transcripts/{t.id}/summarize", data={"provider": "local_llm", "model": "m"})
    assert r.status_code == 200


def test_summary_blocked_once_confirmed_voice_note(client, db_session):
    t = _transcript(db_session, classification_status="success", kind="voice_note")
    r = client.post(f"/api/transcripts/{t.id}/summarize", data={"provider": "local_llm", "model": "m"})
    assert r.status_code == 400


@pytest.mark.parametrize("status", UNCONFIRMED_STATUSES)
def test_reformat_blocked_across_unconfirmed_states(client, db_session, status):
    t = _transcript(db_session, classification_status=status)
    r = client.post(f"/api/transcripts/{t.id}/format/markdown", data={"provider": "local_llm", "model": "m"})
    assert r.status_code == 400


@pytest.mark.parametrize("status", UNCONFIRMED_STATUSES)
def test_rediarize_blocked_across_unconfirmed_states(client, db_session, tmp_path, status):
    """With stored audio present, the OLD blocklist-based guard would let
    this through (kind='meeting' is never in the dictation/voice_note
    blocklist) -- the allow-list must block it anyway since no kind is
    confirmed yet (design decision 8: an unconfirmed classification must
    never behave like an accepted 'meeting')."""
    t = _transcript_with_audio(db_session, tmp_path, classification_status=status)
    r = client.post(f"/api/transcripts/{t.id}/rediarize")
    assert r.status_code == 400


def test_rediarize_allowed_when_accepted_meeting(client, db_session, tmp_path):
    """Regression guard: the allow-list must still admit the normal case
    (confirmed meeting) once classification succeeds — with real stored
    audio present, this reaches job enqueue (200), not the audio-missing
    400 a fixture without audio would give either way."""
    t = _transcript_with_audio(db_session, tmp_path, classification_status="success", kind="meeting")
    r = client.post(f"/api/transcripts/{t.id}/rediarize")
    assert r.status_code == 200


def test_rediarize_blocked_when_prepass_rejects(client, db_session, tmp_path):
    """Issue #416: the audio-feature pre-pass is ANDed on top of the kind
    allow-list, and is NOT overridable by an explicit rediarize request --
    an accepted meeting with stored audio that the pre-pass rejects must
    still 400, with the rejection reason surfaced in the detail message."""
    t = _transcript_with_audio(db_session, tmp_path, classification_status="success", kind="meeting")
    with patch("app.evaluate_diarization_eligibility_async", new_callable=AsyncMock,
               return_value=DiarizationEligibility(False, "too short to diarize")):
        r = client.post(f"/api/transcripts/{t.id}/rediarize")
    assert r.status_code == 400
    assert "too short to diarize" in r.json()["detail"]


def test_rediarize_allowed_when_prepass_reports_eligible(client, db_session, tmp_path):
    """Complement of the test above: same accepted meeting, pre-pass
    patched eligible, still reaches job enqueue (200) -- proves the gate
    isn't an unconditional veto."""
    t = _transcript_with_audio(db_session, tmp_path, classification_status="success", kind="meeting")
    with patch("app.evaluate_diarization_eligibility_async", new_callable=AsyncMock, return_value=DiarizationEligibility(True)):
        r = client.post(f"/api/transcripts/{t.id}/rediarize")
    assert r.status_code == 200


@pytest.mark.parametrize("status", UNCONFIRMED_STATUSES)
def test_voice_match_blocked_across_unconfirmed_states(client, db_session, tmp_path, status):
    t = _transcript_with_audio(db_session, tmp_path, classification_status=status)
    r = client.post(f"/api/transcripts/{t.id}/voice-match")
    assert r.status_code == 400


def test_voice_match_allowed_when_accepted_meeting(client, db_session, tmp_path):
    t = _transcript_with_audio(db_session, tmp_path, classification_status="success", kind="meeting")
    r = client.post(f"/api/transcripts/{t.id}/voice-match")
    assert r.status_code == 200


@pytest.mark.parametrize("status", UNCONFIRMED_STATUSES)
def test_voice_note_rerun_blocked_across_unconfirmed_states(client, db_session, status):
    t = _transcript(db_session, classification_status=status)
    r = client.post(f"/api/transcripts/{t.id}/voice-note/rerun", data={"provider": "local_llm", "model": "m"})
    assert r.status_code == 400


def test_diarize_standalone_blocked_when_prepass_rejects(client, db_session, tmp_path):
    """#417: the standalone /api/diarize (no transcript, so no kind guard is
    expressible) gains the same audio-feature pre-pass as the other two
    diarization sites. An explicit request does not override it — same reading
    as decision 11 row 9. Mutation check: replacing the new clause's body with
    'pass' turns this 400 into a 500 (the endpoint falls through to the
    heuristic, which chokes on the junk bytes)."""
    import io
    with patch("app.evaluate_diarization_eligibility_async", new_callable=AsyncMock,
               return_value=DiarizationEligibility(False, "too short to diarize")):
        resp = client.post(
            "/api/diarize",
            files={"file": ("t.wav", io.BytesIO(b"RIFF0000WAVE"), "audio/wav")},
            data={"method": "heuristic"},
        )
    assert resp.status_code == 400
    assert "too short to diarize" in resp.json()["detail"]


def test_diarize_standalone_allowed_when_prepass_reports_eligible(client, db_session, tmp_path):
    """Complement: pre-pass eligible still reaches the diarizer (200). Patch
    the diarizer itself so the junk bytes don't matter — this test exercises
    the eligibility gate, not the diarization engine."""
    import io
    from services.diarization import DiarizationResult, DiarizationSegment
    async def fake_heuristic(*a, **k):
        return DiarizationResult(
            segments=[DiarizationSegment(start=0.0, end=1.0, speaker="SPEAKER_00", text="hi")],
            speaker_count=1, method="heuristic",
        )
    with patch("app.evaluate_diarization_eligibility_async", new_callable=AsyncMock,
               return_value=DiarizationEligibility(True)), \
         patch("app.diarization_service.diarize_heuristic", fake_heuristic):
        resp = client.post(
            "/api/diarize",
            files={"file": ("t.wav", io.BytesIO(b"RIFF0000WAVE"), "audio/wav")},
            data={"method": "heuristic"},
        )
    assert resp.status_code == 200
    assert resp.json()["speaker_count"] == 1


@pytest.mark.parametrize("status", UNCONFIRMED_STATUSES)
def test_dictation_job_fields_meeting_shaped_across_unconfirmed_states(db_session, status):
    """_dictation_job_fields must not surface a stray classify_intent job
    under any of the three unconfirmed states, even with a placeholder
    kind='dictation' left over from before classification ran."""
    from app import _serialize_transcript, _batch_latest_jobs
    user = User(username=f"dictfields_{status}", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="t", filename="t.mp3", status="completed",
        full_text="x", segments=[], kind="dictation", classification_status=status,
    )
    db_session.add(t)
    db_session.commit()
    db_session.add(LlmJob(
        user_id=user.id, transcript_id=t.id, kind="classify_intent",
        provider="local_llm", model="m", status="pending",
    ))
    db_session.commit()
    out = _serialize_transcript(db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]))
    assert out["classify_intent_job"] is None
