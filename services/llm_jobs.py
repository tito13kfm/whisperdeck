"""Background LLM jobs — correction and summary runs against transcripts.

Mirrors the transcription queue's shape (pending rows claimed by a worker
loop, commit-before-await) but runs in its own loop so a minutes-long
correction can't starve chunk dispatch. Each job executes in its own DB
session; the only cross-session signal is the status column (cancel flips
it, the runner re-reads it between batches).
"""
import asyncio
import datetime

from database import LlmJob, Transcript

ACTIVE_STATUSES = ("pending", "running")
_MAX_CONCURRENT_JOBS = 2

VALID_KINDS = ("correction", "summary", "rediarize")


def serialize_llm_job(job: LlmJob) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "transcript_id": job.transcript_id,
        "status": job.status,
        "progress": {"done": job.progress_done or 0, "total": job.progress_total or 0},
        "provider": job.provider,
        "model": job.model,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def get_active_job(db, transcript_id: int, kind: str) -> LlmJob | None:
    return (
        db.query(LlmJob)
        .filter(
            LlmJob.transcript_id == transcript_id,
            LlmJob.kind == kind,
            LlmJob.status.in_(ACTIVE_STATUSES),
        )
        .first()
    )


def latest_job(db, transcript_id: int, kind: str) -> LlmJob | None:
    return (
        db.query(LlmJob)
        .filter(LlmJob.transcript_id == transcript_id, LlmJob.kind == kind)
        .order_by(LlmJob.id.desc())
        .first()
    )


def enqueue_llm_job(db, user_id: int, transcript_id: int, kind: str,
                    provider: str, model: str, error: str | None = None) -> LlmJob:
    """One active job per transcript+kind — returns the existing one instead
    of stacking duplicates. `error` pre-fails the job (e.g. 'no key saved')
    so the skip is visible and rerunnable in the queue."""
    if kind not in VALID_KINDS:
        raise ValueError(f"Unknown LLM job kind: {kind}")
    existing = get_active_job(db, transcript_id, kind)
    if existing:
        return existing
    job = LlmJob(
        user_id=user_id, transcript_id=transcript_id, kind=kind,
        provider=provider, model=model,
        status="failed" if error else "pending", error=error,
    )
    db.add(job)
    db.commit()
    return job


def enqueue_auto_correction(db, transcript, user_settings: dict) -> LlmJob:
    """Auto-correct entry point for the inline and chunked-finalize paths.
    Keyless providers fail the job immediately with the skip reason (also
    recorded on the transcript so the corrected tab explains itself)."""
    from services.settings import resolve_provider_key

    provider = user_settings.get("correction_provider", "groq")
    model = user_settings.get("correction_model", "llama-3.3-70b-versatile")
    api_key, _ = resolve_provider_key(db, transcript.user_id, provider)
    error = None
    if provider != "local" and not api_key:
        error = f"auto-correct skipped: no {provider} API key saved (see service panel)"
        transcript.correction_error = error
        db.commit()
    return enqueue_llm_job(db, transcript.user_id, transcript.id, "correction", provider, model, error=error)


def cancel_llm_job(db, user_id: int, job_id: int) -> LlmJob:
    job = db.query(LlmJob).filter(LlmJob.id == job_id, LlmJob.user_id == user_id).first()
    if not job:
        raise LookupError("Job not found")
    if job.status not in ACTIVE_STATUSES:
        raise ValueError(f"Cannot cancel a job with status '{job.status}'")
    # pending dies instantly; a running correction notices between batches.
    job.status = "cancelled"
    job.updated_at = datetime.datetime.utcnow()
    db.commit()
    return job


def rerun_llm_job(db, user_id: int, job_id: int) -> LlmJob:
    job = db.query(LlmJob).filter(LlmJob.id == job_id, LlmJob.user_id == user_id).first()
    if not job:
        raise LookupError("Job not found")
    if job.status not in ("failed", "cancelled"):
        raise ValueError(f"Can only rerun failed or cancelled jobs (this one is '{job.status}')")
    return enqueue_llm_job(db, user_id, job.transcript_id, job.kind, job.provider, job.model)


def _finish(db, job: LlmJob, status: str, error: str | None = None) -> None:
    """Set a terminal state — unless a cancel raced in, which always wins."""
    db.refresh(job)
    if job.status == "cancelled":
        return
    job.status = status
    job.error = error
    job.updated_at = datetime.datetime.utcnow()
    db.commit()


