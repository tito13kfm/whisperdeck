"""Fixtures for browser-driven e2e tests.

Starts a real uvicorn server in a background thread (so the browser can hit
it over a real socket) and skips the whole module if Playwright or
Chromium aren't installed. Per-test isolation is handled by conftest.py
at the parent level (WHISPERDECK_DATA_DIR redirect, rate-limiter reset).
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
