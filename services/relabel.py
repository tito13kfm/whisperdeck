"""Inverse-patch recording for bulk speaker relabels, powering undo.

Call record_relabel in the same transaction as the relabel itself, BEFORE
the commit, so the history entry and the new labels land (or roll back)
together."""
from database import RelabelHistory

MAX_HISTORY = 20

# combine_with_transcript() stamps this literal onto any transcript segment
# that overlapped no diarization turn (services/diarization.py), so it is a
# real value inside persisted segments. It means "nobody attributed", not a
# participant, and the diarization services compute their own speaker_count
# from the diarization turns BEFORE that fallback is applied. Counting it as
# a speaker here would report a higher number than the diarize path ever
# would for the same segment list.
NON_SPEAKER_LABELS = frozenset({"unknown"})


def count_distinct_speakers(segments) -> int:
    """Distinct real speaker labels in a persisted transcript.segments list.

    The single definition for every path that rewrites segments without
    re-running diarization (voice match, speaker rename, segment retag,
    relabel-undo, the segments PATCH). Those paths no longer hold the
    diarization turns that services/diarization.py counts from, so they have
    to recount from the stored labels; keeping that in one place is what
    stops the five of them from answering the "Unknown" question differently.
    """
    return len({
        name
        for seg in (segments or [])
        if (name := (seg.get("speaker") or "").strip())
        and name.casefold() not in NON_SPEAKER_LABELS
    })


def record_relabel(db, transcript, kind: str, changed: list[tuple[int, str]],
                   corrected_text_before: str | None = None, description: str = "") -> RelabelHistory | None:
    """changed: [(segment_index, old_speaker), ...] for every segment the
    action rewrote. corrected_text_before: full before-image when the action
    also rewrites corrected_text (renames); None otherwise. Renames are not
    invertible by reverse transform (renaming A to an already-present B
    merges them), hence the before-image.

    Returns the new entry so callers that also rewrite corrected_text can
    stamp the after-image into inverse["corrected_text_after"] once the
    rewrite is done — relabel-undo restores the before-image only while
    corrected_text still matches that after-image (a correction re-run in
    between must not be clobbered by a stale snapshot)."""
    if not changed:
        return None
    entry = RelabelHistory(
        transcript_id=transcript.id,
        kind=kind,
        inverse={
            "segments": [{"index": i, "speaker": old} for i, old in changed],
            "corrected_text": corrected_text_before,
        },
        description=description[:255],
    )
    db.add(entry)
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
    return entry


def latest_relabel(db, transcript_id: int) -> RelabelHistory | None:
    """Newest history entry — the one relabel-undo would pop. The single
    definition keeps the undo button's preview (serializer) and the entry
    the undo endpoint actually applies from ever drifting apart."""
    return (
        db.query(RelabelHistory)
        .filter(RelabelHistory.transcript_id == transcript_id)
        .order_by(RelabelHistory.id.desc())
        .first()
    )


def clear_relabel_history(db, transcript_id: int) -> None:
    """Drop all history for a transcript. Must be called by anything that
    regenerates transcript.segments wholesale (rediarize, queue finalize):
    the inverse patches are index-based snapshots of the segmentation they
    were recorded against, and applying one to a rebuilt segment list would
    stamp stale labels onto unrelated lines."""
    db.query(RelabelHistory).filter(
        RelabelHistory.transcript_id == transcript_id
    ).delete(synchronize_session=False)
