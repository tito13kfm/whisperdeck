# Cross-Transcript Chunk Parallelism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `queue_worker_tick` dispatch and finalize-check multiple transcripts concurrently within one tick (via `asyncio.gather` instead of a sequential per-transcript `for`/`await` loop), so a hosted-provider transcript's chunks are no longer blocked behind an unrelated transcript's dispatch+finalize — while explicitly re-enforcing, via a shared lock, the local-provider (Moonshine/builtin) global concurrency cap of 1 that today only holds by accident of that same sequential loop.

**Scope note:** this is a multi-transcript / multi-user throughput fix. A single transcript's own behavior (its per-transcript `max_concurrent_chunks` cap, its chunk ordering, its budget checks) is unchanged. Local providers get no speedup from this change — they still run one chunk at a time, globally — they just stop accidentally blocking *other* transcripts' hosted-provider work while they run.

**Architecture:** Extract the current per-transcript loop body (transcript lookup, concurrency-cap/slot computation, provider-config lookup, budget-gated dispatch, finalize check) into a new coroutine `_process_transcript_jobs`, and `asyncio.gather` it across every transcript in `by_transcript`, with `return_exceptions=True` so one transcript's failure can't cancel a sibling's in-flight chunk job. All transcripts continue to share the ONE `db` session `queue_worker_tick` already opens per tick — this is deliberate: a single SQLite connection can't lock against itself, whereas giving each transcript its own session would introduce real cross-connection "database is locked" risk that doesn't exist today. Sharing one session across concurrent coroutines is safe only because of an existing, and now widened, invariant: every coroutine that touches `db` must commit (or roll back) before its own await points, so whichever sibling coroutine the event loop switches to next always sees a clean, fully-committed session — never another coroutine's half-finished write. `_run_chunk_job` already satisfies this; `_finalize_if_done`'s diarization branch already satisfies this (explicit `db.rollback()` before its awaits, re-fetch after) for a *different* reason (a concurrent `/cancel` HTTP request), and this plan extends that same discipline to cover sibling transcripts processed in the same tick. Local-provider safety, which today is an accidental side effect of the sequential loop, becomes an explicit `asyncio.Semaphore(1)` created fresh inside each `queue_worker_tick` call and threaded down to `_run_chunk_job`, acquired only around the actual `provider.transcribe()` call for local providers — **not** around the whole job, because `job.status` must already be committed to `"running"` before a job parks on the semaphore, otherwise a later tick's "pending jobs" query would re-dispatch the same job while it's still queued (double-processing). The semaphore is created per-tick, not at module scope, because `asyncio.Semaphore` binds to the event loop of its first use; `queue_worker_loop` fully awaits each tick before starting the next, so a per-tick instance still enforces "at most one local `transcribe()` in flight, across every transcript in this tick" — the exact window where the new cross-transcript concurrency exists.

