"""Chunk-upload job queue: rate-limit budget tracking and result reassembly.

The dispatch worker loop lives in this same module — see the bottom half
of this file (queue_worker_tick, queue_worker_loop), added alongside the
functions below.
"""
import asyncio
import datetime
from typing import Optional

from database import Transcript, TranscriptionJob
from backends import get_provider, ProviderError
from database import ProviderConfig

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


MAX_ATTEMPTS = 3


def create_chunk_jobs(db, transcript_id: int, chunks: list) -> None:
    """Insert one pending TranscriptionJob per chunk dict (as returned by
    services.audio_prep.chunk_audio)."""
    for chunk in chunks:
        db.add(TranscriptionJob(
            transcript_id=transcript_id,
            chunk_index=chunk["index"],
            start_time=chunk["start_time"],
            end_time=chunk["end_time"],
            audio_path=chunk["path"],
        ))
    db.commit()


def retry_failed_chunks(db, transcript_id: int) -> int:
    """Reset every permanently-failed job for this transcript back to
    pending so the worker picks it up again. Returns how many were reset."""
    failed = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "failed")
        .all()
    )
    for job in failed:
        job.status = "pending"
        job.attempts = 0
        job.error = None
    if failed:
        transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
        if transcript:
            transcript.status = "processing"
        db.commit()
    return len(failed)


def _retry_eligible(job) -> bool:
    if job.attempts >= MAX_ATTEMPTS:
        return False
    backoff = min(60, 5 * (2 ** job.attempts))
    elapsed = (datetime.datetime.utcnow() - job.updated_at).total_seconds()
    return elapsed >= backoff


# SAFETY INVARIANT: this coroutine runs concurrently with sibling
# _run_chunk_job calls via asyncio.gather, all sharing ONE db session
# (see queue_worker_tick). This is only safe because every mutation here
# is committed BEFORE the one await point (the provider call) — so at
# every point asyncio could switch between concurrent jobs, the session
# has no other job's uncommitted dirty state. If you add a second
# mutation after the await, or move the commit, you MUST commit before
# any await or use a separate session per job instead.
async def _run_chunk_job(db, job, provider_config: dict, provider_name: str, language: str) -> None:
    job.status = "running"
    job.attempts += 1
    db.commit()
    try:
        provider = get_provider(provider_name, provider_config)
        result = await provider.transcribe(job.audio_path, language=language, temperature=0.0)
        job.result_json = {
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text, "speaker": s.speaker, "confidence": s.confidence}
                for s in result.segments
            ],
            "full_text": result.full_text,
            "language": result.language,
            "model": result.model,
        }
        job.status = "completed"
        job.error = None
    except (ProviderError, Exception) as e:
        # Always land on "failed", never straight back to "pending" — the
        # tick's own _retry_eligible + backoff pass (below) is what
        # resurrects a job to "pending" once its backoff window has
        # elapsed. Setting "pending" here directly would skip that check
        # and let a job that fails immediately get redispatched on the
        # very next tick (~5s later), hammering the provider on repeated
        # failures instead of backing off. Once attempts reaches
        # MAX_ATTEMPTS, _retry_eligible permanently refuses to resurrect
        # it — that's what makes "failed" terminal.
        job.status = "failed"
        job.error = str(e)
    db.commit()


