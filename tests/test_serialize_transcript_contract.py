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
    "id", "source_transcript_id", "batch_id", "kind",
    "classification_status", "classification_confidence", "classification_provenance",
    "title", "filename",
    "duration_seconds", "provider", "model", "language", "status",
    "full_text", "segments", "speaker_count", "diarization_method",
    "num_speakers", "error", "corrected_text", "correction_error",
    "correction_model", "created_at", "updated_at", "has_summary",
    "has_audio", "has_video", "job_progress", "processed_size_bytes",
    "queue_status",
    "correction_job", "summary_job", "voice_match_job", "classify_pipeline_job",
    "format_markdown_job", "format_email_job", "format_coding_prompt_job",
    "classify_intent_job", "classify_intent_hint",
    "voice_note_job", "tagging_job",
    "tags",
    "cost",
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
    # tagging_job is uniform across all kinds (tagging runs on every
    # kind, not just one); null in this fixture because no job exists yet.
    assert m["tagging_job"] is None
    assert d["tagging_job"] is None
    assert v["tagging_job"] is None
    # classify_pipeline_job is uniform too (issue #267) — null until a
    # classification job exists, regardless of kind.
    assert m["classify_pipeline_job"] is None
    assert d["classify_pipeline_job"] is None
    assert v["classify_pipeline_job"] is None
    # tags list is also uniform, empty when no job has completed.
    assert m["tags"] == []
    assert d["tags"] == []
    assert v["tags"] == []


def test_classification_state_defaults_to_override(db_session):
    """Every transcript created today picks an explicit kind (issue #268
    hasn't introduced the 'auto' sentinel yet) — the column default reflects
    that: 'override', no confidence, no provenance until #268/#269 wire real
    override provenance recording."""
    user, t = _build_transcript(db_session, kind="meeting", username="contract-override")
    out = _serialize_transcript(db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]))
    assert out["classification_status"] == "override"
    assert out["classification_confidence"] is None
    assert out["classification_provenance"] is None


def test_pending_transcript_gets_meeting_shaped_job_fields(db_session):
    """_dictation_job_fields must branch on effective_kind(), not raw kind
    (design decision 11) -- a placeholder kind on a still-pending 'auto'
    transcript must not surface a stray classify_intent job under
    classify_intent_job, since no kind-gated job should have been dispatched
    for it while unclassified (routes to the same branch as meeting).
    Uses a real LlmJob row (not just "is the field None") so the assertion
    actually discriminates between the dictation branch and the fallback
    branch -- both would show None here if no job existed at all."""
    from database import LlmJob
    user = User(username="contract-pending", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="c", filename="c.mp3", kind="dictation",
        classification_status="pending", status="processing", full_text="", segments=[],
    )
    db_session.add(t)
    db_session.commit()
    db_session.add(LlmJob(user_id=user.id, transcript_id=t.id, kind="classify_intent", provider="local_llm", model="m", status="pending"))
    db_session.commit()
    out = _serialize_transcript(db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]))
    assert out["classify_intent_job"] is None, "a pending transcript must not surface classify_intent_job even if a stray job row exists"


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


def test_auto_kind_pending_serialization(db_session):
    """An auto-classified transcript in pending state serializes with
    classification_status='pending' and the placeholder kind, not the
    raw 'auto' value (which is never stored on the row)."""
    user = User(username="contract-auto", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="c", filename="c.mp3", kind="meeting",
        classification_status="pending", status="processing",
        full_text="some text", segments=[],
    )
    db_session.add(t)
    db_session.commit()
    out = _serialize_transcript(db_session, t, jobs_map=_batch_latest_jobs(db_session, [t.id]))
    assert out["kind"] == "meeting"
    assert out["classification_status"] == "pending"
    assert out["classification_confidence"] is None
    assert out["classification_provenance"] is None
    # _dictation_job_fields with effective_kind()=None (pending) returns
    # meeting-shaped job fields (all dictation/voice-note fields are None).
    assert out["classify_intent_job"] is None
    assert out["voice_note_job"] is None
