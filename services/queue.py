"""Chunk-upload job queue: rate-limit budget tracking and result reassembly.

The dispatch worker loop lives in this same module — see the bottom half
of this file (queue_worker_tick, queue_worker_loop), added alongside the
functions below.
"""
import datetime
from typing import Optional

from database import Transcript, TranscriptionJob

# Free-tier numbers confirmed live against https://console.groq.com/docs/rate-limits
# on 2026-07-01. Paid/dev tiers raise these — kept here as a dict (not a
# per-user setting) since it's provider capability, not user preference,
# but easy to adjust in code as tiers change.
PROVIDER_LIMITS = {
    "groq": {"rpm": 20, "rpd": 2000, "ash": 7200, "asd": 28800},
}
DEFAULT_LIMITS = {"rpm": 20, "rpd": 2000, "ash": 7200, "asd": 28800}


def compute_audio_seconds_used(db, user_id: int, provider: str, window_seconds: int) -> float:
    """Sum audio-seconds this user has sent to `provider` within the
    trailing `window_seconds`, combining two sources that are strict
    logical complements over parent Transcript.status, so no row is ever
    counted by both (double-count-free) and no non-NULL status value is
    ever counted by neither (undercount-free — Transcript.status defaults
    to "pending" and the app always sets it, but the column isn't
    DB-enforced NOT NULL, so a NULL status would fall outside both the
    IN and NOT IN filters at the SQL level):
      - completed/partial Transcripts (duration_seconds, updated_at) —
        counts exactly when Transcript.status IN (completed, partial).
      - TranscriptionJobs (end_time - start_time, updated_at) for jobs
        already dispatched (running or completed) whose PARENT Transcript
        counts exactly when Transcript.status NOT IN (completed, partial)
        — i.e. processing, failed, pending, or any other non-terminal
        status. This covers chunked transcripts still in flight AND
        chunked transcripts whose parent ended in a terminal-but-not-
        finalized state (e.g. failed) while still having job rows that
        reached 'completed' before the overall transcript failed — those
        job rows' audio was really sent to the provider and must still be
        counted somewhere. Once the parent transcript finalizes to
        completed/partial, its job rows stop contributing here even though
        the individual TranscriptionJob.status values remain 'completed'
        permanently — only the transcript-side sum counts it from then on.
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=window_seconds)

    transcript_total = (
        db.query(Transcript)
        .filter(
            Transcript.user_id == user_id,
            Transcript.provider == provider,
            Transcript.status.in_(["completed", "partial"]),
            Transcript.updated_at >= cutoff,
        )
        .all()
    )
    transcript_seconds = sum(t.duration_seconds or 0 for t in transcript_total)

    job_rows = (
        db.query(TranscriptionJob)
        .join(Transcript, TranscriptionJob.transcript_id == Transcript.id)
        .filter(
            Transcript.user_id == user_id,
            Transcript.provider == provider,
            Transcript.status.notin_(["completed", "partial"]),
            TranscriptionJob.status.in_(["running", "completed"]),
            TranscriptionJob.updated_at >= cutoff,
        )
        .all()
    )
    job_seconds = sum((j.end_time - j.start_time) for j in job_rows)

    return transcript_seconds + job_seconds


def has_budget(db, user_id: int, provider: str, additional_seconds: float) -> bool:
    """True if submitting a job of additional_seconds would keep this user
    under both the hourly and daily audio-second budget for provider."""
    limits = PROVIDER_LIMITS.get(provider, DEFAULT_LIMITS)
    used_hour = compute_audio_seconds_used(db, user_id, provider, 3600)
    used_day = compute_audio_seconds_used(db, user_id, provider, 86400)
    return (used_hour + additional_seconds) <= limits["ash"] and (used_day + additional_seconds) <= limits["asd"]


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _is_duplicate_boundary(prev_tail: str, next_head: str) -> bool:
    """True if next_head looks like the same text as the tail of
    prev_tail — i.e. the overlap window produced a duplicate segment at
    a chunk boundary. Anchored (prefix/suffix) rather than arbitrary
    substring containment, with a minimum length floor, so a short
    generic segment (e.g. "the") can't falsely match unrelated text."""
    if not next_head or not prev_tail:
        return False
    if next_head == prev_tail:
        return True
    MIN_MATCH_LEN = 8  # characters — below this, treat as coincidence, not overlap
    if len(next_head) < MIN_MATCH_LEN or len(prev_tail) < MIN_MATCH_LEN:
        return False
    return prev_tail.endswith(next_head) or next_head.startswith(prev_tail)


def merge_chunk_results(jobs: list) -> tuple:
    """Merge completed TranscriptionJob rows (already sorted or not) into
    one absolute-timeline segment list plus rebuilt full_text. Jobs without
    a result_json (failed chunks) are skipped — callers decide separately
    whether that makes the transcript 'completed' or 'partial'.
    """
    ordered = sorted([j for j in jobs if j.result_json], key=lambda j: j.chunk_index)
    merged_segments = []

    for job in ordered:
        raw_segments = job.result_json.get("segments", [])
        offset_segments = [
            {
                "start": s.get("start", 0) + job.start_time,
                "end": s.get("end", 0) + job.start_time,
                "text": s.get("text", ""),
                "speaker": s.get("speaker"),
                "confidence": s.get("confidence"),
            }
            for s in raw_segments
        ]

        if merged_segments and offset_segments:
            prev_tail = _normalize(merged_segments[-1]["text"])
            next_head = _normalize(offset_segments[0]["text"])
            if _is_duplicate_boundary(prev_tail, next_head):
                offset_segments = offset_segments[1:]

        merged_segments.extend(offset_segments)

    full_text = " ".join(s["text"].strip() for s in merged_segments if s["text"].strip())
    return merged_segments, full_text