async def _finalize_if_done(db, transcript_id: int, diarization_service) -> None:
    jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == transcript_id).all()
    if not jobs or any(j.status in ("pending", "running") for j in jobs):
        return  # still work outstanding

    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        return

    segments, full_text = merge_chunk_results(jobs)
    transcript.segments = segments
    transcript.full_text = full_text
    transcript.duration_seconds = max((j.end_time for j in jobs), default=0)
    completed_count = sum(1 for j in jobs if j.status == "completed")
    failed_count = sum(1 for j in jobs if j.status == "failed")
    if failed_count == 0:
        transcript.status = "completed"
    elif completed_count == 0:
        transcript.status = "failed"
    else:
        transcript.status = "partial"
    transcript.updated_at = datetime.datetime.utcnow()

    if transcript.diarize_requested and segments and transcript.audio_path:
        try:
            if diarization_service._check_pyannote():
                # num_speakers=None lets pyannote auto-detect the count.
                result = await diarization_service.diarize_pyannote(
                    transcript.audio_path, num_speakers=transcript.num_speakers
                )
            else:
                # Heuristic fallback can't auto-detect — needs a real
                # count, default to 2 if the user left it blank.
                result = await diarization_service.diarize_heuristic(
                    transcript.audio_path, num_speakers=transcript.num_speakers or 2, segments=segments,
                )
            merged = await diarization_service.combine_with_transcript(result, segments)
            transcript.segments = merged
            transcript.speaker_count = result.speaker_count
        except Exception as e:
            print(f"[queue] non-fatal diarization failure for transcript {transcript_id}: {e}")

    db.commit()


async def queue_worker_tick(SessionLocal, diarization_service) -> None:
    """One pass: retry-eligible failed jobs become pending, then dispatch
    pending jobs (grouped by user+provider) up to that user's concurrency
    setting, skipping any dispatch that would exceed rate-limit budget."""
    db = SessionLocal()
    try:
        from services.settings import get_user_settings  # local import avoids a module-load cycle with app.py

        pending_or_retry = (
            db.query(TranscriptionJob)
            .filter(TranscriptionJob.status.in_(["pending", "failed"]))
            .all()
        )
        for job in pending_or_retry:
            if job.status == "failed" and _retry_eligible(job):
                job.status = "pending"
                job.error = None
        db.commit()

        pending = db.query(TranscriptionJob).filter(TranscriptionJob.status == "pending").all()
        by_transcript = {}
        for job in pending:
            by_transcript.setdefault(job.transcript_id, []).append(job)

        for transcript_id, jobs in by_transcript.items():
            transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
            if not transcript:
                continue
            settings = get_user_settings(db, transcript.user_id)
            concurrency_cap = settings["max_concurrent_chunks"]

            already_running = (
                db.query(TranscriptionJob)
                .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "running")
                .count()
            )
            slots = max(0, concurrency_cap - already_running)
            if slots == 0:
                continue

            prov_cfg = (
                db.query(ProviderConfig)
                .filter(ProviderConfig.user_id == transcript.user_id, ProviderConfig.name == transcript.provider)
                .first()
            )
            provider_config = {
                "api_key": prov_cfg.api_key if prov_cfg else "",
                "api_url": prov_cfg.api_url if prov_cfg else "",
                "default_model": (prov_cfg.default_model if prov_cfg else "") or transcript.model,
            }

            jobs.sort(key=lambda j: j.chunk_index)
            dispatched = []
            for job in jobs[:slots]:
                job_duration = job.end_time - job.start_time
                if not has_budget(db, transcript.user_id, transcript.provider, job_duration):
                    break  # over budget — leave remaining jobs pending for a later tick
                dispatched.append(job)

            if dispatched:
                # All dispatched jobs share the single `db` session opened at the top of
                # this tick — safe only because _run_chunk_job commits before its await
                # point (see the safety invariant comment on _run_chunk_job itself).
                await asyncio.gather(*[
                    _run_chunk_job(db, job, provider_config, transcript.provider, transcript.language)
                    for job in dispatched
                ])

            await _finalize_if_done(db, transcript_id, diarization_service)
    finally:
        db.close()


async def queue_worker_loop(SessionLocal, diarization_service, interval_seconds: float = 5.0) -> None:
    """Runs forever (until cancelled) — call via asyncio.create_task from
    app.py's lifespan startup."""
    while True:
        try:
            await queue_worker_tick(SessionLocal, diarization_service)
        except Exception as e:
            print(f"[queue] worker tick failed: {e}")
        await asyncio.sleep(interval_seconds)
