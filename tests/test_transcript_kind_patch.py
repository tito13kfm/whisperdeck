"""PATCH /api/transcripts/{id} kind field: validation, the processing guard
(the pipeline reads kind mid-job to decide diarization), and serialization."""
from database import Transcript, User


def _testuser(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _make_transcript(db_session, **overrides):
    user = _testuser(db_session)
    fields = dict(user_id=user.id, title="t", filename="t.mp3",
                  status="completed", full_text="x", kind="meeting")
    fields.update(overrides)
    t = Transcript(**fields)
    db_session.add(t)
    db_session.commit()
    return t


def test_patch_kind_toggles_and_serializes(client, db_session):
    t = _make_transcript(db_session, kind="meeting")
    r = client.patch(f"/api/transcripts/{t.id}", json={"kind": "dictation"})
    assert r.status_code == 200
    assert r.json()["kind"] == "dictation"
    db_session.refresh(t)
    assert t.kind == "dictation"


def test_patch_kind_accepts_voice_note(client, db_session):
    """Issue #169: voice_note is a third valid kind. PATCH must accept
    it in the allowlist alongside meeting/dictation."""
    t = _make_transcript(db_session, kind="meeting")
    r = client.patch(f"/api/transcripts/{t.id}", json={"kind": "voice_note"})
    assert r.status_code == 200
    assert r.json()["kind"] == "voice_note"
    db_session.refresh(t)
    assert t.kind == "voice_note"


def test_patch_kind_rejects_unknown_value(client, db_session):
    t = _make_transcript(db_session)
    r = client.patch(f"/api/transcripts/{t.id}", json={"kind": "podcast"})
    assert r.status_code == 400
    db_session.refresh(t)
    assert t.kind == "meeting"


def test_patch_kind_rejected_while_processing(client, db_session):
    t = _make_transcript(db_session, status="processing")
    r = client.patch(f"/api/transcripts/{t.id}", json={"kind": "dictation"})
    assert r.status_code == 409
    db_session.refresh(t)
    assert t.kind == "meeting"


def test_patch_same_kind_allowed_while_processing(client, db_session):
    t = _make_transcript(db_session, status="processing", kind="meeting")
    r = client.patch(f"/api/transcripts/{t.id}", json={"kind": "meeting"})
    assert r.status_code == 200


def test_patch_kind_records_explicit_override(client, db_session):
    """Explicitly picking a kind via PATCH is a manual override (design
    decision 5) — must flip classification_status to 'override' even if the
    transcript was previously 'pending' (auto-classification in flight)."""
    t = _make_transcript(db_session, kind="meeting", classification_status="pending")
    r = client.patch(f"/api/transcripts/{t.id}", json={"kind": "dictation"})
    assert r.status_code == 200
    db_session.refresh(t)
    assert t.classification_status == "override"


def test_patch_auto_sets_pending_placeholder(db_session, client):
    """PATCH kind='auto' reverts to auto-classification: stores 'meeting'
    placeholder with classification_status='pending' (same pattern as a
    fresh 'auto' recording)."""
    t = _make_transcript(db_session, kind="dictation", classification_status="override")
    r = client.patch(f"/api/transcripts/{t.id}", json={"kind": "auto"})
    assert r.status_code == 200
    db_session.refresh(t)
    assert t.kind == "meeting"
    assert t.classification_status == "pending"
    assert t.classification_confidence is None
    assert t.classification_provenance is None


def test_patch_auto_accepted_by_serializer(db_session, client):
    """PATCH kind='auto' serializes with the placeholder kind but
    classification_status='pending'."""
    t = _make_transcript(db_session, kind="dictation", classification_status="override")
    r = client.patch(f"/api/transcripts/{t.id}", json={"kind": "auto"})
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "meeting"
    assert data["classification_status"] == "pending"
    assert data["classification_confidence"] is None
