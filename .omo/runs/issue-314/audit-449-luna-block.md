# Audit follow-up — PR #449 BLOCK (GPT-5.6 Luna)

Source: tito13kfm comment IC_kwDOTLF4Cs8AAAABRg83RQ on PR #449.

## Finding 1 — BLOCK — Cancel race leaking a child job
Location: services/llm_jobs.py:671-680 (pre-fix).
Failure: classify_pipeline branch committed the follow-up enqueues (dictation/voice_note/voice_dump) before calling _finish(). A cancel that landed after the classification-state commit but before _finish() would still leave a newly committed child job queued while the parent ended cancelled (the same class of race fixed around PR #389).
Fix taken: move _finish() before any enqueue, guarding them with `if not _finish(db, job, "completed"): return`. Now a raced cancel makes _finish() lose its compare-and-set (status IN active), roll back any pending writes, and return False — enqueues never run. Same pattern as the correction branch at llm_jobs.py:545-561. Applied to all three arms (dictation + the two pre-existing voice_note/voice_dump sibs).

## Finding 2 — BLOCK — Tautological mutation guard
Location: tests/test_llm_jobs.py:920-943 (pre-fix).
Failure: the test patched enqueue_auto_classify to a no-op and asserted no row exists, which passes even if the new dispatch call is deleted from run_llm_job.
Fix taken: replaced with the auditor's prescribed regression — test_run_llm_job_classify_pipeline_cancel_suppresses_dictation_follow_on — which races a cancel into _finish (patching _finish to mark the job cancelled before the real compare-and-set runs), then asserts the parent is cancelled and no classify_intent row exists. Removal detection is now owned by the unpatched positive test (test_run_llm_job_classify_pipeline_accepted_dictation_retroactively_enqueues_classify_intent), which would fail if the dictation arm were deleted.

## Evidence
- python -m pytest tests/test_llm_jobs.py -k classify_pipeline -q → 8 passed
- python -m pytest tests/test_llm_jobs.py -q → 68 passed
