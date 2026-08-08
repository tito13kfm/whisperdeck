# Investigation: Issue #125 - Concurrent registration race condition

## Target Issue

**Issue #125**: Concurrent registration race: unhandled IntegrityError returns 500

## Current Code State

### Register Route (app.py:403-422)

```python
@app.post("/api/register")
async def register(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else None
    if client_ip and not rate_limiter.check(f"register:{client_ip}", max_requests=5, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many registration attempts — try again later")
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    if db.query(User).filter(User.username == username).first():  # LINE 413: SELECT check
        raise HTTPException(status_code=400, detail="Username already taken")
    ok, reason = validate_password(password)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    user = create_user(db, username, password)  # LINE 419: calls db.commit()
    request.session["user_id"] = user.id
    rotate_csrf_token(request.session)
    return {"ok": True, "username": user.username}
```

### create_user Function (services/auth.py:44-56)

```python
def create_user(db, username: str, password: str) -> User:
    """Create a new user. The first user (empty table) is auto-admin."""
    is_first = db.query(User).count() == 0
    salt = generate_salt()
    user = User(
        username=username,
        password_salt=salt,
        password_hash=hash_password(password, salt),
        is_admin=is_first,
    )
    db.add(user)
    db.commit()  # LINE 55: This is where IntegrityError would be raised
    return user
```

## Call Sites for create_user

| File | Line | Caller | Context |
|------|------|--------|---------|
| app.py | 419 | register() | User registration endpoint |
| services/auth.py | 75 | get_or_create_fallback_user() | Migration fallback user creation |

## IntegrityError Handlers

**None found.** Zero occurrences of `IntegrityError` import or catch block in project source (confirmed via grep across all .py files excluding .venv and dist).

## Sibling Sweep: Similar SELECT-then-INSERT Patterns

Found 4 additional patterns with the same race condition shape:

1. **hotwords.py:18-29** - `add_hotword()`: SELECT existing, INSERT if not found
2. **voice_id.py:95-107** - `enroll()`: SELECT profile, INSERT if not found
3. **llm_jobs.py:108-109** - `enqueue_llm_job()`: SELECT active job, INSERT if not found
4. **transcription.py:268-270** - `summarize()`: SELECT summary, UPDATE or INSERT

**Note:** Issue #125 specifically targets the register route. These siblings are documented but out of scope for this fix unless investigation reveals they share the same user-facing impact.

## Issue's Suggested Fix vs Reality

Issue suggests three options:
1. Wrap create_user in try/except IntegrityError, return 400
2. Use INSERT OR IGNORE / ON CONFLICT DO NOTHING
3. Use DB-level advisory lock

**Recommended approach:** Option 1 (try/except IntegrityError in register route). This is:
- Minimal change, localized to the affected endpoint
- Preserves existing validation logic
- Returns proper 400 with user-friendly message
- Does not require schema changes or complex locking

**What issue's snippet misses:** The issue's code snippet shows line 413 as the SELECT check, but does not mention that create_user itself does a `db.query(User).count() == 0` at line 46 to determine admin status. This is a separate race condition (two concurrent first-users could both see count==0 and both become admin), but it's a distinct issue from the username uniqueness race.

## Acceptance Criteria

From issue #125:
- [ ] Concurrent registration requests for same username return 400 "Username already taken" instead of 500
- [ ] No unhandled IntegrityError propagates to client
- [ ] Existing registration flow (single request) still works correctly

## Fix Scope

**In scope:**
- app.py register() endpoint: wrap create_user call in try/except IntegrityError
- Import IntegrityError from sqlalchemy.exc

**Out of scope:**
- Sibling patterns (hotwords, voice_id, llm_jobs, summarize) - separate issues
- Admin-status race in create_user - separate issue
- Schema changes or advisory locks
