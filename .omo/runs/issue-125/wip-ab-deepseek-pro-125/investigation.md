# Issue #125 Investigation — Concurrent Registration Race

**Branch**: `wip/ab-deepseek-pro-125`
**Orchestrator**: deepseek-v4-pro (Sisyphus)
**Date**: 2026-07-26

## 1. Issue Summary

Concurrent registration requests for the same username can trigger an unhandled `IntegrityError`, returning a 500 Internal Server Error instead of "Username already taken."

## 2. Root Cause Verified

### 2.1 The race window

**app.py:414-419** (register route):
```python
if db.query(User).filter(User.username == username).first():  # line 414
    raise HTTPException(status_code=400, detail="Username already taken")  # line 415
ok, reason = validate_password(password)  # line 416
if not ok:
    raise HTTPException(status_code=400, detail=reason)
user = create_user(db, username, password)  # line 419
```

**services/auth.py:44-56** (create_user):
```python
def create_user(db, username: str, password: str) -> User:
    is_first = db.query(User).count() == 0
    salt = generate_salt()
    user = User(
        username=username,
        password_salt=salt,
        password_hash=hash_password(password, salt),
        is_admin=is_first,
    )
    db.add(user)
    db.commit()  # ← IntegrityError here on UNIQUE constraint violation
    return user
```

The SELECT check (line 414) and the INSERT commit (auth.py:55) are not atomic. With SQLite's WAL mode (single writer), two concurrent requests can:
1. Both pass the SELECT check (no matching row found)
2. Request A commits — username inserted
3. Request B's `db.commit()` hits UNIQUE constraint on `User.username` → `IntegrityError` → uncaught → 500

### 2.2 User model constraint

**database/__init__.py:21**:
```python
username = Column(String(64), unique=True, nullable=False)
```

This is the DB-level UNIQUE constraint that enforces uniqueness. The Python-side check (line 414) is a best-effort advisory, not a guarantee.

### 2.3 No existing IntegrityError handling

Confirmed via grep: zero `IntegrityError` references in the entire codebase. No handler exists anywhere.

## 3. Sibling Sweep

Searched all `db.query()` + `db.commit()` call pairs in `app.py` and `services/*.py` for the same SELECT-check-then-INSERT pattern.

### 3.1 Direct sibling: get_or_create_fallback_user

**services/auth.py:68-75**:
```python
def get_or_create_fallback_user(db) -> User:
    user = db.query(User).filter(User.username == "local").first()
    if user:
        return user
    return create_user(db, "local", "changeme")
```

Same check-then-create pattern, same race. However, this function is called **only from `init_db()`** in `database/__init__.py`, which runs once at startup during database initialization. No concurrent access in practice — not a real risk.

### 3.2 Voice profiles, hotwords, settings: properly guarded

- **services/voice_id.py:enroll()** (line ~117): Checks for existing profile first, returns existing if found. Guard present.
- **services/voice_id.py:add_clip()** (line ~163): Checks profile exists first.
- **services/hotwords.py:add_hotword()** (line ~34): Checks for existing term (case-insensitive), returns existing if found. Guard present.
- **services/settings.py**: Uses atomic UPDATE with `json_patch()` — no race possible.

### 3.3 Transcript/job creation: not affected

Transcript and job creation use auto-increment IDs (no user-facing unique constraints that could race). No practical IntegrityError risk from concurrent inserts.

### 3.4 Sibling sweep conclusion

**No additional high-risk call sites found beyond the register route.** The only sibling (`get_or_create_fallback_user`) is a startup-only migration helper. All other write paths have proper guards or don't involve user-facing unique constraints.

## 4. Issue Body Accuracy Assessment

The issue's root cause description is **accurate**: the race window exists exactly as described. The three proposed fix approaches are all valid.

The issue's line numbers for app.py (348-353) are **stale** — the register route is now at lines 404-422. The code pattern is unchanged.

## 5. Recommended Fix

**Option 1 (recommended)**: Wrap the `create_user()` call in try/except IntegrityError in the register route:

```python
# app.py:419 - replace
user = create_user(db, username, password)
# with:
from sqlalchemy.exc import IntegrityError
try:
    user = create_user(db, username, password)
except IntegrityError:
    raise HTTPException(status_code=400, detail="Username already taken")
```

This is the simplest, most idiomatic fix for the codebase. It keeps `create_user()` as a thin DB function and lets the caller decide error handling.

Why not the other options:
- **Option 2 (INSERT OR IGNORE / raw SQL)**: Would require replacing the ORM-based create_user with raw SQL, breaking the existing pattern.
- **Option 3 (DB-level advisory lock)**: SQLite has no native advisory locks. Would require application-level locking (e.g., a mutex), overkill for this case.

### Scope boundary: registration only

The fix targets only the register route. `get_or_create_fallback_user` is not fixed because:
1. It's called once at startup during `init_db()` — no concurrent access in practice
2. If it did fail, the startup error would be logged and would need a human to address (not a user-facing 500)
3. Adding error handling there would obscure a real startup problem

## 6. Call Sites / Complement Rule Check

The `create_user()` function has two callers:
1. `app.py:419` — **register route** → fix applied
2. `auth.py:75` — **get_or_create_fallback_user** → no fix needed (startup-only, see above)

No other callers. No complement gaps.
