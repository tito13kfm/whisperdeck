# Split Concurrent LLM-Job Pools by Resource Type Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single shared `_MAX_CONCURRENT_JOBS` cap in `services/llm_jobs.py` with two independent caps — one for I/O-bound job kinds (`correction`, `summary`) and one for CPU-bound job kinds (`rediarize`, `voice_match`) — so a full CPU pool can no longer stall I/O dispatch and vice versa.

**Architecture:** `llm_worker_tick` currently runs one "count running → compute slots → claim pending" pass against a single global cap. This plan changes that to two independent passes, one per kind-group, each with its own cap and its own `running`/`slots`/claim query — the claim-then-commit-before-await shape is preserved exactly, just repeated per pool instead of run once globally. No architectural change: same function, same invariants, same dispatch loop, only the cap becomes per-resource-type.

**Tech Stack:** Python, SQLAlchemy (SQLite), asyncio, pytest.

## Global Constraints

- Run tests with this project's virtualenv: `.venv\Scripts\python.exe -m pytest <args>` (per `tests/conftest.py`'s interpreter guard).
- Preserve the existing "claim lands before any await" invariant: all `status = "running"` writes and the single `db.commit()` must happen before `db.close()` and before `asyncio.gather` starts awaiting `run_llm_job`. Do not commit per-pool — one claim-then-commit for both pools combined, exactly like today.
- `_MAX_CONCURRENT_JOBS` is referenced nowhere outside `services/llm_jobs.py` (confirmed via repo-wide grep) — this is a single-file change, no `app.py` or frontend changes needed.
- Default cap values (justified, not left open): `_MAX_CONCURRENT_IO_JOBS = 2` (unchanged from today's shared cap — `correction`/`summary` are provider API calls, bounded by provider rate limits rather than local resources) and `_MAX_CONCURRENT_CPU_JOBS = 1` (`rediarize`/`voice_match` are local CPU-bound compute — diarization clustering / embedding extraction — kept to one at a time so a rediarize job doesn't compete with itself or a voice-match job for the same CPU). The total concurrent-job ceiling rises from 2 to 3 as an intended consequence: an I/O job and a CPU job are meant to run at the same time now, which is the entire point of the split.

---

### Task 1: Split the cap into IO/CPU pools in `llm_worker_tick`

**Files:**
- Modify: `services/llm_jobs.py:16-20` (constants block), `services/llm_jobs.py:332-356` (`llm_worker_tick`)
- Test: `tests/test_llm_jobs.py` (append)

**Interfaces:**
- Produces: `IO_KINDS`, `CPU_KINDS` (tuples partitioning `VALID_KINDS`), `_MAX_CONCURRENT_IO_JOBS`, `_MAX_CONCURRENT_CPU_JOBS` — consumed only by `llm_worker_tick` in this same task (no later task depends on them, so nothing here is front-loaded).
- Removes: `_MAX_CONCURRENT_JOBS` (replaced in the same commit as its only call site).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_jobs.py` (the file already imports `enqueue_llm_job`, `llm_worker_tick`, `AsyncMock`, `patch`, `asyncio`, `_make_user_and_transcript`, `_FakeResponse`, `_NoCloseSession` at the top — no new imports needed except `TranscriptionService`, which is already imported ad hoc inside `test_run_llm_job_summary_saves_result_snapshot`):

```python
def test_io_cpu_pools_partition_valid_kinds():
    """Sanity check: every valid job kind must belong to exactly one pool,
    so a future kind addition can't silently fall through uncapped."""
    from services.llm_jobs import IO_KINDS, CPU_KINDS, VALID_KINDS
    assert set(IO_KINDS) | set(CPU_KINDS) == set(VALID_KINDS)
    assert set(IO_KINDS) & set(CPU_KINDS) == set()


