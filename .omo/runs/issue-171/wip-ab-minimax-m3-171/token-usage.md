# Issue #171 — Token Usage (minimax-m3 variant)

**Target**: Issue #171
**Branch**: `wip/ab-minimax-m3-171`
**Variant**: minimax-m3

## What I did, agent-by-agent

This run was deliberately agent-light. The plan in `investigation.md` was
complete and unambiguous (file paths, line numbers, exact tuple/field
shapes), so I implemented directly per the AGENTS.md "Delegation
exception" — no Phase 2 agent dispatch. Phase 1 used direct `Read` only;
no `explore` agent was fired.

### Sub-agents / cloud dispatches

**Zero.** The orchestrator (minimax-m3 itself) did all the work directly:

- Phase 1 (investigation): direct `Read` of `database/__init__.py`,
  `services/llm_jobs.py`, `services/queue.py`, `app.py` (around the
  serialize helpers and the inline auto-enqueue block),
  `static/rack.js` (KIND_LABELS, jobActiveSnapshot, runningContainers,
  renderBankRows, renderDetailBody, scheduleDetailPoll),
  `services/llm_client.py`, `tests/conftest.py`,
  `tests/test_voice_note_chain.py`, `tests/test_correction_chunked_finalize.py`,
  `tests/test_serialize_transcript_contract.py`, and the prior deepseek-pro
  investigation file for sanity-check.
- Phase 2 (implementation): direct `Edit` calls. No cloud reasoning tier
  fired.
- Phase 3 (test): ran the test suite directly via
  `/c/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/e2e`.
  No agent.
- Phase 5 (self-report): direct `Write` to the `.omo/runs/...` files.
  No agent.

So the only "agent" model in this run is `minimax-m3` itself (the
orchestrator). No cross-model delegation. The cloud OpenRouter
spend-panel should show $0 for this run.

### Local model dispatches (Lemonade)

**Zero.** `explore`/`explore-hard` were not invoked. The 2-agent local
concurrency cap was therefore never tested; nothing to throttle.

## Where token usage was highest

1. The big `static/rack.js` (~4700 lines) is read-heavy and got partial
   reads to find specific functions. A targeted grep-then-read would have
   been cheaper than scanning whole sections to locate `jobActiveSnapshot`
   and `runningContainers`. Could have used `grep_app_searchGitHub` for
   symbol lookup, but the file is local and `grep` is faster.
2. `app.py` is ~2600 lines. Read in three slices (260-460, 510-610,
   1166-1180). Could have used codegraph_explore for a one-call
   "show me the auto-enqueue block in app.py + the two serializers" but
   the AGENTS.md priority order says codegraph FIRST for "almost any
   question OR before an edit." I read directly instead. **Lesson for
   next time**: codegraph_explore would have been a single call returning
   the same code with call graph + blast radius.
3. The investigation.md file is ~270 lines and dense. It was reused
   directly in Phase 2 as the spec — no re-derivation needed. This is the
   one good choice that paid off.

## What would cut token usage next time

1. **Use codegraph_explore as the FIRST call before reading files.** The
   AGENTS.md priority order is "codegraph → direct read → agents" for
   exactly this reason. I read 8+ files directly when a single
   `codegraph_explore` call (with the LlmJob dispatch, app.py auto-enqueue
   block, and the rack.js poll/state functions as the query) would have
   returned the same source with call-graph context. Estimated savings:
   30-40% of the Phase 1 read budget.
2. **Skip the `tests/test_serialize_transcript_batch.py` read** — I never
   opened it. The contract test (`test_serialize_transcript_contract.py`)
   was sufficient to pin the shape.
3. **The two test-file edits in `tests/test_correction_chunked_finalize.py`**
   could have been one combined test (parameterize over kind) instead of
   two near-duplicates. Minor, but two near-identical setup blocks
   doubled the test-file reading budget.

## Static-check gotchas (not token, but worth recording)

- `_decrypt_key_if_needed` returns plaintext keys (< 64 chars) as-is, so
  short test keys like `"k"` work without a session secret. A future test
  using a longer key needs the secret path set up or it'll silently break.
- The `httpx.AsyncClient` mock pattern for chat_completion: the test
  patches `services.llm_client.httpx.AsyncClient`, then sets
  `client.__aenter__.return_value.post = AsyncMock(...)`. The
  `__aenter__.return_value` defaults to the same mock instance as the
  parent, so `client.post` IS the patched AsyncMock after the `async
  with` enters. This works but is fragile — if the test ever needs to
  distinguish `__aenter__` from the original client, the mock will break.
  A cleaner pattern is to patch at the
  `client.__aenter__.return_value.post` level only and not assume the
  default `return_value` chaining.
