"""Probe: does a trigger-issued 'delete' for a never-indexed rowid corrupt the
FTS5 index, and does an immediately-following insert (the UPDATE trigger's
shape) mask it?

Run against the real schema via init_db so nothing is synthetic.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\Claude\WhisperDeck\.claude\worktrees\issue-309-fts-delete-trigger")

from sqlalchemy import text  # noqa: E402
from database import init_db, User, Transcript  # noqa: E402

SEG = ("COALESCE((SELECT group_concat(json_extract(value,'$.text'),' ') "
       "FROM json_each({r}.segments)), '')")


def integrity(conn, label):
    try:
        conn.execute(text("INSERT INTO transcripts_fts(transcripts_fts, rank) "
                          "VALUES('integrity-check', 1)"))
        print(f"  {label}: integrity-check OK")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  {label}: integrity-check FAILED -> {type(e).__name__}: "
              f"{str(e).splitlines()[0]}")
        return False


def make(db, title, full_text, segments=None):
    t = Transcript(user_id=1, title=title, filename="f.mp3", status="completed",
                   full_text=full_text, segments=segments or [])
    db.add(t)
    db.commit()
    return t


def scenario(name, body):
    tmp = Path(tempfile.mkdtemp(prefix="probe-"))
    engine, SessionLocal, _ = init_db(str(tmp / "p.db"))
    db = SessionLocal()
    db.add(User(username="alice", password_hash="x", password_salt="y"))
    db.commit()
    print(f"\n=== {name} ===")
    try:
        body(engine, db)
    finally:
        db.close()
        engine.dispose()


def a_delete_only(engine, db):
    """Unindexed row, DELETE only (no following insert)."""
    keeper = make(db, "keeper", "alpha unique")
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_insert"))
    ghost = make(db, "ghost", "beta unique")
    with engine.connect() as conn:
        print("  docsize before:", [r[0] for r in conn.execute(
            text("SELECT id FROM transcripts_fts_docsize ORDER BY id"))])
        integrity(conn, "before delete")
    db.delete(ghost)
    db.commit()
    with engine.connect() as conn:
        print("  docsize after :", [r[0] for r in conn.execute(
            text("SELECT id FROM transcripts_fts_docsize ORDER BY id"))])
        integrity(conn, "after delete of never-indexed row")
    print("  keeper id:", keeper.id)


def b_update_delete_then_insert(engine, db):
    """Unindexed row, UPDATE (existing trigger: 'delete' then insert)."""
    make(db, "keeper", "alpha unique")
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_insert"))
    ghost = make(db, "ghost", "beta unique")
    with engine.connect() as conn:
        integrity(conn, "before update")
    ghost.full_text = "gamma unique"
    db.commit()
    with engine.connect() as conn:
        print("  docsize after :", [r[0] for r in conn.execute(
            text("SELECT id FROM transcripts_fts_docsize ORDER BY id"))])
        integrity(conn, "after update of never-indexed row")


def c_guarded_delete(engine, db):
    """Unindexed row, DELETE with a docsize membership guard on the trigger."""
    make(db, "keeper", "alpha unique")
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_delete"))
        conn.execute(text(
            "CREATE TRIGGER trg_transcripts_fts_delete "
            "AFTER DELETE ON transcripts "
            "WHEN EXISTS (SELECT 1 FROM transcripts_fts_docsize WHERE id = OLD.id) "
            "BEGIN "
            "INSERT INTO transcripts_fts(transcripts_fts, rowid, title, full_text, "
            "corrected_text, segment_text) "
            "VALUES('delete', OLD.id, OLD.title, OLD.full_text, OLD.corrected_text, "
            + SEG.format(r="OLD") + "); END"
        ))
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_insert"))
    ghost = make(db, "ghost", "beta unique")
    db.delete(ghost)
    db.commit()
    with engine.connect() as conn:
        print("  docsize after ghost delete:", [r[0] for r in conn.execute(
            text("SELECT id FROM transcripts_fts_docsize ORDER BY id"))])
        integrity(conn, "after guarded delete of never-indexed row")


def d_guarded_delete_indexed(engine, db):
    """Guarded trigger still cleans an indexed row."""
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_transcripts_fts_delete"))
        conn.execute(text(
            "CREATE TRIGGER trg_transcripts_fts_delete "
            "AFTER DELETE ON transcripts "
            "WHEN EXISTS (SELECT 1 FROM transcripts_fts_docsize WHERE id = OLD.id) "
            "BEGIN "
            "INSERT INTO transcripts_fts(transcripts_fts, rowid, title, full_text, "
            "corrected_text, segment_text) "
            "VALUES('delete', OLD.id, OLD.title, OLD.full_text, OLD.corrected_text, "
            + SEG.format(r="OLD") + "); END"
        ))
    keeper = make(db, "keeper", "alpha unique")
    doomed = make(db, "doomed", "beta unique",
                  segments=[{"speaker": "A", "text": "segterm", "start": 0, "end": 1}])
    db.delete(doomed)
    db.commit()
    with engine.connect() as conn:
        print("  docsize after :", [r[0] for r in conn.execute(
            text("SELECT id FROM transcripts_fts_docsize ORDER BY id"))],
            "(keeper is", keeper.id, ")")
        for term in ("beta", "segterm", "alpha"):
            ids = [r[0] for r in conn.execute(
                text("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH :q"),
                {"q": f'"{term}"'})]
            print(f"  MATCH {term}: {ids}")
        integrity(conn, "after guarded delete of indexed row")


scenario("A: unguarded delete of never-indexed row", a_delete_only)
scenario("B: update of never-indexed row (existing trigger)", b_update_delete_then_insert)
scenario("C: guarded delete of never-indexed row", c_guarded_delete)
scenario("D: guarded delete of indexed row", d_guarded_delete_indexed)
