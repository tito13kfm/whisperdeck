# Per-User Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace WhisperDeck's single shared provider-key/data model with per-user accounts, so each logged-in user has their own API keys, transcripts, and voice profiles.

**Architecture:** Session-cookie auth via Starlette's `SessionMiddleware` (signed cookie, no session table). Passwords hashed with stdlib `hashlib.pbkdf2_hmac` (no new compiled dependency). A `User` table plus a `user_id` FK added to `ProviderConfig`, `Transcript`, `VoiceProfile`; every route and service method that touches those tables is scoped to the logged-in user. A one-time startup migration rebuilds the three affected tables (their old single-column `UNIQUE` constraints can't be altered in place in SQLite) and assigns existing rows to a fallback account so nothing is lost.

**Tech Stack:** FastAPI, Starlette `SessionMiddleware`, SQLAlchemy (SQLite), stdlib `hashlib`/`secrets` for password hashing. New dependency: `itsdangerous` (required by `SessionMiddleware`).

## Global Constraints

- No test suite exists in this repo (confirmed by inspection — no `tests/` dir, no pytest config, no CI). Every task's verification is a manual PowerShell/curl sequence against a running `app.py`, matching how this codebase has been verified throughout its development. Do not introduce a pytest suite as part of this plan.
- Design spec: `docs/superpowers/specs/2026-06-30-per-user-auth-design.md`. Decisions already made there (server-side per-user keys, real login not anonymous cookies, private-per-user data, open self-serve registration) are not up for re-litigation during implementation.
- Do not implement password reset, admin roles, or login rate-limiting — explicitly out of scope per the spec.
- Do not implement provider-queue/rate-limit/fallback tracking — explicitly deferred to a separate future project per the spec.
- Every step that touches `app.py`, `services/transcription.py`, or `services/voice_id.py` must preserve the existing per-request `db: Session = Depends(get_db)` pattern already in place — do not reintroduce a shared/global session.
- Before Task 3's verification step runs against the **real** `data/whisperdesk.db` (not a copy), back it up first: `Copy-Item data\whisperdesk.db data\whisperdesk.db.bak`. That step performs an irreversible table rebuild.

---

## File Structure

- **Modify `database/__init__.py`**: add `User` model, add `user_id` to `Transcript`/`ProviderConfig`/`VoiceProfile`, change `ProviderConfig`/`VoiceProfile` uniqueness to be per-user, add `migrate_schema()` and `backfill_user_id()` migration helpers, change `init_db()` to return a 3-tuple.
- **Create `services/auth.py`**: password hashing (`hash_password`, `verify_password`, `generate_salt`), user CRUD (`create_user`, `authenticate_user`, `get_or_create_fallback_user`). No FastAPI/HTTP concerns here — pure functions taking a `db` session, matching the existing service-layer pattern.
- **Modify `app.py`**: startup migration bootstrap, `SessionMiddleware`, `get_current_user` dependency, `POST /api/register`, `POST /api/login`, `POST /api/logout`, `GET /api/me`, and `user_id`/`current_user` scoping added to every existing route that touches `ProviderConfig`, `Transcript`, or `VoiceProfile`.
- **Modify `services/transcription.py`**: every DB-touching method gains a `user_id` parameter.
- **Modify `services/voice_id.py`**: every DB-touching method gains a `user_id` parameter.
- **Modify `static/index.html`**: add a login/register view shown before the existing app UI when not authenticated, plus a logout action.
- **Modify `requirements.txt`**: add `itsdangerous`.

---

### Task 1: User model, per-user schema, and migration helpers

**Files:**
- Modify: `database/__init__.py` (full rewrite — see below)
- Modify: `app.py:39` (one-line call-site update so the app still boots after this task)

**Interfaces:**
- Produces: `User` model (`id`, `username`, `password_hash`, `password_salt`, `created_at`), `user_id` column on `Transcript`/`ProviderConfig`/`VoiceProfile`, `migrate_schema(engine) -> list[str]`, `backfill_user_id(engine, migrated_tables: list[str], user_id: int) -> None`, `init_db(db_path: str) -> (engine, SessionLocal, migrated_tables: list[str])`.
- Consumes: nothing new (pure schema/migration layer).

**Why a full table rebuild, not just `ALTER TABLE ADD COLUMN`:** `ProviderConfig.name` and `VoiceProfile.name` currently have a single-column `UNIQUE` constraint. SQLite cannot drop or alter a constraint on an existing table without rebuilding it. Adding `user_id` via a plain `ALTER TABLE ADD COLUMN` would leave the old single-column unique constraint in place, silently breaking multi-user support the moment a second user tries to save a `"groq"` provider config or a voice profile with a name that already exists for someone else. `migrate_schema()` renames the old table out of the way so `Base.metadata.create_all()` recreates it with the corrected per-user constraint; `backfill_user_id()` copies the data back in and assigns ownership.

- [ ] **Step 1: Rewrite `database/__init__.py`**

```python
"""SQLAlchemy models for WhisperDeck."""
import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey,
    JSON, Boolean, UniqueConstraint, create_engine, inspect, text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    password_salt = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    duration_seconds = Column(Float, default=0)
    provider = Column(String(64), default="groq")
    model = Column(String(64), default="whisper-large-v3-turbo")
    language = Column(String(10), default="auto")
    status = Column(String(32), default="pending")  # pending, processing, completed, failed
    full_text = Column(Text, default="")
    segments = Column(JSON, default=list)  # [{start, end, speaker, text}]
    speaker_count = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    summary = relationship("Summary", back_populates="transcript", uselist=False, cascade="all, delete-orphan")


class Summary(Base):
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=False)
    short_summary = Column(Text, default="")
    key_points = Column(JSON, default=list)
    action_items = Column(JSON, default=list)
    decisions = Column(JSON, default=list)
    model = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    transcript = relationship("Transcript", back_populates="summary")


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_voice_profile_user_name"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String(128), nullable=False)
    embedding = Column(JSON, nullable=True)  # stored as list of floats
    embedding_model = Column(String(64), default="speechbrain/spkrec-ecapa-voxceleb")
    sample_count = Column(Integer, default=0)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_provider_config_user_name"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String(64), nullable=False)  # groq, openai, replicate, local
    display_name = Column(String(128), default="")
    api_key = Column(String(512), default="")
    api_url = Column(String(512), default="")
    default_model = Column(String(64), default="")
    is_active = Column(Boolean, default=True)
    config = Column(JSON, default=dict)


def migrate_schema(engine) -> list[str]:
    """Rename any pre-existing tables that predate per-user scoping, so
    create_all() can recreate them with the new (user_id-aware) schema.

    Returns the list of table names that were migrated — empty on a fresh
    database or one that's already current. Callers use this list to know
    whether backfill_user_id() needs to run.
    """
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    targets = ["provider_configs", "transcripts", "voice_profiles"]
    migrated = []
    for table_name in targets:
        if table_name not in existing_tables:
            continue
        columns = [c["name"] for c in inspector.get_columns(table_name)]
        if "user_id" in columns:
            continue
        migrated.append(table_name)

    if not migrated:
        return []

    with engine.begin() as conn:
        for table_name in migrated:
            conn.execute(text(f"ALTER TABLE {table_name} RENAME TO {table_name}_old"))

    return migrated


def backfill_user_id(engine, migrated_tables: list[str], user_id: int) -> None:
    """Copy rows from the *_old tables (renamed by migrate_schema) into the
    freshly created tables, assigning user_id to every row, then drop the
    old tables. Must run after Base.metadata.create_all() has recreated
    the target tables.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name in migrated_tables:
            old_table = f"{table_name}_old"
            old_columns = [c["name"] for c in inspector.get_columns(old_table)]
            cols = ", ".join(old_columns)
            conn.execute(
                text(f"INSERT INTO {table_name} ({cols}, user_id) SELECT {cols}, :uid FROM {old_table}"),
                {"uid": user_id},
            )
            conn.execute(text(f"DROP TABLE {old_table}"))


def init_db(db_path: str = "data/whisperdesk.db") -> tuple:
    """Initialize the database. Returns (engine, SessionLocal, migrated_tables).

    SessionLocal is a sessionmaker, not a live session — callers create one
    session per request (see app.py's get_db dependency) rather than
    sharing a single session across all concurrent requests.

    migrated_tables is the list from migrate_schema() — non-empty only on
    the first startup against a pre-existing pre-auth database. Callers
    use it to trigger the one-time fallback-user backfill.
    """
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    migrated_tables = migrate_schema(engine)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal, migrated_tables


__all__ = [
    "Base", "User", "Transcript", "Summary", "VoiceProfile", "ProviderConfig",
    "init_db", "migrate_schema", "backfill_user_id",
]
```