def test_worker_tick_io_cap_limits_dispatch(db_session, tmp_path):
    """IO pool cap defaults to 2 — one already-running IO job plus one
    freshly claimed IO job fill it; a third pending IO job must wait."""
    from services.transcription import TranscriptionService

    user1, t1 = _make_user_and_transcript(db_session, username="ioq1")
    running = enqueue_llm_job(db_session, user1.id, t1.id, "correction", "groq", "m")
    running.status = "running"
    db_session.commit()

    user2, t2 = _make_user_and_transcript(db_session, username="ioq2")
    claimable = enqueue_llm_job(db_session, user2.id, t2.id, "summary", "groq", "m1")

    user3, t3 = _make_user_and_transcript(db_session, username="ioq3")
    overflow = enqueue_llm_job(db_session, user3.id, t3.id, "correction", "groq", "m1")

    factory = lambda: _NoCloseSession(db_session)
    svc = TranscriptionService(str(tmp_path))
    fake_post = AsyncMock(return_value=_FakeResponse(
        '{"short_summary": "s", "key_points": [], "action_items": [], "decisions": []}'
    ))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(llm_worker_tick(factory, transcription_service=svc))

    db_session.refresh(running)
    db_session.refresh(claimable)
    db_session.refresh(overflow)
    assert running.status == "running"      # untouched — already counted against the cap
    assert claimable.status == "completed"  # 2nd IO slot — claimed and ran
    assert overflow.status == "pending"     # 3rd IO job — cap (2) already full, must wait


def test_worker_tick_cpu_cap_limits_dispatch(db_session):
    """CPU pool cap defaults to 1 — a second pending CPU-kind job must wait
    even though the IO pool is completely empty."""
    user1, t1 = _make_user_and_transcript(db_session, username="cpuq1")
    claimable = enqueue_llm_job(db_session, user1.id, t1.id, "rediarize", "", "")

    user2, t2 = _make_user_and_transcript(db_session, username="cpuq2")
    overflow = enqueue_llm_job(db_session, user2.id, t2.id, "rediarize", "", "")

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None, diarization_service=None))

    db_session.refresh(claimable)
    db_session.refresh(overflow)
    # claimed (dispatched) even though diarization_service is unavailable —
    # it fails fast rather than being left pending, which is what proves it
    # was picked up by the claim query.
    assert claimable.status == "failed"
    assert claimable.error == "Diarization service unavailable"
    assert overflow.status == "pending"  # CPU cap (1) already full, must wait


def test_worker_tick_full_io_pool_does_not_block_cpu_dispatch(db_session):
    """The actual point of the split: two running IO-kind jobs — deliberately
    equal to the OLD shared global cap of 2 — must not stall a pending
    CPU-kind job. Under the pre-split shared cap, running=2 >= cap=2 would
    have produced slots=0 for every kind, including this rediarize job."""
    user1, t1 = _make_user_and_transcript(db_session, username="mixq1")
    io_a = enqueue_llm_job(db_session, user1.id, t1.id, "correction", "groq", "m")
    io_a.status = "running"

    user2, t2 = _make_user_and_transcript(db_session, username="mixq2")
    io_b = enqueue_llm_job(db_session, user2.id, t2.id, "correction", "groq", "m")
    io_b.status = "running"
    db_session.commit()

    user3, t3 = _make_user_and_transcript(db_session, username="mixq3")
    cpu_job = enqueue_llm_job(db_session, user3.id, t3.id, "rediarize", "", "")

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None, diarization_service=None))

    db_session.refresh(cpu_job)
    assert cpu_job.status == "failed"  # dispatched then failed fast — not left pending behind the full IO pool


