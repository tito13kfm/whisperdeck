"""Chunk-upload job queue: rate-limit budget tracking and result reassembly.

The dispatch worker loop lives in this same module — see the bottom half
of this file (queue_worker_tick, queue_worker_loop), added alongside the
functions below.
"""
import asyncio
import datetime
from typing import Optional

from database import Transcript, TranscriptionJob, utcnow_naive
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
    cutoff = utcnow_naive() - datetime.timedelta(seconds=window_seconds)

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
    from backends import LOCAL_PROVIDERS
    if provider in LOCAL_PROVIDERS:
        # On-device work has no rate limit to budget against — the DEFAULT_LIMITS
        # fallback would nonsensically throttle local CPU inference.
        return True
    limits = PROVIDER_LIMITS.get(provider, DEFAULT_LIMITS)
    used_hour = compute_audio_seconds_used(db, user_id, provider, 3600)
    used_day = compute_audio_seconds_used(db, user_id, provider, 86400)
    return (used_hour + additional_seconds) <= limits["ash"] and (used_day + additional_seconds) <= limits["asd"]


def _oldest_contributing_timestamp(db, user_id: int, provider: str, window_seconds: int):
    """Return the earliest updated_at among the rows compute_audio_seconds_used
    would count for this user+provider within the trailing window_seconds —
    i.e. the row whose usage will be the next to age out. Returns None if
    nothing is currently contributing (budget isn't actually constrained)."""
    cutoff = utcnow_naive() - datetime.timedelta(seconds=window_seconds)

    transcript_times = [
        t.updated_at for t in db.query(Transcript).filter(
            Transcript.user_id == user_id,
            Transcript.provider == provider,
            Transcript.status.in_(["completed", "partial"]),
            Transcript.updated_at >= cutoff,
        ).all()
    ]
    job_times = [
        j.updated_at for j in (
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
    ]
    all_times = transcript_times + job_times
    return min(all_times) if all_times else None


def estimate_resume_seconds(db, user_id: int, provider: str, additional_seconds: float) -> float:
    """Best-effort estimate of when has_budget would next return True for a
    job needing additional_seconds. Checks both the hourly (ash) and daily
    (asd) windows — whichever is actually blocking — and returns how long
    until the oldest row counted in that window ages out. Approximate: it
    doesn't account for other jobs adding new usage before then, since this
    is a UI estimate ("resuming in ~Nm"), not a scheduling guarantee."""
    limits = PROVIDER_LIMITS.get(provider, DEFAULT_LIMITS)
    now = utcnow_naive()
    candidates = []
    for window_seconds, cap_key in ((3600, "ash"), (86400, "asd")):
        used = compute_audio_seconds_used(db, user_id, provider, window_seconds)
        if used + additional_seconds > limits[cap_key]:
            oldest = _oldest_contributing_timestamp(db, user_id, provider, window_seconds)
            if oldest:
                expiry = oldest + datetime.timedelta(seconds=window_seconds)
                candidates.append((expiry - now).total_seconds())
    if not candidates:
        return 0.0
    return max(0.0, max(candidates))


def compute_queue_status(db, transcript) -> Optional[dict]:
    """Live status for a 'processing' transcript, computed on read (never
    persisted) — tells the frontend WHY an upload looks like it's waiting:
    actively transcribing a chunk, queued behind concurrency, or blocked on
    the provider's rate-limit budget. Returns None once status isn't
    'processing' anymore (terminal states carry their own meaning)."""
    if transcript.status != "processing":
        return None

    jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == transcript.id).all()
    if not jobs:
        return None  # single-shot sync path never has job rows

    chunks_done = sum(1 for j in jobs if j.status == "completed")
    chunks_total = len(jobs)

    if any(j.status == "running" for j in jobs):
        return {"state": "transcribing", "chunks_done": chunks_done, "chunks_total": chunks_total}

    pending = sorted((j for j in jobs if j.status == "pending"), key=lambda j: j.chunk_index)
    if not pending:
        # Everything left is "failed" awaiting its backoff window — not
        # rate-limited, just waiting on the retry timer.
        return {"state": "queued", "chunks_done": chunks_done, "chunks_total": chunks_total}

    next_job = pending[0]
    job_duration = next_job.end_time - next_job.start_time
    if has_budget(db, transcript.user_id, transcript.provider, job_duration):
        return {"state": "queued", "chunks_done": chunks_done, "chunks_total": chunks_total}

    resume_in = estimate_resume_seconds(db, transcript.user_id, transcript.provider, job_duration)
    return {
        "state": "rate_limited",
        "chunks_done": chunks_done,
        "chunks_total": chunks_total,
        "resume_in_seconds": round(resume_in),
    }


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
                "no_speech_prob": s.get("no_speech_prob"),
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
            transcript.queue_dismissed = False
        db.commit()
    return len(failed)


