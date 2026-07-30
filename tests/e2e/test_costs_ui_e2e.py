"""Browser-driven e2e test for Costs page and Queue budget gauge (issue #210).

Verifies via Playwright on a real served page (rack.min.js via static/index.html):
1. 'Costs' button in rail nav navigates to #page-costs.
2. Costs page renders Monthly Spend, Lifetime Spend, and Rate-Limit Budget units.
3. Queue page renders the rate-limit budget gauge.
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
    _ensure_test_user(live_server, "e2e_costs_test_user", "e2e_costs_pass_1234")
    return ("e2e_costs_test_user", "e2e_costs_pass_1234")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def test_costs_page_and_queue_gauge_navigation(page, registered_user, live_server):
    """Navigate to Costs page and Queue page in real browser and verify UI elements."""
    username, password = registered_user
    page.goto(live_server)
    _login(page, username, password)

    # 1. Click Costs rail button
    costs_btn = page.locator("button[data-nav='costs']")
    assert costs_btn.is_visible()
    costs_btn.click()

    # Wait for #page-costs to become active
    page.wait_for_selector("#page-costs.active", timeout=5000)

    # Verify Costs page content
    page_text = page.locator("#page-costs").inner_text().lower()
    assert "costs" in page_text
    assert "monthly spend" in page_text
    assert "lifetime spend" in page_text
    assert "rate-limit budget" in page_text

    # 2. Click Queue rail button
    queue_btn = page.locator("button[data-nav='queue']")
    assert queue_btn.is_visible()
    queue_btn.click()

    # Wait for #page-queue to become active
    page.wait_for_selector("#page-queue.active", timeout=5000)

    # Verify Queue page budget gauge
    gauge_locator = page.locator(".budget-gauge")
    page.wait_for_selector(".budget-gauge", timeout=5000)
    gauge_text = gauge_locator.inner_text()
    assert "audio-seconds used today" in gauge_text
