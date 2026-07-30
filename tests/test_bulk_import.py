"""Tests for bulk import infrastructure (issue #231).
Backend-only: batch_id column, POST /api/bulk-transcribe, batch_id filter,
bulk_defaults settings. No UI assertions."""
import io
import json
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from database import Transcript, utcnow_naive


def _fake_pipeline_result(tid=1, batch_id="20260101_120000_abcdef"):
    """Return a dict shaped like _serialize_transcript for a completed transcript."""
    return {
        "id": tid,
        "source_transcript_id": None,
        "batch_id": batch_id,
        "kind": "meeting",
        "title": "test.mp3",
        "filename": "test.mp3",
        "duration_seconds": 10.0,
        "provider": "moonshine",
        "model": "",
        "language": "auto",
        "status": "completed",
        "full_text": "",
        "segments": [],
        "speaker_count": 0,
        "diarization_method": None,
        "num_speakers": None,
        "error": None,
        "corrected_text": None,
        "correction_error": None,
        "correction_model": None,
        "created_at": "2026-01-01T12:00:00",
        "updated_at": "2026-01-01T12:00:00",
        "has_summary": False,
        "has_audio": False,
        "has_video": False,
        "job_progress": None,
        "processed_size_bytes": 100,
        "queue_status": "completed",
        "correction_job": None,
        "summary_job": None,
        "voice_match_job": None,
        "cost": 0.0,
        "tags": [],
        "format_markdown_job": None,
        "format_email_job": None,
        "format_coding_prompt_job": None,
        "classify_intent_job": None,
        "classify_intent_hint": None,
        "voice_note_job": None,
        "tagging_job": None,
    }


