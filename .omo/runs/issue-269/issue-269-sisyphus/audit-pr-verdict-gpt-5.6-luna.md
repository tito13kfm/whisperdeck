## PR Audit: #281 feat(ui): default mode to Auto, show classification status   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: BLOCK

### Blocking
- `tests/test_serialize_transcript_contract.py:148` The self-report marks `test_auto_kind_pending_serialization` as a mutation-checked test, but it creates no `LlmJob` row and therefore cannot distinguish the pending/effective-kind serializer branch from a serializer that always returns the fallback null job fields. Failure scenario: `_dictation_job_fields()` regresses to branch on raw `t.kind`, or is replaced with an unconditional fallback, and this test still passes for its `kind="meeting"` fixture. Fix: make this test exercise a pending placeholder with a raw kind that would select a kind-specific branch, add a pending `classify_intent` job, and assert the serialized field is `None` (or rely on the existing stronger fixture and remove the false mutation-check claim). Regression test: create a pending transcript with `kind="dictation"` plus a pending `classify_intent` job, serialize it, and assert `out["classify_intent_job"] is None`.
- `.omo/runs/issue-269/issue-269-sisyphus/self-audit.md:25` The checked `[x]` claim says the mode cell shows classification provenance, but `static/rack.js:4759` renders only the status text (`Classifying…`, confidence, `Manual override`); it never reads or displays `t.classification_provenance`. Failure scenario: an auto-classified transcript has provider/model provenance in the API response, the detail page still omits it, and the claimed provenance UI is absent. Fix: either render the approved provenance data in the mode cell or change the self-report and scope claim to status text only. Regression test: render a detail fixture with `classification_status="success"` and `classification_provenance={provider:"local_llm",model:"m"}`, then assert the mode cell contains the provider/model text.

### Should fix
- None.

### Nits
- Static scan found `asyncio.run()` only in documentation/tests and the pre-existing `services/cost.py` path, not in the changed request path. Existing `except Exception` hits were pre-existing; no newly added swallowing handler was found.

### Honesty check
- self-audit.md [x] lines verified: 17/19. False [x] found: the mutation-check claim for `test_auto_kind_pending_serialization` is vacuous; the mode-cell provenance claim is not implemented.
- Vacuous / loosened tests: `tests/test_serialize_transcript_contract.py:148` as described above. No loosened acceptance-value assertions found.
- Undisclosed scope (diff vs claims): none found.

### Read scope
- Focused read on `app.py`, `static/rack.js`, `static/rack.min.js`, the three changed test files, and called classification/queue helpers. The six-file diff is small, but `static/rack.min.js` was checked by rebuilding from the checked-out `static/rack.js` and comparing bytes.

### Summary
The implementation paths and the checked-out worktree tests are otherwise consistent: the three targeted files passed with 37 tests, and the full suite passed with 723 passed, 8 deselected. This must not merge as reported because one checked `[x]` mutation claim is false and the new serialization test does not prove the behavior it claims to lock down.
