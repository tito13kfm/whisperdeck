"""Migration for issue #267 (design decision 7): every transcript that
existed before classification_provenance was added becomes a permanent
manual-override record on first upgrade, and — critically — a transcript
created AFTER that point with a still-null provenance must never be
relabeled as legacy on a later restart (see database.backfill_legacy_classification
for why column absence, not a null check, is what triggers the backfill)."""
from sqlalchemy import create_engine, text

from database import init_db, Transcript, User, LlmJob


def test_backfill_legacy_classification_marks_preexisting_rows_as_override(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine, SessionLocal, _ = init_db(str(db_path))
    db = SessionLocal()
    user = User(username="legacyu", password_hash="x", password_salt="y")
    db.add(user)
    db.commit()
    t1 = Transcript(user_id=user.id, title="t1", filename="f1.mp3", kind="meeting")
    db.add(t1)
    db.add(Transcript(user_id=user.id, title="t2", filename="f2.mp3", kind="dictation"))
    db.commit()
    # A completed correction job makes backfill_llm_job_result_snapshots'
    # ORM query over Transcript non-empty — the discriminating case: if the
    # classification columns aren't added until AFTER that snapshot backfill
    # runs, this query 500s with "no such column: transcripts.classification_status"
    # on every real upgraded install (which always has completed jobs).
    db.add(LlmJob(user_id=user.id, transcript_id=t1.id, kind="correction", status="completed", provider="groq", model="m1"))
    db.commit()
    db.close()
    engine.dispose()

    # Simulate a pre-#267 database: drop the columns this migration adds
    # (SQLite 3.35+ supports DROP COLUMN) so the next init_db() sees a
    # genuinely old schema, exactly like an existing production install.
    raw_engine = create_engine(f"sqlite:///{db_path}")
    with raw_engine.connect() as conn:
        conn.execute(text("ALTER TABLE transcripts DROP COLUMN classification_status"))
        conn.execute(text("ALTER TABLE transcripts DROP COLUMN classification_confidence"))
        conn.execute(text("ALTER TABLE transcripts DROP COLUMN classification_provenance"))
        conn.commit()
    raw_engine.dispose()

    # Re-run init_db on the same file, as every server restart does.
    engine2, SessionLocal2, _ = init_db(str(db_path))
    db2 = SessionLocal2()
    try:
        rows = db2.query(Transcript).order_by(Transcript.id).all()
        assert len(rows) == 2
        for row in rows:
            assert row.classification_status == "override"
            assert row.classification_confidence is None
            assert row.classification_provenance == {"legacy_migration": True}
    finally:
        db2.close()
        engine2.dispose()


def test_backfill_legacy_classification_does_not_relabel_post_migration_rows(tmp_path):
    """A transcript created after the column already exists (e.g. a future
    #268 'auto'-kind transcript still awaiting classification) has a null
    provenance for a legitimate reason — a later restart must leave it
    exactly as pending, not stamp it as legacy."""
    db_path = tmp_path / "fresh.db"
    engine, SessionLocal, _ = init_db(str(db_path))
    db = SessionLocal()
    user = User(username="freshu", password_hash="x", password_salt="y")
    db.add(user)
    db.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.mp3")
    t.classification_status = "pending"
    t.classification_provenance = None
    db.add(t)
    db.commit()
    t_id = t.id
    db.close()
    engine.dispose()

    # Restart the server (re-run init_db on the same file).
    engine2, SessionLocal2, _ = init_db(str(db_path))
    db2 = SessionLocal2()
    try:
        refreshed = db2.query(Transcript).filter(Transcript.id == t_id).first()
        assert refreshed.classification_status == "pending"
        assert refreshed.classification_provenance is None
    finally:
        db2.close()
        engine2.dispose()
