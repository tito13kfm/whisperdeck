"""One-shot contract check: confirm _serialize_transcript returns the same
set of keys for both meeting and dictation transcripts as it did before the
batch-load refactor. Issue #147 acceptance criterion: 'No behavioral change
in the API response'. This is a check the existing suite doesn't cover
because it compares the FULL key set, not individual values."""
from database import Transcript, User, ProviderConfig, utcnow_naive
from app import _serialize_transcript, _batch_latest_jobs


def _build_transcript(db, kind, username="contract"):
    user = User(username=username, password_hash="x", password_salt="y")
    db.add(user)
    db.commit()
    t = Transcript(
        user_id=user.id, title="c", filename="c.mp3", kind=kind,
        status="completed", full_text="hi", segments=[],
    )
    db.add(t)
    db.add(ProviderConfig(user_id=user.id, name="groq", api_key="fake"))
    db.commit()
    return user, t


# Expected key set from a hand-trace of the current code. Any drift from
# this is a regression.
EXPECTED_KEYS = {
    "id", "source_transcript_id", "kind", "title", "filename",
    "duration_seconds", "provider", "model", "language", "status",
    "full_text", "segments", "speaker_count", "diarization_method",
    "num_speakers", "error", "corrected_text", "correction_error",
    "correction_model", "created_at", "updated_at", "has_summary",
    "has_audio", "has_video", "job_progress", "processed_size_bytes",
    "queue_status",
    "correction_job", "summary_job", "voice_match_job",
    "format_markdown_job", "format_email_job", "format_coding_prompt_job",
    "classify_intent_job", "classify_intent_hint",
    "voice_note_job",
}


def test_meeting_transcript_key_set_matches_expected(db_session):
    user, t = _build_transcript(db_session, kind="meeting")
    out = _serialize_transcript(db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]))
    assert set(out.keys()) == EXPECTED_KEYS, f"missing: {EXPECTED_KEYS - set(out.keys())}, extra: {set(out.keys()) - EXPECTED_KEYS}"


def test_dictation_transcript_key_set_matches_expected(db_session):
    user, t = _build_transcript(db_session, kind="dictation")
    out = _serialize_transcript(db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]))
    assert set(out.keys()) == EXPECTED_KEYS


def test_voice_note_transcript_key_set_matches_expected(db_session):
    user, t = _build_transcript(db_session, kind="voice_note", username="contract-vn")
    out = _serialize_transcript(db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]))
    assert set(out.keys()) == EXPECTED_KEYS


def test_all_kinds_have_same_job_field_names(db_session):
    """The dictation-only / voice-note-only fields are always present
    (null for kinds that never have that job). The shape is uniform
    across all kinds so the frontend doesn't have to switch on kind to
    read the response."""
    _, meeting = _build_transcript(db_session, kind="meeting", username="contract-meeting")
    _, dictation = _build_transcript(db_session, kind="dictation", username="contract-dictation")
    _, voice_note = _build_transcript(db_session, kind="voice_note", username="contract-vn2")
    m = _serialize_transcript(db_session, meeting, jobs_map=_batch_latest_jobs(db_session, [meeting.id]))
    d = _serialize_transcript(db_session, dictation, jobs_map=_batch_latest_jobs(db_session, [dictation.id]))
    v = _serialize_transcript(db_session, voice_note, jobs_map=_batch_latest_jobs(db_session, [voice_note.id]))
    assert set(m.keys()) == set(d.keys()) == set(v.keys())
    for k in ("format_markdown_job", "format_email_job", "format_coding_prompt_job",
              "classify_intent_job", "classify_intent_hint", "voice_note_job"):
        assert m[k] is None, f"{k} should be None for meeting, got {m[k]!r}"
        assert d[k] is None, f"{k} should be None for dictation, got {d[k]!r}"
    # voice_note has the format_* fields null (chain is separate) but
    # voice_note_job is the new active field; the meeting/dictation
    # rows have it null too.
    assert v["voice_note_job"] is None  # no job yet in this fixture


def test_include_relabel_adds_only_last_relabel_key(db_session):
    user, t = _build_transcript(db_session, kind="meeting")
    base = _serialize_transcript(db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]))
    with_relabel = _serialize_transcript(
        db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]), include_relabel=True,
    )
    assert "last_relabel" not in base
    assert with_relabel["last_relabel"] is None  # no relabel row exists
    # No other keys should differ.
    assert set(base.keys()) | {"last_relabel"} == set(with_relabel.keys())