async def run_llm_job(SessionLocal, job_id: int, transcription_service, diarization_service=None) -> None:
    """Execute one claimed (already 'running') job in its own session."""
    import os

    from services.correction import correct_transcript
    from services.settings import resolve_provider_key

    db = SessionLocal()
    try:
        job = db.query(LlmJob).filter(LlmJob.id == job_id).first()
        if not job:
            return
        transcript = db.query(Transcript).filter(Transcript.id == job.transcript_id).first()
        if not transcript:
            _finish(db, job, "failed", "Transcript no longer exists")
            return

        api_key, provider_config = None, None
        if job.kind != "rediarize":  # rediarize is local compute — no LLM key involved
            api_key, provider_config = resolve_provider_key(db, job.user_id, job.provider)
            if job.provider != "local" and not api_key:
                _finish(db, job, "failed", f"no {job.provider} API key saved (see service panel)")
                return

        if job.kind == "correction":
            def progress(done, total):
                job.progress_done = done
                job.progress_total = total
                db.commit()

            def cancelled():
                db.refresh(job)
                return job.status == "cancelled"

            result = await correct_transcript(
                db, transcript, api_key=api_key, provider_name=job.provider,
                model=job.model, provider_config=provider_config,
                progress_cb=progress, cancel_cb=cancelled,
            )
            if result == "ok":
                _finish(db, job, "completed")
            elif result == "failed":
                _finish(db, job, "failed", transcript.correction_error)
            # 'cancelled': status already set by cancel_llm_job — leave it.
        elif job.kind == "summary":
            job.progress_total = 1
            db.commit()
            try:
                await transcription_service.summarize(
                    db, job.user_id, job.transcript_id, api_key=api_key,
                    provider_name=job.provider, provider_config=provider_config,
                    model=job.model,
                )
                job.progress_done = 1
                _finish(db, job, "completed")
            except Exception as e:
                _finish(db, job, "failed", str(e))
        elif job.kind == "rediarize":
            job.progress_total = 1
            db.commit()
            if diarization_service is None:
                _finish(db, job, "failed", "Diarization service unavailable")
                return
            if not (transcript.audio_path and os.path.exists(transcript.audio_path)):
                _finish(db, job, "failed", "No stored audio for this transcript")
                return
            from services.settings import get_user_settings
            user_settings = get_user_settings(db, job.user_id)
            try:
                merged, speaker_count = await diarization_service.diarize_and_merge(
                    transcript.audio_path,
                    num_speakers=transcript.num_speakers,
                    segments=transcript.segments or [],
                    hf_token=user_settings.get("hf_token"),
                )
                transcript.segments = merged
                transcript.speaker_count = speaker_count
                transcript.updated_at = datetime.datetime.utcnow()
                job.progress_done = 1
                db.commit()
                _finish(db, job, "completed")
            except Exception as e:
                _finish(db, job, "failed", str(e))
        else:
            _finish(db, job, "failed", f"Unknown job kind '{job.kind}'")
    except Exception as e:
        try:
            job = db.query(LlmJob).filter(LlmJob.id == job_id).first()
            if job:
                _finish(db, job, "failed", str(e))
        except Exception:
            pass
        print(f"[llm-jobs] job {job_id} failed unexpectedly: {e}")
    finally:
        db.close()


async def llm_worker_tick(SessionLocal, transcription_service, diarization_service=None) -> None:
    db = SessionLocal()
    try:
        running = db.query(LlmJob).filter(LlmJob.status == "running").count()
        slots = max(0, _MAX_CONCURRENT_JOBS - running)
        if slots == 0:
            return
        claimed = (
            db.query(LlmJob)
            .filter(LlmJob.status == "pending")
            .order_by(LlmJob.id.asc())
            .limit(slots)
            .all()
        )
        if not claimed:
            return
        for job in claimed:
            job.status = "running"
            job.updated_at = datetime.datetime.utcnow()
        db.commit()  # claim lands before any await — same invariant as the chunk queue
        job_ids = [job.id for job in claimed]
    finally:
        db.close()

    await asyncio.gather(*(run_llm_job(SessionLocal, jid, transcription_service, diarization_service) for jid in job_ids))


async def llm_worker_loop(SessionLocal, transcription_service, diarization_service=None, interval_seconds: float = 3.0) -> None:
    """Runs forever (until cancelled) — started from app.py's lifespan."""
    while True:
        try:
            await llm_worker_tick(SessionLocal, transcription_service, diarization_service)
        except Exception as e:
            print(f"[llm-jobs] worker tick failed: {e}")
        await asyncio.sleep(interval_seconds)
