"""Security helpers: CSRF protection, rate limiting, API key encryption.

No FastAPI/HTTP concerns here — callers pass in the request session dict
and get back tokens or validation results.
"""
import os
import time
import hashlib
import secrets
import base64
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# ── CSRF Protection ────────────────────────────────────────────────────────

def generate_csrf_token(session: dict) -> str:
    """Return the session's CSRF token, generating one only if none exists yet."""
    existing = session.get("csrf_token")
    if existing:
        return existing
    token = secrets.token_hex(32)
    session["csrf_token"] = token
    return token


def rotate_csrf_token(session: dict) -> str:
    """Generate a fresh CSRF token, replacing any existing one.

    Use on privilege escalation (login, register, password reset) to prevent session-fixation
    attacks where an anonymous-session token survives into an authenticated session.
    """
    token = secrets.token_hex(32)
    session["csrf_token"] = token
    return token


def validate_csrf_token(session: dict, token: str) -> bool:
    """Validate a CSRF token against the one stored in the session."""
    expected = session.get("csrf_token")
    if not expected or not token:
        return False
    return secrets.compare_digest(expected, token)


# ── Rate Limiting ──────────────────────────────────────────────────────────

class RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Tracks request timestamps per key (e.g. IP + endpoint) and rejects
    if the count in the window exceeds the limit. Not distributed-safe
    (single-process only) — adequate for a self-hosted app.
    """

    # Above this many keys, mutating calls first drop buckets whose entries
    # have all expired. Bounds memory when an attacker controls the key
    # (e.g. per-username login buckets, issue #124).
    MAX_KEYS = 1024
    # Longest window any caller uses; entries older than this are dead
    # regardless of which limit their bucket belongs to.
    _MAX_WINDOW_SECONDS = 3600

    def __init__(self):
        self._buckets: dict[str, list[float]] = {}

    def _evict_stale(self) -> None:
        if len(self._buckets) <= self.MAX_KEYS:
            return
        cutoff = time.time() - self._MAX_WINDOW_SECONDS
        self._buckets = {
            k: v for k, v in self._buckets.items() if v and v[-1] > cutoff
        }

    def check(self, key: str, max_requests: int = 10, window_seconds: int = 60) -> bool:
        """Check if *key* is within rate limits. Returns True if allowed."""
        self._evict_stale()
        now = time.time()
        bucket = self._buckets.get(key, [])
        # Prune expired entries
        cutoff = now - window_seconds
        bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= max_requests:
            self._buckets[key] = bucket
            return False
        bucket.append(now)
        self._buckets[key] = bucket
        return True

    def peek(self, key: str, max_requests: int = 10, window_seconds: int = 60) -> bool:
        """Like check(), but never records an attempt.

        check() consumes a slot on every allowed call, which is wrong for
        buckets that should only count *failures* (e.g. per-username login
        throttling, where a run of successful logins must not lock the
        account out). Callers pair peek() with an explicit record() on the
        failure path.
        """
        now = time.time()
        cutoff = now - window_seconds
        bucket = [t for t in self._buckets.get(key, []) if t > cutoff]
        self._buckets[key] = bucket
        return len(bucket) < max_requests

    def record(self, key: str) -> None:
        """Record one attempt against *key* (companion to peek())."""
        self._evict_stale()
        self._buckets.setdefault(key, []).append(time.time())


# Singleton — shared across all requests
rate_limiter = RateLimiter()


# ── API Key Encryption ─────────────────────────────────────────────────────

def _derive_fernet_key(secret: str, salt: bytes) -> bytes:
    """Derive a 32-byte Fernet-compatible key from the session secret."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8")))


def encrypt_api_key(plaintext: str, session_secret: str) -> str:
    """Encrypt an API key using a key derived from the session secret.

    Returns a base64-encoded token containing the salt + ciphertext.
    """
    salt = os.urandom(16)
    key = _derive_fernet_key(session_secret, salt)
    f = Fernet(key)
    token = f.encrypt(plaintext.encode("utf-8"))
    # Prepend salt so we can re-derive the key during decryption
    return base64.b64encode(salt + token).decode("utf-8")


def decrypt_api_key(encrypted: str, session_secret: str) -> str:
    """Decrypt an API key that was encrypted with encrypt_api_key()."""
    raw = base64.b64decode(encrypted.encode("utf-8"))
    salt = raw[:16]
    token = raw[16:]
    key = _derive_fernet_key(session_secret, salt)
    f = Fernet(key)
    return f.decrypt(token).decode("utf-8")
