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
