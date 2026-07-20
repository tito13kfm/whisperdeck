"""Smoke test: the app actually loads in a real headless browser.

This is the "is the e2e infrastructure working?" test. It exercises:
  - the live_server fixture (uvicorn in a thread)
  - the browser fixture (headless chromium)
  - the page fixture (navigated context)
  - a real DOM query against the rendered page
  - the auth flow end-to-end (register via API, login via UI)

If this passes, future e2e tests can rely on the same fixtures. The test
itself is a no-op for app behavior — it just proves we can drive the UI.
"""
import http.cookiejar
import urllib.request
import json
import pytest

pytestmark = pytest.mark.e2e


# Pre-register the e2e_smoke user via the live server's /api/register
# endpoint so the login flow has a real account to authenticate against.
# Done at module-import time rather than as a fixture so the network
# call only happens once and is visible if it fails.
def _ensure_test_user(base_url):
    # /api/register is CSRF-protected too (issue #36): use a cookie-jar-backed
    # opener so the anonymous session from GET /api/csrf-token carries over
    # to the register POST, same as the SPA's checkAuth() -> register flow.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf_token = json.loads(opener.open(base_url + "/api/csrf-token", timeout=5).read()).get("token")
    body = json.dumps({"username": "e2e_smoke", "password": "e2e_smoke_pass_123"}).encode()
    req = urllib.request.Request(
        base_url + "/api/register",
        data=body,
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token},
        method="POST",
    )
    try:
        opener.open(req, timeout=5).read()
    except urllib.error.HTTPError as e:
        # 409 = already registered, which is fine for a re-run.
        if e.code != 409:
            raise


@pytest.fixture(scope="module")
def registered_user(live_server):
    _ensure_test_user(live_server)
    return ("e2e_smoke", "e2e_smoke_pass_123")


def test_login_page_renders(page):
    """The login form is visible on first load."""
    page.wait_for_selector(
        "input[name='username'], #username, input[type='text']", timeout=5000
    )
    page.wait_for_selector("input[type='password']", timeout=2000)
    page.wait_for_selector(
        "button[type='submit'], button:has-text('Sign'), button:has-text('Log')",
        timeout=2000,
    )
    assert (
        "whisper" in page.title().lower()
        or page.locator("text=WhisperDeck").count() > 0
    )


def test_login_then_see_app_shell(page, registered_user, live_server):
    """Submitting valid creds reveals the rail (the page navigation)."""
    username, password = registered_user

    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click(
        "button[type='submit'], button:has-text('Sign'), button:has-text('Log')"
    )

    # The app shell's <div id="app-shell"> exists from page load but is
    # hidden by `display:none` until `checkAuth()` succeeds. Wait for it
    # to be both attached AND visible. The CSRF token fetch + auth
    # round-trip + state flip takes a beat.
    page.wait_for_selector(
        "#app-shell",
        state="visible",
        timeout=10000,
    )

    # The rail (a flex column of page links) is part of the shell and
    # only mounts after auth. Wait for at least one of the page links.
    page.wait_for_selector(
        ".rail a, .rail-btn, [data-page], a:has-text('Transcribe'), "
        "a:has-text('Bank'), a:has-text('Queue'), a:has-text('Settings'), "
        "a:has-text('Jobs')",
        timeout=10000,
    )

    body_text = page.locator("body").inner_text()
    assert "whisper" in body_text.lower()
