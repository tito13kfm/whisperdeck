# Investigation: #314 — No retroactive classify_intent trigger when auto-classification resolves to dictation

## Symptom
When `kind="auto"` upload is auto-classified as `dictation`, no `classify_intent` (dictation reformat hint) job is enqueued. User picks dictation up front → hint works. Classifier picks dictation → hint silently missing.

## Scope / complement sweep
- `CLASSIFICATION_KINDS` = ("meeting", "dictation", "voice_note") — `services/classification.py:20`.
- `voice_dump` is a separate explicit kind; not in CLASSIFICATION_KINDS, not a classification output.
- Post-transcription dispatch `enqueue_post_transcription_jobs` (`services/llm_jobs.py:355`) runs: correction-or-classify, `enqueue_auto_classify` (dictation-gated via effective_kind), voice_note/voice_dump conditionals, tagging. For `auto` uploads this sees `effective_kind()==None`, so `enqueue_auto_classify` no-ops — correct, must wait for classification.
- Retroactive dispatch in `run_llm_job` classify_pipeline branch (`services/llm_jobs.py:665`) had:
  - `voice_note` → `enqueue_auto_voice_note`
  - `voice_dump` → `enqueue_auto_voice_dump`
  - Missing: `dictation` → `enqueue_auto_classify` (classify_intent).
- Other jobs in `enqueue_post_transcription_jobs`: `enqueue_auto_correction` is pre-classification (runs before classify_pipeline), `enqueue_auto_tagging` is kind-agnostic and runs at transcription completion, not retroactively. Neither needs a retroactive trigger. `meeting` classification correctly does nothing extra.

All dispatch sites checked: `app.py:_run_transcription_pipeline` and `services/queue.py:_finalize_if_done` both call `enqueue_post_transcription_jobs` (single shared helper). No other retroactive call sites.

## Root cause
The classify_pipeline "accepted" branch was written to handle voice_note/voice_dump retroactively but omitted the dictation→classify_intent case. `enqueue_auto_classify` correctly gates on `effective_kind()==dictation`, but was never invoked after classification updated `transcript.kind` to dictation.

## Fix
In `services/llm_jobs.py` classify_pipeline accepted branch, add:
```py
if accepted and result["kind"] == "dictation":
    from services.settings import get_user_settings
    enqueue_auto_classify(db, transcript, get_user_settings(db, job.user_id))
```
Placed before the voice_note/voice_dump triggers. Runs after `transcript.kind` and `classification_status` are committed so `effective_kind()` reflects the new state. `enqueue_llm_job` dedupes on active transcript+kind, so no duplicate if somehow already present. Placed before `_finish(completed)`.

## Tests
- Added `test_run_llm_job_classify_pipeline_accepted_dictation_retroactively_enqueues_classify_intent` — asserts classify_intent row appears.
- Added mutation guard `test_run_llm_job_classify_pipeline_accepted_dictation_mutation_kills_classify_intent` — patching `enqueue_auto_classify` to noop yields zero rows.
- Existing 68 test_llm_jobs + 91 classification/reformatting/guard tests still pass.

## Verification
- `python -m pytest tests/test_llm_jobs.py` — 68 passed.
- `python -m pytest tests/test_pending_classification_guards.py tests/test_classification.py tests/test_reformatting.py` — 91 passed.

## Sib sweep result
No other post-classification job missing. See scope section.
