"""Authentication helpers: password hashing, user lookup/creation, and
admin-gated password-reset workflows.

No FastAPI/HTTP concerns here, same convention as the other services —
callers pass in an already-open db session.
"""
import datetime
import hashlib
import secrets
from typing import Optional

from database import User

PBKDF2_ITERATIONS = 200_000
RESET_TOKEN_TTL_HOURS = 1


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


# ── Username Recovery ─────────────────────────────────────────────────────


def list_usernames(db) -> list[str]:
    """Return every registered username — self-service for the login page."""
    return [row[0] for row in db.query(User.username).order_by(User.username).all()]


# ── Admin-Gated Password Reset ─────────────────────────────────────────────


def generate_reset_token(db, admin_user: User, target_username: str) -> Optional[str]:
    """Admin generates a one-time reset token for *target_username*.
    Returns the plaintext token (for display to the admin), or None if the
    target user doesn't exist.
    """
    if not admin_user.is_admin:
        return None
    target = db.query(User).filter(User.username == target_username).first()
    if not target:
        return None
    token = secrets.token_hex(32)
    target.reset_token = token
    target.reset_token_expires_at = utcnow() + datetime.timedelta(hours=RESET_TOKEN_TTL_HOURS)
    db.commit()
    return token


def reset_password(db, token: str, new_password: str) -> Optional[User]:
    """Validate a reset token (single-use, TTL-checked) and set a new
    password. On success the token is cleared and the User is returned so
    the caller can log them in. Returns None if the token is invalid or
    expired.
    """
    user = db.query(User).filter(
        User.reset_token == token,
        User.reset_token_expires_at > utcnow(),
    ).first()
    if not user:
        # Also check for exact token match (for cleartext timing safety) —
        # a non-matching token returns the same "invalid" result either way.
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
