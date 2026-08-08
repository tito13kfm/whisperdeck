## PR Audit: #280 worktree-esp32-voice-capture-spec (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)

### Should fix          (empty = none)

### Nits
- No correctness nits found in the reviewed device-token flow.

### Honesty check
- No `self-audit.md` or `investigation.md` artifact exists for PR #280, so there is no self-report to reconcile. The PR body and diff are consistent for the reviewed scope.
- No vacuous test issue found in the added token tests. They exercise issuance, hashing, lookup, revocation, route scoping, CSRF boundaries, rate limiting, and session-versus-bearer precedence.
- No undisclosed production scope found. The diff contains the device-token backend, `/api/transcribe` bearer opt-in, narrowly scoped CSRF exemption, device-upload rate limiting, settings UI, minified asset update, tests, and the related planning documents.

### Read scope
- Reviewed the full diff against the correct base `d6e8092`, including `app.py`, `database/__init__.py`, `services/auth.py`, `static/rack.js`, `static/rack.min.js`, and both new test modules.
- Traced startup migration and additive user-column creation, session-first/bearer-fallback authentication, the `/api/transcribe` route, device rate-limit state, settings-token routes, CSRF middleware, and all device-token call sites.
- Confirmed the source and shipped minified frontend both contain the device-token settings flow. `node --check static/rack.min.js` passed and `git diff --check` passed.

### Verification
- Prior full-suite run in this audit: 738 passed, 8 deselected, 1 Starlette deprecation warning.
- A second focused pytest invocation was unavailable in the current shell because `pytest` is not on PATH. This does not contradict the recorded full-suite result.
- No live browser flow was run in this audit. Frontend behavior is therefore source-checked, not browser-verified.

### Summary
PR #280 correctly adds one per-user, hashed device bearer token and permits it only on `/api/transcribe`. Session authentication remains preferred, bearer authentication is opt-in, settings routes remain session-only, and the CSRF exemption is limited to the sole route that honors the bearer token. Device uploads receive a separate 30-per-hour limiter while normal session uploads do not. Regeneration and revocation invalidate prior plaintext tokens through the stored hash. The migration is additive and idempotent, and the source/minified settings UI paths are aligned. No blocking correctness or security regression was found.
