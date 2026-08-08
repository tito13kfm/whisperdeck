## PR Audit: #326 feat(settings): expose the audio cleanup stage in the UI (#317)   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)
- None.

### Should fix          (empty = none)
- None.

### Nits                (empty = none)
- None.

### Honesty check
- self-audit.md [x] lines verified: 42/42. False [x] found: none.
- Vacuous / loosened tests: none. The new threshold assertions use exact equality, the UI coverage tests inspect the registry and committed bundle, and the real-filter test constructs both surviving and removed-segment cases.
- Undisclosed scope (diff vs claims): none. The PR body and self-audit disclose the eleven-control scope, intentional Demucs exclusion, queue mirror-path fix, generated bundle, and lack of a permanent settings e2e test.

### Read scope
- Focused read on the six-file diff, including changed hunks and surrounding `services/queue.py`, `static/rack.js`, `services/settings.py`, `app.py`, `services/audio_cleanup.py`, and the generated bundle. The generated `static/rack.min.js` was checked through the build and key-presence tests rather than read start-to-finish.

### Summary
The UI registry, save path, numeric handling, committed bundle, and chunked hallucination settings fix agree with the stated scope. The touched pytest files pass with 28 tests, the full Python suite passes with 823 passed and 22 deselected, the JavaScript suite passes with 25 tests, and a rebuild produces no bundle diff. Existing `asyncio.run()` and broad exception hits are outside the new path or are guarded appropriately.
