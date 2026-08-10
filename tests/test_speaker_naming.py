"""Speaker naming: audio serving, per-transcript speaker rename, and
voice enrollment from seed clips."""
import os
import shutil
import wave
import struct
from unittest.mock import AsyncMock, patch

import pytest

from database import Transcript, User, VoiceProfile


def _test_user(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _transcript(db_session, tmp_path=None, **overrides):
    user = _test_user(db_session)
    fields = dict(
        user_id=user.id, title="mtg", filename="mtg.mp3", status="completed",
        full_text="hello there general",
        segments=[
            {"start": 0.0, "end": 2.0, "text": "hello there", "speaker": "SPEAKER_00"},
            {"start": 2.0, "end": 4.0, "text": "general kenobi", "speaker": "SPEAKER_01"},
            {"start": 4.0, "end": 6.0, "text": "you are bold", "speaker": "SPEAKER_00"},
        ],
    )
    if tmp_path is not None:
        audio = tmp_path / "mtg.mp3"
        audio.write_bytes(b"fake mp3 bytes")
        fields["audio_path"] = str(audio)
    fields.update(overrides)
    t = Transcript(**fields)
    db_session.add(t)
    db_session.commit()
    return t


# ── GET /audio ─────────────────────────────────────────────────────────────

def test_audio_route_serves_stored_file(client, db_session, tmp_path):
    t = _transcript(db_session, tmp_path)
    r = client.get(f"/api/transcripts/{t.id}/audio")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.content == b"fake mp3 bytes"


def test_audio_route_404_without_stored_audio(client, db_session, tmp_path):
    no_path = _transcript(db_session)
    gone = _transcript(db_session, audio_path=str(tmp_path / "never-existed.mp3"))
    for tid in (no_path.id, gone.id):
        r = client.get(f"/api/transcripts/{tid}/audio")
        assert r.status_code == 404
        assert "No stored audio" in r.json()["detail"]


# ── POST /speakers/rename ──────────────────────────────────────────────────

def test_rename_updates_all_matching_segments(client, db_session):
    t = _transcript(db_session)
    r = client.post(f"/api/transcripts/{t.id}/speakers/rename",
                    json={"from": "SPEAKER_00", "to": "Alice"})
    assert r.status_code == 200
    assert r.json()["renamed"] == 2

    # Re-query from a clean session state — guards the JSON-column
    # change-tracking gotcha (in-place mutation would not persist).
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    speakers = [s["speaker"] for s in t2.segments]
    assert speakers == ["Alice", "SPEAKER_01", "Alice"]


def test_rename_rewrites_corrected_text_line_anchored_only(client, db_session):
    corrected = (
        "SPEAKER_00: I met SPEAKER_00: impersonators once.\n\n"
        "SPEAKER_01: Sure.\n\n"
        "SPEAKER_00: Really."
    )
    t = _transcript(db_session, corrected_text=corrected)
    r = client.post(f"/api/transcripts/{t.id}/speakers/rename",
                    json={"from": "SPEAKER_00", "to": "Alice"})
    assert r.status_code == 200
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    lines = t2.corrected_text.splitlines()
    # line-leading prefix renamed; the same string mid-line untouched
    assert lines[0] == "Alice: I met SPEAKER_00: impersonators once."
    assert lines[2] == "SPEAKER_01: Sure."
    assert lines[4] == "Alice: Really."


def test_rename_validation(client, db_session):
    t = _transcript(db_session)
    assert client.post(f"/api/transcripts/{t.id}/speakers/rename",
                       json={"from": "", "to": "Alice"}).status_code == 400
    assert client.post(f"/api/transcripts/{t.id}/speakers/rename",
                       json={"from": "SPEAKER_00", "to": "  "}).status_code == 400
    r = client.post(f"/api/transcripts/{t.id}/speakers/rename",
                    json={"from": "SPEAKER_99", "to": "Alice"})
    assert r.status_code == 400
    assert "No segments" in r.json()["detail"]


# ── POST /segments/retag ──────────────────────────────────────────────────

def test_retag_only_changes_selected_indices(client, db_session):
    t = _transcript(db_session)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0], "speaker": "Bob"})
    assert r.status_code == 200
    assert r.json()["retagged"] == 1
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    speakers = [s["speaker"] for s in t2.segments]
    # index 0 retagged; index 2 (also originally SPEAKER_00) untouched
    assert speakers == ["Bob", "SPEAKER_01", "SPEAKER_00"]


def test_retag_leaves_corrected_text_untouched(client, db_session):
    corrected = "SPEAKER_00: hello there\n\nSPEAKER_01: general kenobi"
    t = _transcript(db_session, corrected_text=corrected)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0], "speaker": "Bob"})
    assert r.status_code == 200
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t2.corrected_text == corrected