- [ ] **Step 2: Update the one `init_db` call site so the app still boots**

In `app.py`, change:
```python
engine, SessionLocal = init_db(str(DB_PATH))
```
to:
```python
# migrated_tables is consumed by the migration bootstrap block added in
# Task 3 of the per-user-auth plan — unused until then.
engine, SessionLocal, migrated_tables = init_db(str(DB_PATH))
```

- [ ] **Step 3: Verify against a throwaway copy of the real database**

```powershell
cd C:\Claude\whisperdesk
Copy-Item data\whisperdesk.db data\_test_migrate.db
.venv\Scripts\python.exe -c "
from database import init_db, migrate_schema, backfill_user_id, Transcript
engine, SessionLocal, migrated = init_db('data/_test_migrate.db')
print('migrated tables:', migrated)
db = SessionLocal()
print('transcript count:', db.query(Transcript).count())
if migrated:
    backfill_user_id(engine, migrated, 999)
    db2 = SessionLocal()
    t = db2.query(Transcript).first()
    print('first transcript user_id after backfill:', t.user_id if t else 'no rows')
"
Remove-Item data\_test_migrate.db
```
Expected: `migrated tables: ['provider_configs', 'transcripts', 'voice_profiles']` (or whichever subset exist in your current DB), transcript count matches what `/api/status` reported before this change, and after backfill the first transcript's `user_id` prints `999`.

- [ ] **Step 4: Confirm the app still imports and boots**

