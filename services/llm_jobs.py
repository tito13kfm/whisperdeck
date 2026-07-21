"""Background LLM jobs — correction and summary runs against transcripts.

Mirrors the transcription queue's shape (pending rows claimed by a worker
loop, commit-before-await) but runs in its own loop so a minutes-long
correction can't starve chunk dispatch. Each job executes in its own DB
session; the only cross-session signal is the status column (cancel flips
it, the runner re-reads it between batches).
"""
import asyncio
import datetime

from database import LlmJob, Transcript, VoiceProfile, utcnow_naive
from services.audio_prep import extract_clips_concat
from services.queue import MAX_ATTEMPTS, _retry_eligible
from services.voice_id import voice_id_service

ACTIVE_STATUSES = ("pending", "running")
TERMINAL_LLM_STATUSES = ("completed", "failed", "cancelled")

VALID_KINDS = (
    "correction", "summary", "rediarize", "voice_match",
    "format_markdown", "format_email", "format_coding_prompt", "classify_intent",
)
# Auto-retry (issue #14) is scoped to network-dependent kinds only —
# correction/summary/format_*/classify_intent call a provider API and can
# fail transiently. rediarize/voice_match are local CPU-bound compute
# (diarization clustering, voice-embedding extraction); a failure there is
# far more likely to be deterministic (bad audio, missing backend, no
# enrolled voices) than transient, so blindly retrying up to MAX_ATTEMPTS
# would just re-run expensive local inference against the same failure. See
# "Open Design Questions" in
# docs/superpowers/plans/2026-07-07-queue-audit-llmjob-auto-retry.md
# if reconsidering.
AUTO_RETRY_KINDS = ("correction", "summary", "format_markdown", "format_email", "format_coding_prompt", "classify_intent")
# Two independent concurrency pools, capped separately (issue #14): I/O-bound
# kinds are provider API calls (bounded by provider rate limits, not local
# resources), CPU-bound kinds are local compute (diarization clustering /
# embedding extraction) and stay small so they don't fight each other for
# the same CPU. IO_KINDS/CPU_KINDS must partition VALID_KINDS exactly — see
# test_io_cpu_pools_partition_valid_kinds.
IO_KINDS = ("correction", "summary", "format_markdown", "format_email", "format_coding_prompt", "classify_intent")
CPU_KINDS = ("rediarize", "voice_match")
_MAX_CONCURRENT_IO_JOBS = 2
_MAX_CONCURRENT_CPU_JOBS = 1


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


def reset_stuck_llm_jobs(db) -> int:
    """Startup reconciliation: an LlmJob left 'running' means the process
    died mid-job. attempts was already incremented before the crashed
    await (see the claim loop in llm_worker_tick), so land it on 'failed'
    and let the normal sweep + _retry_eligible backoff resurrect it (for
    AUTO_RETRY_KINDS) — never straight back to 'pending'. Mirrors
    reset_stuck_transcription_jobs' identical reasoning for TranscriptionJob."""
    stuck = db.query(LlmJob).filter(LlmJob.status == "running").all()
    for job in stuck:
        job.status = "failed"
        job.error = "Interrupted by server restart"
    db.commit()
    return len(stuck)


def dismiss_llm_job(db, user_id: int, job_id: int) -> LlmJob:
    """Hide a terminal job from the Queue screen. Non-destructive — the row
    (and any correction/summary text already merged into the transcript)
    is untouched, only `dismissed` flips."""
    job = db.query(LlmJob).filter(LlmJob.id == job_id, LlmJob.user_id == user_id).first()
    if not job:
        raise LookupError("Job not found")
    if job.status not in TERMINAL_LLM_STATUSES:
        raise ValueError(f"Cannot dismiss a job that is still {job.status}")
    job.dismissed = True
    db.commit()
    return job


def clear_finished_llm_jobs(db, user_id: int) -> int:
    jobs = (
        db.query(LlmJob)
        .filter(
            LlmJob.user_id == user_id,
            LlmJob.status.in_(TERMINAL_LLM_STATUSES),
            LlmJob.dismissed.is_(False),
        )
        .all()
    )
    for job in jobs:
        job.dismissed = True
    db.commit()
    return len(jobs)


