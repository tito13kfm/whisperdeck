"""Shared compare-and-set primitive for job status transitions.

Both LlmJob and TranscriptionJob use the same status column pattern
(id, status, updated_at) and face the same race: a worker claims or
finishes, a cancel or retry sweep moves the same row, and any two can
be in flight against the same row at once. Every writer must use this
CAS form rather than read-then-write.

Extracted from services/llm_jobs.py:_transition (PR #389) so
services/queue.py can share the same primitive — this is the port
issue #402 calls for.
"""

from database import utcnow_naive


def transition(db, model_cls, job_id: int, new_status: str, *, expect: tuple[str, ...], **fields) -> bool:
    """Compare-and-set on ``model_cls.status``. True when this call made the move.

    Emits a single ``UPDATE {table} SET status=?, updated_at=?, ...fields
    WHERE id=? AND status IN (expect)``, atomic against a concurrent writer:
    it takes sqlite's write lock, so either it matches and the other writer
    serializes after our commit, or the other writer already committed and
    the row no longer matches ``expect`` — then this returns False with
    nothing written.

    Autoflush means the caller's own pending writes flush into this same
    transaction, so a caller that rolls back on False rolls those back too.
    """
    if "updated_at" in fields:
        values = {"status": new_status, **fields}
    else:
        values = {"status": new_status, "updated_at": utcnow_naive(), **fields}
    return bool(
        db.query(model_cls)
        .filter(model_cls.id == job_id, model_cls.status.in_(expect))
        .update(values, synchronize_session=False)
    )