def cancel_transcript_jobs(db, transcript_id: int) -> int:
    """Mark every still-pending job for this transcript as cancelled, so
    the worker stops dispatching new work for it, and flip the transcript
    itself to 'cancelled' immediately. Jobs already 'running' are left
    alone — they're mid-flight to the provider and will finish naturally;
    _finalize_if_done checks the transcript's own status (not per-job
    status) and refuses to overwrite 'cancelled' with a job-driven
    completed/partial/failed result once they do. Returns how many
    pending jobs were cancelled.

    The transcript-level flip (rather than waiting for every job to reach
    a terminal state) is what makes cancellation stick even when every
    job is 'running' at the moment cancel is called: in that case there
    are zero pending jobs to mark, but the transcript must still end up
    'cancelled', not 'completed', once the in-flight jobs finish normally."""
    pending = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "pending")
        .all()
    )
    for job in pending:
        job.status = "cancelled"

    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if transcript:
        transcript.status = "cancelled"
        transcript.updated_at = utcnow_naive()
    db.commit()
    return len(pending)


def resume_cancelled_chunks(db, transcript_id: int) -> int:
    """Reset every cancelled job for this transcript back to pending so
    the worker picks it up again, and clear the transcript's own
    'cancelled' marker so _finalize_if_done stops refusing to finalize it.

    Note this always flips transcript.status back to 'processing', even
    when there are zero cancelled jobs to reset (the all-running-at-cancel
    case: every job already finished normally to 'completed'/'failed'
    while the transcript sat at 'cancelled'). In that case resume means
    "un-cancel and let the existing results stand" — the very next
    queue_worker_tick pass will finalize this transcript to
    completed/partial/failed from those already-finished jobs, via the
    'processing transcripts with no pending jobs this tick' discovery
    path in queue_worker_tick (finalize_candidate_ids), since a
    fully-completed job set means it will never re-enter by_transcript."""
    cancelled = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "cancelled")
        .all()
    )
    for job in cancelled:
        job.status = "pending"
        job.attempts = 0
        job.error = None

    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if transcript and transcript.status == "cancelled":
        transcript.status = "processing"
        transcript.queue_dismissed = False
    db.commit()
    return len(cancelled)


TERMINAL_TRANSCRIPT_STATUSES = ("completed", "failed", "partial", "cancelled")


def dismiss_transcript_queue_entry(db, user_id: int, transcript_id: int) -> Transcript:
    """Hide a terminal transcription entry from the Queue screen. Non-destructive
    — the transcript and its content are untouched, only queue_dismissed flips."""
    t = db.query(Transcript).filter(Transcript.id == transcript_id, Transcript.user_id == user_id).first()
    if not t:
        raise LookupError("Transcript not found")
    if t.status not in TERMINAL_TRANSCRIPT_STATUSES:
        raise ValueError(f"Cannot dismiss a transcript that is still {t.status}")
    t.queue_dismissed = True
    db.commit()
    return t


def clear_finished_transcript_queue_entries(db, user_id: int) -> int:
    transcripts = (
        db.query(Transcript)
        .filter(
            Transcript.user_id == user_id,
            Transcript.status.in_(TERMINAL_TRANSCRIPT_STATUSES),
            Transcript.queue_dismissed.is_(False),
        )
        .all()
    )
    for t in transcripts:
        t.queue_dismissed = True
    db.commit()
    return len(transcripts)


def reset_stuck_transcription_jobs(db) -> int:
    """Startup reconciliation: a TranscriptionJob left 'running' means the
    process died mid-job. attempts was already incremented before the
    crashed await (see _run_chunk_job), so land it on 'failed' and let the
    normal _retry_eligible backoff resurrect it — never straight back to
    'pending' (same reasoning as _run_chunk_job's own except branch)."""
    stuck = db.query(TranscriptionJob).filter(TranscriptionJob.status == "running").all()
    for job in stuck:
        job.status = "failed"
        job.error = "Interrupted by server restart"
    db.commit()
    return len(stuck)


