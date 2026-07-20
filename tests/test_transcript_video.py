"""GET /api/transcripts/{id}/video and has_video serialization."""
from database import Transcript


def _video_transcript(db_session, tmp_path, video_path=None):
    t = Transcript(user_id=1, title="t", filename="t.mp4", status="completed",
                   full_text="x", audio_path=str(tmp_path / "a.mp3"), video_path=video_path)
    db_session.add(t)
    db_session.commit()
    return t


def test_has_video_false_when_no_video_path(client, db_session, tmp_path):
    t = _video_transcript(db_session, tmp_path)
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["has_video"] is False


def test_has_video_false_when_file_missing(client, db_session, tmp_path):
    t = _video_transcript(db_session, tmp_path, video_path=str(tmp_path / "gone.mp4"))
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["has_video"] is False


def test_has_video_true_when_file_present(client, db_session, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake mp4 bytes")
    t = _video_transcript(db_session, tmp_path, video_path=str(video))
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["has_video"] is True


def test_video_route_serves_file(client, db_session, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"real mp4 bytes")
    t = _video_transcript(db_session, tmp_path, video_path=str(video))
    r = client.get(f"/api/transcripts/{t.id}/video")
    assert r.status_code == 200
    assert r.content == b"real mp4 bytes"
    assert r.headers["content-type"] == "video/mp4"


def test_video_route_404_when_no_video_path(client, db_session, tmp_path):
    t = _video_transcript(db_session, tmp_path)
    r = client.get(f"/api/transcripts/{t.id}/video")
    assert r.status_code == 404


def test_video_route_404_for_other_users_transcript(client, db_session, tmp_path):
    from database import User
    other = User(username="otheruser", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"bytes")
    t = Transcript(user_id=other.id, title="t", filename="t.mp4", status="completed",
                   full_text="x", video_path=str(video))
    db_session.add(t)
    db_session.commit()
    r = client.get(f"/api/transcripts/{t.id}/video")
    assert r.status_code == 404


def test_video_route_supports_range_requests(client, db_session, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0123456789")
    t = _video_transcript(db_session, tmp_path, video_path=str(video))
    r = client.get(f"/api/transcripts/{t.id}/video", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"