def enqueue_auto_correction(db, transcript, user_settings: dict) -> LlmJob:
    """Auto-correct entry point for the inline and chunked-finalize paths.
    Keyless providers fail the job immediately with the skip reason (also
    recorded on the transcript so the corrected tab explains itself)."""
    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS

    provider = user_settings.get("correction_provider", "groq")
    model = user_settings.get("correction_model", "llama-3.3-70b-versatile")
    api_key, _ = resolve_provider_key(db, transcript.user_id, provider)
    error = None
    if provider not in KEYLESS_PROVIDERS and not api_key:
        error = f"auto-correct skipped: no {provider} API key saved (see service panel)"
        transcript.correction_error = error
        db.commit()
    return enqueue_llm_job(db, transcript.user_id, transcript.id, "correction", provider, model, error=error)


def enqueue_auto_classify(db, transcript, user_settings: dict) -> LlmJob | None:
    """Auto-classify entry point for the inline and chunked-finalize paths —
    dictation transcripts only. Guesses which reformat action (Markdown /
    email / coding prompt) best fits, surfaced as a UI hint; the underlying
    classify_intent() call never raises, so this never needs an error path
    of its own beyond the usual missing-API-key skip."""
    if transcript.kind != "dictation":
        return None
    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS

    provider = user_settings.get("format_provider", "groq")
    model = user_settings.get("format_model", "llama-3.3-70b-versatile")
    api_key, _ = resolve_provider_key(db, transcript.user_id, provider)
    error = None
    if provider not in KEYLESS_PROVIDERS and not api_key:
        error = f"auto-classify skipped: no {provider} API key saved (see service panel)"
    return enqueue_llm_job(db, transcript.user_id, transcript.id, "classify_intent", provider, model, error=error)


def cancel_llm_job(db, user_id: int, job_id: int) -> LlmJob:
    job = db.query(LlmJob).filter(LlmJob.id == job_id, LlmJob.user_id == user_id).first()
    if not job:
        raise LookupError("Job not found")
    if job.status not in ACTIVE_STATUSES:
        raise ValueError(f"Cannot cancel a job with status '{job.status}'")
    # pending dies instantly; a running correction notices between batches.
    job.status = "cancelled"
    job.updated_at = utcnow_naive()
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
    job.updated_at = utcnow_naive()
    db.commit()


