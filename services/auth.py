"""Authentication helpers: password hashing, user lookup/creation, and
admin-gated password-reset workflows.

No FastAPI/HTTP concerns here, same convention as the other services —
callers pass in an already-open db session.
"""
import datetime
import os
import hashlib
import secrets
from typing import Optional

from sqlalchemy import text

from database import InviteToken, User

PBKDF2_ITERATIONS = 200_000
RESET_TOKEN_TTL_HOURS = 1
REGISTRATION_MODES = ("open", "invite", "closed")
INVITE_TOKEN_TTL_HOURS = 72


def hash_reset_token(token: str) -> str:
    """Hash a reset token for storage. Uses SHA-256 (not PBKDF2) because
    the token itself is 256-bit random — no need for slow key derivation.
    Prevents account takeover if the database is leaked within the TTL."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utcnow():
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def generate_salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return secrets.compare_digest(hash_password(password, salt), expected_hash)


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


def create_user(db, username: str, password: str) -> User:
    """Create a new user. The first user is auto-admin, elected by id == 1
    *after* insert rather than by a pre-insert count: SQLite assigns rowids
    under writer serialization, so concurrent first registrations cannot
    both win (issue #301). Deleting every user and re-registering still
    re-elects an admin (rowid restarts at 1 without AUTOINCREMENT)."""
    salt = generate_salt()
    user = User(
        username=username,
        password_salt=salt,
        password_hash=hash_password(password, salt),
        is_admin=False,
    )
    db.add(user)
    db.flush()  # assigns user.id inside the open transaction
    user.is_admin = user.id == 1
    db.commit()
    return user


def authenticate_user(db, username: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None
    if not verify_password(password, user.password_salt, user.password_hash):
        return None
    return user


def get_or_create_fallback_user(db) -> tuple[User, Optional[str]]:
    """Used only during migration of a pre-existing database, to own rows
    that predate user accounts. Username 'local'.

    Returns (user, plaintext_password). The password is generated randomly
    on creation and returned exactly once so the caller can print it; on
    every later call (user already exists) it is None. A static default
    here would sit outside the password policy forever (issue #302)."""
    user = db.query(User).filter(User.username == "local").first()
    if user:
        return user, None
    password = secrets.token_urlsafe(16)
    return create_user(db, "local", password), password


# ── Registration Gate (issue #395) ─────────────────────────────────────────


def registration_mode(db) -> str:
    """Current registration mode: 'open' | 'invite' | 'closed'.

    Read at call time (same convention as password_min_length) so env
    changes and test monkeypatching take effect without restart.

    - Zero users: always 'open' — the first (admin) registration must be
      possible, or the install is bricked. A conflicting env value is
      ignored with a warning.
    - Otherwise: REGISTRATION_MODE env var if valid, else 'invite'.
      'invite' with no outstanding tokens rejects everything, exactly like
      'closed', but lets an admin onboard someone at runtime by minting an
      invite from the Service panel instead of editing env vars.
    """
    env = os.environ.get("REGISTRATION_MODE", "").strip().lower()
    if env == "open":
        # Zero users would also resolve to open — skip the count on the
        # bootstrap/register hot path when it can't change the answer.
        return "open"
    if db.query(User).count() == 0:
        if env in ("invite", "closed"):
            print(f"[auth] REGISTRATION_MODE={env} ignored while no users exist — "
                  "the first registration must stay open")
        return "open"
    if env in REGISTRATION_MODES:
        return env
    return "invite"


def hash_invite_token(token: str) -> str:
    """Same construction and rationale as hash_reset_token: the token is
    256-bit random, so plain SHA-256 (not PBKDF2) is enough to make a DB
    leak useless within the TTL."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_invite_token(db, admin_user: User) -> Optional[tuple[str, datetime.datetime]]:
    """Admin mints a single-use registration invite. Returns
    (plaintext_token, expires_at) for one-time display, or None if the
    caller is not an admin."""
    if not admin_user.is_admin:
        return None
    token = secrets.token_hex(32)
    expires_at = utcnow() + datetime.timedelta(hours=INVITE_TOKEN_TTL_HOURS)
    db.add(InviteToken(
        token_hash=hash_invite_token(token),
        created_by=admin_user.id,
        expires_at=expires_at,
    ))
    db.commit()
    return token, expires_at


def get_valid_invite_token(db, token: str) -> Optional[InviteToken]:
    """Non-consuming validity peek (unused, unexpired). Lets the route
    report a bad token before password-policy errors, mirroring the
    reset-password ordering convention."""
    return db.query(InviteToken).filter(
        InviteToken.token_hash == hash_invite_token(token),
        InviteToken.used_at.is_(None),
        InviteToken.expires_at > utcnow(),
    ).first()


def consume_invite_token(db, token: str) -> bool:
    """Compare-and-set consumption: marks the token used iff it is still
    unused and unexpired. Executes but does NOT commit — the caller's
    create_user() commit finalizes both atomically, so a registration that
    fails after this call (e.g. username UNIQUE collision → rollback) does
    not burn the token. Under SQLite's writer serialization the loser of a
    concurrent race matches zero rows and gets False."""
    res = db.execute(
        text(
            "UPDATE invite_tokens SET used_at = :now "
            "WHERE token_hash = :h AND used_at IS NULL AND expires_at > :now"
        ),
        {"h": hash_invite_token(token), "now": utcnow()},
    )
    return res.rowcount == 1


def mark_invite_used(db, token: str, user_id: int) -> None:
    """Audit-trail backfill of used_by after a successful invite
    registration. Genuinely best-effort: the registration has already
    committed and the token is consumed, so a failure here (e.g. a lost
    SQLite write lock) must not turn a successful registration into a 500."""
    try:
        row = db.query(InviteToken).filter(
            InviteToken.token_hash == hash_invite_token(token)
        ).first()
        if row:
            row.used_by = user_id
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"[auth] invite used_by backfill failed (non-fatal): {e}")


# ── Username Recovery ─────────────────────────────────────────────────────


def list_usernames(db) -> list[str]:
    """Return every registered username — self-service for the login page."""
    return [row[0] for row in db.query(User.username).order_by(User.username).all()]


# ── Admin-Gated Password Reset ─────────────────────────────────────────────


def generate_reset_token(db, admin_user: User, target_username: str) -> Optional[str]:
    """Admin generates a one-time reset token for *target_username*.
    Returns the plaintext token (for display to the admin), or None if the
    target user doesn't exist. The token is hashed before storage so a DB
    leak within the TTL window cannot be used for account takeover.
    """
    if not admin_user.is_admin:
        return None
    target = db.query(User).filter(User.username == target_username).first()
    if not target:
        return None
    token = secrets.token_hex(32)
    target.reset_token = hash_reset_token(token)
    target.reset_token_expires_at = utcnow() + datetime.timedelta(hours=RESET_TOKEN_TTL_HOURS)
    db.commit()
    return token


def get_user_by_reset_token(db, token: str) -> Optional[User]:
    """Look up a user by a reset token (TTL-checked) without consuming it.
    Returns None if the token is invalid or expired. Used by the route to
    validate the token BEFORE checking password policy, so a bad token +
    weak password reports the token error, not the password error.
    """
    token_hash = hash_reset_token(token)
    return db.query(User).filter(
        User.reset_token == token_hash,
        User.reset_token_expires_at > utcnow(),
    ).first()


def reset_password(db, token: str, new_password: str) -> Optional[User]:
    """Validate a reset token (single-use, TTL-checked) and set a new
    password. On success the token is cleared and the User is returned so
    the caller can log them in. Returns None if the token is invalid or
    expired.
    """
    user = get_user_by_reset_token(db, token)
    if not user:
        return None
    salt = generate_salt()
    user.password_salt = salt
    user.password_hash = hash_password(new_password, salt)
    user.reset_token = None
    user.reset_token_expires_at = None
    db.commit()
    return user


# ── Admin User Management ──────────────────────────────────────────────────


def set_admin_status(db, admin_user: User, target_username: str, is_admin: bool) -> Optional[User]:
    """Promote or demote another user. Admin cannot demote themselves.
    Returns the updated user, or None on failure.
    """
    if not admin_user.is_admin:
        return None
    target = db.query(User).filter(User.username == target_username).first()
    if not target:
        return None
    if not is_admin and target.id == admin_user.id:
        return None  # cannot demote self
    target.is_admin = is_admin
    db.commit()
    return target


def get_all_users(db) -> list[dict]:
    """Admin-only: list all users with their admin status and join date."""
    users = db.query(User).order_by(User.id.asc()).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "is_admin": bool(u.is_admin),
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


def password_min_length() -> int:
    """Read and validate PASSWORD_MIN_LENGTH from the environment.
    Falls back to 8 if unset or non-numeric; clamps to >= 1 so a
    misconfiguration cannot silently disable the length check.
    Single source of truth — the route injects this into the page
    meta tag so the client and hint text read from the same value.
    """
    try:
        min_length = int(os.environ.get("PASSWORD_MIN_LENGTH", "8"))
    except (ValueError, TypeError):
        min_length = 8
    return max(min_length, 1)


def validate_password(password: str) -> tuple[bool, str]:
    """Validate password against the minimum policy.
    Returns (ok, reason). Reads PASSWORD_MIN_LENGTH at call time (not import)
    so tests that set env vars work correctly. Falls back to 8 if the env
    var is non-numeric; clamps to >= 1 so a misconfiguration cannot silently
    disable the length check. Uses ASCII-only letter/digit checks to match
    the client-side mirror in rack.js (avoids a Unicode/regex asymmetry
    where non-ASCII letters pass server but fail client pre-check).
    """
    min_length = password_min_length()
    if len(password) < min_length:
        return False, f"Password must be at least {min_length} characters"
    has_letter = any(c.isascii() and c.isalpha() for c in password)
    has_digit = any(c.isascii() and c.isdigit() for c in password)
    if not has_letter:
        return False, "Password must contain at least one letter"
    if not has_digit:
        return False, "Password must contain at least one digit"
    return True, ""

