## PR Audit: #336 fix(voice_id): report the MFCC fallback instead of silently matching nothing (#109)   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: BLOCK

### Blocking
- `app.py:2449-2452`, `services/voice_id.py:104-111` The transcript enrollment path creates a new profile with the selected strong backend's `embedding_model` before calling `add_clip()`. If extraction falls back to MFCC, `_ensure_not_orphan_model()` sees that same newly created profile as a different-model profile and rejects the clip, even when the roster was empty. Failure scenario: on a speechbrain install, enroll a new speaker from marked transcript clips while speechbrain fails but librosa succeeds, and the request returns 400, deletes the new profile, and stores no clip instead of completing with the warning promised by the PR. Fix: do not pre-seed a newly created profile with the strong backend model, or exclude the target profile from `_ensure_not_orphan_model()` and let the actual clip model establish it. Regression test: patch `add_clip` extraction to return `(np.array([...]), MFCC_MODEL_ID)` with `_backend = "speechbrain"`, POST `/api/transcripts/{id}/enroll-speaker` for a new name and empty roster, then assert 200, a non-null MFCC warning, and one persisted clip.

### Should fix
- [feature] `app.py:3359-3364` A normal rejected enrollment now raises `ValueError` from `_ensure_not_orphan_model()`, but this route still catches it under `except Exception` and returns HTTP 500. Failure scenario: an existing profile uses speechbrain, a new `/api/voices/enroll` sample falls back to MFCC, and the user receives a server-error response for an expected validation refusal. Fix: catch `ValueError` separately and return HTTP 400, matching the add-clip and transcript-enrollment routes.

### Nits
- `services/llm_jobs.py:752` The broad per-segment `except Exception` also covers the new detailed-outcome field access, so an internal `KeyError` or malformed outcome is reported as an extraction skip. This is intentional tolerance inherited from the old path, but it can mislabel programming errors.
- The changed frontend warning toast and identify rendering were not browser-driven in the available evidence. The JavaScript suite passed, but its 25 tests do not exercise these voice controls.

### Honesty check
- self-audit.md [x] lines verified: 0/0. False [x] found: no self-audit.md artifact was present in the expected main-checkout run directory.
- Vacuous / loosened tests: none found in the touched tests. The new backend tests assert the relevant booleans, counts, exact empty match lists, and MFCC warning content.
- Undisclosed scope (diff vs claims): the added transcript-enrollment behavior is not safe on the MFCC fallback because of the pre-seeded model field described above. No self-report artifact was available to reconcile.

### Read scope
- Focused read on `app.py`, `services/voice_id.py`, `services/llm_jobs.py`, `static/rack.js`, and the touched voice/job/relabel tests. The total diff was 522 changed lines, so this was below the large-diff cost guard, but the minified bundle and source map were not read start-to-finish.

### Summary
The core identify and voice-match reporting path is covered by passing tests, and the touched Python tests passed, with the full available suite reporting 836 passed and 22 deselected. The PR still blocks because transcript-based enrollment pre-seeds the new profile with the wrong model, causing every new-profile MFCC fallback enrollment to fail before the warning can be returned.
