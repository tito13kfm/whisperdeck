"""Per-account login throttling (issue #124).

The IP bucket (login:{ip}, 10/60s) is unchanged. These tests cover the new
failures-only bucket keyed on (hashed username, client IP) at 5/300s, built
on RateLimiter.peek()/record() — check() consumes a slot on every allowed
call, which would lock an account after 5 *successful* logins.

The key is scoped by client IP on purpose: a pure per-username key would
let anyone keep an arbitrary account locked out of login with a trickle of
wrong passwords (usernames are enumerable by design via /api/forgot-username).
Under TestClient every request shares client IP "testclient", so these tests
exercise the single-client behavior; the IP scoping itself is pinned at the
key level in TestRateLimiterPrimitives.

Mutation checks: test_five_failed_logins_lock_username fails if record() is
never called on the failure path (the 6th attempt would be a plain 401) or
if peek() always returns True. test_successful_logins_do_not_consume_bucket
fails if the peek/record split is "simplified" back to a naive check().
"""
import hashlib
import time

from services.security import RateLimiter, rate_limiter


def _user_key(username, client_ip="testclient"):
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:32]
    return f"login-user:{digest}:{client_ip}"


def _fresh_csrf(client):
    token = client.get("/api/csrf-token").json()["token"]
    client.headers["X-CSRF-Token"] = token


def _login(client, username, password):
    return client.post(
        "/api/login", json={"username": username, "password": password}
    )


class TestPerAccountBucket:
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

    def test_key_embeds_digest_not_raw_username(self, client):
        # Attacker-chosen usernames must not control bucket-key size: the
        # key stores a fixed-length digest, never the raw string.
        huge = "x" * 100_000
        assert _login(client, huge, "wrongpass").status_code == 401
        assert _user_key(huge) in rate_limiter._buckets
        assert all(len(k) < 128 for k in rate_limiter._buckets)

    def test_successful_logins_do_not_consume_bucket(self, client):
        # More successes than the bucket holds (5), staying under the IP
        # bucket (10/60s). Login rotates the CSRF token, so re-fetch it
        # after every success.
        for _ in range(7):
            resp = _login(client, "testuser", "testpass123")
            assert resp.status_code == 200
            _fresh_csrf(client)

    def test_failures_unlock_after_window(self, client):
        # Backdate the recorded failures past the 300s window and confirm
        # the account unlocks — pins the sliding-window prune in peek().
        for _ in range(5):
            assert _login(client, "testuser", "wrongpass").status_code == 401
        key = _user_key("testuser")
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

    def test_ip_scoped_keys_are_distinct(self):
        # The lockout-DoS defense: the same username from two clients maps
        # to two independent buckets.
        assert _user_key("victim", "1.2.3.4") != _user_key("victim", "5.6.7.8")

    def test_cap_drops_fully_stale_buckets_first(self):
        rl = RateLimiter()
        rl.MAX_KEYS = 3
        stale = time.time() - rl._MAX_WINDOW_SECONDS - 1
        for i in range(4):
            rl._buckets[f"stale:{i}"] = [stale]
        rl.record("fresh")
        assert "fresh" in rl._buckets
        assert not any(k.startswith("stale:") for k in rl._buckets)

    def test_cap_is_hard_even_for_fresh_keys(self):
        # MAX_KEYS is a bound, not just a sweep trigger: a flood of fresh
        # keys still cannot grow the dict past the cap (+1 for the insert
        # that follows the enforcement pass).
        rl = RateLimiter()
        rl.MAX_KEYS = 10
        for i in range(50):
            rl.record(f"fresh:{i}")
        assert len(rl._buckets) <= rl.MAX_KEYS + 1

    def test_cap_keeps_newest_buckets(self):
        rl = RateLimiter()
        rl.MAX_KEYS = 2
        now = time.time()
        rl._buckets["old"] = [now - 100]
        rl._buckets["newer"] = [now - 10]
        rl._buckets["newest"] = [now - 1]
        rl.record("fresh")
        assert "fresh" in rl._buckets
        assert "old" not in rl._buckets
