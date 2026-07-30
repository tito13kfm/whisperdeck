"""Tests for batch management API (issue #232).
Backend-only: GET /api/batches, GET /api/batches/{batch_id},
POST /api/batches/{batch_id}/cancel."""
from database import Transcript, TranscriptionJob, utcnow_naive


def _transcript(user_id=1, title="Test", filename="test.mp3",
                provider="moonshine", status="completed", batch_id=None,
                duration_seconds=10.0, **kwargs):
    return Transcript(
        user_id=user_id, title=title, filename=filename,
        provider=provider, status=status, batch_id=batch_id,
        duration_seconds=duration_seconds, **kwargs,
    )


class TestListBatches:
    """Tests for GET /api/batches."""

    def test_list_batches(self, client, db_session):
        """Create transcripts in 2 batches. Assert GET /api/batches returns
        both with correct aggregate counts. Null batch_id transcripts excluded.
        Mutation check: aggregate SQL missing → wrong counts."""
        t1 = _transcript(batch_id="BATCH_A", status="completed", duration_seconds=30.0)
        t2 = _transcript(batch_id="BATCH_A", status="failed", duration_seconds=20.0)
        t3 = _transcript(batch_id="BATCH_B", status="completed", duration_seconds=10.0)
        t4 = _transcript(batch_id="BATCH_B", status="pending", duration_seconds=5.0)
        t5 = _transcript(batch_id=None, status="completed")  # excluded
        db_session.add_all([t1, t2, t3, t4, t5])
        db_session.commit()

        resp = client.get("/api/batches")
        assert resp.status_code == 200
        data = resp.json()
        assert "batches" in data
        batches = data["batches"]
        assert len(batches) == 2

        # Newest first (by created_at). Both batches have same created_at
        # (default=utcnow_naive), so order is not deterministic. Sort for assertions.
        batches.sort(key=lambda b: b["batch_id"])

        b_a = batches[0]  # BATCH_A
        assert b_a["batch_id"] == "BATCH_A"
        assert b_a["total"] == 2
        assert b_a["completed"] == 1
        assert b_a["failed"] == 1
        assert b_a["pending"] == 0
        assert b_a["total_duration_seconds"] == 50.0
        assert b_a["first_title"] in ("Test", None)

        b_b = batches[1]  # BATCH_B
        assert b_b["batch_id"] == "BATCH_B"
        assert b_b["total"] == 2
        assert b_b["completed"] == 1
        assert b_b["pending"] == 1
        assert b_b["total_duration_seconds"] == 15.0

    def test_list_batches_empty(self, client, db_session):
        """No transcripts with batch_id set. Assert empty list.
        Mutation check: null guard missing → null batches appear."""
        t = _transcript(batch_id=None, status="completed")
        db_session.add(t)
        db_session.commit()

        resp = client.get("/api/batches")
        assert resp.status_code == 200
        data = resp.json()
        assert data["batches"] == []

    def test_list_batches_limit_offset(self, client, db_session):
        """Verify limit and offset query params work."""
        for i in range(5):
            db_session.add(_transcript(
                batch_id=f"BATCH_{i:03d}", status="completed",
                title=f"T{i}", filename=f"t{i}.mp3",
            ))
        db_session.commit()

        # limit=2
        resp = client.get("/api/batches?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()["batches"]) == 2

        # offset=2
        resp = client.get("/api/batches?offset=2&limit=10")
        assert resp.status_code == 200
        assert len(resp.json()["batches"]) == 3


class TestGetBatch:
    """Tests for GET /api/batches/{batch_id}."""

    def test_get_batch_detail(self, client, db_session):
        """Create 2 transcripts in a batch. Assert detail endpoint returns both
        with correct aggregate stats. Mutation check: serializer missing → empty."""
        t1 = _transcript(batch_id="BATCH_DETAIL", status="completed",
                         title="First", filename="first.mp3",
                         duration_seconds=60.0, full_text="Hello")
        t2 = _transcript(batch_id="BATCH_DETAIL", status="pending",
                         title="Second", filename="second.mp3",
                         duration_seconds=30.0, full_text="World")
        db_session.add_all([t1, t2])
        db_session.commit()

        resp = client.get("/api/batches/BATCH_DETAIL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["batch_id"] == "BATCH_DETAIL"
        assert data["total"] == 2
        assert data["total_duration_seconds"] == 90.0
        assert data["status_counts"]["completed"] == 1
        assert data["status_counts"]["pending"] == 1
        assert len(data["transcripts"]) == 2
        # Transcripts ordered by id
        assert data["transcripts"][0]["title"] == "First"
        assert data["transcripts"][1]["title"] == "Second"
        # Full _serialize_transcript fields present
        for tdata in data["transcripts"]:
            assert "id" in tdata
            assert "batch_id" in tdata
            assert tdata["batch_id"] == "BATCH_DETAIL"

    def test_get_batch_not_found(self, client):
        """Non-existent batch_id returns 404.
        Mutation check: 404 guard missing → 200 with empty response."""
        resp = client.get("/api/batches/NONEXISTENT")
        assert resp.status_code == 404

    def test_get_batch_other_user_excluded(self, client, db_session):
        """Batch with only other user's transcripts returns 404."""
        t = _transcript(user_id=999, batch_id="OTHER_USER_BATCH", status="completed")
        db_session.add(t)
        db_session.commit()

        resp = client.get("/api/batches/OTHER_USER_BATCH")
        assert resp.status_code == 404


class TestCancelBatch:
    """Tests for POST /api/batches/{batch_id}/cancel."""

    def test_cancel_batch_all_pending(self, client, db_session):
        """Create batch with 3 pending transcripts. Cancel. Assert all
        become 'cancelled'. Mutation check: cancel_not_called → status unchanged."""
        t1 = _transcript(batch_id="BATCH_CANCEL", status="pending")
        t2 = _transcript(batch_id="BATCH_CANCEL", status="pending")
        t3 = _transcript(batch_id="BATCH_CANCEL", status="pending")
        db_session.add_all([t1, t2, t3])
        db_session.commit()

        resp = client.post("/api/batches/BATCH_CANCEL/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["batch_id"] == "BATCH_CANCEL"
        assert data["cancelled"] == 3
        assert data["already_terminal"] == 0

        # Verify statuses in DB
        db_session.expire_all()
        for t in [t1, t2, t3]:
            db_session.refresh(t)
            assert t.status == "cancelled"

    def test_cancel_batch_mixed_statuses(self, client, db_session):
        """Batch with pending, processing, and completed transcripts.
        Assert only active (pending+processing) get cancelled, already-terminal
        are counted but not changed. Mutation check: terminal overwritten → lost data."""
        t1 = _transcript(batch_id="BATCH_MIXED", status="pending")
        t2 = _transcript(batch_id="BATCH_MIXED", status="processing")
        t3 = _transcript(batch_id="BATCH_MIXED", status="completed")
        t4 = _transcript(batch_id="BATCH_MIXED", status="failed")
        db_session.add_all([t1, t2, t3, t4])
        db_session.commit()

        resp = client.post("/api/batches/BATCH_MIXED/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] == 2
        assert data["already_terminal"] == 2

        db_session.expire_all()
        db_session.refresh(t1)
        assert t1.status == "cancelled"
        db_session.refresh(t2)
        assert t2.status == "cancelled"
        db_session.refresh(t3)
        assert t3.status == "completed"   # unchanged
        db_session.refresh(t4)
        assert t4.status == "failed"      # unchanged

    def test_cancel_batch_not_found(self, client):
        """Non-existent batch_id returns 404.
        Mutation check: 404 guard missing → 200 with zeros."""
        resp = client.post("/api/batches/NONEXISTENT/cancel")
        assert resp.status_code == 404

    def test_cancel_batch_idempotent(self, client, db_session):
        """Cancel a batch twice. Second cancel should report all
        already-terminal (no double-counting)."""
        t = _transcript(batch_id="BATCH_IDEM", status="pending")
        db_session.add(t)
        db_session.commit()

        # First cancel
        resp1 = client.post("/api/batches/BATCH_IDEM/cancel")
        assert resp1.status_code == 200
        assert resp1.json()["cancelled"] == 1

        # Second cancel — now already "cancelled" (terminal)
        resp2 = client.post("/api/batches/BATCH_IDEM/cancel")
        assert resp2.status_code == 200
        assert resp2.json()["cancelled"] == 0
        assert resp2.json()["already_terminal"] == 1

    def test_cancel_batch_no_active(self, client, db_session):
        """Cancel batch where all transcripts are already terminal.
        Assert cancelled=0, already_terminal=all."""
        t1 = _transcript(batch_id="BATCH_DONE", status="completed")
        t2 = _transcript(batch_id="BATCH_DONE", status="failed")
        db_session.add_all([t1, t2])
        db_session.commit()

        resp = client.post("/api/batches/BATCH_DONE/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] == 0
        assert data["already_terminal"] == 2
