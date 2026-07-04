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
         patch("app.voice_id_service.enroll", return_value=profile) as fake_enroll:
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Alice", "clips": [{"start": 0.0, "end": 2.0}]})
    assert r.status_code == 200
    assert r.json()["name"] == "Alice"
    fake_extract.assert_awaited_once()
    fake_enroll.assert_called_once()
    assert not sample.exists()  # temp sample cleaned up


def test_enroll_speaker_cleans_up_when_enroll_fails(client, db_session, tmp_path):
    t = _transcript(db_session, tmp_path)
    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")

    with patch("app.extract_clips_concat", AsyncMock(return_value=str(sample))), \
         patch("app.voice_id_service.enroll", side_effect=ValueError("no backend")):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Alice", "clips": [{"start": 0.0, "end": 2.0}]})
    assert r.status_code == 400
    assert "no backend" in r.json()["detail"]
    assert not sample.exists()


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
