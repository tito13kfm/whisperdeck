"""e2e regression for issue #131: every polling timer (bg-job, bank, detail,
dash, queue) must stop firing once the user logs out, instead of hammering
the server with requests that come back 401 indefinitely.

All three A/B variants for this issue correctly fixed the reported bg-job
infinite loop, but none of them cleared queuePollTimer — a sibling with the
identical page-guarded self-reschedule shape as bankPollTimer/detailPollTimer/
dashPollTimer (Complement Rule gap). This test lands the user on the queue
page with an active job so queuePollTimer actually arms, then proves no
further /api/ requests fire in the several seconds after logout.
"""
import http.cookiejar
import json
import time
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
    _ensure_test_user(live_server, "e2e_logout_poll_test", "e2e_logout_poll_test_pass_123")
    return ("e2e_logout_poll_test", "e2e_logout_poll_test_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def _make_running_job(username):
    import app as app_module
    from database import LlmJob, Transcript, User

    db = app_module.SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        transcript = Transcript(
            user_id=user.id, title="logout-poll-test transcript", filename="f.mp3",
            status="completed", kind="meeting",
            segments=[{"start": 0, "end": 2, "speaker": "Alice", "text": "hi"}],
        )
        db.add(transcript)
        db.commit()
        job = LlmJob(
            user_id=user.id, transcript_id=transcript.id, kind="summary",
            status="running", progress_done=1, progress_total=4,
            provider="groq", model="llama-3.3-70b-versatile",
        )
        db.add(job)
        db.commit()
        return transcript.id, job.id
    finally:
        db.close()


def test_logout_stops_all_polling_timers_including_queue_page(page, registered_user, live_server):
    username, password = registered_user
    _login(page, username, password)

    _make_running_job(username)

    # Land on the queue page with an active job present — queuePollTimer
    # only self-schedules when S.page === 'queue' AND active > 0, so this is
    # required to actually arm the one timer none of the three A/B variants
    # cleared on logout.
    page.evaluate("() => navigate('queue')")
    page.wait_for_selector(".page-status--busy", timeout=6000)

    api_requests_after_logout = []
    cutoff = {"t": None}

    def on_request(request):
        if "/api/" in request.url and cutoff["t"] is not None:
            api_requests_after_logout.append((time.time() - cutoff["t"], request.url))

    page.on("request", on_request)

    # logout() is a global async function defined in rack.js; Playwright's
    # sync evaluate() awaits the returned promise before continuing.
    page.evaluate("() => logout()")
    cutoff["t"] = time.time()

    # Longest relevant interval is bgJobPollTimer at 8s; give it margin so a
    # slow CI runner doesn't produce a false pass.
    page.wait_for_timeout(9500)

    late = [(round(dt, 2), url) for dt, url in api_requests_after_logout if dt > 0.5]
    assert not late, f"API requests fired after logout (timer not cleared): {late}"
