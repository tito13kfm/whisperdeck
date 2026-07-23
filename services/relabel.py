"""Inverse-patch recording for bulk speaker relabels, powering undo.

Call record_relabel in the same transaction as the relabel itself, BEFORE
the commit, so the history entry and the new labels land (or roll back)
together."""
from database import RelabelHistory

MAX_HISTORY = 20


def record_relabel(db, transcript, kind: str, changed: list[tuple[int, str]],
                   corrected_text_before: str | None = None, description: str = "") -> None:
    """changed: [(segment_index, old_speaker), ...] for every segment the
    action rewrote. corrected_text_before: full before-image when the action
    also rewrites corrected_text (renames); None otherwise. Renames are not
    invertible by reverse transform (renaming A to an already-present B
    merges them), hence the before-image."""
    if not changed:
        return
    db.add(RelabelHistory(
        transcript_id=transcript.id,
        kind=kind,
        inverse={
            "segments": [{"index": i, "speaker": old} for i, old in changed],
            "corrected_text": corrected_text_before,
        },
        description=description[:255],
    ))
    stale = (
        db.query(RelabelHistory.id)
        .filter(RelabelHistory.transcript_id == transcript.id)
        .order_by(RelabelHistory.id.desc())
        # Default SQLAlchemy autoflush means the db.query() below flushes the
        # pending add() above before executing, so the row just added is
        # already counted here -- offset by the full MAX_HISTORY, not -1.
        .offset(MAX_HISTORY)
        .all()
    )
    stale_ids = [row_id for (row_id,) in stale]
    if stale_ids:
        db.query(RelabelHistory).filter(RelabelHistory.id.in_(stale_ids)).delete(
            synchronize_session=False
        )
