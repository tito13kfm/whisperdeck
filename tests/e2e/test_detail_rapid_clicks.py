"""e2e regression for issue #129: rapid clicks on different transcript rows
must always end up showing the most-recently-clicked transcript, even when
the first-clicked row's API response arrives AFTER the second-clicked row's.

The bug: loadTranscriptDetail() unconditionally assigned the awaited
/api/transcripts/{id} response to detailData without verifying the user
hadn't already navigated elsewhere. Click A then click B with A's backend
response slow: A would overwrite B's detail view once it finally resolved.

The fix: a module-level detailLoadGen counter is incremented at the start
of every loadTranscriptDetail() call; the response is only committed if
the counter still matches after the await, so a slower, older call's
response is discarded once a newer one has started.

This test controls resolution order deterministically instead of racing
real network timing. Two attempts at a network-delay-based version both
failed for reasons unrelated to the fix itself: static/sw.js intercepts
/api/ requests inside its own fetch handler, which Playwright's page-level
route interception can't see (page.route only hooks frame-initiated
requests) — service_workers="block" on the context works around that, but
a real delay then needs to run without blocking Playwright's own sync-API
driver thread, and neither a direct time.sleep() (blocks the whole driver,
delaying the *second* navigate() call too) nor a background thread
(route.continue_() called off Playwright's own thread silently hangs) is
safe. Monkey-patching the page's global fetch() to hand back promises the
test resolves itself sidesteps all of that and is exactly as deterministic
as the assertion needs to be.

Patch window.fetch, not window.api: static/rack.js is bundled by esbuild
into an IIFE, and its bootstrap does `Object.assign(window, { api, ... })`
once at load time as a value copy — internal code calls the bare `api`
identifier, which resolves to rack.js's own closure-local binding, not to
`window.api`. Reassigning window.api after load therefore never intercepts
anything rack.js calls. `fetch` is never declared locally in rack.js, so it
stays a free variable resolved against the real global on every call,
which is what makes patching window.fetch actually take effect.
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
    _ensure_test_user(live_server, "e2e_rapid_clicks_test", "e2e_rapid_clicks_pass_123")
    return ("e2e_rapid_clicks_test", "e2e_rapid_clicks_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def _make_two_transcripts(username):
    """Create two transcripts with distinguishable titles so the detail
    view's heading reveals which one is currently shown."""
    import app as app_module
    from database import Transcript, User

    db = app_module.SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        ta = Transcript(
            user_id=user.id, title="ALPHA-transcript", filename="a.mp3",
            status="completed", kind="meeting",
            segments=[{"start": 0, "end": 2, "speaker": "A", "text": "alpha"}],
        )
        tb = Transcript(
            user_id=user.id, title="BETA-transcript", filename="b.mp3",
            status="completed", kind="meeting",
            segments=[{"start": 0, "end": 2, "speaker": "B", "text": "beta"}],
        )
        db.add_all([ta, tb])
        db.commit()
        return ta.id, tb.id
    finally:
        db.close()


def test_rapid_clicks_show_last_clicked_even_when_first_response_is_slow(page, registered_user, live_server):
    username, password = registered_user
    _login(page, username, password)

    a_id, b_id = _make_two_transcripts(username)

    # Trap fetch() calls for single-transcript fetches so the test controls
    # exactly when each one resolves, instead of relying on real network
    # delay (see module docstring for why that approach doesn't work here,
    # and why this patches window.fetch rather than window.api).
    page.evaluate("""() => {
        window.__origFetch = window.fetch;
        window.__pending = {};
        window.fetch = function(url, opts) {
            if (/\\/api\\/transcripts\\/\\d+$/.test(url)) {
                return new Promise((resolve, reject) => {
                    window.__pending[url] = { resolve, reject };
                });
            }
            return window.__origFetch(url, opts);
        };
    }""")

    # Click A, then click B — both loadTranscriptDetail() calls start and
    # both hang on their trapped fetch() promise.
    page.evaluate(f"() => navigate('detail', {a_id})")
    page.wait_for_timeout(50)
    page.evaluate(f"() => navigate('detail', {b_id})")
    page.wait_for_timeout(50)

    # Resolve B (clicked last) first, then A (clicked first) — simulating
    # A's response arriving after B's, the exact race the issue reports.
    # Resolving with the real Response object (unconsumed body) lets api()'s
    # own res.json()/res.ok/res.status handling run unchanged.
    page.evaluate(f"""async () => {{
        const bRes = await window.__origFetch('/api/transcripts/{b_id}');
        window.__pending['/api/transcripts/{b_id}'].resolve(bRes);
    }}""")
    page.wait_for_timeout(200)
    page.evaluate(f"""async () => {{
        const aRes = await window.__origFetch('/api/transcripts/{a_id}');
        window.__pending['/api/transcripts/{a_id}'].resolve(aRes);
    }}""")
    page.wait_for_timeout(500)

    # The detail page heading reflects the currently-loaded transcript's
    # title. CSS applies text-transform:uppercase to some of this chrome,
    # so compare case-insensitively rather than trusting inner_text()'s
    # casing. If we end up on ALPHA, the race won; if BETA, the fix worked.
    body_text = page.inner_text("body").lower()
    assert "beta-transcript" in body_text, (
        f"Expected BETA-transcript to be shown after clicking A then B. "
        f"Body sample: {body_text[:400]!r}"
    )
    assert "alpha-transcript" not in body_text, (
        "ALPHA-transcript should NOT be visible — the slower A response "
        "must have been discarded after the user clicked B."
    )