def test_retag_stamps_user_assigned_confidence(client, db_session):
    """Issue #305: a manual retag overrides the diarizer, so the diarizer's
    stale confidence must not survive on the corrected line (it kept the "?"
    uncertainty marker on lines the user just fixed)."""
    from services.relabel import USER_ASSIGNED_CONFIDENCE
    t = _transcript(db_session, segments=[
        {"start": 0.0, "end": 2.0, "text": "hello there", "speaker": "SPEAKER_00", "speaker_confidence": 0.3},
        {"start": 2.0, "end": 4.0, "text": "general kenobi", "speaker": "SPEAKER_01", "speaker_confidence": 0.9},
        {"start": 4.0, "end": 6.0, "text": "you are bold", "speaker": "SPEAKER_00", "speaker_confidence": 0.2},
    ])
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0], "speaker": "Bob"})
    assert r.status_code == 200
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    confs = [s["speaker_confidence"] for s in t2.segments]
    # index 0 gets the sentinel; untouched lines keep the diarizer's numbers,
    # including index 2 which shares the retagged line's original label
    assert confs == [USER_ASSIGNED_CONFIDENCE, 0.9, 0.2]


def test_retag_stamps_sentinel_even_without_prior_confidence(client, db_session):
    """A never-diarized transcript has no speaker_confidence keys at all; a
    retag still marks the line user-assigned rather than leaving it bare."""
    from services.relabel import USER_ASSIGNED_CONFIDENCE
    t = _transcript(db_session)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [1], "speaker": "Bob"})
    assert r.status_code == 200
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t2.segments[1]["speaker_confidence"] == USER_ASSIGNED_CONFIDENCE
    assert "speaker_confidence" not in t2.segments[0]
    assert "speaker_confidence" not in t2.segments[2]


def test_retag_validation(client, db_session):
    t = _transcript(db_session)
    assert client.post(f"/api/transcripts/{t.id}/segments/retag",
                       json={"indices": [], "speaker": "Bob"}).status_code == 400
    assert client.post(f"/api/transcripts/{t.id}/segments/retag",
                       json={"indices": [0], "speaker": "  "}).status_code == 400
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [99], "speaker": "Bob"})
    assert r.status_code == 400
    assert "out of range" in r.json()["detail"]


# ── POST /enroll-speaker ───────────────────────────────────────────────────

def test_enroll_speaker_happy_path(client, db_session, tmp_path):
    t = _transcript(db_session, tmp_path)
    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")

    fake_extract = AsyncMock(return_value=str(sample))
    user = _test_user(db_session)
    profile = VoiceProfile(user_id=user.id, name="Alice", embedding=[0.1],
                           embedding_model="MFCC fingerprint (librosa)",
                           sample_count=1, notes="Seeded from transcript")
    db_session.add(profile)
    db_session.commit()

    with patch("app.extract_clips_concat", fake_extract), \
         patch("app.voice_id_service.add_clip") as fake_add_clip:
        from database import VoiceClip
        fake_add_clip.return_value = VoiceClip(id=1, voice_profile_id=profile.id)
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Alice", "clips": [{"start": 0.0, "end": 2.0}]})
    assert r.status_code == 200
    assert r.json()["name"] == "Alice"
    fake_extract.assert_awaited_once()
    fake_add_clip.assert_called_once()
    assert not sample.exists()  # temp sample cleaned up


def test_enroll_speaker_cleans_up_when_enroll_fails(client, db_session, tmp_path):
    t = _transcript(db_session, tmp_path)
    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")

    with patch("app.extract_clips_concat", AsyncMock(return_value=str(sample))), \
         patch("app.voice_id_service.add_clip", side_effect=ValueError("no backend")):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Alice", "clips": [{"start": 0.0, "end": 2.0}]})
    assert r.status_code == 400
    assert "no backend" in r.json()["detail"]
    assert not sample.exists()


def test_enroll_speaker_accepts_a_fallback_clip_for_a_brand_new_name(client, db_session, tmp_path):
    """Issue #109. This route stamps the new profile with `backend_name` before
    extracting anything, so the row it just created is the only thing on the
    roster carrying a model id. The orphan guard has to skip profiles with no
    clips, or a fallback clip collides with its own placeholder and enrollment
    fails even though nothing was enrolled yet."""
    import numpy as np
    from database import VoiceClip
    t = _transcript(db_session, tmp_path)
    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")

    with patch("app.extract_clips_concat", AsyncMock(return_value=str(sample))), \
         patch("app.voice_id_service._backend", "speechbrain"), \
         patch("app.voice_id_service._extract_embedding",
               return_value=(np.array([1.0, 2.0]), "MFCC fingerprint (librosa)")):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Newcomer", "clips": [{"start": 0.0, "end": 2.0}]})

    assert r.status_code == 200, r.json()
    body = r.json()
    # the degradation is reported rather than silently accepted
    assert body["warning"] is not None
    assert "MFCC" in body["warning"]
    # and the clip really landed
    profile = db_session.query(VoiceProfile).filter(VoiceProfile.name == "Newcomer").first()
    assert profile is not None
    assert profile.embedding_model == "MFCC fingerprint (librosa)"
    assert db_session.query(VoiceClip).filter(
        VoiceClip.voice_profile_id == profile.id).count() == 1


