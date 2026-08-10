"""Per-username login throttling (issue #124).

The IP bucket (login:{ip}, 10/60s) is unchanged. These tests cover the new
failures-only per-username bucket (login-username:{username}, 5/300s) built
on RateLimiter.peek()/record() — check() consumes a slot on every allowed
call, which would lock an account after 5 *successful* logins.

Mutation checks: test_five_failed_logins_lock_username fails if record() is
never called on the failure path (the 6th attempt would be a plain 401) or
if peek() always returns True. test_successful_logins_do_not_consume_bucket
fails if the peek/record split is "simplified" back to a naive check().
"""
import time

from services.security import RateLimiter, rate_limiter


def _fresh_csrf(client):
    token = client.get("/api/csrf-token").json()["token"]
    client.headers["X-CSRF-Token"] = token


def _login(client, username, password):
    return client.post(
        "/api/login", json={"username": username, "password": password}
    )


class TestPerUsernameBucket:
    def test_five_failed_logins_lock_username(self, client):
        for _ in range(5):
            resp = _login(client, "testuser", "wrongpass")
            assert resp.status_code == 401
        resp = _login(client, "testuser", "wrongpass")
        assert resp.status_code == 429
        assert "account" in resp.json()["detail"]

    def test_429_fires_even_with_correct_password(self, client):
        # The lock must not act as a password-validity oracle: once the
        # bucket is full, the correct password also gets 429, not 200/401.
        for _ in range(5):
            assert _login(client, "testuser", "wrongpass").status_code == 401
        resp = _login(client, "testuser", "testpass123")
        assert resp.status_code == 429

    def test_bucket_is_per_username(self, client):
        for _ in range(5):
            assert _login(client, "testuser", "wrongpass").status_code == 401
        # A different (even nonexistent) username is judged on its own
        # bucket: plain 401, not 429.
        resp = _login(client, "someone-else", "wrongpass")
        assert resp.status_code == 401

    def test_successful_logins_do_not_consume_bucket(self, client):
        # More successes than the bucket holds (5), staying under the IP
        # bucket (10/60s). Login rotates the CSRF token, so re-fetch it
        # after every success.
        for _ in range(7):
            resp = _login(client, "testuser", "testpass123")
            assert resp.status_code == 200
            _fresh_csrf(client)

    def test_failures_then_success_after_window_would_clear(self, client):
        # Backdate the recorded failures past the 300s window and confirm
        # the account unlocks — pins the sliding-window prune in peek().
        for _ in range(5):
            assert _login(client, "testuser", "wrongpass").status_code == 401
        key = "login-username:testuser"
        rate_limiter._buckets[key] = [t - 301 for t in rate_limiter._buckets[key]]
        resp = _login(client, "testuser", "testpass123")
        assert resp.status_code == 200


class TestRateLimiterPrimitives:
    def test_peek_does_not_consume(self):
        rl = RateLimiter()
        for _ in range(100):
            assert rl.peek("k", max_requests=5, window_seconds=300)
        assert rl._buckets.get("k", []) == []

    def test_record_appends_and_peek_sees_it(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.record("k")
        assert not rl.peek("k", max_requests=5, window_seconds=300)
        assert rl.peek("k", max_requests=6, window_seconds=300)

    def test_check_still_consumes(self):
        # Existing callers rely on check() recording every allowed call.
        rl = RateLimiter()
        for _ in range(3):
            assert rl.check("k", max_requests=3, window_seconds=60)
        assert not rl.check("k", max_requests=3, window_seconds=60)

    def test_eviction_drops_fully_stale_buckets(self):
        rl = RateLimiter()
        rl.MAX_KEYS = 3
        stale = time.time() - rl._MAX_WINDOW_SECONDS - 1
        for i in range(4):
            rl._buckets[f"stale:{i}"] = [stale]
        rl.record("fresh")
        assert "fresh" in rl._buckets
        assert not any(k.startswith("stale:") for k in rl._buckets)

    def test_eviction_keeps_live_buckets(self):
        rl = RateLimiter()
        rl.MAX_KEYS = 2
        now = time.time()
        rl._buckets["live:0"] = [now]
        rl._buckets["live:1"] = [now]
        rl._buckets["stale:0"] = [now - rl._MAX_WINDOW_SECONDS - 1]
        rl.record("fresh")
        assert set(rl._buckets) == {"live:0", "live:1", "fresh"}