**Tech Stack:** Python 3.12 (`.venv`), `asyncio`, SQLAlchemy (sync ORM, one `Session` per tick), pytest + pytest-asyncio (existing tests call `asyncio.run(...)` directly from plain `def test_...` functions rather than `async def`, matching this repo's established convention in `tests/test_local_chunking.py`).

**Ship-together constraint:** Task 1 alone (the `gather` restructuring) removes today's *accidental* cross-transcript safety for local providers before Task 2 adds the *explicit* replacement. Do not merge or deploy Task 1 without Task 2 — they must land as one unit. Task 1's own new tests intentionally do not cover local-provider cross-transcript safety (that test is Task 2's, and is deliberately red against Task 1's intermediate state) to make this gap visible rather than accidentally hidden.

---

## File Structure

| File | Responsibility | Tasks touching it |
|---|---|---|
| `services/queue.py` | Queue worker tick: dispatch, finalize, concurrency caps | 1, 2 |
| `backends/moonshine.py` | Moonshine local provider — model cache comment | 2 |
| `backends/builtin.py` | Built-in (faster-whisper) local provider — model cache comment | 2 |
| `tests/test_queue_cross_transcript_concurrency.py` (new) | Cross-transcript dispatch overlap, exception isolation, global local-provider cap | 1, 2 |
| `tests/test_local_chunking.py` | Existing per-transcript serial-dispatch regression test — must keep passing unmodified | 1, 2 (verification only) |

---

### Task 1: Gather transcript dispatch+finalize across transcripts instead of awaiting them sequentially

**Files:**
- Modify: `services/queue.py:399-406` (safety invariant comment on `_run_chunk_job`)
- Modify: `services/queue.py:440-447` (add a concurrency note to `_finalize_if_done`'s docstring-comment block)
- Modify: `services/queue.py:544-642` (`queue_worker_tick` — extract `_process_transcript_jobs`, replace the sequential loop with two gathers)
- Create: `tests/test_queue_cross_transcript_concurrency.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_queue_cross_transcript_concurrency.py`:

```python
"""Cross-transcript concurrency in the queue worker tick: hosted-provider
transcripts should dispatch concurrently instead of one fully finishing
(dispatch + finalize) before the next even starts, and one transcript's
finalize failure must not cancel a sibling transcript's in-flight chunk
job. The local-provider global-cap regression test,
test_local_provider_cap_of_one_holds_globally_across_transcripts, is added
in the next task — it needs this task's gather restructuring in place
first to be meaningful."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from database import Transcript, TranscriptionJob, User


class _NoClose:
    """Wraps a db session so queue_worker_tick's `db.close()` in its
    `finally` block doesn't tear down the test's shared session."""
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def _make_transcript_with_job(db_session, username, provider, chunk_seconds=60.0):
    user = User(username=username, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="processing", provider=provider, model="base")
    db_session.add(t)
    db_session.commit()
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=chunk_seconds,
        audio_path="c0.mp3", status="pending",
    ))
    db_session.commit()
    return t


def test_two_hosted_transcripts_dispatch_concurrently(db_session):
    """Two different transcripts on a hosted provider (groq) must both be
    able to have their chunk in flight at the same time — proving
    queue_worker_tick no longer fully awaits one transcript's batch before
    even looking at the next."""
    from services.queue import queue_worker_tick

    t1 = _make_transcript_with_job(db_session, "hosted1", "groq")
    t2 = _make_transcript_with_job(db_session, "hosted2", "groq")

    entered = []
    both_entered = asyncio.Event()

    class _FakeProvider:
        async def transcribe(self, *a, **k):
            entered.append(1)
            if len(entered) >= 2:
                both_entered.set()
            # Each call waits for the OTHER to have entered too, with NO
            # timeout of its own — only satisfiable if both transcripts'
            # chunk jobs are genuinely in flight at the same time. If run
            # sequentially, this hangs forever (t2 never gets a turn while
            # t1 is still awaiting here) — the outer asyncio.wait_for below
            # is what turns that hang into a clean test failure instead of
            # an actually-hung test process. (A timeout on this inner wait
            # would be silently swallowed by _run_chunk_job's own
            # `except (ProviderError, Exception)`, masking the hang as a
            # "failed" job instead of surfacing it — deliberately avoided.)
            await both_entered.wait()
            return SimpleNamespace(segments=[], full_text="", language="en", model="whisper-large-v3-flash")

    with patch("services.queue.get_provider", return_value=_FakeProvider()), \
         patch("services.queue._finalize_if_done", AsyncMock()):
        try:
            asyncio.run(asyncio.wait_for(
                queue_worker_tick(lambda: _NoClose(db_session), diarization_service=None),
                timeout=5.0,
            ))
        except asyncio.TimeoutError:
            pytest.fail(
                "queue_worker_tick timed out — transcripts are still dispatched "
                "sequentially, so the second transcript's chunk never started "
                "while the first was in flight"
            )

    assert len(entered) == 2
    job1 = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t1.id).first()
    job2 = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t2.id).first()
    assert job1.status == "completed"
    assert job2.status == "completed"


def test_one_transcript_finalize_exception_does_not_cancel_sibling_dispatch(db_session):
    """A raising _finalize_if_done for one transcript must not cancel a
    sibling transcript's in-flight chunk job. Plain asyncio.gather (without
    return_exceptions=True) cancels every other pending task the moment one
    raises — cancelling a job parked at `await provider.transcribe(...)`
    would leave it stuck at status="running" forever, since CancelledError
    isn't caught by _run_chunk_job's `except (ProviderError, Exception)`.

    `broken`'s chunk is given a duration that blows its rate-limit budget,
    so has_budget() rejects it and _process_transcript_jobs calls
    _finalize_if_done immediately (no dispatch, no sleep) — while
    `healthy`'s chunk is still mid-flight in its own 0.05s fake transcribe
    call. This makes the exception land deterministically while a sibling
    is still in flight, rather than relying on scheduling luck.
    """
    from services.queue import queue_worker_tick

    broken = _make_transcript_with_job(db_session, "broken", "groq", chunk_seconds=100_000.0)
    healthy = _make_transcript_with_job(db_session, "healthy", "groq")

    class _FakeProvider:
        async def transcribe(self, *a, **k):
            await asyncio.sleep(0.05)
            return SimpleNamespace(segments=[], full_text="", language="en", model="whisper-large-v3-flash")

    async def _boom(db, transcript_id, diarization_service):
        if transcript_id == broken.id:
            raise RuntimeError("simulated finalize failure")

    with patch("services.queue.get_provider", return_value=_FakeProvider()), \
         patch("services.queue._finalize_if_done", AsyncMock(side_effect=_boom)):
        asyncio.run(asyncio.wait_for(
            queue_worker_tick(lambda: _NoClose(db_session), diarization_service=None),
            timeout=5.0,
        ))

    healthy_job = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == healthy.id).first()
    assert healthy_job.status == "completed"  # not stuck at "running" from a cancelled sibling task
```

- [ ] **Step 2: Run `test_two_hosted_transcripts_dispatch_concurrently` to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_cross_transcript_concurrency.py::test_two_hosted_transcripts_dispatch_concurrently -v`

Expected: **FAILS** (via `pytest.fail`, message `queue_worker_tick timed out — transcripts are still dispatched sequentially...`). Today's `for transcript_id, jobs in by_transcript.items(): ... await asyncio.gather(...)` loop fully awaits transcript 1's one-job dispatch before even looking at transcript 2, regardless of which transcript happens to iterate first — whichever one runs first blocks forever on `await both_entered.wait()`, since the *other* transcript never gets a turn to enter while the first transcript's turn hasn't finished. This deadlock is caught by the outer 5s `asyncio.wait_for` in the test and converted into a clean `pytest.fail` instead of an actually-hung test process.

Do **not** run `test_one_transcript_finalize_exception_does_not_cancel_sibling_dispatch` yet against today's original code — its outcome there depends on SQLite's (unspecified) row-return order for the two transcripts' jobs: if `healthy` happens to be processed before `broken` in the sequential loop, `healthy` would already be fully completed by the time `broken` raises, and the test would misleadingly pass without proving anything. This test's meaningful red/green transition happens across the two implementation sub-steps below instead, where the ordering is irrelevant because both transcripts are genuinely concurrent.

- [ ] **Step 3: Implement — extract `_process_transcript_jobs` and gather across transcripts, in two sub-steps**

**Step 3a — gather without exception isolation (deliberately incomplete, to drive the second test red for the right reason):**

In `services/queue.py`, update the safety-invariant comment on `_run_chunk_job` (currently lines 399-406):

Current:
```python
# SAFETY INVARIANT: this coroutine runs concurrently with sibling
# _run_chunk_job calls via asyncio.gather, all sharing ONE db session
# (see queue_worker_tick). This is only safe because every mutation here
# is committed BEFORE the one await point (the provider call) — so at
# every point asyncio could switch between concurrent jobs, the session
# has no other job's uncommitted dirty state. If you add a second
# mutation after the await, or move the commit, you MUST commit before
# any await or use a separate session per job instead.
```

Replace with:
```python
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
```

Then, in `_finalize_if_done`'s existing comment block right after its `def` line (currently lines 440-447, ending just before `jobs = db.query(...)`), add one sentence to the existing docstring:

Current:
```python
async def _finalize_if_done(db, transcript_id: int, diarization_service) -> None:
    jobs = db.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == transcript_id).all()
```

Replace with:
```python
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
```

Now replace the body of `queue_worker_tick` (currently lines 544-642). Current:

```python
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

        for transcript_id, jobs in by_transcript.items():
            transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
            if not transcript:
                continue
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

        for transcript_id in finalize_candidate_ids - set(by_transcript.keys()):
            # Transcripts with no pending jobs this tick (e.g. everything
            # still running from a prior tick, or already fully terminal
            # apart from a status flip cancel_transcript_jobs deferred to
            # here) still need a finalize check — see the comment above
            # processing_ids for why this is necessary.
            await _finalize_if_done(db, transcript_id, diarization_service)
    finally:
        db.close()
```

Replace with (this sub-step deliberately omits `return_exceptions=True` — that's added in Step 3b below):

```python
async def _process_transcript_jobs(db, transcript_id: int, jobs: list, diarization_service) -> None:
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


async def queue_worker_tick(SessionLocal, diarization_service) -> None:
    """One pass: retry-eligible failed jobs become pending, then dispatch
    pending jobs (grouped by user+provider) up to that user's concurrency
    setting, skipping any dispatch that would exceed rate-limit budget.

    Every transcript with pending jobs this tick is processed CONCURRENTLY
    (asyncio.gather over _process_transcript_jobs), not one-at-a-time —
    a hosted-provider transcript no longer waits for an unrelated
    transcript's dispatch+finalize to finish before its own chunks even
    start.
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

        await asyncio.gather(*[
            _process_transcript_jobs(db, transcript_id, jobs, diarization_service)
            for transcript_id, jobs in by_transcript.items()
        ])

        # Transcripts with no pending jobs this tick (e.g. everything
        # still running from a prior tick, or already fully terminal
        # apart from a status flip cancel_transcript_jobs deferred to
        # here) still need a finalize check — see the comment above
        # processing_ids for why this is necessary. Also gathered
        # concurrently, for the same reason as the dispatch loop above.
        await asyncio.gather(*[
            _finalize_if_done(db, transcript_id, diarization_service)
            for transcript_id in finalize_candidate_ids - set(by_transcript.keys())
        ])
    finally:
        db.close()
```

Note the local `from services.settings import get_user_settings` import moved from `queue_worker_tick`'s top into `_process_transcript_jobs` (its only remaining call site) — `queue_worker_tick` itself no longer calls `get_user_settings` directly.

**Run the interim check:**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_cross_transcript_concurrency.py -v`

Expected: `test_two_hosted_transcripts_dispatch_concurrently` now **PASSES** (transcripts genuinely dispatch concurrently). `test_one_transcript_finalize_exception_does_not_cancel_sibling_dispatch` now **FAILS deterministically** — regardless of dict iteration order, `broken` and `healthy` are both started as concurrent tasks by `asyncio.gather`; `broken`'s `_finalize_if_done` raises almost immediately (no dispatch, straight to finalize) while `healthy` is still genuinely in flight (asleep 0.05s inside its fake `transcribe()`). Plain `asyncio.gather` without `return_exceptions=True` propagates `broken`'s exception out of `queue_worker_tick` immediately; `asyncio.run()`'s own shutdown then cancels `healthy`'s still-pending task before it finishes, so `healthy_job.status` never reaches `"completed"` (the test fails on that assertion, or errors if the cancellation surfaces as an exception instead — either way, not a pass). This is the exact regression this task must not ship without Step 3b.

**Step 3b — add `return_exceptions=True` and per-transcript exception logging:**

In `queue_worker_tick`, replace the two `asyncio.gather(...)` calls just added:

Current:
```python
        await asyncio.gather(*[
            _process_transcript_jobs(db, transcript_id, jobs, diarization_service)
            for transcript_id, jobs in by_transcript.items()
        ])

        # Transcripts with no pending jobs this tick (e.g. everything
        # still running from a prior tick, or already fully terminal
        # apart from a status flip cancel_transcript_jobs deferred to
        # here) still need a finalize check — see the comment above
        # processing_ids for why this is necessary. Also gathered
        # concurrently, for the same reason as the dispatch loop above.
        await asyncio.gather(*[
            _finalize_if_done(db, transcript_id, diarization_service)
            for transcript_id in finalize_candidate_ids - set(by_transcript.keys())
        ])
```

Replace with:
```python
        transcript_ids = list(by_transcript.keys())
        results = await asyncio.gather(
            *[
                _process_transcript_jobs(db, transcript_id, jobs, diarization_service)
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
```

Also update `queue_worker_tick`'s docstring (added in Step 3a) to explain why this matters — add this paragraph after the existing one:
```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_cross_transcript_concurrency.py -v`
Expected: both tests **PASS**.

Run: `.venv\Scripts\python.exe -m pytest tests/test_local_chunking.py -v`
Expected: all existing tests **PASS** unmodified, including `test_local_chunks_dispatch_serially` (single transcript, unaffected by the cross-transcript restructuring — `_process_transcript_jobs` for one transcript behaves identically to the old inline loop body for one transcript).

- [ ] **Step 5: Commit**

```bash
git add services/queue.py tests/test_queue_cross_transcript_concurrency.py
git commit -m "feat: gather cross-transcript dispatch+finalize instead of awaiting sequentially"
```

---

### Task 2: Enforce the local-provider concurrency cap of 1 globally via a per-tick semaphore

**Files:**
- Modify: `services/queue.py` (`_run_chunk_job`, `_process_transcript_jobs`, `queue_worker_tick` — all as they exist after Task 1)
- Modify: `backends/moonshine.py:15-20` (model cache comment)
- Modify: `backends/builtin.py:85-88` (model cache comment)
- Modify: `tests/test_queue_cross_transcript_concurrency.py` (add the local-cap test)

**Why this is a separate task from Task 1:** Task 1's gather restructuring, on its own, lets two different transcripts on a local provider (Moonshine/builtin) each independently compute `concurrency_cap = 1` and dispatch one chunk each — concurrently, since dispatch is no longer serialized across transcripts. That's two `provider.transcribe()` calls in flight at once against the same process-wide model cache, which `backends/moonshine.py`/`backends/builtin.py` explicitly document as relying on serialized dispatch for safety. This task closes that gap with an explicit lock instead of leaving it to accidental serialization.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_queue_cross_transcript_concurrency.py`, after the existing tests:

```python
def test_local_provider_cap_of_one_holds_globally_across_transcripts(db_session):
    """Two different transcripts both on a local provider (moonshine) must
    never have their chunk actually transcribing at the same instant, even
    though queue_worker_tick now dispatches both transcripts concurrently
    (Task 1). This is the safety property that today's sequential loop
    gave for free by accident — this test pins it explicitly."""
    from services.queue import queue_worker_tick

    t1 = _make_transcript_with_job(db_session, "local1", "moonshine")
    t2 = _make_transcript_with_job(db_session, "local2", "moonshine")

    state = {"current": 0, "peak": 0}

    class _FakeProvider:
        async def transcribe(self, *a, **k):
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            await asyncio.sleep(0.05)  # hold the "slot" long enough for a real race to show up
            state["current"] -= 1
            return SimpleNamespace(segments=[], full_text="", language="en", model="base")

    with patch("services.queue.get_provider", return_value=_FakeProvider()), \
         patch("services.queue._finalize_if_done", AsyncMock()):
        asyncio.run(asyncio.wait_for(
            queue_worker_tick(lambda: _NoClose(db_session), diarization_service=None),
            timeout=5.0,
        ))

    assert state["peak"] == 1, f"expected at most 1 concurrent local transcribe() call, saw {state['peak']}"
    job1 = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t1.id).first()
    job2 = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t2.id).first()
    assert job1.status == "completed"
    assert job2.status == "completed"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_cross_transcript_concurrency.py::test_local_provider_cap_of_one_holds_globally_across_transcripts -v`

Expected: **FAILS** — `AssertionError: expected at most 1 concurrent local transcribe() call, saw 2`. After Task 1, `_process_transcript_jobs` for `t1` and `t2` run concurrently via the outer `asyncio.gather`, each independently computes `concurrency_cap = 1` for its own transcript, and each dispatches its one job — both `_run_chunk_job` calls reach `provider.transcribe()` with nothing yet serializing them across transcripts.

- [ ] **Step 3: Implement — add a per-tick semaphore and thread it through**

In `services/queue.py`, update `_run_chunk_job` (as it exists after Task 1, still at its original position, lines 407-437 unless Task 1 shifted line numbers — locate by signature). Current:

```python
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
        job.status = "failed"
        job.error = str(e)
    db.commit()
```

Replace with:

```python
async def _run_chunk_job(db, job, provider_config: dict, provider_name: str, language: str,
                          local_provider_lock: asyncio.Semaphore) -> None:
    job.status = "running"
    job.attempts += 1
    db.commit()
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
                result = await provider.transcribe(job.audio_path, language=language, temperature=0.0)
        else:
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
        job.status = "failed"
        job.error = str(e)
    db.commit()
```

Now update `_process_transcript_jobs` (added in Task 1) to accept and thread through the same lock. Two separate edits to this one function — its signature line, and its dispatch block further down (unchanged in between). Current signature line:

```python
async def _process_transcript_jobs(db, transcript_id: int, jobs: list, diarization_service) -> None:
```

Current dispatch block (further down in the same function body, unchanged from Task 1):
```python
    if dispatched:
        # All dispatched jobs share the single `db` session opened at the top of
        # this tick — safe only because _run_chunk_job commits before its await
        # point (see the safety invariant comment on _run_chunk_job itself).
        await asyncio.gather(*[
            _run_chunk_job(db, job, provider_config, transcript.provider, transcript.language)
            for job in dispatched
        ])
```

Replace the signature line with:
```python
async def _process_transcript_jobs(db, transcript_id: int, jobs: list, diarization_service,
                                    local_provider_lock: asyncio.Semaphore) -> None:
```

Replace the dispatch block with:
```python
    if dispatched:
        # All dispatched jobs share the single `db` session opened at the top of
        # this tick — safe only because _run_chunk_job commits before its await
        # point (see the safety invariant comment on _run_chunk_job itself).
        await asyncio.gather(*[
            _run_chunk_job(db, job, provider_config, transcript.provider, transcript.language, local_provider_lock)
            for job in dispatched
        ])
```

Now update `queue_worker_tick` (as it exists after Task 1) to create the semaphore and pass it down. Current:
```python
        transcript_ids = list(by_transcript.keys())
        results = await asyncio.gather(
            *[
                _process_transcript_jobs(db, transcript_id, jobs, diarization_service)
                for transcript_id, jobs in by_transcript.items()
            ],
            return_exceptions=True,
        )
```

Replace with:
```python
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
```

- [ ] **Step 4: Update the local-provider backend comments and run all tests to verify they pass**

In `backends/moonshine.py`, current (lines 15-19):
```python
# Process-wide transcriber cache. get_provider() constructs a fresh provider
# instance per call (one per chunk job in the queue), and a Moonshine model
# load is multi-second + multi-GB — without this, every chunk of a chunked
# local run would reload the model. Safe because local chunk dispatch is
# serialized (see services/queue.py concurrency rule for local providers).
```

Replace with:
```python
# Process-wide transcriber cache. get_provider() constructs a fresh provider
# instance per call (one per chunk job in the queue), and a Moonshine model
# load is multi-second + multi-GB — without this, every chunk of a chunked
# local run would reload the model. Safe because services/queue.py's
# per-tick local_provider_lock (an asyncio.Semaphore(1)) guarantees at most
# one transcribe() call is ever in flight across ALL local-provider chunk
# jobs, across every transcript, at a time.
```

In `backends/builtin.py`, current (lines 85-88):
```python
        # Process-wide cache — get_provider() makes a fresh provider per chunk
        # job, and a WhisperModel load is expensive. Keyed by everything that
        # changes the loaded artifact. Serial local dispatch (services/queue.py)
        # keeps the shared instance uncontended.
```

Replace with:
```python
        # Process-wide cache — get_provider() makes a fresh provider per chunk
        # job, and a WhisperModel load is expensive. Keyed by everything that
        # changes the loaded artifact. services/queue.py's per-tick
        # local_provider_lock (an asyncio.Semaphore(1)) keeps the shared
        # instance uncontended across every transcript, not just this one.
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_queue_cross_transcript_concurrency.py -v`
Expected: all three tests **PASS**, including `test_local_provider_cap_of_one_holds_globally_across_transcripts`.

Run: `.venv\Scripts\python.exe -m pytest tests/test_local_chunking.py -v`
Expected: all existing tests **PASS** unmodified (the new `local_provider_lock` parameter is threaded through everywhere `_run_chunk_job`/`_process_transcript_jobs` are called; `test_local_chunks_dispatch_serially` patches `_run_chunk_job` wholesale with `AsyncMock(side_effect=fake_run)` where `fake_run(db, job, *a, **k)` absorbs the new positional arg via `*a`, so it's unaffected).

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `161 passed` (158 baseline + 3 new tests), plus the 1 pre-existing unrelated failure in `tests/test_voice_id.py::test_embed_speechbrain_caches_classifier_across_calls` (`ModuleNotFoundError: No module named 'torch'` — confirmed pre-existing and unrelated to this change before starting this plan).

- [ ] **Step 5: Commit**

```bash
git add services/queue.py backends/moonshine.py backends/builtin.py tests/test_queue_cross_transcript_concurrency.py
git commit -m "fix: enforce local-provider concurrency cap of 1 globally via a per-tick semaphore"
```

---

## Final verification (after both tasks)

- [ ] Run the full backend suite: `.venv\Scripts\python.exe -m pytest -q` — expect `161 passed`, same single pre-existing `test_voice_id.py` failure as the pre-plan baseline, no new failures or new warnings.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/test_queue_cross_transcript_concurrency.py tests/test_local_chunking.py -v` one more time in isolation to confirm no cross-test interaction (e.g. leftover `_TRANSCRIBER_CACHE`/`_MODEL_CACHE` state) affects the new tests.
- [ ] Re-read the final `services/queue.py` `queue_worker_tick` + `_process_transcript_jobs` + `_run_chunk_job` together and confirm: (a) every mutation still commits before its next await, (b) `return_exceptions=True` is present on both `asyncio.gather` calls in `queue_worker_tick`, (c) `local_provider_lock` is created once per tick and passed to every `_process_transcript_jobs` call in that tick.
- [ ] Comment on GitHub issue #14 noting item 1 (cross-transcript chunk parallelism) is resolved, referencing the two commits, and that items 2-4 (split `_MAX_CONCURRENT_JOBS` pools, `enqueue_llm_job` dedupe race, `LlmJob` auto-retry) remain open — after user confirmation, since commenting on shared tracker state should be confirmed, not assumed.
