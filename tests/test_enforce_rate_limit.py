"""_enforce_rate_limit (app.py, issue #406 dedup): the shared 429 gate now
used at all 7 rate_limiter.check() call sites in app.py (register, login,
forgot-username, forgot-password, reset-password, admin-invite-mint,
device-upload). Login's per-account peek/record throttle deliberately still
has its own inline logic and is out of scope here.

Mutation check: test_raises_429... fails if the raise is dropped (a full
bucket would then silently let the call through). test_falsy_enabled...
fails if the `if not enabled: return` early-out is dropped (a disabled
check would then still consume/inspect the bucket, and with a 1-request
cap it would raise instead of running 50 times cleanly).
"""
import pytest
from fastapi import HTTPException

from app import _enforce_rate_limit
from services.security import rate_limiter


def _clear(key):
    rate_limiter._buckets.pop(key, None)


def test_enforce_rate_limit_raises_429_with_exact_detail_when_bucket_full():
    key = "test-enforce-rate-limit:full"
    _clear(key)
    try:
        for _ in range(3):
            _enforce_rate_limit(True, key, max_requests=3, window_seconds=60, detail="nope")
        with pytest.raises(HTTPException) as exc_info:
            _enforce_rate_limit(True, key, max_requests=3, window_seconds=60, detail="nope")
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "nope"
    finally:
        _clear(key)


def test_enforce_rate_limit_falsy_enabled_skips_and_consumes_no_bucket_slot():
    key = "test-enforce-rate-limit:disabled"
    _clear(key)
    try:
        # max_requests=1 means a single real check() call would already be
        # at capacity on the second iteration; enabled=False must skip the
        # check entirely, so this must run all 50 times without ever raising
        # and without ever creating the bucket key.
        for _ in range(50):
            _enforce_rate_limit(False, key, max_requests=1, window_seconds=60, detail="nope")
        assert key not in rate_limiter._buckets
    finally:
        _clear(key)
