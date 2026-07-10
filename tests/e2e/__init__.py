r"""E2E test package — real-browser tests via Playwright.

These tests are NOT part of the default `pytest` run. They require:
  - Playwright + Chromium installed (`pip install -r requirements-browser.txt`
    followed by `playwright install chromium`).
  - A real HTTP server (the `live_server` fixture below starts one for the
    test session).
  - The `e2e` marker.

Run only the e2e suite with:
    .venv\Scripts\python.exe -m pytest tests/e2e -m e2e

Skip e2e tests in the default suite with:
    .venv\Scripts\python.exe -m pytest -m "not e2e"
"""