def _retry_eligible(job) -> bool:
    if job.attempts >= MAX_ATTEMPTS:
        return False
    backoff = min(60, 5 * (2 ** job.attempts))
    elapsed = (utcnow_naive() - job.updated_at).total_seconds()
    return elapsed >= backoff


# SAFETY INVARIANT: this coroutine runs concurrently with sibling
# _run_chunk_job calls for the SAME transcript (the inner asyncio.gather
# in _process_transcript_jobs) AND with _run_chunk_job/_finalize_if_done
# calls for OTHER transcripts in the same tick (the outer asyncio.gather
# in queue_worker_tick) — all sharing ONE db session opened once per tick.
# This is only safe because every mutation here is committed BEFORE the
# one await point (the provider call) — so at every point asyncio could
# switch to ANY sibling coroutine, same transcript or a different one,
# the session has no uncommitted dirty state left behind. If you add a
# second mutation after the await, or move the commit, you MUST commit
# before any await or use a separate session per job instead.
async def _run_chunk_job(db, job, provider_config: dict, provider_name: str, language: str,
                          local_provider_lock: asyncio.Semaphore) -> None:
    job.status = "running"
    job.attempts += 1
    db.commit()

    # Fetch VAD settings for this transcript's owner (issue #270: mirror path
    # to app.py's inline transcribe). Read-only, safe before the await.
    from services.settings import get_user_settings
    from database import Transcript
    t = db.query(Transcript).filter(Transcript.id == job.transcript_id).first()
    vad_settings = {}
    hallu_enabled = False
    if t is not None:
        settings = get_user_settings(db, t.user_id)
        vad_settings["vad_filter"] = settings.get("cleanup_vad_enabled", True)
        vad_settings["vad_threshold"] = settings.get("cleanup_vad_threshold", 0.5)
        vad_settings["vad_min_silence_duration_ms"] = settings.get("cleanup_vad_min_silence_ms", 100)
        hallu_enabled = settings.get("cleanup_hallu_enabled", False)

    try:
        provider = get_provider(provider_name, provider_config)
        from backends import LOCAL_PROVIDERS
        if provider_name in LOCAL_PROVIDERS:
            # Local providers (Moonshine/builtin) share one process-wide model
            # cache (see backends/moonshine.py / backends/builtin.py) whose
            # thread-safety under concurrent calls is unverified. Dispatch is
            # now concurrent across transcripts (queue_worker_tick), so this
            # lock — not accidental per-transcript serialization — is what
            # enforces "at most one local transcribe() in flight" globally.
            # Acquired here, AFTER job.status was already committed to
            # "running" above, so a job parked on this lock is never
            # mistaken for "pending" and re-dispatched by a later tick.
            async with local_provider_lock:
                result = await provider.transcribe(job.audio_path, language=language, temperature=0.0, **vad_settings)
        else:
            result = await provider.transcribe(job.audio_path, language=language, temperature=0.0, **vad_settings)

        if hallu_enabled and result.segments:
            seg_dicts = [{"text": s.text, "confidence": s.confidence,
                          "no_speech_prob": s.no_speech_prob} for s in result.segments]
            from services.audio_cleanup import filter_hallucinations
            filtered = filter_hallucinations(seg_dicts, rep_window=3, logprob_cutoff=-2.0,
                                              no_speech_cutoff=0.6)
            filtered_ids = {id(d) for d in filtered}
            result.segments = [s for i, s in enumerate(result.segments)
                               if id(seg_dicts[i]) in filtered_ids]

        job.result_json = {
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text, "speaker": s.speaker,
                 "confidence": s.confidence, "no_speech_prob": s.no_speech_prob}
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
    # This coroutine may now run concurrently with _process_transcript_jobs /
    # _finalize_if_done calls for OTHER transcripts in the same tick (see
    # queue_worker_tick), sharing the same db session — safe under the same
    # "commit or roll back before any await" discipline documented on
    # _run_chunk_job. The diarization branch below already follows this
    # (explicit db.rollback() before its awaits, re-fetch after) for a
    # different reason — a concurrent /cancel request on a separate
    # session — and that same discipline is what keeps it safe here too.
    jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == transcript_id).all()
    if not jobs or any(j.status in ("pending", "running") for j in jobs):
        return  # still work outstanding

    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        return

    if transcript.status == "cancelled":
        # Cancellation always wins, and is decided at the TRANSCRIPT level
        # (set by cancel_transcript_jobs the moment /cancel is called), not
        # by waiting for a job to reach status == "cancelled". A transcript
        # can be marked cancelled while every one of its jobs is still
        # "running" (all chunks in flight when cancel was requested) — in
        # that case no job ever becomes "cancelled", but the transcript
        # must still discard whatever those jobs produce once they finish,
        # rather than being overwritten with a job-driven completed/
        # partial/failed status. Refusing to overwrite here is what makes
        # that invariant hold regardless of job-completion timing.
        return

    segments, full_text = merge_chunk_results(jobs)
    duration_seconds = max((j.end_time for j in jobs), default=0)
    completed_count = sum(1 for j in jobs if j.status == "completed")
    failed_count = sum(1 for j in jobs if j.status == "failed")
    if failed_count == 0:
        new_status = "completed"
    elif completed_count == 0:
        new_status = "failed"
    else:
        new_status = "partial"

    speaker_count = None
    diarization_method = None
    if transcript.diarize_requested and segments and transcript.audio_path:
        # IMPORTANT: nothing above this point may leave a dirty write on
        # `transcript` — diarization result, segments, and the new status
        # are all kept in local variables only. If `transcript.status` (or
        # any other transcript attribute) is set in-memory before the
        # awaits below, SQLAlchemy's autoflush would push that dirty write
        # to the DB on the very next `db.query(...)` call (e.g.
        # get_user_settings), inside this same still-open transaction —
        # which means the post-await re-check further down would just be
        # re-reading this session's own uncommitted write, not a
        # concurrent session's commit. Ending the transaction (rollback,
        # no-op since nothing is dirty) before the await is what actually
        # lets a concurrent /cancel's commit land and be observed.
        db.rollback()
        transcript_user_id = transcript.user_id
        audio_path = transcript.audio_path
        stereo_audio_path = transcript.stereo_audio_path
        num_speakers = transcript.num_speakers
        try:
            from services.settings import get_user_settings  # local import avoids a module-load cycle with app.py
            user_settings = get_user_settings(db, transcript_user_id)
            # diarize_and_merge picks pyannote when installed, else the
            # pause-gap heuristic (which needs a real count, default 2 inside).
            merged, speaker_count, diarization_method = await diarization_service.diarize_and_merge(
                audio_path, num_speakers=num_speakers, segments=segments,
                hf_token=user_settings.get("hf_token"),
                stereo_audio_path=stereo_audio_path,
            )
            segments = merged
        except Exception as e:
            print(f"[queue] non-fatal diarization failure for transcript {transcript_id}: {e}")

        # Diarization awaits above can take several seconds, during which a
        # /cancel request on another request-handling coroutine (separate
        # db session/connection) can commit transcript.status = "cancelled".
        # Because nothing was left dirty on `transcript` going into the
        # await (see above), this is a genuine fresh read — no pending
        # write to autoflush-shadow it — so it will actually observe a
        # concurrent commit. Re-fetch the transcript from a clean session
        # state and bail out without writing if cancel won the race.
        db.expire_all()
        transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
        if not transcript or transcript.status == "cancelled":
            return

    # Finalize replaces segments wholesale. Normally there is no relabel
    # history yet (first completion), but on a resume/retry re-finalize any
    # entries recorded against the previous segmentation are index-stale —
    # clear them so undo can't stamp old labels onto the new lines.
    from services.relabel import clear_relabel_history
    clear_relabel_history(db, transcript.id)
    transcript.segments = segments
    transcript.full_text = full_text
    transcript.duration_seconds = duration_seconds
    transcript.status = new_status
    if speaker_count is not None:
        transcript.speaker_count = speaker_count
        transcript.diarization_method = diarization_method
    transcript.updated_at = utcnow_naive()
    db.commit()

    if new_status in ("completed", "partial"):
        from services.settings import get_user_settings  # local import avoids a module-load cycle with app.py
        user_settings = get_user_settings(db, transcript.user_id)
        # Mirror the inline-path branch in app.py:_run_transcription_pipeline
        # (design decision 11): correction runs unconditional of kind now,
        # gated only by auto_correct; enqueue_auto_classify and the
        # voice-note-chain enqueue below both no-op on the wrong kind via
        # effective_kind(), so calling them unconditionally is safe. A long
        # voice-note recording (above LOCAL_CHUNK_SECONDS) takes this path,
        # so the same voice-note-chain enqueue needs to fire here too.
        from services.llm_jobs import enqueue_auto_correction, enqueue_auto_classify, enqueue_auto_voice_note, enqueue_pipeline_classify
        from services.classification import effective_kind
        if user_settings.get("auto_correct", True):
            # Queued as a background LlmJob — the LLM worker loop picks it
            # up, so chunk finalization never blocks on a correction pass.
            enqueue_auto_correction(db, transcript, user_settings)
        else:
            # No correction pass means correction-completion (the usual
            # classify_pipeline trigger) never fires — trigger it directly,
            # matching the inline path's identical fallback (issue #268
            # comment 2's gap). No-ops via its own status guard when kind
            # was explicitly chosen.
            enqueue_pipeline_classify(db, transcript, user_settings)
        enqueue_auto_classify(db, transcript, user_settings)
        if effective_kind(transcript) == "voice_note":
            enqueue_auto_voice_note(db, transcript, user_settings)
        # Tagging fires for every kind — keep this site in lockstep
        # with app.py:_run_transcription_pipeline (issue #171).
        from services.llm_jobs import enqueue_auto_tagging
        enqueue_auto_tagging(db, transcript, user_settings)