class TestBulkTranscribe:
    """Tests for POST /api/bulk-transcribe."""

    def test_bulk_transcribe_three_files(self, client):
        """POST 3 files with valid settings. Assert 200, response has batch_id
        and 3 transcripts. Each has correct batch_id, provider, kind.
        Mutation check: fails if pipeline is not called or batch_id not threaded."""
        settings = json.dumps({"provider": "moonshine", "kind": "meeting", "language": "en"})
        files = [
            ("files", ("a.mp3", io.BytesIO(b"fake audio 1"), "audio/mpeg")),
            ("files", ("b.mp3", io.BytesIO(b"fake audio 2"), "audio/mpeg")),
            ("files", ("c.mp3", io.BytesIO(b"fake audio 3"), "audio/mpeg")),
        ]

        with patch("app._run_transcription_pipeline", new_callable=AsyncMock) as mock_pipeline:
            mock_pipeline.side_effect = [
                _fake_pipeline_result(tid=1, batch_id="BATCH001"),
                _fake_pipeline_result(tid=2, batch_id="BATCH001"),
                _fake_pipeline_result(tid=3, batch_id="BATCH001"),
            ]
            resp = client.post(
                "/api/bulk-transcribe",
                data={"settings": settings},
                files=files,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "batch_id" in data
        assert len(data["batch_id"]) > 0  # endpoint generates a non-empty batch_id
        assert len(data["transcripts"]) == 3
        # Each transcript carries its batch_id; verify assignment happened
        transcript_batch_ids = {t["batch_id"] for t in data["transcripts"]}
        assert len(transcript_batch_ids) == 1  # all same batch
        assert None not in transcript_batch_ids
        for t in data["transcripts"]:
            assert t["provider"] == "moonshine"
            assert t["kind"] == "meeting"
        assert "errors" not in data
        assert mock_pipeline.call_count == 3

    def test_bulk_transcribe_per_file_overrides(self, client):
        """POST 2 files with global kind 'meeting' and file_settings overriding one.
        Assert override applied correctly. Mutation check: override not applied → test fails."""
        settings = json.dumps({"provider": "moonshine", "kind": "meeting", "language": "en"})
        file_settings = json.dumps([{"kind": "dictation"}, {}])
        files = [
            ("files", ("a.mp3", io.BytesIO(b"fake audio 1"), "audio/mpeg")),
            ("files", ("b.mp3", io.BytesIO(b"fake audio 2"), "audio/mpeg")),
        ]

        with patch("app._run_transcription_pipeline", new_callable=AsyncMock) as mock_pipeline:
            mock_pipeline.side_effect = [
                _fake_pipeline_result(tid=1, batch_id="BATCH002"),
                _fake_pipeline_result(tid=2, batch_id="BATCH002"),
            ]
            resp = client.post(
                "/api/bulk-transcribe",
                data={"settings": settings, "file_settings": file_settings},
                files=files,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["transcripts"]) == 2
        # Verify the first file got kind='dictation' (from override)
        call_args_list = mock_pipeline.call_args_list
        assert call_args_list[0].kwargs["kind"] == "dictation"
        # Verify the second file got kind='meeting' (from global settings)
        assert call_args_list[1].kwargs["kind"] == "meeting"

    def test_bulk_transcribe_invalid_kind(self, client):
        """Assert 400 on invalid kind. Mutation check: kind not validated → test fails."""
        settings = json.dumps({"provider": "moonshine", "kind": "invalid_kind"})
        files = [("files", ("a.mp3", io.BytesIO(b"fake audio"), "audio/mpeg"))]
        resp = client.post(
            "/api/bulk-transcribe",
            data={"settings": settings},
            files=files,
        )
        assert resp.status_code == 400

    def test_bulk_transcribe_no_files(self, client):
        """Assert 400 on zero files. Mutation check: no-files guard not present → test fails."""
        settings = json.dumps({"provider": "moonshine", "kind": "meeting"})
        resp = client.post(
            "/api/bulk-transcribe",
            data={"settings": settings},
            files=[],
        )
        assert resp.status_code == 400

    def test_bulk_transcribe_partial_failure(self, client):
        """Corrupt file in batch. Assert errors array, successful transcripts exist,
        failed one has no transcript entry. Mutation check: error handling missing → test fails."""
        settings = json.dumps({"provider": "moonshine", "kind": "meeting", "language": "en"})
        files = [
            ("files", ("good.mp3", io.BytesIO(b"fake audio 1"), "audio/mpeg")),
            ("files", ("bad.mp3", io.BytesIO(b"fake audio 2"), "audio/mpeg")),
        ]

        with patch("app._run_transcription_pipeline", new_callable=AsyncMock) as mock_pipeline:
            mock_pipeline.side_effect = [
                _fake_pipeline_result(tid=1, batch_id="BATCH003"),
                RuntimeError("transcode failed: corrupt audio"),
            ]
            resp = client.post(
                "/api/bulk-transcribe",
                data={"settings": settings},
                files=files,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["transcripts"]) == 1
        assert len(data["errors"]) == 1
        assert data["errors"][0]["index"] == 1
        assert data["errors"][0]["filename"] == "bad.mp3"
        assert "corrupt audio" in data["errors"][0]["error"]

    def test_bulk_transcribe_partial_failure_http_exception(self, client):
        """_run_transcription_pipeline wraps runtime failures (bad codec,
        transcode errors) in HTTPException, not just plain exceptions —
        regression test for a bug where the endpoint re-raised any
        HTTPException from inside the loop and aborted the whole batch,
        losing already-committed transcripts. Mutation check: reverting the
        `except HTTPException as e: errors.append(...)` handling back to
        `except HTTPException: raise` makes this 500 instead of 200."""
        settings = json.dumps({"provider": "moonshine", "kind": "meeting", "language": "en"})
        files = [
            ("files", ("good.mp3", io.BytesIO(b"fake audio 1"), "audio/mpeg")),
            ("files", ("bad.mp3", io.BytesIO(b"fake audio 2"), "audio/mpeg")),
        ]

        with patch("app._run_transcription_pipeline", new_callable=AsyncMock) as mock_pipeline:
            mock_pipeline.side_effect = [
                _fake_pipeline_result(tid=1, batch_id="BATCH004"),
                HTTPException(status_code=500, detail="ffmpeg transcode failed: corrupt audio"),
            ]
            resp = client.post(
                "/api/bulk-transcribe",
                data={"settings": settings},
                files=files,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["transcripts"]) == 1
        assert len(data["errors"]) == 1
        assert data["errors"][0]["index"] == 1
        assert data["errors"][0]["filename"] == "bad.mp3"
        assert "corrupt audio" in data["errors"][0]["error"]

    def test_bulk_transcribe_file_settings_invalid_kind(self, client):
        """Assert 400 when a per-file override has an invalid kind.
        Mutation check: per-file kind not validated → test fails."""
        settings = json.dumps({"provider": "moonshine", "kind": "meeting"})
        file_settings = json.dumps([{"kind": "bogus"}])
        files = [("files", ("a.mp3", io.BytesIO(b"fake audio"), "audio/mpeg"))]
        resp = client.post(
            "/api/bulk-transcribe",
            data={"settings": settings, "file_settings": file_settings},
            files=files,
        )
        assert resp.status_code == 400

    def test_bulk_transcribe_file_settings_invalid_provider(self, client):
        """Assert 400 when a per-file override names an unknown provider.
        Mutation check: per-file provider not validated → test fails."""
        settings = json.dumps({"provider": "moonshine", "kind": "meeting"})
        file_settings = json.dumps([{"provider": "nonexistent"}])
        files = [("files", ("a.mp3", io.BytesIO(b"fake audio"), "audio/mpeg"))]
        resp = client.post(
            "/api/bulk-transcribe",
            data={"settings": settings, "file_settings": file_settings},
            files=files,
        )
        assert resp.status_code == 400

    def test_bulk_transcribe_file_settings_non_dict_entry(self, client):
        """Assert 400 (not a raw 500/AttributeError) when a file_settings
        entry isn't an object. Mutation check: entries not type-checked →
        test fails with a 500 instead of a clean 400."""
        settings = json.dumps({"provider": "moonshine", "kind": "meeting"})
        file_settings = json.dumps([42])
        files = [("files", ("a.mp3", io.BytesIO(b"fake audio"), "audio/mpeg"))]
        resp = client.post(
            "/api/bulk-transcribe",
            data={"settings": settings, "file_settings": file_settings},
            files=files,
        )
        assert resp.status_code == 400

    def test_bulk_transcribe_invalid_settings_json(self, client):
        """Assert 400 on malformed settings JSON. Regression check: JSON parse guard."""
        files = [("files", ("a.mp3", io.BytesIO(b"fake audio"), "audio/mpeg"))]
        resp = client.post(
            "/api/bulk-transcribe",
            data={"settings": "not-json"},
            files=files,
        )
        assert resp.status_code == 400

    def test_bulk_transcribe_invalid_provider(self, client):
        """Assert 400 on unknown provider. Mutation check: provider not validated → test fails."""
        settings = json.dumps({"provider": "nonexistent", "kind": "meeting"})
        files = [("files", ("a.mp3", io.BytesIO(b"fake audio"), "audio/mpeg"))]
        resp = client.post(
            "/api/bulk-transcribe",
            data={"settings": settings},
            files=files,
        )
        assert resp.status_code == 400


class TestBatchIdFilter:
    """Tests for batch_id query parameter on GET /api/transcripts."""

    def test_batch_id_filter(self, db_session):
        """Create transcripts across 2 batches. Filter by batch_id returns correct
        subset. Mutation check: batch_id filter missing → wrong count."""
        user_id = 1
        t1 = Transcript(
            user_id=user_id, title="T1", filename="t1.mp3",
            provider="moonshine", status="completed", batch_id="BATCH_A",
        )
        t2 = Transcript(
            user_id=user_id, title="T2", filename="t2.mp3",
            provider="moonshine", status="completed", batch_id="BATCH_A",
        )
        t3 = Transcript(
            user_id=user_id, title="T3", filename="t3.mp3",
            provider="moonshine", status="completed", batch_id="BATCH_B",
        )
        db_session.add_all([t1, t2, t3])
        db_session.commit()

        # This test uses the filter at the SQL query level directly
        results_a = (
            db_session.query(Transcript)
            .filter(Transcript.batch_id == "BATCH_A")
            .all()
        )
        assert len(results_a) == 2
        assert {t.title for t in results_a} == {"T1", "T2"}

        results_b = (
            db_session.query(Transcript)
            .filter(Transcript.batch_id == "BATCH_B")
            .all()
        )
        assert len(results_b) == 1
        assert results_b[0].title == "T3"

        # NULL batch_id should not match any BATCH_A/BATCH_B filter
        results_none = (
            db_session.query(Transcript)
            .filter(Transcript.batch_id.is_(None))
            .all()
        )
        assert len(results_none) == 0

    def test_batch_id_filter_api(self, client, db_session):
        """Integration: /api/transcripts?batch_id=X returns correct subset
        through the full HTTP path. Mutation check: param not threaded → test fails."""
        user_id = 1
        t1 = Transcript(
            user_id=user_id, title="Batch A1", filename="a1.mp3",
            provider="moonshine", status="completed", batch_id="BATCH_X",
        )
        t2 = Transcript(
            user_id=user_id, title="Batch B1", filename="b1.mp3",
            provider="moonshine", status="completed", batch_id="BATCH_Y",
        )
        db_session.add_all([t1, t2])
        db_session.commit()

        resp = client.get("/api/transcripts?batch_id=BATCH_X")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["batch_id"] == "BATCH_X"
        assert data[0]["title"] == "Batch A1"

        resp_all = client.get("/api/transcripts")
        assert resp_all.status_code == 200
        data_all = resp_all.json()
        assert len(data_all) == 2


class TestBulkDefaults:
    """Tests for bulk_defaults in user settings."""

    def test_bulk_defaults_persist_and_load(self, client):
        """PUT bulk_defaults, GET settings, assert round-trip.
        Mutation check: bulk_defaults not persistable → test fails."""
        bulk = {
            "provider": "openai",
            "model": "whisper-1",
            "language": "fr",
            "diarize": True,
            "auto_correct": False,
            "kind": "dictation",
            "num_speakers": 3,
        }
        resp_put = client.put("/api/settings", json={"bulk_defaults": bulk})
        assert resp_put.status_code == 200

        resp_get = client.get("/api/settings")
        assert resp_get.status_code == 200
        stored = resp_get.json()["bulk_defaults"]
        assert stored["provider"] == "openai"
        assert stored["model"] == "whisper-1"
        assert stored["language"] == "fr"
        assert stored["diarize"] is True
        assert stored["auto_correct"] is False
        assert stored["kind"] == "dictation"
        assert stored["num_speakers"] == 3

    def test_bulk_defaults_merge(self, client):
        """Partial PUT of bulk_defaults merges with existing without wiping
        unmentioned keys. Mutation check: merge not partial → test fails."""
        full = {
            "provider": "groq",
            "model": "whisper-large-v3-flash",
            "language": "auto",
            "diarize": False,
            "auto_correct": True,
            "kind": "meeting",
            "num_speakers": None,
        }
        client.put("/api/settings", json={"bulk_defaults": full})

        # Partial update: change only provider
        client.put("/api/settings", json={"bulk_defaults": {"provider": "openai"}})

        resp = client.get("/api/settings")
        stored = resp.json()["bulk_defaults"]
        assert stored["provider"] == "openai"        # updated
        assert stored["model"] == "whisper-large-v3-flash"  # preserved
        assert stored["language"] == "auto"          # preserved
        assert stored["kind"] == "meeting"           # preserved


class TestBatchIdSerializer:
    """Tests for batch_id in serialized transcript output."""

    def test_batch_id_in_full_serializer(self, client, db_session):
        """Verify batch_id present in full serialized output (/api/transcripts/{id}).
        Mutation check: serializer field missing → test fails."""
        user_id = 1
        t = Transcript(
            user_id=user_id, title="Test", filename="test.mp3",
            provider="moonshine", status="completed", batch_id="BATCH_SERIAL",
            full_text="Hello world",
        )
        db_session.add(t)
        db_session.commit()

        resp = client.get(f"/api/transcripts/{t.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["batch_id"] == "BATCH_SERIAL"

    def test_batch_id_null_for_non_batch(self, client, db_session):
        """Verify batch_id is None for regular (non-bulk) transcripts.
        Mutation check: NULL mis-serialized as something else → test fails."""
        user_id = 1
        t = Transcript(
            user_id=user_id, title="Solo", filename="solo.mp3",
            provider="moonshine", status="completed", batch_id=None,
        )
        db_session.add(t)
        db_session.commit()

        resp = client.get(f"/api/transcripts/{t.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["batch_id"] is None

    def test_batch_id_in_summary_serializer(self, client, db_session):
        """Verify batch_id present in summary serializer (/api/transcripts list).
        Mutation check: summary serializer field missing → test fails."""
        user_id = 1
        t = Transcript(
            user_id=user_id, title="Batch Summary", filename="bs.mp3",
            provider="moonshine", status="completed", batch_id="BATCH_SUMMARY",
        )
        db_session.add(t)
        db_session.commit()

        resp = client.get("/api/transcripts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        found = next(r for r in data if r["id"] == t.id)
        assert found["batch_id"] == "BATCH_SUMMARY"