```powershell
.venv\Scripts\python.exe -c "import app; print('OK')"
```
Expected: `OK` (no errors — `migrated_tables` being unused is fine, it's just an unpacked variable, not an import-time error).

- [ ] **Step 5: Commit**

```powershell
git add database/__init__.py app.py
git commit -m "Add User model and per-user schema migration helpers"
```

---

### Task 2: Password hashing and user CRUD (`services/auth.py`)

**Files:**
- Create: `services/auth.py`

**Interfaces:**
- Consumes: `database.User` (from Task 1).
- Produces: `generate_salt() -> str`, `hash_password(password: str, salt: str) -> str`, `verify_password(password: str, salt: str, expected_hash: str) -> bool`, `create_user(db, username: str, password: str) -> User`, `authenticate_user(db, username: str, password: str) -> Optional[User]`, `get_or_create_fallback_user(db) -> User`. All take an already-open `db` session (same convention as `services/transcription.py` and `services/voice_id.py` after the earlier per-request-session refactor) — none of these open or close a session themselves.

- [ ] **Step 1: Create `services/auth.py`**

```python
"""Authentication helpers: password hashing and user lookup/creation.

No FastAPI/HTTP concerns here, same convention as the other services —
callers pass in an already-open db session.
"""
import hashlib
import secrets
from typing import Optional

from database import User

PBKDF2_ITERATIONS = 200_000


def generate_salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return secrets.compare_digest(hash_password(password, salt), expected_hash)


def create_user(db, username: str, password: str) -> User:
    salt = generate_salt()
    user = User(
        username=username,
        password_salt=salt,
        password_hash=hash_password(password, salt),
    )
    db.add(user)
    db.commit()
    return user


def authenticate_user(db, username: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None
    if not verify_password(password, user.password_salt, user.password_hash):
        return None
    return user


def get_or_create_fallback_user(db) -> User:
    """Used only during migration of a pre-existing database, to own rows
    that predate user accounts. Username 'local', password 'changeme' —
    the user should change it after first login."""
    user = db.query(User).filter(User.username == "local").first()
    if user:
        return user
    return create_user(db, "local", "changeme")
```

- [ ] **Step 2: Verify hashing and user creation round-trip against a throwaway database**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe -c "
from database import init_db
from services.auth import create_user, authenticate_user, verify_password, hash_password, generate_salt

engine, SessionLocal, _ = init_db('data/_test_auth.db')
db = SessionLocal()

salt = generate_salt()
h = hash_password('correct-horse', salt)
assert verify_password('correct-horse', salt, h)
assert not verify_password('wrong-password', salt, h)
print('hash round-trip OK')

u = create_user(db, 'alice', 'hunter2')
print('created user id:', u.id, 'username:', u.username)

ok = authenticate_user(db, 'alice', 'hunter2')
bad = authenticate_user(db, 'alice', 'wrong')
missing = authenticate_user(db, 'nobody', 'anything')
print('correct login:', ok.username if ok else None)
print('wrong password:', bad)
print('unknown user:', missing)
"
Remove-Item data\_test_auth.db
```
Expected: `hash round-trip OK`, `created user id: 1 username: alice`, `correct login: alice`, `wrong password: None`, `unknown user: None`.

- [ ] **Step 3: Commit**

```powershell
git add services/auth.py
git commit -m "Add password hashing and user CRUD helpers"
```

---

### Task 3: Wire the migration bootstrap into app startup

**Files:**
- Modify: `app.py:19-42` (imports and startup block)

**Interfaces:**
- Consumes: `database.backfill_user_id` (Task 1), `services.auth.get_or_create_fallback_user` (Task 2), the `migrated_tables` variable introduced in Task 1 Step 2.
- Produces: on a pre-existing database, a `local`/`changeme` fallback user owning every row that existed before this change. No new routes yet — that's Task 4.

- [ ] **Step 1: Update imports**

In `app.py`, change:
```python
from database import init_db, Transcript, Summary, VoiceProfile, ProviderConfig
```
to:
```python
from database import init_db, backfill_user_id, Transcript, Summary, VoiceProfile, ProviderConfig, User
from services.auth import get_or_create_fallback_user
```

- [ ] **Step 2: Replace the startup block**

Change:
```python
# migrated_tables is consumed by the migration bootstrap block added in
# Task 3 of the per-user-auth plan — unused until then.
engine, SessionLocal, migrated_tables = init_db(str(DB_PATH))
transcription_service = TranscriptionService(str(UPLOAD_DIR))
diarization_service = DiarizationService()
voice_id_service = VoiceIdentificationService(str(VOICES_DIR))
```
to:
```python
engine, SessionLocal, migrated_tables = init_db(str(DB_PATH))

if migrated_tables:
    _migration_db = SessionLocal()
    try:
        _fallback_user = get_or_create_fallback_user(_migration_db)
        backfill_user_id(engine, migrated_tables, _fallback_user.id)
        print(
            f"[migration] assigned {len(migrated_tables)} pre-existing table(s) "
            f"to fallback user 'local' (password: changeme — change it after logging in)"
        )
    finally:
        _migration_db.close()

transcription_service = TranscriptionService(str(UPLOAD_DIR))
diarization_service = DiarizationService()
voice_id_service = VoiceIdentificationService(str(VOICES_DIR))
```

- [ ] **Step 3: Back up the real database, then verify against it**

```powershell
cd C:\Claude\whisperdesk
Copy-Item data\whisperdesk.db data\whisperdesk.db.bak
.venv\Scripts\python.exe -c "import app; print('OK')"
```
Expected output includes `OK` and, if your `data/whisperdesk.db` predates this change, a line like:
```
[migration] assigned 3 pre-existing table(s) to fallback user 'local' (password: changeme — change it after logging in)
```

- [ ] **Step 4: Confirm the migration is idempotent (safe to import again)**

```powershell
.venv\Scripts\python.exe -c "import app; print('OK')"
```
Expected: `OK`, with **no** `[migration]` line this time — `migrate_schema()` finds `user_id` already present and returns `[]`.

- [ ] **Step 5: Confirm existing data survived**

```powershell
.venv\Scripts\python.exe -c "
import sqlite3
c = sqlite3.connect('data/whisperdesk.db')
print('users:', c.execute('select id, username from users').fetchall())
print('transcripts with user_id:', c.execute('select id, user_id from transcripts').fetchall())
"
```
Expected: a `local` user exists, and every transcript row shows a non-null `user_id` matching that user's id.

- [ ] **Step 6: Commit**

```powershell
git add app.py
git commit -m "Bootstrap fallback user and backfill on startup migration"
```

---

### Task 4: Session middleware, `get_current_user`, and auth endpoints

**Files:**
- Modify: `app.py` (imports, middleware setup, new endpoints)
- Modify: `requirements.txt` (add `itsdangerous`)

**Interfaces:**
- Consumes: `services.auth.create_user`, `authenticate_user` (Task 2), `database.User` (Task 1).
- Produces: `get_current_user(request: Request, db: Session = Depends(get_db)) -> User` dependency, used by every task from here on. Routes: `POST /api/register`, `POST /api/login`, `POST /api/logout`, `GET /api/me`.

- [ ] **Step 1: Add `itsdangerous` to requirements**

In `requirements.txt`, add a line:
```
itsdangerous>=2.1.0
```
(Required by Starlette's `SessionMiddleware`; not always pulled in as a transitive dependency of FastAPI.)

```powershell
.venv\Scripts\python.exe -m pip install itsdangerous
```

- [ ] **Step 2: Update imports**

In `app.py`, change:
```python
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Body, Depends
```
to:
```python
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Body, Depends, Request
```

Change:
```python
from database import init_db, backfill_user_id, Transcript, Summary, VoiceProfile, ProviderConfig, User
from services.auth import get_or_create_fallback_user
```
to:
```python
from database import init_db, backfill_user_id, Transcript, Summary, VoiceProfile, ProviderConfig, User
from services.auth import get_or_create_fallback_user, create_user, authenticate_user
```

Add near the top, with the other imports:
```python
import secrets
from starlette.middleware.sessions import SessionMiddleware
```

- [ ] **Step 3: Add persistent session secret and the middleware**

After the existing `for d in [DATA_DIR, ...]:` directory-creation block and before `engine, SessionLocal, migrated_tables = init_db(...)`, add:
```python
SESSION_SECRET_PATH = DATA_DIR / ".session_secret"
if SESSION_SECRET_PATH.exists():
    SESSION_SECRET = SESSION_SECRET_PATH.read_text().strip()
else:
    SESSION_SECRET = secrets.token_hex(32)
    SESSION_SECRET_PATH.write_text(SESSION_SECRET)
```

Immediately after the existing `app.add_middleware(CORSMiddleware, ...)` block, add:
```python
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
```
(Persisting the secret to a file means logins survive app restarts — without this, every restart would invalidate every session and force everyone to log in again.)

- [ ] **Step 4: Add `get_current_user` dependency**

In the `# ── Helpers ──` section, after the existing `get_db()` function, add:
```python
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not logged in")
    return user
```

- [ ] **Step 5: Add the auth routes**

Add a new section right after the `# ── Helpers ──` section (before `# ── API Routes ──`):
```python
# ── Auth ──────────────────────────────────────────────────────────────────

@app.post("/api/register")
async def register(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    user = create_user(db, username, password)
    request.session["user_id"] = user.id
    return {"ok": True, "username": user.username}


@app.post("/api/login")
async def login(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = authenticate_user(db, username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    request.session["user_id"] = user.id
    return {"ok": True, "username": user.username}


@app.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(current_user: User = Depends(get_current_user)):
    return {"username": current_user.username}
```

- [ ] **Step 6: Verify the full login lifecycle**

```powershell
cd C:\Claude\whisperdesk
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" -NoNewWindow -RedirectStandardOutput "run_out.log" -RedirectStandardError "run_err.log" -PassThru
Start-Sleep -Seconds 3
Get-Content run_err.log

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Write-Host "register:"
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"alice","password":"hunter2"}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Select-Object -ExpandProperty Content
Write-Host "me (should show alice):"
Invoke-WebRequest -Uri http://localhost:9781/api/me -WebSession $session -UseBasicParsing | Select-Object -ExpandProperty Content
Write-Host "logout:"
Invoke-WebRequest -Uri http://localhost:9781/api/logout -Method Post -WebSession $session -UseBasicParsing | Select-Object -ExpandProperty Content
Write-Host "me after logout (expect 401):"
try { Invoke-WebRequest -Uri http://localhost:9781/api/me -WebSession $session -UseBasicParsing } catch { $_.Exception.Response.StatusCode }
Write-Host "login again:"
Invoke-WebRequest -Uri http://localhost:9781/api/login -Method Post -Body '{"username":"alice","password":"hunter2"}' -ContentType "application/json" -WebSession $session -UseBasicParsing | Select-Object -ExpandProperty Content
Write-Host "wrong password (expect 401):"
try { Invoke-WebRequest -Uri http://localhost:9781/api/login -Method Post -Body '{"username":"alice","password":"wrong"}' -ContentType "application/json" -UseBasicParsing } catch { $_.Exception.Response.StatusCode }

Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item run_out.log,run_err.log -ErrorAction SilentlyContinue
```
Expected: register returns `{"ok":true,"username":"alice"}`; `/api/me` returns `{"username":"alice"}`; logout returns `{"ok":true}`; `/api/me` after logout raises `Unauthorized` (401); login again succeeds; wrong password raises `Unauthorized` (401).

Note: existing routes (`/api/providers`, `/api/transcripts`, etc.) still work unauthenticated at this point — they aren't scoped yet. That's Tasks 5–7. This task only proves the login mechanism itself works in isolation.

- [ ] **Step 7: Commit**

```powershell
git add app.py requirements.txt
git commit -m "Add session auth: register/login/logout/me endpoints"
```

---

### Task 5: Scope `ProviderConfig` routes to the logged-in user

**Files:**
- Modify: `app.py` — `get_providers`, `get_provider_config`, `update_provider_config`, `list_provider_models`, and the inline `ProviderConfig` lookups inside `transcribe_audio` and `summarize_transcript`.

**Interfaces:**
- Consumes: `get_current_user` (Task 4).
- Produces: `current_user` becomes a parameter on `transcribe_audio` and `summarize_transcript` for the first time — Task 6 will reuse that same parameter for `Transcript` scoping, not add a duplicate one.

- [ ] **Step 1: Scope `get_providers`**

Change:
```python
@app.get("/api/providers")
async def get_providers(db: Session = Depends(get_db)):
    """List available providers with their metadata."""
    providers = list_providers()
    # Merge in saved config status
    for p in providers:
        saved = db.query(ProviderConfig).filter(
            ProviderConfig.name == p["id"]
        ).first()
```
to:
```python
@app.get("/api/providers")
async def get_providers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List available providers with their metadata."""
    providers = list_providers()
    # Merge in saved config status
    for p in providers:
        saved = db.query(ProviderConfig).filter(
            ProviderConfig.user_id == current_user.id,
            ProviderConfig.name == p["id"],
        ).first()
```

- [ ] **Step 2: Scope `get_provider_config`**

Change:
```python
@app.get("/api/providers/{name}")
async def get_provider_config(name: str, db: Session = Depends(get_db)):
    cfg = db.query(ProviderConfig).filter(ProviderConfig.name == name).first()
```
to:
```python
@app.get("/api/providers/{name}")
async def get_provider_config(name: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == name,
    ).first()
```

- [ ] **Step 3: Scope `update_provider_config`**

Change:
```python
@app.put("/api/providers/{name}")
async def update_provider_config(name: str, data: dict = Body(...), db: Session = Depends(get_db)):
    cfg = db.query(ProviderConfig).filter(ProviderConfig.name == name).first()
    if not cfg:
        cfg = ProviderConfig(name=name)
        db.add(cfg)
```
to:
```python
@app.put("/api/providers/{name}")
async def update_provider_config(name: str, data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == name,
    ).first()
    if not cfg:
        cfg = ProviderConfig(name=name, user_id=current_user.id)
        db.add(cfg)
```

- [ ] **Step 4: Scope `list_provider_models`**

Change:
```python
@app.get("/api/providers/{name}/models")
async def list_provider_models(name: str, db: Session = Depends(get_db)):
    """Fetch available transcription models for a given provider (live if possible)."""
    from backends import get_provider, list_providers

    # Check provider exists
    known = [p["id"] for p in list_providers()]
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")

    # Get saved config
    cfg = db.query(ProviderConfig).filter(ProviderConfig.name == name).first()
```
to:
```python
@app.get("/api/providers/{name}/models")
async def list_provider_models(name: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Fetch available transcription models for a given provider (live if possible)."""
    from backends import get_provider, list_providers

    # Check provider exists
    known = [p["id"] for p in list_providers()]
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")

    # Get saved config
    cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == name,
    ).first()
```

- [ ] **Step 5: Scope the provider lookup inside `transcribe_audio`**

Change:
```python
@app.post("/api/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    provider: str = Form("groq"),
    model: Optional[str] = Form(None),
    language: str = Form("en"),
    temperature: float = Form(0.0),
    diarize: bool = Form(False),
    db: Session = Depends(get_db),
):
```
to:
```python
@app.post("/api/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    provider: str = Form("groq"),
    model: Optional[str] = Form(None),
    language: str = Form("en"),
    temperature: float = Form(0.0),
    diarize: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
```

Change:
```python
    # Get provider config
    prov_cfg = db.query(ProviderConfig).filter(ProviderConfig.name == provider).first()
```
to:
```python
    # Get provider config
    prov_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == provider,
    ).first()
```

- [ ] **Step 6: Scope the provider lookup inside `summarize_transcript`**

Change:
```python
@app.post("/api/transcripts/{transcript_id}/summarize")
async def summarize_transcript(
    transcript_id: int,
    provider: str = Form("groq"),
    model: str = Form("llama-3.3-70b-versatile"),
    db: Session = Depends(get_db),
):
    """Generate an LLM summary of a completed transcript."""
    prov_cfg = db.query(ProviderConfig).filter(ProviderConfig.name == provider).first()
```
to:
```python
@app.post("/api/transcripts/{transcript_id}/summarize")
async def summarize_transcript(
    transcript_id: int,
    provider: str = Form("groq"),
    model: str = Form("llama-3.3-70b-versatile"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate an LLM summary of a completed transcript."""
    prov_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == provider,
    ).first()
```

- [ ] **Step 7: Verify two users get isolated provider configs**

```powershell
cd C:\Claude\whisperdesk
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" -NoNewWindow -RedirectStandardOutput "run_out.log" -RedirectStandardError "run_err.log" -PassThru
Start-Sleep -Seconds 3

$sessionA = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$sessionB = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"userA","password":"pw1"}' -ContentType "application/json" -WebSession $sessionA -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"userB","password":"pw2"}' -ContentType "application/json" -WebSession $sessionB -UseBasicParsing | Out-Null

Invoke-WebRequest -Uri http://localhost:9781/api/providers/groq -Method Put -Body '{"api_key":"gsk_AAAA111111111111"}' -ContentType "application/json" -WebSession $sessionA -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/providers/groq -Method Put -Body '{"api_key":"gsk_BBBB222222222222"}' -ContentType "application/json" -WebSession $sessionB -UseBasicParsing | Out-Null

Write-Host "userA sees their own key:"
(Invoke-WebRequest -Uri http://localhost:9781/api/providers/groq -WebSession $sessionA -UseBasicParsing).Content
Write-Host "userB sees their own key:"
(Invoke-WebRequest -Uri http://localhost:9781/api/providers/groq -WebSession $sessionB -UseBasicParsing).Content

Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item run_out.log,run_err.log -ErrorAction SilentlyContinue
```
Expected: userA's response ends in `...1111` and userB's ends in `...2222` — each sees only their own masked key, never the other's.

- [ ] **Step 8: Commit**

```powershell
git add app.py
git commit -m "Scope provider config routes to the logged-in user"
```

---

### Task 6: Scope `Transcript` routes and `TranscriptionService` to the logged-in user

**Files:**
- Modify: `services/transcription.py` — every method gains a `user_id` parameter.
- Modify: `app.py` — `transcribe_audio` (Transcript-specific part), `list_transcripts`, `get_transcript`, `delete_transcript`, `update_transcript`, `summarize_transcript` (Transcript-specific part), `get_summary`, `full_status`.

**Interfaces:**
- Consumes: `current_user` (already a parameter on `transcribe_audio`/`summarize_transcript` from Task 5; newly added to the other five routes in this task).
- Produces: no new interfaces consumed by later tasks — this is the last DB-model-scoping task before the frontend.

- [ ] **Step 1: Update `TranscriptionService.transcribe` to accept and store `user_id`**

In `services/transcription.py`, change:
```python
    async def transcribe(
        self,
        db,
        audio_path: str,
        provider_name: str = "groq",
        provider_config: Optional[dict] = None,
        title: Optional[str] = None,
        language: str = "en",
        model: Optional[str] = None,
        temperature: float = 0.0,
        **kwargs,
    ) -> Transcript:
        """Transcribe an audio file and persist the result."""
        provider_config = provider_config or {}
        if model:
            provider_config["default_model"] = model

        provider = get_provider(provider_name, provider_config)

        filename = os.path.basename(audio_path)
        transcript = Transcript(
            title=title or os.path.splitext(filename)[0],
            filename=filename,
            provider=provider_name,
            model=provider_config.get("default_model", ""),
            language=language,
            status="processing",
        )
```
to:
```python
    async def transcribe(
        self,
        db,
        user_id: int,
        audio_path: str,
        provider_name: str = "groq",
        provider_config: Optional[dict] = None,
        title: Optional[str] = None,
        language: str = "en",
        model: Optional[str] = None,
        temperature: float = 0.0,
        **kwargs,
    ) -> Transcript:
        """Transcribe an audio file and persist the result."""
        provider_config = provider_config or {}
        if model:
            provider_config["default_model"] = model

        provider = get_provider(provider_name, provider_config)

        filename = os.path.basename(audio_path)
        transcript = Transcript(
            user_id=user_id,
            title=title or os.path.splitext(filename)[0],
            filename=filename,
            provider=provider_name,
            model=provider_config.get("default_model", ""),
            language=language,
            status="processing",
        )
```

- [ ] **Step 2: Update `get_transcript`, `list_transcripts`, `delete_transcript`, `summarize` to filter by `user_id`**

Change:
```python
    def get_transcript(self, db, transcript_id: int) -> Optional[Transcript]:
        return db.query(Transcript).filter(Transcript.id == transcript_id).first()

    def list_transcripts(self, db, limit: int = 50, offset: int = 0) -> list[Transcript]:
        return (
            db.query(Transcript)
            .order_by(Transcript.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def delete_transcript(self, db, transcript_id: int) -> bool:
        t = self.get_transcript(db, transcript_id)
        if not t:
            return False
        db.delete(t)
        db.commit()
        return True

    async def summarize(
        self,
        db,
        transcript_id: int,
        api_key: str = "",
        provider_name: str = "groq",
        provider_config: Optional[dict] = None,
        model: str = "llama-3.3-70b-versatile",
    ) -> Summary:
        """Generate an LLM summary of a completed transcript."""
        transcript = self.get_transcript(db, transcript_id)
```
to:
```python
    def get_transcript(self, db, user_id: int, transcript_id: int) -> Optional[Transcript]:
        return db.query(Transcript).filter(
            Transcript.id == transcript_id, Transcript.user_id == user_id
        ).first()

    def list_transcripts(self, db, user_id: int, limit: int = 50, offset: int = 0) -> list[Transcript]:
        return (
            db.query(Transcript)
            .filter(Transcript.user_id == user_id)
            .order_by(Transcript.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def delete_transcript(self, db, user_id: int, transcript_id: int) -> bool:
        t = self.get_transcript(db, user_id, transcript_id)
        if not t:
            return False
        db.delete(t)
        db.commit()
        return True

    async def summarize(
        self,
        db,
        user_id: int,
        transcript_id: int,
        api_key: str = "",
        provider_name: str = "groq",
        provider_config: Optional[dict] = None,
        model: str = "llama-3.3-70b-versatile",
    ) -> Summary:
        """Generate an LLM summary of a completed transcript."""
        transcript = self.get_transcript(db, user_id, transcript_id)
```

- [ ] **Step 3: Update `transcribe_audio` in `app.py` to pass `user_id`**

Change:
```python
    try:
        transcript = await transcription_service.transcribe(
            db,
            audio_path=str(save_path),
```
to:
```python
    try:
        transcript = await transcription_service.transcribe(
            db,
            current_user.id,
            audio_path=str(save_path),
```

- [ ] **Step 4: Scope `list_transcripts` route**

Change:
```python
@app.get("/api/transcripts")
async def list_transcripts(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    transcripts = (
        db.query(Transcript)
        .order_by(Transcript.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_serialize_transcript(t) for t in transcripts]
```
to:
```python
@app.get("/api/transcripts")
async def list_transcripts(limit: int = 50, offset: int = 0, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    transcripts = (
        db.query(Transcript)
        .filter(Transcript.user_id == current_user.id)
        .order_by(Transcript.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_serialize_transcript(t) for t in transcripts]
```

- [ ] **Step 5: Scope `get_transcript`, `delete_transcript`, `update_transcript` routes**

Change:
```python
@app.get("/api/transcripts/{transcript_id}")
async def get_transcript(transcript_id: int, db: Session = Depends(get_db)):
    t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return _serialize_transcript(t)


@app.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: int, db: Session = Depends(get_db)):
    t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.patch("/api/transcripts/{transcript_id}")
async def update_transcript(transcript_id: int, data: dict = Body(...), db: Session = Depends(get_db)):
    t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
```
to:
```python
@app.get("/api/transcripts/{transcript_id}")
async def get_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return _serialize_transcript(t)


@app.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.patch("/api/transcripts/{transcript_id}")
async def update_transcript(transcript_id: int, data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
```

- [ ] **Step 6: Update `summarize_transcript` to pass `user_id`, and scope `get_summary`**

Change:
```python
    try:
        summary = await transcription_service.summarize(
            db,
            transcript_id=transcript_id,
```
to:
```python
    try:
        summary = await transcription_service.summarize(
            db,
            current_user.id,
            transcript_id=transcript_id,
```

Change:
```python
@app.get("/api/transcripts/{transcript_id}/summary")
async def get_summary(transcript_id: int, db: Session = Depends(get_db)):
    summary = db.query(Summary).filter(Summary.transcript_id == transcript_id).first()
    if not summary:
        raise HTTPException(status_code=404, detail="No summary found")
    return _serialize_summary(summary)
```
to:
```python
@app.get("/api/transcripts/{transcript_id}/summary")
async def get_summary(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    summary = db.query(Summary).filter(Summary.transcript_id == transcript_id).first()
    if not summary:
        raise HTTPException(status_code=404, detail="No summary found")
    return _serialize_summary(summary)
```
(`Summary` has no `user_id` of its own — ownership is checked via its parent `Transcript` first, so a summary for someone else's transcript can't be fetched by guessing its `transcript_id`.)

- [ ] **Step 7: Scope the transcript counts in `full_status`**

Change:
```python
@app.get("/api/status")
async def full_status(db: Session = Depends(get_db)):
    """Return comprehensive app status for the frontend dashboard."""
    total = db.query(Transcript).count()
    completed = db.query(Transcript).filter(Transcript.status == "completed").count()
    processing = db.query(Transcript).filter(Transcript.status == "processing").count()
    failed = db.query(Transcript).filter(Transcript.status == "failed").count()
    total_duration = (
        db.query(Transcript.duration_seconds)
        .filter(Transcript.status == "completed")
        .all()
    )
    total_minutes = sum(d[0] for d in total_duration if d[0]) / 60
    voice_count = db.query(VoiceProfile).count()

    # Get active provider
    active_prov = db.query(ProviderConfig).filter(ProviderConfig.is_active == True).first()  # noqa: E712
```
to:
```python
@app.get("/api/status")
async def full_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Return comprehensive app status for the frontend dashboard."""
    total = db.query(Transcript).filter(Transcript.user_id == current_user.id).count()
    completed = db.query(Transcript).filter(
        Transcript.user_id == current_user.id, Transcript.status == "completed"
    ).count()
    processing = db.query(Transcript).filter(
        Transcript.user_id == current_user.id, Transcript.status == "processing"
    ).count()
    failed = db.query(Transcript).filter(
        Transcript.user_id == current_user.id, Transcript.status == "failed"
    ).count()
    total_duration = (
        db.query(Transcript.duration_seconds)
        .filter(Transcript.user_id == current_user.id, Transcript.status == "completed")
        .all()
    )
    total_minutes = sum(d[0] for d in total_duration if d[0]) / 60
    voice_count = db.query(VoiceProfile).filter(VoiceProfile.user_id == current_user.id).count()

    # Get active provider
    active_prov = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id, ProviderConfig.is_active == True  # noqa: E712
    ).first()
```

- [ ] **Step 8: Verify two-user transcript isolation**

```powershell
cd C:\Claude\whisperdesk
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=2" -c:a libmp3lame test_audio.mp3 2>&1 | Out-Null
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" -NoNewWindow -RedirectStandardOutput "run_out.log" -RedirectStandardError "run_err.log" -PassThru
Start-Sleep -Seconds 3

$sessionA = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$sessionB = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"userC","password":"pw1"}' -ContentType "application/json" -WebSession $sessionA -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"userD","password":"pw2"}' -ContentType "application/json" -WebSession $sessionB -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/providers/groq -Method Put -Body '{"api_key":"gsk_AAAA111111111111"}' -ContentType "application/json" -WebSession $sessionA -UseBasicParsing | Out-Null

$form = @{ file = Get-Item "test_audio.mp3"; provider = "groq"; language = "en"; model = "whisper-large-v3" }
$r = Invoke-WebRequest -Uri http://localhost:9781/api/transcribe -Method Post -Form $form -WebSession $sessionA -UseBasicParsing
$tid = ($r.Content | ConvertFrom-Json).id
Write-Host "userA created transcript id: $tid"

Write-Host "userA list (expect 1 item):"
(Invoke-WebRequest -Uri http://localhost:9781/api/transcripts -WebSession $sessionA -UseBasicParsing).Content
Write-Host "userB list (expect empty):"
(Invoke-WebRequest -Uri http://localhost:9781/api/transcripts -WebSession $sessionB -UseBasicParsing).Content
Write-Host "userB GET userA's transcript (expect 404):"
try { Invoke-WebRequest -Uri "http://localhost:9781/api/transcripts/$tid" -WebSession $sessionB -UseBasicParsing } catch { $_.Exception.Response.StatusCode }
Write-Host "userB DELETE userA's transcript (expect 404):"
try { Invoke-WebRequest -Uri "http://localhost:9781/api/transcripts/$tid" -Method Delete -WebSession $sessionB -UseBasicParsing } catch { $_.Exception.Response.StatusCode }
Write-Host "userA deletes their own (expect ok:true):"
(Invoke-WebRequest -Uri "http://localhost:9781/api/transcripts/$tid" -Method Delete -WebSession $sessionA -UseBasicParsing).Content

Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item run_out.log,run_err.log,test_audio.mp3 -ErrorAction SilentlyContinue
Get-ChildItem data\uploads | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-10) } | Remove-Item
```
Expected: userA's list has one item, userB's list is `[]`, userB's GET and DELETE on userA's transcript both raise `NotFound` (404), and userA's own delete succeeds with `{"ok":true}`.

- [ ] **Step 9: Commit**

```powershell
git add app.py services/transcription.py
git commit -m "Scope transcript routes and TranscriptionService to the logged-in user"
```

---

### Task 7: Scope `VoiceProfile` routes and `VoiceIdentificationService` to the logged-in user

**Files:**
- Modify: `services/voice_id.py` — `enroll`, `identify`, `list_profiles`, `delete_profile` each gain a `user_id` parameter.
- Modify: `app.py` — `list_voices`, `enroll_voice`, `identify_speaker`, `delete_voice_profile`.

**Interfaces:**
- Consumes: `get_current_user` (Task 4).
- Produces: nothing consumed by later tasks — this is the last backend task before the frontend.

- [ ] **Step 1: Update `services/voice_id.py` method signatures**

Change:
```python
    def enroll(
        self,
        db,
        name: str,
        audio_path: str,
        notes: str = "",
    ) -> VoiceProfile:
        """Enroll a speaker by name from an audio sample."""
        embedding = self._extract_embedding(audio_path)
        if embedding is None:
            if self._backend == "none":
                raise ValueError(
                    "No voice embedding backend available. "
                    "Install speechbrain: pip install speechbrain"
                )
            raise ValueError(
                f"Voice embedding extraction failed using the {self.backend_name} "
                f"backend. Check that the audio file is valid and the backend's "
                f"dependencies (e.g. torch, torchaudio) are working correctly."
            )

        existing = db.query(VoiceProfile).filter(VoiceProfile.name == name).first()
        if existing:
            existing.embedding = embedding.tolist() if isinstance(embedding, np.ndarray) else embedding
            existing.sample_count += 1
            existing.notes = notes or existing.notes
            existing.updated_at = datetime.datetime.utcnow()
            profile = existing
        else:
            profile = VoiceProfile(
                name=name,
                embedding=embedding.tolist() if isinstance(embedding, np.ndarray) else embedding,
                embedding_model=self.backend_name,
                sample_count=1,
                notes=notes,
            )
            db.add(profile)

        db.commit()
        return profile

    def identify(self, db, audio_path: str, threshold: float = 0.65) -> list[dict]:
        """Identify a speaker from an audio sample. Returns ranked candidates."""
        probe_embedding = self._extract_embedding(audio_path)
        if probe_embedding is None:
            return []

        profiles = db.query(VoiceProfile).all()
        if not profiles:
            return []
```
to:
```python
    def enroll(
        self,
        db,
        user_id: int,
        name: str,
        audio_path: str,
        notes: str = "",
    ) -> VoiceProfile:
        """Enroll a speaker by name from an audio sample."""
        embedding = self._extract_embedding(audio_path)
        if embedding is None:
            if self._backend == "none":
                raise ValueError(
                    "No voice embedding backend available. "
                    "Install speechbrain: pip install speechbrain"
                )
            raise ValueError(
                f"Voice embedding extraction failed using the {self.backend_name} "
                f"backend. Check that the audio file is valid and the backend's "
                f"dependencies (e.g. torch, torchaudio) are working correctly."
            )

        existing = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == user_id, VoiceProfile.name == name
        ).first()
        if existing:
            existing.embedding = embedding.tolist() if isinstance(embedding, np.ndarray) else embedding
            existing.sample_count += 1
            existing.notes = notes or existing.notes
            existing.updated_at = datetime.datetime.utcnow()
            profile = existing
        else:
            profile = VoiceProfile(
                user_id=user_id,
                name=name,
                embedding=embedding.tolist() if isinstance(embedding, np.ndarray) else embedding,
                embedding_model=self.backend_name,
                sample_count=1,
                notes=notes,
            )
            db.add(profile)

        db.commit()
        return profile

    def identify(self, db, user_id: int, audio_path: str, threshold: float = 0.65) -> list[dict]:
        """Identify a speaker from an audio sample. Returns ranked candidates."""
        probe_embedding = self._extract_embedding(audio_path)
        if probe_embedding is None:
            return []

        profiles = db.query(VoiceProfile).filter(VoiceProfile.user_id == user_id).all()
        if not profiles:
            return []
```

Change:
```python
    def list_profiles(self, db) -> list[dict]:
        return [
            {
                "id": p.id,
                "name": p.name,
                "sample_count": p.sample_count,
                "embedding_model": p.embedding_model,
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in db.query(VoiceProfile).order_by(VoiceProfile.name).all()
        ]

    def delete_profile(self, db, profile_id: int) -> bool:
        p = db.query(VoiceProfile).filter(VoiceProfile.id == profile_id).first()
        if not p:
            return False
        db.delete(p)
        db.commit()
        return True
```
to:
```python
    def list_profiles(self, db, user_id: int) -> list[dict]:
        return [
            {
                "id": p.id,
                "name": p.name,
                "sample_count": p.sample_count,
                "embedding_model": p.embedding_model,
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in db.query(VoiceProfile)
            .filter(VoiceProfile.user_id == user_id)
            .order_by(VoiceProfile.name)
            .all()
        ]

    def delete_profile(self, db, user_id: int, profile_id: int) -> bool:
        p = db.query(VoiceProfile).filter(
            VoiceProfile.id == profile_id, VoiceProfile.user_id == user_id
        ).first()
        if not p:
            return False
        db.delete(p)
        db.commit()
        return True
```

- [ ] **Step 2: Update `app.py` voice routes to pass `user_id` and require login**

Change:
```python
@app.get("/api/voices")
async def list_voices(db: Session = Depends(get_db)):
    """List all enrolled voice profiles."""
    return voice_id_service.list_profiles(db)


@app.post("/api/voices/enroll")
async def enroll_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """Enroll a new speaker from an audio sample."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"enroll_{name}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        profile = voice_id_service.enroll(db, name=name, audio_path=str(save_path), notes=notes)
```
to:
```python
@app.get("/api/voices")
async def list_voices(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List all enrolled voice profiles."""
    return voice_id_service.list_profiles(db, current_user.id)


@app.post("/api/voices/enroll")
async def enroll_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enroll a new speaker from an audio sample."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"enroll_{name}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        profile = voice_id_service.enroll(db, current_user.id, name=name, audio_path=str(save_path), notes=notes)
```

Change:
```python
@app.post("/api/voices/identify")
async def identify_speaker(
    file: UploadFile = File(...),
    threshold: float = Form(0.65),
    db: Session = Depends(get_db),
):
    """Identify a speaker from an audio sample against enrolled profiles."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"ident_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        matches = voice_id_service.identify(db, str(save_path), threshold=threshold)
        return {
            "matches": matches,
            "total_profiles": len(voice_id_service.list_profiles(db)),
            "backend": voice_id_service._backend,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/voices/{profile_id}")
async def delete_voice_profile(profile_id: int, db: Session = Depends(get_db)):
    ok = voice_id_service.delete_profile(db, profile_id)
```
to:
```python
@app.post("/api/voices/identify")
async def identify_speaker(
    file: UploadFile = File(...),
    threshold: float = Form(0.65),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Identify a speaker from an audio sample against enrolled profiles."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"ident_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        matches = voice_id_service.identify(db, current_user.id, str(save_path), threshold=threshold)
        return {
            "matches": matches,
            "total_profiles": len(voice_id_service.list_profiles(db, current_user.id)),
            "backend": voice_id_service._backend,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/voices/{profile_id}")
async def delete_voice_profile(profile_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ok = voice_id_service.delete_profile(db, current_user.id, profile_id)
```

- [ ] **Step 3: Verify two-user voice isolation — including that the new per-user unique constraint actually works**

```powershell
cd C:\Claude\whisperdesk
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=2" -c:a libmp3lame test_voice.mp3 2>&1 | Out-Null
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" -NoNewWindow -RedirectStandardOutput "run_out.log" -RedirectStandardError "run_err.log" -PassThru
Start-Sleep -Seconds 3

$sessionA = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$sessionB = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"userE","password":"pw1"}' -ContentType "application/json" -WebSession $sessionA -UseBasicParsing | Out-Null
Invoke-WebRequest -Uri http://localhost:9781/api/register -Method Post -Body '{"username":"userF","password":"pw2"}' -ContentType "application/json" -WebSession $sessionB -UseBasicParsing | Out-Null

$form = @{ file = Get-Item "test_voice.mp3"; name = "Sarah"; notes = "userE's Sarah" }
Write-Host "userE enrolls Sarah:"
try { Invoke-WebRequest -Uri http://localhost:9781/api/voices/enroll -Method Post -Form $form -WebSession $sessionA -UseBasicParsing | Select-Object -ExpandProperty Content }
catch { $_.ErrorDetails.Message }

$form2 = @{ file = Get-Item "test_voice.mp3"; name = "Sarah"; notes = "userF's Sarah" }
Write-Host "userF ALSO enrolls Sarah (same name, different user -- must succeed):"
try { Invoke-WebRequest -Uri http://localhost:9781/api/voices/enroll -Method Post -Form $form2 -WebSession $sessionB -UseBasicParsing | Select-Object -ExpandProperty Content }
catch { $_.ErrorDetails.Message }

Write-Host "userE list (expect only their Sarah):"
(Invoke-WebRequest -Uri http://localhost:9781/api/voices -WebSession $sessionA -UseBasicParsing).Content
Write-Host "userF list (expect only their Sarah):"
(Invoke-WebRequest -Uri http://localhost:9781/api/voices -WebSession $sessionB -UseBasicParsing).Content

Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item run_out.log,run_err.log,test_voice.mp3 -ErrorAction SilentlyContinue
```
Note: if no embedding backend is installed (the default state for this app — see `INSTALL.md`), both enroll calls will return a 500 with `"No voice embedding backend available"`. That's the expected error path and does **not** test what this step needs to test. If you don't have `speechbrain`/`librosa` installed, skip straight to Step 4's alternative direct-DB check instead.

Expected (with a backend installed): both enrolls succeed (userF's does **not** fail with a uniqueness error), and each user's list shows exactly one "Sarah" — their own.

- [ ] **Step 4: Alternative verification if no embedding backend is installed — confirm the constraint directly**

```powershell
.venv\Scripts\python.exe -c "
from database import init_db, VoiceProfile
engine, SessionLocal, _ = init_db('data/whisperdesk.db')
db = SessionLocal()
db.add(VoiceProfile(user_id=1, name='ConstraintTest', embedding=[0.1, 0.2]))
db.add(VoiceProfile(user_id=2, name='ConstraintTest', embedding=[0.3, 0.4]))
db.commit()
print('two users with the same voice-profile name: OK, no constraint violation')
db.query(VoiceProfile).filter(VoiceProfile.name == 'ConstraintTest').delete()
db.commit()
"
```
Expected: `two users with the same voice-profile name: OK, no constraint violation` — proves the Task 1 migration correctly replaced the old global-unique constraint with a per-user one.

- [ ] **Step 5: Commit**

```powershell
git add app.py services/voice_id.py
git commit -m "Scope voice profile routes and VoiceIdentificationService to the logged-in user"
```

---

### Task 8: Frontend login/register view

**Files:**
- Modify: `static/index.html` — wrap the existing app UI in a hidden container, add a login/register view, add a logout action, gate startup on an auth check.

**Interfaces:**
- Consumes: `GET /api/me`, `POST /api/register`, `POST /api/login`, `POST /api/logout` (Task 4).
- Produces: nothing consumed by later tasks — this is the last task in the plan.

No build step exists for this frontend (single static HTML file, vanilla JS) — this task edits it directly, matching the existing pattern.

- [ ] **Step 1: Wrap the existing app UI so it can be hidden pre-login**

Find the line right after `<body>`:
```html
<body>

<!-- ══════════ SIDEBAR ══════════ -->
<aside class="sidebar">
```
Change it to:
```html
<body>

<!-- ══════════ AUTH ══════════ -->
<div id="authShell" style="display:none;height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg-page)">
  <div style="width:340px;max-width:90vw;background:var(--bg-elevated);border:1px solid var(--border);border-radius:var(--radius-lg);padding:28px;box-shadow:var(--shadow-lg)">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:20px">
      <div class="sb-logo">W</div>
      <span style="font-size:16px;font-weight:600">WhisperDeck</span>
    </div>
    <h3 id="authTitle" style="font-size:15px;font-weight:600;margin-bottom:14px">Log in</h3>
    <div class="cfg-f" style="margin-bottom:10px">
      <label>Username</label>
      <input type="text" id="authUsername" style="width:100%;padding:7px 10px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-family:var(--font);outline:none">
    </div>
    <div class="cfg-f" style="margin-bottom:16px">
      <label>Password</label>
      <input type="password" id="authPassword" style="width:100%;padding:7px 10px;font-size:12px;background:var(--bg-page);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-family:var(--font);outline:none">
    </div>
    <div id="authError" style="display:none;color:var(--error);font-size:12px;margin-bottom:12px"></div>
    <button class="btn btn-primary" id="authSubmitBtn" style="width:100%;justify-content:center;margin-bottom:10px" onclick="submitAuth()">Log in</button>
    <div style="text-align:center;font-size:12px;color:var(--text-secondary)">
      <span id="authToggleText">Don't have an account?</span>
      <a href="#" onclick="toggleAuthMode();return false" id="authToggleLink">Register</a>
    </div>
  </div>
</div>

<!-- ══════════ APP SHELL ══════════ -->
<div id="appShell" style="display:none">

<!-- ══════════ SIDEBAR ══════════ -->
<aside class="sidebar">
```

- [ ] **Step 2: Close the new wrapper `<div id="appShell">` where the old body content ends**

Find:
```html
<!-- ══════ TOAST ══════ -->
<div class="toast-container" id="toastContainer"></div>

<script>
```
Change it to:
```html
<!-- ══════ TOAST ══════ -->
<div class="toast-container" id="toastContainer"></div>

</div><!-- /appShell -->

<script>
```

- [ ] **Step 3: Add auth JS functions and gate startup on `/api/me`**

Find, near the top of the `<script>` block:
```javascript
const API = '';
let currentPage = 'dashboard';
```
Change it to:
```javascript
const API = '';
let currentPage = 'dashboard';
let authMode = 'login';
```

Find the very last line of the script:
```javascript
loadDashboard();
```
Change it to:
```javascript
checkAuth();

/* ═══════════════════════════════════════════════════════
   AUTH
   ═══════════════════════════════════════════════════════ */
async function checkAuth() {
  try {
    const r = await fetch(API + '/api/me');
    if (r.ok) {
      document.getElementById('authShell').style.display = 'none';
      document.getElementById('appShell').style.display = 'block';
      loadDashboard();
      return;
    }
  } catch (e) {}
  document.getElementById('appShell').style.display = 'none';
  document.getElementById('authShell').style.display = 'flex';
}

function toggleAuthMode() {
  authMode = authMode === 'login' ? 'register' : 'login';
  document.getElementById('authTitle').textContent = authMode === 'login' ? 'Log in' : 'Create account';
  document.getElementById('authSubmitBtn').textContent = authMode === 'login' ? 'Log in' : 'Register';
  document.getElementById('authToggleText').textContent = authMode === 'login' ? "Don't have an account?" : 'Already have an account?';
  document.getElementById('authToggleLink').textContent = authMode === 'login' ? 'Register' : 'Log in';
  document.getElementById('authError').style.display = 'none';
}

async function submitAuth() {
  const username = document.getElementById('authUsername').value.trim();
  const password = document.getElementById('authPassword').value;
  const errEl = document.getElementById('authError');
  errEl.style.display = 'none';
  if (!username || !password) {
    errEl.textContent = 'Username and password are required.';
    errEl.style.display = 'block';
    return;
  }
  const endpoint = authMode === 'login' ? '/api/login' : '/api/register';
  try {
    const r = await fetch(API + endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || 'Authentication failed');
    }
    checkAuth();
  } catch (e) {
    errEl.textContent = e.message || 'Authentication failed';
    errEl.style.display = 'block';
  }
}

async function logout() {
  await fetch(API + '/api/logout', { method: 'POST' });
  document.getElementById('authUsername').value = '';
  document.getElementById('authPassword').value = '';
  checkAuth();
}
```

- [ ] **Step 4: Add a logout button to the Settings page**

Find:
```html
        <div class="set-card">
          <h4><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>About</h4>
          <p style="font-size:12px;color:var(--text-muted)">WhisperDeck v0.6 — Transcribe meeting recordings using local or hosted Whisper models. Supports Groq, OpenAI, Replicate, and custom endpoints. Features speaker diarization, LLM summarization, and voice identification database.</p>
          <p style="font-size:11px;color:var(--text-muted);margin-top:8px">Backend: <span id="aboutBackend">FastAPI + SQLite</span></p>
        </div>
```
Change it to:
```html
        <div class="set-card">
          <h4><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>About</h4>
          <p style="font-size:12px;color:var(--text-muted)">WhisperDeck v0.6 — Transcribe meeting recordings using local or hosted Whisper models. Supports Groq, OpenAI, Replicate, and custom endpoints. Features speaker diarization, LLM summarization, and voice identification database.</p>
          <p style="font-size:11px;color:var(--text-muted);margin-top:8px">Backend: <span id="aboutBackend">FastAPI + SQLite</span></p>
        </div>
        <div class="set-card">
          <h4>Account</h4>
          <button class="btn btn-sm" onclick="logout()">Log out</button>
        </div>
```

- [ ] **Step 5: Verify in a real browser**

```powershell
cd C:\Claude\whisperdesk
.venv\Scripts\python.exe app.py
```
Open `http://localhost:9781` in a browser:
1. You should see the login screen, not the dashboard (unless a session cookie from earlier testing is still valid — clear cookies for `localhost:9781` first if so).
2. Click "Register", create a test account, confirm the dashboard loads immediately after.
3. Reload the page — confirm you stay logged in (session cookie persists).
4. Go to Settings, click "Log out" — confirm you're returned to the login screen.
5. Log back in with the same credentials — confirm it works.
6. Stop the server (Ctrl+C), restart it, reload the page — confirm you're still logged in (proves the session secret is being persisted to `data/.session_secret`, not regenerated on restart).

- [ ] **Step 6: Commit**

```powershell
git add static/index.html
git commit -m "Add login/register view and logout to the frontend"
```

---

## Post-implementation note

After Task 3 runs against the real database, the pre-existing `local` / `changeme` account owns everything that existed before this feature shipped. Log in as `local` / `changeme` once and change that password via... there is no password-change endpoint in this plan (out of scope per the design spec's "no password reset" exclusion). If a password-change capability turns out to matter in practice, that's a small follow-on task, not a gap in this plan — flag it separately rather than scope-creeping it in here.