async def _process_transcript_jobs(db, transcript_id: int, jobs: list, diarization_service,
                                    local_provider_lock: asyncio.Semaphore) -> None:
    """Dispatch this transcript's pending jobs (up to its concurrency cap
    and rate-limit budget) and finalize-check it, all sharing the ONE db
    session opened by the calling queue_worker_tick. Extracted from
    queue_worker_tick's old per-transcript loop body so it can be awaited
    concurrently, via asyncio.gather, with this same function running for
    OTHER transcripts — see the SAFETY INVARIANT comment on _run_chunk_job
    for why sharing one session across concurrent transcripts is safe."""
    from services.settings import get_user_settings  # local import avoids a module-load cycle with app.py

    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        return
    settings = get_user_settings(db, transcript.user_id)
    from backends import LOCAL_PROVIDERS
    if transcript.provider in LOCAL_PROVIDERS:
        # Serial: local backends share one process-wide model instance
        # (see backends/moonshine.py cache comment) whose thread-safety
        # under concurrent calls is unverified, and parallel local
        # inference would multiply RAM for no wall-clock win on CPU.
        concurrency_cap = 1
    else:
        concurrency_cap = settings["max_concurrent_chunks"]

    already_running = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.transcript_id == transcript_id, TranscriptionJob.status == "running")
        .count()
    )
    slots = max(0, concurrency_cap - already_running)
    if slots == 0:
        return

    prov_cfg = (
        db.query(ProviderConfig)
        .filter(ProviderConfig.user_id == transcript.user_id, ProviderConfig.name == transcript.provider)
        .first()
    )
    raw_key = prov_cfg.api_key if prov_cfg else ""
    # Decrypt the API key transparently if it was encrypted on save
    from services.settings import _decrypt_key_if_needed
    from pathlib import Path
    import os as os_module
    _queue_data = Path(os_module.environ.get("WHISPERDECK_DATA_DIR") or os_module.environ.get("WHISPERDESK_DATA_DIR") or str(Path(__file__).parent.parent / "data"))
    _queue_secret_path = _queue_data / ".session_secret"
    _queue_secret = _queue_secret_path.read_text().strip() if _queue_secret_path.exists() else ""
    decrypted_key = _decrypt_key_if_needed(raw_key, _queue_secret)
    provider_config = {
        "api_key": decrypted_key,
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
            _run_chunk_job(db, job, provider_config, transcript.provider, transcript.language, local_provider_lock)
            for job in dispatched
        ])

    await _finalize_if_done(db, transcript_id, diarization_service)


