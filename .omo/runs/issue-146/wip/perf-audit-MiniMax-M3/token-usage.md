# Token usage — issue #146

## Where tokens went

The bulk of the token spend was in the **Phase 1 investigation**, not
the implementation. Specifically:

1. **Discovering the static-serving structure** without `codegraph`
   available. The worktree at `../whisperdesk-146-ab` is not
   codegraph-indexed (no `.codegraph/`), so the typical
   `codegraph_explore → source` shortcut was unavailable. I had to
   use `grep` + `Read` to find:
   - `app.py:2377` — the `/static` mount
   - `app.py:192-203` — the `static_cache_headers` middleware (#140)
   - `app.py:568-571` — the `/favicon.ico` 204 endpoint
   - `static/rack.js:222-254` — the `api()` fetch wrapper
   - `static/rack.js:4375` — the `DOMContentLoaded` init point
   - `static/index.html:10,138` — the static asset references
   - `tests/test_static_cache.py` — the test pattern to mirror
   This is ~8 separate file-lookups. With codegraph it would have
   been 1 call returning all of the above.

2. **Realizing the issue's own suggested fix has a scope bug** (the
   `/static/sw.js` → scope `/static/` issue). The mistake is obvious
   once you know the Service Worker spec, but the issue body
   confidently presents the wrong snippet as the answer. Cost: an
   extra Read pass to confirm StaticFiles-served /static/sw.js would
   only have scope /static/.

3. **Static source-level check (per AGENTS.md testing tiers)**. The
   `TestClient` invocation of `app.app` is the cheapest live check
   available, and I ran it twice — once to verify the headers, once
   to verify the body. Together this is ~50 lines of context that
   could be replaced with one more test in `test_service_worker.py`
   if we wanted a strict "tests-only" pass.

## What would cut it next time

1. **Index the worktree with codegraph before investigating** — the
   project lives in `C:\Claude\whisperdesk-146-ab`, the *index* is
   in `C:\Claude\whisperdesk`. A `codegraph init` in the worktree
   (or a re-link from the master worktree) would have made Phase 1
   a single codegraph call returning the static mount, the cache
   middleware, the favicon route, and the rack.js api() wrapper.
   Estimated saving: ~2k tokens of investigation.

2. **Issue-body preview before fetching** — `gh issue view 146
   --json body` (which I ran) returns the body in one go, but I
   should also have grepped the issue body for `#\d+` cross-refs
   before assuming the "see #138" claim was true. It would have
   caught the stale cross-ref without a second `gh issue view` call.

3. **Run the new tests in the same pass as the static-cache tests**
   from the start — I split them into two runs, but they share
   fixtures. A single `pytest tests/test_static_cache.py
   tests/test_service_worker.py` would have given the same evidence
   with one fewer shell round-trip.

## What was NOT expensive

- The actual implementation (3 files edited, 1 created, 1 test file
  added) was small. Rack.js got 12 lines, app.py got 29, sw.js is
  115 lines, the test is 60 lines. The "writing the code" half of
  the task was not the bottleneck.
- No background agents were fired. The issue scope was small and
  bounded (3 files + 1 test), direct reads were faster than
  dispatching an explore agent, and the local-agent cap (2) was
  never at risk.
- No e2e run. AGENTS.md reserves `e2e-regression-http` for changes
  that touch request/response contracts or cross-feature flow; this
  change is additive and orthogonal to the API contract (it only
  changes caching behavior on the client). The static `TestClient`
  contract check is the right tier for this change.
