"""Regression tests for the e2e harness itself (issue #316).

Two harness defects, both of which make other tests lie rather than fail:

1. The `/api/register` rate-limit bucket is a process-wide singleton and was
   never reset for e2e tests, so running the whole directory in one pytest
   invocation 429'd partway through and errored every remaining module's
   `registered_user` fixture. Fixed by conftest.py's `pytest_runtest_setup`.

2. `static/sw.js` reissues every `/api/*` request from the service worker's
   own scope, so `page.route()` on the shared `page` fixture never fires --
   a test that stubs a failure response silently gets the real success
   response instead and can still pass. Fixed by the `page_no_sw` fixture.

Cross-test leakage is the thing under test, so one test has to make a mess
and the next has to find it cleaned up -- pytest runs them in source order.
But neither test may *depend* on that order to pass: both must also pass
when selected on their own with `pytest <file>::<test>`, which is how
anybody debugging one of them will run it. So each registers under its own
username and asserts a result that holds either way. What running them in
order adds is the red side: with the reset removed, the second test finds a
full bucket left by the first.
"""
import http.cookiejar
import json
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.e2e

# app.py:501 -- rate_limiter.check(f"register:{client_ip}", max_requests=5, window_seconds=300)
REGISTER_LIMIT = 5

# Status codes /api/register actually returns. The duplicate-username branch
# is app.py:508's `raise HTTPException(status_code=400, detail="Username
# already taken")` -- a 400, not a 409. (The nine `_ensure_test_user` helpers
# under tests/e2e/ swallow only 409, so their duplicate guard never fires;
# harmless while every session gets a fresh temp DB, but don't copy it.)
REGISTER_OK = 200
REGISTER_DUPLICATE = 400
REGISTER_LIMITED = 429

# One username per test, so no test's outcome depends on whether another
# one ran first (see the module docstring).
_PROBE_USER = "e2e_harness_isolation_probe"
_RESET_PROBE_USER = "e2e_harness_isolation_reset_probe"
_PROBE_PASS = "e2e_harness_isolation_pass_123"


def _post_register(base_url, username, password):
    """POST /api/register and return the HTTP status code."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf_token = json.loads(opener.open(base_url + "/api/csrf-token", timeout=5).read()).get("token")
    req = urllib.request.Request(
        base_url + "/api/register",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token},
        method="POST",
    )
    try:
        return opener.open(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def test_register_bucket_really_fills_at_the_limit(live_server):
    """Establishes the mess the next test has to find cleaned up.

    Also pins the limit itself: the (LIMIT + 1)th registration from one
    client IP inside the window is rejected with 429, and none of the first
    LIMIT are. Without this, `test_..._cleared_between_e2e_tests` below
    could pass on an empty bucket that was never filled in the first place.
    """
    codes = [_post_register(live_server, _PROBE_USER, _PROBE_PASS) for _ in range(REGISTER_LIMIT + 1)]

    expected = [REGISTER_OK] + [REGISTER_DUPLICATE] * (REGISTER_LIMIT - 1) + [REGISTER_LIMITED]
    assert codes == expected, (
        f"expected the first registration to succeed, the next "
        f"{REGISTER_LIMIT - 1} to be rejected as duplicates, and #"
        f"{REGISTER_LIMIT + 1} to be rate-limited; got {codes}"
    )


def test_rate_limit_buckets_are_cleared_between_e2e_tests(live_server):
    """The fix for issue #316's defect 1.

    In source order the previous test leaves `register:127.0.0.1` full, and
    this one has to find it empty again: every e2e test must start from an
    empty bucket, otherwise the ninth module in the directory can never
    register its user and `pytest tests/e2e` cannot pass as a whole.

    Asserted twice, on the limiter's own state and on observable HTTP
    behaviour, because the second is what actually breaks other tests. Both
    assertions hold when this test is selected on its own -- it registers a
    username no other test uses, so a fresh registration is a success either
    way, and only a 429 (the leak) can make it fail.
    """
    from services.security import rate_limiter

    assert rate_limiter._buckets == {}, (
        f"rate-limit buckets leaked into this test: {sorted(rate_limiter._buckets)}"
    )
    assert _post_register(live_server, _RESET_PROBE_USER, _PROBE_PASS) == REGISTER_OK, (
        "a registration at the start of a fresh e2e test did not succeed "
        "(a 429 here means the per-test reset in tests/e2e/conftest.py is "
        "not running)"
    )


def _route_csrf_token_to_stub(pg, seen):
    """Stub GET /api/csrf-token with a sentinel token, recording each hit."""
    def handler(route):
        seen.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"token": "STUB-TOKEN-316"}),
        )

    pg.route("**/api/csrf-token", handler)


def _fetch_csrf_token(pg):
    return pg.evaluate("() => fetch('/api/csrf-token').then(r => r.json()).then(j => j.token)")


def test_page_no_sw_lets_page_route_intercept_api(page_no_sw):
    """The fix for issue #316's defect 2.

    With service workers blocked, a page-level route handler actually sees
    /api/* and its stub is what the page receives.

    The two steps before the routing are load-bearing, not ceremony. A
    freshly-created context has no controlling service worker yet even when
    service workers are allowed -- registration, install and activation all
    have to finish first -- so routing immediately after `goto` succeeds
    whether or not the worker was blocked, and proves nothing. Waiting on
    `navigator.serviceWorker.ready` gives the worker every chance to come
    up (it never resolves when blocked, hence the bounded timeout), and the
    reload then puts the page under its control if one exists. Only after
    that is `controller === null` evidence that blocking is what did it.
    """
    outcome = page_no_sw.evaluate(
        """() => new Promise(resolve => {
            if (!navigator.serviceWorker) return resolve('unsupported');
            const timer = setTimeout(() => resolve('never-ready'), 5000);
            navigator.serviceWorker.ready.then(() => {
                clearTimeout(timer);
                resolve('ready');
            });
        })"""
    )
    page_no_sw.reload()

    assert page_no_sw.evaluate("() => navigator.serviceWorker.controller") is None, (
        f"a service worker is controlling the page_no_sw fixture "
        f"(navigator.serviceWorker.ready reported {outcome!r}); "
        "service_workers='block' is not in effect on its context"
    )

    seen = []
    _route_csrf_token_to_stub(page_no_sw, seen)

    token = _fetch_csrf_token(page_no_sw)

    assert token == "STUB-TOKEN-316", (
        f"page.route() stub did not apply under page_no_sw; got token {token!r}"
    )
    assert len(seen) == 1, f"expected exactly one intercepted request, got {seen}"


def test_service_worker_defeats_page_route_on_the_plain_page_fixture(page):
    """Why `page_no_sw` has to exist -- this is the vacuous-test trap.

    On the shared `page` fixture the service worker is active, so the stub
    above is silently ignored and the page gets the real backend's token.
    A test written this way to drive an error state would assert against a
    success response and pass.

    If this ever fails, the service worker stopped swallowing /api/*: at
    that point `page_no_sw` is dead weight and should be removed, not
    quietly kept.
    """
    page.wait_for_function(
        "() => navigator.serviceWorker && navigator.serviceWorker.controller !== null",
        timeout=10000,
    )

    seen = []
    _route_csrf_token_to_stub(page, seen)

    token = _fetch_csrf_token(page)

    assert token != "STUB-TOKEN-316", (
        "page.route() intercepted /api/* on the plain `page` fixture -- the "
        "service worker no longer swallows API requests, so page_no_sw and "
        "this test are both obsolete"
    )
    assert seen == [], f"route handler fired despite the service worker: {seen}"