async def queue_worker_tick(SessionLocal, diarization_service) -> None:
    """One pass: retry-eligible failed jobs become pending, then dispatch
    pending jobs (grouped by user+provider) up to that user's concurrency
    setting, skipping any dispatch that would exceed rate-limit budget.

    Every transcript with pending jobs this tick is processed CONCURRENTLY
    (asyncio.gather over _process_transcript_jobs), not one-at-a-time —
    a hosted-provider transcript no longer waits for an unrelated
    transcript's dispatch+finalize to finish before its own chunks even
    start.

    return_exceptions=True is required on both gathers above: without it,
    one transcript raising would propagate immediately and, once
    asyncio.run() tears down any still-pending sibling tasks, could cut
    off a sibling's _run_chunk_job mid-await — leaving that job's status
    stuck at "running" forever, since CancelledError isn't caught by
    _run_chunk_job's own `except (ProviderError, Exception)`. Exceptions
    are logged per-transcript instead, and every other transcript still
    runs to completion this tick; the failed transcript is simply retried
    (re-queried from "pending"/"processing") on the next tick, same as it
    would be if queue_worker_loop's outer try/except had caught it today.
    """
    db = SessionLocal()
    try:
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

        # Finalize-check every processing transcript, not just ones with
        # pending jobs this tick. Needed for cancel: cancelling every
        # pending job on a transcript that still has a job 'running'
        # leaves it with zero pending jobs from then on, so it would never
        # appear in by_transcript again — without this, it would never
        # reach _finalize_if_done once that running job completes and
        # would stay stuck at status='processing' forever.
        processing_ids = {
            row[0] for row in db.query(Transcript.id).filter(Transcript.status == "processing").all()
        }
        finalize_candidate_ids = set(by_transcript.keys()) | processing_ids

        # Created fresh per tick, not at module scope: asyncio.Semaphore binds
        # to the event loop of its first use, and each queue_worker_loop tick
        # gets its own loop iteration in tests (asyncio.run per test) — a
        # module-level instance would raise "bound to a different event loop"
        # on the second test that touches it. queue_worker_loop always awaits
        # one tick to full completion before starting the next, so a per-tick
        # semaphore still enforces "at most one local transcribe() in flight,
        # across every transcript processed in this tick" — the only window
        # where cross-transcript local-provider concurrency can occur.
        local_provider_lock = asyncio.Semaphore(1)

        transcript_ids = list(by_transcript.keys())
        results = await asyncio.gather(
            *[
                _process_transcript_jobs(db, transcript_id, jobs, diarization_service, local_provider_lock)
                for transcript_id, jobs in by_transcript.items()
            ],
            return_exceptions=True,
        )
        for transcript_id, result in zip(transcript_ids, results):
            if isinstance(result, Exception):
                print(f"[queue] transcript {transcript_id} dispatch/finalize failed: {result}")

        # Transcripts with no pending jobs this tick (e.g. everything
        # still running from a prior tick, or already fully terminal
        # apart from a status flip cancel_transcript_jobs deferred to
        # here) still need a finalize check — see the comment above
        # processing_ids for why this is necessary. Also gathered
        # concurrently, for the same reason as the dispatch loop above.
        remaining_ids = list(finalize_candidate_ids - set(by_transcript.keys()))
        finalize_results = await asyncio.gather(
            *[_finalize_if_done(db, transcript_id, diarization_service) for transcript_id in remaining_ids],
            return_exceptions=True,
        )
        for transcript_id, result in zip(remaining_ids, finalize_results):
            if isinstance(result, Exception):
                print(f"[queue] transcript {transcript_id} finalize failed: {result}")
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


