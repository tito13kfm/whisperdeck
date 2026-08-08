# Investigation — Issue #125, variant `minimax-m3`

**Real target:** #125 (standalone issue, body does NOT reference a tracking
issue or a Findings Summary table — confirmed via `gh issue view 125
--json body` and direct read).

**Variant label:** `minimax-m3` (the human-supplied token identifying this
orchestrator-model run for the A/B comparison).

## What the issue claims

`app.py:348-353` (register route) does a SELECT-then-INSERT on the User
table. With two concurrent requests for the same username:
1. Both pass the SELECT check.
2. First commits.
3. Second hits the `users.username` UNIQUE constraint, raises
   `sqlalchemy.exc.IntegrityError`, no handler, request returns 500.

## What I found (current `master` / branch base)

### Line numbers in the issue body are stale

The issue body cites `app.py:348-353` and shows the snippet:
```python
if db.query(User).filter(User.username == username).first():
    raise HTTPException(status_code=400, detail="Username already taken")
...
user = create_user(db, username, password)  # calls db.commit()
```

That line range no longer matches. On `master` (HEAD `58906b9`), the
register route is at **`app.py:403-422`**, with the duplicate check at
**`app.py:414-415`** and the `create_user` call at **`app.py:419`**. I
read the file directly to confirm (see "Direct verification" below). The
shape of the bug is the same; the line numbers just drifted. This is a
recurring pattern flagged in the runner prompt — issue bodies in this
tracker have stale line numbers, never implement the suggested fix
verbatim.

### Direct verification (read the files, don't trust the issue)

- `app.py:403-422` — register route (`@app.post("/api/register")`).
  The duplicate check is at `app.py:414-415`. The `create_user` call
  is at `app.py:419`. Session is closed by FastAPI's dependency
  teardown on the `get_db` generator.
- `services/auth.py:44-56` — `create_user`. Does:
  ```python
  is_first = db.query(User).count() == 0
  ...
  db.add(user)
  db.commit()
  ```
  No `try/except`, no `db.rollback()` on failure.
- `services/auth.py:68-75` — `get_or_create_fallback_user`. Calls
  `create_user(db, "local", "changeme")`. The "local" user is created
  only if not already present, then committed. Same race exposure
  (one-shot, only on first migration of a pre-existing DB; very
  unlikely in practice, but a sibling of the same shape).
- `database/__init__.py:17-30` — `User` class. `username = Column(
  String(64), unique=True, nullable=False)` at line 21. The UNIQUE
  constraint is a `unique=True` column-level index, which SQLAlchemy
  translates to `CREATE UNIQUE INDEX` on the column. SQLite enforces
  this index at commit time, raising
  `sqlite3.IntegrityError: UNIQUE constraint failed: users.username`,
  which SQLAlchemy wraps as `sqlalchemy.exc.IntegrityError`.
- `app.py:232-239` — `get_db()`. Yields a fresh `SessionLocal()`
  per request, closed on teardown. This is the per-request session
  path; each request gets its own connection.
- `tests/conftest.py:71-122` — `db_session` and `client` fixtures.
  `db_session` yields a single SQLAlchemy session bound to a per-test
  SQLite file. `client` overrides `app.dependency_overrides[
  app_module.get_db]` to funnel all requests through that single
  shared session. Important consequence for the regression test:
  this override serializes requests onto ONE session, which masks
  the very race #125 is about. The race test must NOT use the
  `client` fixture; it needs a custom fixture that gives each
  concurrent request its own session.

### No `IntegrityError` handler exists anywhere in app.py

Grep for `IntegrityError` and `except` in `app.py` returns no matches
for `IntegrityError`. The app's error handling relies on FastAPI's
default `RequestValidationError` and `HTTPException` propagation; any
uncaught SQLAlchemy error propagates as a 500 with the standard
"Internal Server Error" body. Same for the `services/` directory:
no service catches `IntegrityError` and re-raises as a domain
exception.

### Sibling-sweep (per runner prompt: actively search for siblings the
issue itself never named)

I enumerated every model with a UNIQUE constraint and every code path
that does a SELECT-then-INSERT against it, not just the one the issue
named:

| Model | UNIQUE constraint | Insert site | Has try/except IntegrityError? |
|---|---|---|---|
| `User.username` | `database/__init__.py:21` | `services/auth.py:54-55` via `create_user` called from `app.py:419` and `services/auth.py:75` (fallback) | **No** |
| `VoiceProfile(user_id, name)` | `database/__init__.py:156` | `services/voice_id.py:95-104` (the `enroll_voice` flow) | **No** (SELECT then INSERT, same race shape) |
| `ProviderConfig(user_id, name)` | `database/__init__.py:186` | `app.py:816-841` (`PUT /api/providers/{name}`) | **No** (same shape, but provider configs are typically single-user driven; race window is narrower since the client only edits its own row) |
| `HotwordEntry(user_id, term)` | **No UNIQUE constraint** at the model level | n/a | n/a — the agent who flagged this as a sibling was wrong; the service-layer dup check is the only barrier. Not a fix target. |

