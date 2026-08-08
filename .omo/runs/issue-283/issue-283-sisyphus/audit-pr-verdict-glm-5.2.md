## PR Audit: #288 feat: add voice_dump kind plumbing and VoiceDumpItem table (#283)   (reviewer: openai/gpt-5.6-luna, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)
- None.

### Should fix
- [feature] `tests/test_serialize_transcript_contract.py:63` does not include a `voice_dump` transcript in the uniform serializer-field test. Failure scenario: a future kind-gating change can return the wrong job-field shape specifically for `voice_dump` while the current meeting, dictation, and voice-note fixtures remain green. Fix: add a `voice_dump` fixture and assert its `voice_dump_job` value and common key set.

### Nits                (empty = none)
- None.

### Honesty check
- self-audit.md [x] lines verified: 25/25. False [x] found: none.
- Vacuous / loosened tests: none.
- Undisclosed scope (diff vs claims): none. The extra retranscribe validation update is disclosed in the self-report and is correct.

### Read scope
- Focused read on the changed Python files, the changed serializer test, the relevant frontend hunks, their callers and kind-switch siblings, plus the issue and self-report artifacts. `static/rack.js` was not read start-to-finish because it is a large file.

### Verification
- Full worktree suite: 745 passed, 8 deselected, 1 warning.
- Claimed focused tests: 97 passed, 1 warning.
- Database smoke check: two `VoiceDumpItem` rows persisted for one transcript.
- `git diff --check`: clean.
- Frontend browser behavior was not driven in this audit; the changed UI is limited to the two declared kind-picker options, and no self-report claims browser verification.

### Summary
The PR correctly implements the requested schema and kind plumbing without prematurely implementing the deferred voice-dump job chain. Complementary validation sites, diarization forcing, serializer shape, LLM kind partitioning, and generated frontend artifacts were checked. The only recommendation is to extend the serializer contract fixture to cover the new kind directly.

Verdict: APPROVE. 0 blocking, 1 should-fix, 0 nits. Honesty: 0 false claims, 0 vacuous tests.
