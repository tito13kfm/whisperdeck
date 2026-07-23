"""Regression coverage for issue #66: QueuePool exhaustion during active
use (tagging + seeking). init_db() must configure a pool with enough
headroom for the background workers plus bursty HTTP traffic, and WAL
mode + an explicit busy_timeout so connections queue briefly under
contention instead of erroring out."""
from database import init_db


def test_wal_and_busy_timeout_enabled(tmp_path):
    engine, _, _ = init_db(str(tmp_path / "test.db"))
    try:
        with engine.connect() as conn:
            journal_mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
            busy_timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
        assert journal_mode.lower() == "wal"
        assert busy_timeout == 5000
    finally:
        engine.dispose()


def test_pool_sized_for_background_plus_burst(tmp_path):
    engine, _, _ = init_db(str(tmp_path / "test.db"))
    try:
        assert engine.pool.size() == 10
        assert engine.pool._max_overflow == 20
    finally:
        engine.dispose()


def test_concurrent_checkout_does_not_exhaust_pool(tmp_path):
    """Simulates the issue's burst pattern: more simultaneous connection
    checkouts than the old default of 15 (pool_size=5 + max_overflow=10)
    must succeed without a QueuePool timeout."""
    engine, _, _ = init_db(str(tmp_path / "test.db"))
    try:
        held = [engine.connect() for _ in range(20)]
        try:
            for conn in held:
                conn.exec_driver_sql("SELECT 1")
        finally:
            for conn in held:
                conn.close()
    finally:
        engine.dispose()