def test_enroll_speaker_still_refuses_a_fallback_clip_against_an_enrolled_roster(client, db_session, tmp_path):
    """The complement of the test above, so the guard cannot quietly become a
    no-op: a profile that really is enrolled under speechbrain means an MFCC
    clip would create a speaker voice match can never find."""
    import numpy as np
    t = _transcript(db_session, tmp_path)
    user = _test_user(db_session)
    db_session.add(VoiceProfile(user_id=user.id, name="Alice", embedding=[0.1, 0.2],
                                embedding_model="speechbrain/spkrec-ecapa-voxceleb",
                                sample_count=1))
    db_session.commit()
    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")

    with patch("app.extract_clips_concat", AsyncMock(return_value=str(sample))), \
         patch("app.voice_id_service._backend", "speechbrain"), \
         patch("app.voice_id_service._extract_embedding",
               return_value=(np.array([1.0, 2.0]), "MFCC fingerprint (librosa)")):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Newcomer", "clips": [{"start": 0.0, "end": 2.0}]})

    assert r.status_code == 400
    assert "MFCC" in r.json()["detail"]


def test_enroll_speaker_appends_clip_to_existing_profile_without_overwriting(client, db_session, tmp_path):
    t = _transcript(db_session, tmp_path)
    user = _test_user(db_session)
    from database import VoiceProfile, VoiceClip
    profile = VoiceProfile(user_id=user.id, name="Alice", embedding=[9.0, 9.0],
                           embedding_model="MFCC fingerprint (librosa)", sample_count=1)
    db_session.add(profile)
    db_session.commit()
    # Back the pre-existing embedding with an actual clip row — add_clip's
    # averaging is computed from VoiceClip rows, not the raw embedding field.
    db_session.add(VoiceClip(voice_profile_id=profile.id, audio_path="/dev/null",
                              embedding=[9.0, 9.0]))
    db_session.commit()

    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")
    fake_extract = AsyncMock(return_value=str(sample))

    with patch("app.extract_clips_concat", fake_extract), \
         patch("app.voice_id_service._extract_embedding", return_value=(__import__("numpy").array([1.0, 3.0]), "MFCC fingerprint (librosa)")):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Alice", "clips": [{"start": 0.0, "end": 2.0}]})
    assert r.status_code == 200
    db_session.expire_all()
    refreshed = db_session.query(VoiceProfile).filter(VoiceProfile.id == profile.id).first()
    # averaged with the existing [9.0, 9.0] embedding, not overwritten to [1.0, 3.0]
    assert refreshed.embedding == [5.0, 6.0]
    assert refreshed.sample_count == 2
    # the new clip's audio must be a permanent copy, not the temp seed sample
    # that the route deletes in its finally block — otherwise the clip is
    # unplayable from the roster.
    clip_id = r.json()["clip_id"]
    new_clip = db_session.query(VoiceClip).filter(VoiceClip.id == clip_id).first()
    assert os.path.exists(new_clip.audio_path)


def test_enroll_speaker_new_profile_rolled_back_when_add_clip_fails(client, db_session, tmp_path):
    """If add_clip fails for a brand-new speaker name, the just-created
    empty VoiceProfile row must be rolled back and the permanent clip
    copy must not be left behind in VOICES_DIR."""
    from app import VOICES_DIR

    t = _transcript(db_session, tmp_path)
    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")

    before = set(os.listdir(VOICES_DIR)) if VOICES_DIR.exists() else set()

    with patch("app.extract_clips_concat", AsyncMock(return_value=str(sample))), \
         patch("app.voice_id_service.add_clip", side_effect=ValueError("boom")):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "BrandNewSpeaker", "clips": [{"start": 0.0, "end": 2.0}]})
    try:
        assert r.status_code == 400
        assert "boom" in r.json()["detail"]

        db_session.expire_all()
        profile = db_session.query(VoiceProfile).filter(
            VoiceProfile.name == "BrandNewSpeaker"
        ).first()
        assert profile is None

        after = set(os.listdir(VOICES_DIR)) if VOICES_DIR.exists() else set()
        leftovers = after - before
        assert leftovers == set()
    finally:
        # test isolation: remove anything the (possibly still-buggy) route left behind
        after = set(os.listdir(VOICES_DIR)) if VOICES_DIR.exists() else set()
        for name in after - before:
            try:
                os.remove(VOICES_DIR / name)
            except OSError:
                pass


