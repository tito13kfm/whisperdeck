"""e2e regression for issue #150: detail-page poll ticks must not rebuild
the segment list, must keep the running-job progress indicator live, and
must reveal a job's completed content (not a blank container) once it
finishes while its tab is open.

Drives real DOM state through the actual 2.5s poll cycle (scheduleDetailPoll
in rack.js) rather than asserting against source text, since the three A/B
variants for this issue all passed static review and their own unit-level
checks but shipped runtime-only regressions (frozen progress, or completed
content never appearing) that only show up by watching the live DOM.
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
    _ensure_test_user(live_server, "e2e_poll_test", "e2e_poll_test_pass_123")
    return ("e2e_poll_test", "e2e_poll_test_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def _make_transcript_with_running_correction(username):
    import app as app_module
    from database import LlmJob, Transcript, User

    db = app_module.SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        transcript = Transcript(
            user_id=user.id, title="poll-test transcript", filename="f.mp3",
            status="completed", kind="meeting",
            segments=[
                {"start": 0, "end": 2, "speaker": "Alice", "text": "hello there"},
                {"start": 2, "end": 4, "speaker": "Bob", "text": "hi Alice"},
            ],
        )
        db.add(transcript)
        db.commit()
        job = LlmJob(
            user_id=user.id, transcript_id=transcript.id, kind="correction",
            status="running", progress_done=1, progress_total=4,
            provider="groq", model="llama-3.3-70b-versatile",
        )
        db.add(job)
        db.commit()
        return transcript.id, job.id
    finally:
        db.close()


def _bump_job(job_id, **fields):
    import app as app_module
    from database import LlmJob

    db = app_module.SessionLocal()
    try:
        job = db.query(LlmJob).filter(LlmJob.id == job_id).one()
        for k, v in fields.items():
            setattr(job, k, v)
        db.commit()
    finally:
        db.close()


def _set_corrected_text(transcript_id, text):
    import app as app_module
    from database import Transcript

    db = app_module.SessionLocal()
    try:
        t = db.query(Transcript).filter(Transcript.id == transcript_id).one()
        t.corrected_text = text
        db.commit()
    finally:
        db.close()


def test_detail_poll_updates_progress_live_without_rebuilding_segments_then_reveals_completion(
    page, registered_user, live_server
):
    username, password = registered_user
    _login(page, username, password)

    transcript_id, job_id = _make_transcript_with_running_correction(username)

    # navigate() is a plain global function defined in rack.js — calling it
    # directly is the simplest deterministic way to land on the detail page
    # without depending on list/pagination markup.
    page.evaluate(f"navigate('detail', {transcript_id})")
    page.wait_for_selector("[data-tab='corrected']", timeout=5000)
    # job-correction only renders on the Corrected tab (correctedHtml()) —
    # the default landing tab is Transcript.
    page.click("[data-tab='corrected']")
    page.wait_for_selector("#job-correction", timeout=5000)
    assert "section 2 of 4" in page.locator("#job-correction").inner_text().lower()

    # Tag the actual DOM node (a JS expando property, not an attribute —
    # attributes on a parent survive innerHTML writes to its own children
    # regardless, so they can't distinguish "live-patched" from "torn down
    # and rebuilt"; a custom property is lost the moment the node itself is
    # replaced by a fresh one parsed out of a new innerHTML string).
    page.evaluate("document.querySelector('#job-correction').__e2eSameNode = true")

    # Simulate the job advancing mid-poll-cycle and wait for the next tick
    # to pick it up.
    _bump_job(job_id, progress_done=2)
    page.wait_for_function(
        "() => { const el = document.querySelector('#job-correction'); "
        "return el && el.innerText.toLowerCase().includes('section 3 of 4'); }",
        timeout=6000,
    )

    # Progress text updated live, but on the SAME node — proves this was an
    # innerHTML patch of #job-correction itself, not a renderDetailBody()
    # rebuild that tore the whole tab down and recreated it.
    assert page.evaluate(
        "document.querySelector('#job-correction').__e2eSameNode === true"
    )

    _bump_job(job_id, status="completed", progress_done=4)
    _set_corrected_text(transcript_id, "Alice: hello there\n\nBob: hi Alice")

    page.wait_for_function(
        "() => { const el = document.querySelector('#detail-body'); "
        "return el && el.innerText.includes('hello there'); }",
        timeout=6000,
    )
    # The running-job widget is gone, replaced by the real corrected text —
    # not just blanked out.
    assert page.locator("#job-correction").count() == 0