def test_worker_tick_full_cpu_pool_does_not_block_io_dispatch(db_session):
    """Reverse direction: two running CPU-kind jobs — deliberately equal to
    the OLD shared global cap of 2, not just the new CPU cap of 1 — must not
    stall a pending IO-kind job."""
    user1, t1 = _make_user_and_transcript(db_session, username="mixq4")
    cpu_a = enqueue_llm_job(db_session, user1.id, t1.id, "rediarize", "", "")
    cpu_a.status = "running"

    user2, t2 = _make_user_and_transcript(db_session, username="mixq5")
    cpu_b = enqueue_llm_job(db_session, user2.id, t2.id, "voice_match", "", "")
    cpu_b.status = "running"
    db_session.commit()

    user3, t3 = _make_user_and_transcript(db_session, username="mixq6")
    io_job = enqueue_llm_job(db_session, user3.id, t3.id, "correction", "groq", "m")

    factory = lambda: _NoCloseSession(db_session)
    fake_post = AsyncMock(return_value=_FakeResponse("S: fixed"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(io_job)
    assert io_job.status == "completed"  # dispatched despite the full CPU pool
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v -k "io_cpu_pools or cap_limits_dispatch or does_not_block"`

Expected, per test, against the still-unmodified shared-cap code:
- `test_io_cpu_pools_partition_valid_kinds` — FAIL with `ImportError: cannot import name 'IO_KINDS'` (neither `IO_KINDS` nor `CPU_KINDS` exist yet).
- `test_worker_tick_io_cap_limits_dispatch` — PASSES even before the change. This scenario only contains IO-kind jobs, and the old shared cap is also 2, so the old single-pool query (running=1, slots=1, claims the lowest-id pending job across all kinds) happens to produce the identical outcome. It is not a regression check by itself — kept as a direct pin of "IO pool cap = 2" behavior against the new code, with the actual regression coverage carried by the next three tests.
- `test_worker_tick_cpu_cap_limits_dispatch` — FAIL. Old code has no per-kind filter: running=0, shared slots=`max(0,2-0)=2`, and it claims both pending rediarize jobs (only 2 exist) instead of just 1. The assertion `overflow.status == "pending"` fails (actual: `"failed"`).
- `test_worker_tick_full_io_pool_does_not_block_cpu_dispatch` — FAIL. Old code: running=2 (both correction jobs, counted globally), shared slots=`max(0,2-2)=0`, nothing is claimed. The assertion `cpu_job.status == "failed"` fails (actual: `"pending"`).
- `test_worker_tick_full_cpu_pool_does_not_block_io_dispatch` — FAIL. Old code: running=2 (both CPU jobs, counted globally), shared slots=`max(0,2-2)=0`, nothing is claimed. The assertion `io_job.status == "completed"` fails (actual: `"pending"`).

- [ ] **Step 3: Implement the pool split**

In `services/llm_jobs.py`, replace lines 16-20:

```python
ACTIVE_STATUSES = ("pending", "running")
TERMINAL_LLM_STATUSES = ("completed", "failed", "cancelled")
_MAX_CONCURRENT_JOBS = 2

VALID_KINDS = ("correction", "summary", "rediarize", "voice_match")
```

with:

```python
ACTIVE_STATUSES = ("pending", "running")
TERMINAL_LLM_STATUSES = ("completed", "failed", "cancelled")

VALID_KINDS = ("correction", "summary", "rediarize", "voice_match")
# Two independent concurrency pools, capped separately (issue #14): I/O-bound
# kinds are provider API calls (bounded by provider rate limits, not local
# resources), CPU-bound kinds are local compute (diarization clustering /
# embedding extraction) and stay small so they don't fight each other for
# the same CPU. IO_KINDS/CPU_KINDS must partition VALID_KINDS exactly — see
# test_io_cpu_pools_partition_valid_kinds.
IO_KINDS = ("correction", "summary")
CPU_KINDS = ("rediarize", "voice_match")
_MAX_CONCURRENT_IO_JOBS = 2
_MAX_CONCURRENT_CPU_JOBS = 1
```

Then replace the body of `llm_worker_tick` (lines 332-356):

```python
async def llm_worker_tick(SessionLocal, transcription_service, diarization_service=None) -> None:
    db = SessionLocal()
    try:
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
            job.updated_at = utcnow_naive()
        db.commit()  # claim lands before any await — same invariant as the chunk queue
        job_ids = [job.id for job in claimed]
    finally:
        db.close()

    await asyncio.gather(*(run_llm_job(SessionLocal, jid, transcription_service, diarization_service) for jid in job_ids))
```

Note what did **not** change: `run_llm_job`, `llm_worker_loop`, the claim-then-single-commit shape, and the final `asyncio.gather` line are byte-for-byte identical to before — only the middle of the `try` block (previously a single count/slots/claim) now loops that same count/slots/claim logic once per pool and concatenates the results into one `claimed` list before the single shared commit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_llm_jobs.py -v`
Expected: PASS (all pre-existing tests in this file plus the 5 new ones — `test_worker_tick_claims_pending_jobs` in particular must still pass unchanged, since a single pending correction job with nothing else running still gets claimed under the new IO-pool logic).

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (no other file references `_MAX_CONCURRENT_JOBS`, `IO_KINDS`, or `CPU_KINDS`, so this should be isolated to `tests/test_llm_jobs.py`).

- [ ] **Step 6: Commit**

```bash
git add services/llm_jobs.py tests/test_llm_jobs.py
git commit -m "feat: split LLM job concurrency cap into IO and CPU pools"
```