def test_enroll_speaker_existing_profile_not_deleted_when_add_clip_fails(client, db_session, tmp_path):
    """If add_clip fails for a speaker that already had a VoiceProfile,
    that pre-existing profile must survive — only profiles created by
    this exact failed request should be cleaned up."""
    from app import VOICES_DIR

    t = _transcript(db_session, tmp_path)
    user = _test_user(db_session)
    profile = VoiceProfile(user_id=user.id, name="ExistingSpeaker", embedding=[0.1],
                           embedding_model="MFCC fingerprint (librosa)",
                           sample_count=1, notes="Seeded from transcript")
    db_session.add(profile)
    db_session.commit()
    profile_id = profile.id

    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")

    before = set(os.listdir(VOICES_DIR)) if VOICES_DIR.exists() else set()

    with patch("app.extract_clips_concat", AsyncMock(return_value=str(sample))), \
         patch("app.voice_id_service.add_clip", side_effect=ValueError("boom")):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "ExistingSpeaker", "clips": [{"start": 0.0, "end": 2.0}]})
    try:
        assert r.status_code == 400
        assert "boom" in r.json()["detail"]

        db_session.expire_all()
        still_there = db_session.query(VoiceProfile).filter(
            VoiceProfile.id == profile_id
        ).first()
        assert still_there is not None
        assert still_there.name == "ExistingSpeaker"

        after = set(os.listdir(VOICES_DIR)) if VOICES_DIR.exists() else set()
        leftovers = after - before
        assert leftovers == set()
    finally:
        after = set(os.listdir(VOICES_DIR)) if VOICES_DIR.exists() else set()
        for name in after - before:
            try:
                os.remove(VOICES_DIR / name)
            except OSError:
                pass


def test_enroll_speaker_validation(client, db_session, tmp_path):
    t = _transcript(db_session, tmp_path)
    assert client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                       json={"name": "", "clips": [{"start": 0, "end": 1}]}).status_code == 400
    assert client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                       json={"name": "Alice", "clips": []}).status_code == 400
    many = [{"start": i, "end": i + 1} for i in range(11)]
    assert client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                       json={"name": "Alice", "clips": many}).status_code == 400
    no_audio = _transcript(db_session)
    assert client.post(f"/api/transcripts/{no_audio.id}/enroll-speaker",
                       json={"name": "Alice", "clips": [{"start": 0, "end": 1}]}).status_code == 404


# ── extract_clips_concat (real ffmpeg) ─────────────────────────────────────

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_clips_concat_durations(tmp_path):
    import asyncio
    from services.audio_prep import extract_clips_concat, get_audio_duration

    # 10s of 440Hz tone at 16kHz mono
    src = tmp_path / "tone.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        import math
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(16000 * 10)
        )
        w.writeframes(frames)

    out = asyncio.run(extract_clips_concat(
        str(src), [{"start": 1.0, "end": 3.0}, {"start": 5.0, "end": 6.5}], str(tmp_path)
    ))
    try:
        assert os.path.exists(out)
        assert abs(get_audio_duration(out) - 3.5) < 0.2
        # intermediates cleaned up
        leftovers = [p for p in os.listdir(tmp_path) if "_seed_part" in p or "_seed_list" in p]
        assert leftovers == []
    finally:
        os.remove(out)


def test_enroll_speaker_route_passes_hf_token_to_add_clip(client, db_session, tmp_path):
    """The transcript-seed enrollment path must thread the user's hf_token
    just like the direct voice routes — otherwise pyannote users with a
    settings-only token silently get MFCC clips from this flow."""
    t = _transcript(db_session, tmp_path)
    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")

    captured = {}

    def fake_add_clip(db, profile_id, user_id, audio_path, source_transcript_id=None, hf_token=None):
        captured["hf_token"] = hf_token
        from database import VoiceClip
        return VoiceClip(id=1, voice_profile_id=profile_id)

    with patch("app.extract_clips_concat", AsyncMock(return_value=str(sample))), \
         patch("app.voice_id_service.add_clip", fake_add_clip), \
         patch("app.get_user_settings", lambda db, uid: {"hf_token": "settings-token-9"}):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Alice", "clips": [{"start": 0.0, "end": 2.0}]})

    assert r.status_code == 200
    assert captured["hf_token"] == "settings-token-9"