def get_rate_limit_gauge(db, user_id: int, provider: str = "groq") -> dict:
    """Return structured rate-limit usage gauge for `provider` (default 'groq')."""
    from backends import LOCAL_PROVIDERS
    from services.pricing import get_provider_stt_rate
    if provider in LOCAL_PROVIDERS:
        return {
            "provider": provider,
            "used_seconds": 0.0,
            "limit_seconds": 0,
            "used_cost": 0.0,
            "limit_cost": 0.0,
            "resets_in_seconds": 0,
            "is_local": True,
        }
    limits = PROVIDER_LIMITS.get(provider, DEFAULT_LIMITS)
    limit_seconds = limits.get("asd", 28800)
    used_seconds = compute_audio_seconds_used(db, user_id, provider, 86400)
    oldest_ts = _oldest_contributing_timestamp(db, user_id, provider, 86400)
    resets_in_seconds = 0
    if oldest_ts:
        now = utcnow_naive()
        expiry = oldest_ts + datetime.timedelta(seconds=86400)
        resets_in_seconds = max(0, round((expiry - now).total_seconds()))
    stt_rate = get_provider_stt_rate(provider)["rate_per_minute"]
    used_cost = round((used_seconds / 60.0) * stt_rate, 4)
    limit_cost = round((limit_seconds / 60.0) * stt_rate, 4)
    return {
        "provider": provider,
        "used_seconds": round(used_seconds, 1),
        "limit_seconds": limit_seconds,
        "used_cost": used_cost,
        "limit_cost": limit_cost,
        "resets_in_seconds": resets_in_seconds,
        "is_local": False,
    }
