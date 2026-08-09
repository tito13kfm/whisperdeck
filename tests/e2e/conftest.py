"""Fixtures for browser-driven e2e tests.

Starts a real uvicorn server in a background thread (so the browser can hit
it over a real socket) and skips the whole module if Playwright or
Chromium aren't installed. Data isolation (the WHISPERDECK_DATA_DIR
redirect) comes from tests/conftest.py at module import time. The
rate-limiter reset does NOT: that one lives inside the parent conftest's
`client` fixture, which no e2e test requests, so this file resets the
limiter itself in pytest_runtest_setup below.
"""
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest

# ── Skip the whole module if Playwright is not installed ─────────────────────
playwright = pytest.importorskip("playwright", reason="Playwright not installed; run `pip install -r requirements-browser.txt`")


# ── Rate-limit isolation for every test in this directory ────────────────────
@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Clear the process-wide rate-limit buckets before each e2e test.

    `rate_limiter` (services/security.py) is a module-level singleton, and
    live_server runs uvicorn in a background *thread* of this same
    interpreter, so the serving code and this hook share one _buckets dict.
    Nine files under tests/e2e each register a user through a module-scoped
    `registered_user` fixture, against a limit of 5 registrations per 300s
    (app.py's `register:{client_ip}` bucket) -- so running the directory in
    one pytest invocation used to 429 partway through and error out every
    remaining module's fixture. /api/login's 10-per-60s bucket has the same
    shape and is cleared by the same call.

    This is a hook rather than an autouse fixture on purpose. `registered_user`
    is module-scoped, and pytest instantiates higher-scoped fixtures first, so
    a function-scoped autouse fixture here would run *after* the registration
    it is supposed to protect. pytest_runtest_setup runs before any fixture
    setup for the item, which is the only placement that covers it.

    Same reset call as tests/conftest.py's `client` fixture, which solved the
    identical problem for the non-e2e suite.
    """
    from services.security import rate_limiter
    rate_limiter._buckets.clear()


# ── live_server: start uvicorn in a background thread for the test session ──
@pytest.fixture(scope="session")
def live_server():
    """A session-scoped uvicorn server bound to a free localhost port.

    Uses the real FastAPI app from app.py so the same routes, middleware,
    and CSRF/rate-limit logic the browser would see in production is what
    the test sees. Data isolation is already handled by tests/conftest.py
    (WHISPERDECK_DATA_DIR redirect).
    """
    import uvicorn
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import app as app_module

    # Find a free port without holding it (uvicorn will rebind)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        app_module.app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="e2e-uvicorn", daemon=True)
    thread.start()

    # Wait for the server to accept connections (max ~5s)
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/", timeout=0.5) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail(f"live_server did not start on {base_url} within 5s")

    yield base_url

    server.should_exit = True
    thread.join(timeout=5)


# ── browser: per-test Chromium instance, headless ───────────────────────────
@pytest.fixture()
def browser():
    """A fresh headless Chromium for each test.

    pytest-playwright provides a `browser` fixture of its own, but its
    scope and CLI flags (`--headed`, `--browser=firefox`) can be surprising
    in CI. This one is minimal: headless chromium, no tracing, no video.
    Screenshots are easy to take in the test body via `page.screenshot()`.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture()
def page(browser, live_server):
    """A blank page already navigated to the test server's root."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.goto(live_server + "/")
    yield pg
    ctx.close()


@pytest.fixture()
def page_no_sw(browser, live_server):
    """Like `page`, but with service workers blocked for the whole context.

    Use this for any test that intercepts network traffic -- page.route(),
    page.expect_request(), page.expect_response(). static/sw.js registers a
    service worker at root scope (static/rack.js's registration is
    unconditional) whose fetch handler answers every /api/* request with
    `e.respondWith(fetch(e.request)...)`. That reissued fetch originates in
    the worker, not the page, so a page-level route handler never fires: the
    real backend answers, the stub is silently ignored, and a test written
    to drive an error or empty state passes against the success response
    instead. Blocking service workers for the context is the fix, and it is
    the only mechanism this app supports -- there is no flag or query param
    that suppresses the registration.

    Tests that don't touch the network should keep using `page`, so the
    service worker stays on the path the real browser actually takes.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
    pg = ctx.new_page()
    pg.goto(live_server + "/")
    yield pg
    ctx.close()