async def run_llm_job(SessionLocal, job_id: int, transcription_service, diarization_service=None) -> None:
    """Execute one claimed (already 'running') job in its own session."""
    import os

    from services.correction import correct_transcript
    from services.reformatting import (
        format_as_markdown, format_as_email, format_as_coding_prompt, classify_intent,
    )
    from services.settings import resolve_provider_key, KEYLESS_PROVIDERS

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
        if job.kind not in CPU_KINDS:  # local compute — no LLM key involved
            api_key, provider_config = resolve_provider_key(db, job.user_id, job.provider)
            if job.provider not in KEYLESS_PROVIDERS and not api_key:
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
                job.result_json = {"corrected_text": transcript.corrected_text}
                db.commit()
                _finish(db, job, "completed")
            elif result == "failed":
                _finish(db, job, "failed", transcript.correction_error)
            # 'cancelled': status already set by cancel_llm_job — leave it.
        elif job.kind == "summary":
            job.progress_total = 1
            db.commit()
            try:
                summary = await transcription_service.summarize(
                    db, job.user_id, job.transcript_id, api_key=api_key,
                    provider_name=job.provider, provider_config=provider_config,
                    model=job.model,
                )
                job.result_json = {
                    "short_summary": summary.short_summary,
                    "key_points": summary.key_points or [],
                    "action_items": summary.action_items or [],
                    "decisions": summary.decisions or [],
                }
                job.progress_done = 1
                db.commit()
                _finish(db, job, "completed")
            except Exception as e:
                _finish(db, job, "failed", str(e))
        elif job.kind in ("format_markdown", "format_email", "format_coding_prompt"):
            job.progress_total = 1
            db.commit()
            generate = {
                "format_markdown": format_as_markdown,
                "format_email": format_as_email,
                "format_coding_prompt": format_as_coding_prompt,
            }[job.kind]
            try:
                text = await generate(
                    transcript, api_key=api_key, provider_name=job.provider,
                    provider_config=provider_config, model=job.model,
                )
                job.result_json = {"text": text}
                job.progress_done = 1
                db.commit()
                _finish(db, job, "completed")
            except Exception as e:
                _finish(db, job, "failed", str(e))
        elif job.kind == "classify_intent":
            job.progress_total = 1
            db.commit()
            label = await classify_intent(
                transcript, api_key=api_key, provider_name=job.provider,
                provider_config=provider_config, model=job.model,
            )
            job.result_json = {"format": label}
            job.progress_done = 1
            db.commit()
            _finish(db, job, "completed")
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
                transcript.updated_at = utcnow_naive()
                job.progress_done = 1
                job.result_json = {"segments": merged}
                db.commit()
                _finish(db, job, "completed")
            except Exception as e:
                _finish(db, job, "failed", str(e))
        elif job.kind == "voice_match":
            if voice_id_service._backend == "none":
                _finish(db, job, "failed", "No voice embedding backend available")
                return
            if not (transcript.audio_path and os.path.exists(transcript.audio_path)):
                _finish(db, job, "failed", "No stored audio for this transcript")
                return
            has_enrolled_voice = (
                db.query(VoiceProfile)
                .filter(VoiceProfile.user_id == job.user_id, VoiceProfile.embedding.isnot(None))
                .first()
                is not None
            )
            if not has_enrolled_voice:
                _finish(db, job, "failed", "No enrolled voices with clips — add a clip to a roster profile first")
                return
            segments = transcript.segments or []
            job.progress_total = len(segments)
            job.progress_done = 0
            db.commit()
            skipped = 0
            new_segments = list(segments)
            for i, seg in enumerate(segments):
                try:
                    clip_path = await extract_clips_concat(
                        transcript.audio_path, [{"start": seg["start"], "end": seg["end"]}],
                        str(os.path.dirname(transcript.audio_path)),
                    )
                    try:
                        # identify() is a plain sync call (embedding extraction is
                        # CPU-bound with no internal await) — run it off the event
                        # loop so one voice_match job doesn't stall the whole app
                        # per segment. Safe to pass `db` across the thread boundary:
                        # sqlite is opened with check_same_thread=False.
                        loop = asyncio.get_event_loop()
                        def _identify():
                            return voice_id_service.identify(db, job.user_id, clip_path, threshold=0.65)
                        matches = await loop.run_in_executor(None, _identify)
                    finally:
                        try:
                            os.remove(clip_path)
                        except OSError:
                            pass
                    if matches:
                        new_segments[i] = {**seg, "speaker": matches[0]["name"]}
                except Exception:
                    skipped += 1
                job.progress_done = i + 1
                db.commit()
            transcript.segments = new_segments
            transcript.updated_at = utcnow_naive()
            db.commit()
            error = f"{skipped} segment(s) skipped (extraction/embedding failed)" if skipped else None
            _finish(db, job, "completed", error)
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
        eligible_failed = (
            db.query(LlmJob)
            .filter(
                LlmJob.status == "failed",
                LlmJob.dismissed.is_(False),
                LlmJob.attempts >= 1,
                LlmJob.kind.in_(AUTO_RETRY_KINDS),
            )
            .all()
        )
        for job in eligible_failed:
            if not _retry_eligible(job):
                continue
            if get_active_job(db, job.transcript_id, job.kind) is not None:
                # A manual rerun already created a fresh pending/running job
                # for this transcript+kind — resurrecting this stale failed
                # row too would dispatch two jobs writing the same
                # transcript concurrently. Leave it failed; it's still
                # manually rerunnable if the fresh sibling later fails too.
                continue
            job.status = "pending"
            job.error = None
        db.commit()

        claimed = []
        for kinds, cap in ((IO_KINDS, _MAX_CONCURRENT_IO_JOBS), (CPU_KINDS, _MAX_CONCURRENT_CPU_JOBS)):
            running = db.query(LlmJob).filter(LlmJob.status == "running", LlmJob.kind.in_(kinds)).count()
            slots = max(0, cap - running)
            if slots == 0:
                continue
            claimed.extend(
                db.query(LlmJob)
                .filter(LlmJob.status == "pending", LlmJob.kind.in_(kinds))
                .order_by(LlmJob.id.asc())
                .limit(slots)
                .all()
            )
        if not claimed:
            return
        for job in claimed:
            job.status = "running"
            job.attempts = (job.attempts or 0) + 1
            job.updated_at = utcnow_naive()
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
