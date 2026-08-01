# Device Token Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a headless device (no browser, no cookie jar) authenticate to `/api/transcribe` with a per-user bearer token, without weakening auth on any other route.

**Architecture:** A new `local_device_token_hash` column on `User`, hashed at rest with the project's existing SHA-256 approach (same rationale as `hash_reset_token`: the token is already a 256-bit random value, not a low-entropy password). A new auth dependency, `get_current_user_or_device`, tries the session cookie first and falls back to `Authorization: Bearer <token>` — used ONLY on `/api/transcribe`, so every other route's auth is untouched. The global CSRF middleware is taught to skip its check when a bearer token is present, since CSRF only exploits ambient cookie auth and a bearer header can't be forged cross-origin.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), pytest + `fastapi.testclient.TestClient`, project's stdlib-only `hashlib`/`secrets` auth helpers (`services/auth.py`).

## Global Constraints

- No new hashing dependency: use `hashlib.sha256` (device token is high-entropy already) or `hashlib.pbkdf2_hmac("sha256", ...)` (only for genuinely low-entropy secrets) — never add bcrypt/passlib.
- Every `/api/*` mutation (non-GET/HEAD/OPTIONS) is CSRF-checked by `enforce_csrf` in `app.py` unless explicitly exempted; exemptions must be narrow and justified inline.
- Schema changes go through `ensure_columns()` in `database/__init__.py`, additive/nullable only — no `ALTER TABLE ... NOT NULL` (SQLite can't do it without a table rebuild, see `ensure_nullable_llm_job_transcript_id` for the rebuild pattern if ever needed).
- No em dashes in commit messages or comments; plain punctuation only.
- Never commit directly to `master`. This work happens on its own branch; open a PR when done, don't push to `master`.
- Tests run via `pytest` in this project's `.venv` (conftest fails fast with a clear message under the wrong interpreter).

---

### Task 1: Schema — device token columns on `User`

**Files:**
- Modify: `database/__init__.py:22-28` (User model), `database/__init__.py:567` (add an `ensure_columns` call)
- Test: `tests/test_device_token.py` (new file)

**Interfaces:**
- Produces: `User.local_device_token_hash: str | None`, `User.local_device_token_created_at: datetime | None` — every later task reads/writes these two attributes directly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_device_token.py
from sqlalchemy import inspect


def test_users_table_has_device_token_columns(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {c["name"] for c in inspector.get_columns("users")}
    assert "local_device_token_hash" in columns
    assert "local_device_token_created_at" in columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token.py -v`
Expected: FAIL, `assert "local_device_token_hash" in columns` — column not present yet.

- [ ] **Step 3: Write minimal implementation**

In `database/__init__.py`, in the `User` class (right after `reset_token_expires_at`, before `created_at` — matches the existing style of grouping related nullable additions together):

```python
    reset_token = Column(String(128), nullable=True)
    reset_token_expires_at = Column(DateTime, nullable=True)
    local_device_token_hash = Column(String(128), nullable=True)
    local_device_token_created_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
```

And in `init_db()`, immediately after the existing `users` `ensure_columns` call (currently reading `ensure_columns(engine, "users", {"is_admin": "BOOLEAN DEFAULT 0", "reset_token": "TEXT", "reset_token_expires_at": "TEXT"})`), add a new call so existing databases pick up the columns too:

```python
    ensure_columns(engine, "users", {"local_device_token_hash": "TEXT", "local_device_token_created_at": "TEXT"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database/__init__.py tests/test_device_token.py
git commit -m "Add device token columns to users table"
```

---

### Task 2: Token generate/hash/lookup helpers

**Files:**
- Modify: `services/auth.py` (add helpers near `hash_reset_token`, after line 41's `verify_password`)
- Test: `tests/test_device_token.py` (extend)

**Interfaces:**
- Consumes: `User.local_device_token_hash`, `User.local_device_token_created_at` (Task 1).
- Produces: `generate_device_token() -> str`, `hash_device_token(token: str) -> str`, `set_device_token(db, user) -> str`, `revoke_device_token(db, user) -> None`, `get_user_by_device_token(db, token) -> User | None` — Tasks 3 and 4 call these by exact name.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_device_token.py (append)
from database import User
from services.auth import (
    create_user, set_device_token, revoke_device_token, get_user_by_device_token,
    hash_device_token,
)


def test_set_device_token_stores_hash_not_plaintext(db_session):
    user = create_user(db_session, "devtok_user", "pass1234")
    token = set_device_token(db_session, user)
    assert user.local_device_token_hash == hash_device_token(token)
    assert user.local_device_token_hash != token
    assert user.local_device_token_created_at is not None


def test_get_user_by_device_token_finds_matching_user(db_session):
    user = create_user(db_session, "devtok_user2", "pass1234")
    token = set_device_token(db_session, user)
    found = get_user_by_device_token(db_session, token)
    assert found is not None
    assert found.id == user.id


def test_get_user_by_device_token_rejects_wrong_token(db_session):
    user = create_user(db_session, "devtok_user3", "pass1234")
    set_device_token(db_session, user)
    assert get_user_by_device_token(db_session, "wrong" * 8) is None


def test_get_user_by_device_token_rejects_empty_token(db_session):
    assert get_user_by_device_token(db_session, "") is None


def test_revoke_device_token_clears_lookup(db_session):
    user = create_user(db_session, "devtok_user4", "pass1234")
    token = set_device_token(db_session, user)
    revoke_device_token(db_session, user)
    assert user.local_device_token_hash is None
    assert get_user_by_device_token(db_session, token) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token.py -v`
Expected: FAIL, `ImportError: cannot import name 'set_device_token' from 'services.auth'`

- [ ] **Step 3: Write minimal implementation**

In `services/auth.py`, right after `verify_password` (line 41):

```python
def generate_device_token() -> str:
    return secrets.token_hex(32)


def hash_device_token(token: str) -> str:
    """Hash a device bearer token for storage. SHA-256, not PBKDF2, same
    reasoning as hash_reset_token: the token is already a 256-bit random
    value, not a low-entropy password, so slow key derivation buys nothing."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def set_device_token(db, user: User) -> str:
    """Generate and store a new device token for *user*, invalidating any
    previous one (single token per user). Returns the plaintext token,
    the only time it is ever visible outside the request that created it."""
    token = generate_device_token()
    user.local_device_token_hash = hash_device_token(token)
    user.local_device_token_created_at = utcnow()
    db.commit()
    return token


def revoke_device_token(db, user: User) -> None:
    user.local_device_token_hash = None
    user.local_device_token_created_at = None
    db.commit()


def get_user_by_device_token(db, token: str) -> Optional[User]:
    """Look up a user by their device bearer token. Returns None for an
    empty token, a token that matches no user, or when no user has a
    token set at all."""
    if not token:
        return None
    token_hash = hash_device_token(token)
    return db.query(User).filter(User.local_device_token_hash == token_hash).first()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add services/auth.py tests/test_device_token.py
git commit -m "Add device token generate/hash/lookup helpers"
```

---

### Task 3: Settings endpoints to generate/revoke/inspect the token

**Files:**
- Modify: `app.py:31-36` (import block), `app.py:838-841` (add routes after `put_settings`)
- Test: `tests/test_device_token.py` (extend)

**Interfaces:**
- Consumes: `set_device_token`, `revoke_device_token` (Task 2).
- Produces: `POST /api/settings/device-token` → `{"token": str, "created_at": str}`, `DELETE /api/settings/device-token` → `{"ok": true}`, `GET /api/settings/device-token` → `{"has_token": bool, "created_at": str | None}`. Task 6 (frontend) calls these three routes by exact path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_device_token.py (append)
class TestDeviceTokenSettingsRoutes:
    def test_generate_returns_plaintext_once(self, client, db_session):
        resp = client.post("/api/settings/device-token")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["token"]) == 64  # secrets.token_hex(32)
        assert data["created_at"] is not None

    def test_status_reflects_generated_state(self, client, db_session):
        status_before = client.get("/api/settings/device-token").json()
        assert status_before["has_token"] is False
        client.post("/api/settings/device-token")
        status_after = client.get("/api/settings/device-token").json()
        assert status_after["has_token"] is True
        assert status_after["created_at"] is not None

    def test_revoke_clears_state(self, client, db_session):
        client.post("/api/settings/device-token")
        resp = client.delete("/api/settings/device-token")
        assert resp.status_code == 200
        status = client.get("/api/settings/device-token").json()
        assert status["has_token"] is False

    def test_generate_requires_csrf(self, client, db_session):
        old = client.headers.pop("X-CSRF-Token", None)
        try:
            resp = client.post("/api/settings/device-token")
            assert resp.status_code == 403
        finally:
            if old:
                client.headers["X-CSRF-Token"] = old

    def test_regenerate_invalidates_previous_token(self, client, db_session):
        first = client.post("/api/settings/device-token").json()["token"]
        second = client.post("/api/settings/device-token").json()["token"]
        assert first != second
        from services.auth import get_user_by_device_token
        assert get_user_by_device_token(db_session, first) is None
        assert get_user_by_device_token(db_session, second) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token.py::TestDeviceTokenSettingsRoutes -v`
Expected: FAIL, 404 (routes don't exist yet)

- [ ] **Step 3: Write minimal implementation**

In `app.py`, change the `services.auth` import block (lines 31-36) to add the two new names:

```python
from services.auth import (
    get_or_create_fallback_user, create_user, authenticate_user, validate_password,
    password_min_length, get_user_by_reset_token,
    list_usernames, generate_reset_token, reset_password,
    set_admin_status, get_all_users,
    set_device_token, revoke_device_token,
)
```

Then, right after `put_settings` (line 840):

```python
@app.post("/api/settings/device-token")
async def generate_device_token_route(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate (or regenerate) this user's device bearer token. Returns
    the plaintext token once; only its hash is ever stored."""
    token = set_device_token(db, current_user)
    return {"token": token, "created_at": current_user.local_device_token_created_at.isoformat()}


@app.get("/api/settings/device-token")
async def device_token_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {
        "has_token": bool(current_user.local_device_token_hash),
        "created_at": current_user.local_device_token_created_at.isoformat() if current_user.local_device_token_created_at else None,
    }


@app.delete("/api/settings/device-token")
async def revoke_device_token_route(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    revoke_device_token(db, current_user)
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token.py -v`
Expected: PASS, all tests in the file green

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_device_token.py
git commit -m "Add settings routes to generate, inspect, and revoke device token"
```

---

### Task 4: Auth dependency + CSRF exemption, wired into `/api/transcribe`

**Files:**
- Modify: `app.py:261-265` (add new dependency after `get_current_user`), `app.py:188-195` (`enforce_csrf`), `app.py:1308` (swap the dependency on `transcribe_audio`)
- Test: `tests/test_device_token_auth.py` (new file)

**Interfaces:**
- Consumes: `get_user_by_device_token` (Task 2), `_resolve_session_user` (existing, `app.py:248`).
- Produces: `get_current_user_or_device(request, db) -> User` dependency, used exclusively by `/api/transcribe`. No other task depends on this name, but it must stay named exactly this — it's referenced in code comments elsewhere in this plan.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_device_token_auth.py
import io
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as app_module
from database import User
from services.auth import set_device_token


async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello")
    db.add(t)
    db.commit()
    return t


def _device_client(db_session):
    """A bare TestClient sharing the test db but carrying no cookies at
    all, simulating a headless device with no browser session."""
    def _override_get_db():
        yield db_session
    app_module.app.dependency_overrides[app_module.get_db] = _override_get_db
    return TestClient(app_module.app)


def test_valid_device_token_uploads_without_cookie_or_csrf(client, db_session):
    user = db_session.query(User).filter(User.username == "testuser").first()
    token = set_device_token(db_session, user)
    device_client = _device_client(db_session)
    with patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        resp = device_client.post(
            "/api/transcribe",
            files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
            data={"provider": "moonshine", "kind": "voice_note"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


def test_invalid_device_token_rejected(client, db_session):
    device_client = _device_client(db_session)
    resp = device_client.post(
        "/api/transcribe",
        files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
        data={"provider": "moonshine", "kind": "voice_note"},
        headers={"Authorization": "Bearer " + "wrong" * 12},
    )
    assert resp.status_code == 401


def test_neither_token_nor_cookie_rejected(client, db_session):
    device_client = _device_client(db_session)
    resp = device_client.post(
        "/api/transcribe",
        files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
        data={"provider": "moonshine", "kind": "voice_note"},
    )
    assert resp.status_code == 401


def test_device_token_not_honored_on_unscoped_route(client, db_session):
    """A bearer token is only meaningful on /api/transcribe. Any other
    authenticated route must still reject a bearer-only caller with no
    session, proving the token's blast radius stayed narrow."""
    user = db_session.query(User).filter(User.username == "testuser").first()
    token = set_device_token(db_session, user)
    device_client = _device_client(db_session)
    resp = device_client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token_auth.py -v`
Expected: FAIL — `test_valid_device_token_uploads_without_cookie_or_csrf` gets 403 (CSRF) or 401 (no dependency yet), not 200. `test_device_token_not_honored_on_unscoped_route` may accidentally pass already (401 is the current behavior for any unauthenticated request) — that's fine, it's a guard-rail test that should stay green through Step 4.

- [ ] **Step 3: Write minimal implementation**

In `app.py`, right after `get_current_user` (line 265), add:

```python
def _resolve_device_token_user(request: Request, db: Session) -> User | None:
    """Look up the user for a device bearer token, if the request carries
    one. Kept separate from _resolve_session_user because this path is
    only trusted on routes that explicitly opt in via
    get_current_user_or_device below, not on get_current_user itself."""
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[len("bearer "):].strip()
    return get_user_by_device_token(db, token)


def get_current_user_or_device(request: Request, db: Session = Depends(get_db)) -> User:
    """Auth dependency for the one route that must also accept a device's
    bearer token. Session cookie is tried first so a logged-in browser tab
    is unaffected; the bearer token is the fallback for a headless caller
    with no cookie jar. Deliberately not the default get_current_user —
    every other route keeps session-only auth."""
    user = _resolve_session_user(request, db)
    if user:
        return user
    user = _resolve_device_token_user(request, db)
    if user:
        return user
    raise HTTPException(status_code=401, detail="Not logged in")
```

Add `get_user_by_device_token` to the `services.auth` import block (same block edited in Task 3):

```python
from services.auth import (
    get_or_create_fallback_user, create_user, authenticate_user, validate_password,
    password_min_length, get_user_by_reset_token,
    list_usernames, generate_reset_token, reset_password,
    set_admin_status, get_all_users,
    set_device_token, revoke_device_token, get_user_by_device_token,
)
```

In `enforce_csrf` (lines 188-195):

```python
async def enforce_csrf(request: Request, call_next):
    if request.method not in _CSRF_SAFE_METHODS and request.url.path.startswith("/api/"):
        # A request bearing a device's Authorization: Bearer token is not
        # cookie/session-authenticated, so it isn't CSRF-exploitable -- a
        # cross-origin page can't attach an Authorization header on the
        # victim's behalf the way it can rely on an ambient cookie. Skip
        # the CSRF check only when a bearer token is present; whether that
        # token is actually valid is decided downstream by whichever auth
        # dependency the route uses (still 401s on a bad or unhonored token).
        has_bearer = (request.headers.get("authorization") or "").lower().startswith("bearer ")
        if not has_bearer:
            csrf = request.headers.get("x-csrf-token") or ""
            if not validate_csrf_token(request.session, csrf):
                return JSONResponse(status_code=403, content={"detail": "Invalid or missing CSRF token"})
    return await call_next(request)
```

Finally, on `transcribe_audio` (the `/api/transcribe` route), change the dependency:

```python
    current_user: User = Depends(get_current_user_or_device),
```

(replacing the existing `current_user: User = Depends(get_current_user)` in that function's signature only — every other route keeps `get_current_user` unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token_auth.py -v`
Expected: PASS, 4 passed

Also run the full existing suite to confirm no regression from the CSRF change:
Run: `.venv\Scripts\python.exe -m pytest tests/test_auth_admin.py tests/test_transcribe_local_transcode.py -v`
Expected: PASS, all previously-passing tests still pass

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_device_token_auth.py
git commit -m "Accept device bearer token on /api/transcribe only"
```

---

### Task 5: Rate limit the device-token upload path

**Files:**
- Modify: `app.py:1293-1309` (`transcribe_audio` signature and body)
- Test: `tests/test_device_token_auth.py` (extend)

**Interfaces:**
- Consumes: `rate_limiter` (existing, imported from `services.security`), `get_current_user_or_device` (Task 4).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_device_token_auth.py (append)
def test_device_token_upload_rate_limited(client, db_session):
    from database import User
    from services.security import rate_limiter
    user = db_session.query(User).filter(User.username == "testuser").first()
    from services.auth import set_device_token
    token = set_device_token(db_session, user)
    rate_limiter._buckets.clear()
    device_client = _device_client(db_session)
    with patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        statuses = []
        for _ in range(31):
            resp = device_client.post(
                "/api/transcribe",
                files={"file": ("note.wav", io.BytesIO(b"fake wav bytes"), "audio/wav")},
                data={"provider": "moonshine", "kind": "voice_note"},
                headers={"Authorization": f"Bearer {token}"},
            )
            statuses.append(resp.status_code)
    assert statuses[-1] == 429
    assert statuses.count(200) == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token_auth.py::test_device_token_upload_rate_limited -v`
Expected: FAIL, all 31 return 200 (no limit applied yet)

- [ ] **Step 3: Write minimal implementation**

In `app.py`, add `request: Request` to the `transcribe_audio` signature (it currently has no `Request` param) and add a rate-limit check as the first line of the function body, right after the `kind` validation:

```python
@app.post("/api/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    provider: str = Form("moonshine"),
    model: Optional[str] = Form(None),
    language: str = Form("en"),
    temperature: float = Form(0.0),
    diarize: bool = Form(False),
    auto_correct: Optional[bool] = Form(None),
    num_speakers: Optional[int] = Form(None),
    context_doc: Optional[str] = Form(None),
    kind: str = Form("meeting"),
    capture_source: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_device),
):
    """Upload and transcribe an audio file."""
    if kind not in ("meeting", "dictation", "voice_note"):
        raise HTTPException(status_code=400, detail="kind must be 'meeting', 'dictation', or 'voice_note'")
    is_device_call = (request.headers.get("authorization") or "").lower().startswith("bearer ")
    if is_device_call and not rate_limiter.check(f"device-upload:{current_user.id}", max_requests=30, window_seconds=3600):
        raise HTTPException(status_code=429, detail="Too many device uploads, try again later")
    # Save uploaded file
```

(The `# Save uploaded file` line marks where the existing body continues unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_device_token_auth.py -v`
Expected: PASS, all tests in the file green

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_device_token_auth.py
git commit -m "Rate limit device-token uploads to 30 per hour"
```

---

### Task 6: Settings UI — device token card

**Files:**
- Modify: `static/rack.js` (`loadSettingsPage`, around line 5705, add a new card after the "Correction & summary defaults" block; add a click handler near the function's other `addEventListener` calls)

**Interfaces:**
- Consumes: `GET /api/settings/device-token`, `POST /api/settings/device-token`, `DELETE /api/settings/device-token` (Task 3).

- [ ] **Step 1: No automated test for this step**

This is a UI-only change with no existing unit-test harness for `rack.js` DOM strings in this codebase (existing coverage for this file is e2e/browser-level, per `tests/e2e/`). Verification is manual: Step 2 below is the check.

- [ ] **Step 2: Write the card markup and handler**

In `static/rack.js`, inside `loadSettingsPage`, fetch the token status alongside the other settings calls. Change the destructuring `Promise.all` call (currently `[provs, settings, health, status, localLlmCfg]`) to also fetch device-token status:

```javascript
  let provs, settings, health, status, localLlmCfg, deviceToken;
  try {
    [provs, settings, health, status, localLlmCfg, deviceToken] = await Promise.all([
      api('/api/providers'), api('/api/settings'), api('/api/health'), api('/api/status'),
      api('/api/providers/local_llm'),
      api('/api/settings/device-token'),
    ]);
  } catch (e) { toast(e.message, 'error'); return; }
```

Add a new card after the "Correction & summary defaults" block (after its closing `</div>` around line 5735 — insert before whatever section currently follows it):

```javascript
    <div style="margin-top:30px">
      <div class="t-cap" style="font-size:10.5px;letter-spacing:0.14em;margin:0 0 8px 36px">Device token — headless capture devices</div>
      <div class="unit unit--svc" style="border-radius:3px;padding:16px 34px;display:flex;flex-direction:column;gap:12px">
        <div style="font-size:11.5px;color:var(--label-dim)">Lets a device with no browser (e.g. a standalone recorder) upload directly to /api/transcribe using a bearer token instead of logging in. Regenerating invalidates the previous token immediately.</div>
        <div id="device-token-status" style="font-family:var(--f-mono);font-size:11.5px;color:var(--label)">${deviceToken.has_token ? `Token active since ${new Date(deviceToken.created_at).toLocaleString()}` : 'No token generated'}</div>
        <div id="device-token-value" style="display:none;font-family:var(--f-mono);font-size:11.5px;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px;border-radius:2px;word-break:break-all"></div>
        <div style="display:flex;gap:8px">
          <button id="device-token-generate" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px 14px;border-radius:2px;cursor:pointer">${deviceToken.has_token ? 'Regenerate' : 'Generate'}</button>
          <button id="device-token-revoke" style="font-family:var(--f-cond);font-weight:600;font-size:12px;text-transform:uppercase;background:var(--input);border:1px solid var(--input-edge);color:var(--label);padding:8px 14px;border-radius:2px;cursor:pointer" ${deviceToken.has_token ? '' : 'disabled'}>Revoke</button>
        </div>
      </div>
    </div>
```

Add handlers alongside the function's other `getElementById(...).addEventListener` wiring (near where `audio-save`'s handler is attached):

```javascript
  $('device-token-generate').addEventListener('click', async () => {
    try {
      const result = await api('/api/settings/device-token', { method: 'POST' });
      const valueBox = $('device-token-value');
      valueBox.textContent = result.token;
      valueBox.style.display = 'block';
      toast('Device token generated. Copy it now, it will not be shown again.', 'success');
      loadSettingsPage();
    } catch (e) { toast(e.message, 'error'); }
  });
  $('device-token-revoke').addEventListener('click', async () => {
    try {
      await api('/api/settings/device-token', { method: 'DELETE' });
      toast('Device token revoked.', 'success');
      loadSettingsPage();
    } catch (e) { toast(e.message, 'error'); }
  });
```

- [ ] **Step 3: Manual verification**

Run the app locally (`python app.py` or whatever this project's `run`/dev-server convention is), log in, open Settings, and confirm: "Generate" shows a 64-char token and flips the button to "Regenerate"; reloading the page shows "Token active since ..." instead of the raw token (it's genuinely not retrievable again); "Revoke" clears the state and disables itself.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "Add device token card to settings page"
```

---

## Notes for the implementer

- `kind="auto"` from the approved spec is NOT part of this plan. Issue #268 (in flight elsewhere) has to land that sentinel on `/api/transcribe` first; until then, any caller (including a future device) sends `kind="voice_note"`. Nothing in this plan needs to change when #268 merges.
- This plan only touches `/api/transcribe`. The batch endpoint (`/api/transcribe/batch`) intentionally does not accept the device token; the spec's narrow-scope requirement means that if batch upload from a device is ever wanted, it needs its own explicit review, not silent inheritance.
