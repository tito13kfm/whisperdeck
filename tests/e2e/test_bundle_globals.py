"""e2e regression for issue #214: esbuild --bundle hides top-level globals.

rack.js is a classic (non-module) script — its top-level function/const
declarations are window globals when loaded directly.  But esbuild --bundle
wraps the file's scope, so rack.min.js (the actual served file) hides them.

This test loads the REAL served page (rack.min.js, via static/index.html),
logs in, and asserts every symbol Playwright tooling depends on is a
callable window global.  If the Object.assign(window, {...}) export block
at the end of rack.js is removed or incomplete, this test fails.

Mutation check: removing the export block from rack.js (and rebuilding
rack.min.js) causes `typeof window.navigate` to be `'undefined'` — this
test catches that.
"""
import http.cookiejar
import json
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.e2e


def _ensure_test_user(base_url, username, password):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf_token = json.loads(opener.open(base_url + "/api/csrf-token", timeout=5).read()).get("token")
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        base_url + "/api/register",
        data=body,
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token},
        method="POST",
    )
    try:
        opener.open(req, timeout=5).read()
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise


@pytest.fixture(scope="module")
def registered_user(live_server):
    _ensure_test_user(live_server, "e2e_bundle_globals_test", "e2e_bundle_globals_pass_123")
    return ("e2e_bundle_globals_test", "e2e_bundle_globals_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


# Symbols that Playwright / screenshot tooling depends on (see issue #214).
# Each must be a callable function on `window` after the bundled rack.min.js
# loads — the Object.assign(window, {...}) block at the end of rack.js is
# what makes this true.
REQUIRED_GLOBALS = ["navigate", "S", "syncTranscribe", "renderDetail", "curProv", "logout", "api"]


def test_bundled_rack_exposes_tooling_globals(page, registered_user, live_server):
    """Load the real rack.min.js (served via static/index.html) and verify
    every symbol Playwright tooling needs is a window global."""
    username, password = registered_user
    _login(page, username, password)

    # The app-shell is visible, so rack.min.js has loaded and executed.
    # Assert each required symbol is present and callable on window.
    for name in REQUIRED_GLOBALS:
        ok = page.evaluate(f"() => typeof window.{name} === 'function' || typeof window.{name} === 'object'")
        typ = page.evaluate(f"() => typeof window.{name}")
        assert ok, f"window.{name} is {typ}, expected function or object — export block missing or incomplete"