The `User.username` race is the only one that has a public-facing
endpoint with a high race-exposure surface (anyone can hit
`/api/register`; there's no auth gate before the INSERT). The
`VoiceProfile` and `ProviderConfig` siblings exist but their typical
access pattern is a single logged-in user editing their own rows, and
they are out of scope for issue #125.

**Decision for this A/B run:** fix the register route (the issue's
ask) and document the siblings in the final report. Do not scope-creep
this PR to cover them. A separate follow-up issue is the right
vehicle for the broader pattern.

### What the issue's own suggested fix is missing / gets wrong

The issue's "Fix" section suggests three options:
1. "Wrap the `create_user` call in a try/except IntegrityError and
   return 400" — correct, but the issue does not mention that the
   session must be rolled back (`db.rollback()`) before the next
   request can use it. SQLite + SQLAlchemy will let you re-use the
   session after a `rollback()`, but the session is in a borked
   transactional state until you call `rollback()` explicitly. The
   `get_db` generator closes the session on teardown, so the bug is
   contained to the current request, but the right thing is still
   `db.rollback()` inside the except block.
2. "Use `INSERT OR IGNORE` / ON CONFLICT DO NOTHING at the DB level"
   — works, but changes the semantics of `create_user` (it would
   return None on conflict instead of raising). Would require
   re-plumbing the route to handle the None case. Heavier than
   needed for this bug.
3. "Use a DB-level advisory lock" — SQLite has no advisory locks
   natively (it has `BEGIN IMMEDIATE` for write transactions, which
   is actually a viable solution and arguably cleaner than catching
   IntegrityError, but it's a bigger refactor).

**Chosen approach for this PR:** option 1, with the explicit
`db.rollback()` the issue's snippet omits. Catch
`sqlalchemy.exc.IntegrityError`, roll back, raise `HTTPException(400,
"Username already taken")`. The detail string matches the
existing duplicate-username error at `app.py:415`, so the test at
`tests/test_auth_admin.py:427-434` (`test_register_taken_username_beats_password_error`)
and any frontend handling keep working unchanged.

### Test gap

`tests/test_auth_admin.py:427-434` — `test_register_taken_username_beats_password_error`
exercises the synchronous duplicate-check path (a single test client
posts a username, then a second test client posts the same username).
It does NOT exercise the race. The agent who explored the test
fixture confirmed: no test fires two concurrent requests, and the
`client` fixture's `get_db` override funnels all requests through
one session, which structurally prevents the race from manifesting
under that fixture.

**Regression test plan (Phase 3):** write a new test that:
- Sets up a fresh temp DB and a per-request session factory (does
  NOT use the `client` fixture's shared session).
- Resets the rate limiter.
- Spawns N concurrent threads (N ≤ 5, to stay under the
  `register:client_ip` rate-limit bucket of 5/5min), each calling
  `POST /api/register` with the same username but distinct CSRF
  tokens.
- Asserts exactly one request returns 200 with `{"ok": True}` and
  the rest return 400 with `detail` containing "Username already
  taken". Critically: assert NO 500 status appears in the results.
- Sanity-check the test fails on `master` (before the fix) and
  passes after the fix. Per the runner prompt: "If a live browser
  tool is available, write a test that reproduces the reported
  symptom against current code, confirm it fails, then confirm your
  fix makes it pass." No browser tool is needed here — TestClient
  threading exercises the real `app.py` route + the real
  SQLAlchemy session machinery against a real per-test SQLite file.

## Acceptance criteria (from the issue body)

The issue does NOT have a formal "Acceptance Criteria" / "Definition
of Done" / "Requirements" section. It only has the bug description
and the suggested fix. The implicit criteria I'll satisfy:

- [ ] **AC1:** Two concurrent `POST /api/register` with the same
  username result in exactly one 200 and one 400 (not 500).
  Verified by the new concurrent-registration regression test.
- [ ] **AC2:** A second sequential `POST /api/register` with an
  already-taken username still returns 400 with the existing
  "Username already taken" detail string. (Pre-existing behavior,
  preserved by the new except block returning the same detail.)
  Verified by the existing
  `test_register_taken_username_beats_password_error` test still
  passing.
- [ ] **AC3:** A successful registration still works exactly as
  before. Verified by the existing `test_register_valid` and the
  rest of the test_auth_admin suite.
- [ ] **AC4:** The session is left in a usable state after the
  IntegrityError (no half-committed data, no orphan pending
  transaction). Verified implicitly by the same client being able
  to register a different username right after the race.
