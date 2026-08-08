## PR Audit: #327 fix(voice_match): refuse jobs whose roster no backend can match   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)
- None.

### Should fix          (empty = none)
- None.

### Nits                (empty = none)
- Static scan: `services/cost.py:96` uses `asyncio.run()` only after confirming no event loop is running, so it is safe. The other service hits are documentation or existing code outside this change. `services/llm_jobs.py:757` intentionally catches per-segment failures and reports the skip count; `services/llm_jobs.py:779` logs the outer failure. No new swallowed exception was introduced.

### Honesty check
- self-audit.md [x] lines verified: 27/27. False [x] found: none.
- Vacuous / loosened tests: none. The legacy NULL test forces NULL after insertion and probes with a different model id. The exact model-set assertions use `==`.
- Undisclosed scope (diff vs claims): none.

### Read scope
- Focused read on `services/llm_jobs.py`, `services/voice_id.py`, `tests/test_relabel_undo.py`, `tests/test_voice_id.py`, and `tests/test_voice_match_job.py`, plus the relevant callers and consumers in `app.py`, `database/__init__.py`, `static/rack.js`, `services/cost.py`, and `services/queue.py`.

### Summary
The guard mirrors the actual `identify()` compatibility rules, including legacy falsy model ids and MFCC fallback, while preserving the no-profile path and user-visible error channel. Touched tests passed, the full suite passed with `802 passed, 22 deselected, 1 warning`, and the PR's cited CI run passed on the exact head SHA.

Verdict: APPROVE. 0 blocking, 0 should-fix, 1 nit. Honesty: 0 false claims, 0 vacuous tests.
