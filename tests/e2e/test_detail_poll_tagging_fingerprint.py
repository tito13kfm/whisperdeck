"""e2e regression for issue #246: _jobFingerprint must include tagging_job

The _jobFingerprint function in static/rack.js builds a poll-comparison string
from various job fields. When tagging shipped, tagging_job was not included in
the fingerprint, so when only tagging's status/progress changed, the fingerprint
string remained unchanged and updateDetailJobStatus/re-render never fired.

This test asserts that the fingerprint string changes when only tagging_job.progress.done
changes, by constructing two transcript payloads differing only in that field and
verifying the UI updates accordingly.
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
    _ensure_test_user(live_server, "e2e_tagging_fp_test", "e2e_tagging_fp_test_pass_123")
    return ("e2e_tagging_fp_test", "e2e_tagging_fp_test_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def _make_transcript_with_running_tagging(username):
    import app as app_module
    from database import LlmJob, Transcript, User

    db = app_module.SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        transcript = Transcript(
            user_id=user.id, title="tagging-fp-test transcript", filename="f.mp3",
            status="completed", kind="meeting",
            segments=[
                {"start": 0, "end": 2, "speaker": "Alice", "text": "hello there"},
                {"start": 2, "end": 4, "speaker": "Bob", "text": "hi Alice"},
            ],
        )
        db.add(transcript)
        db.commit()
        job = LlmJob(
            user_id=user.id, transcript_id=transcript.id, kind="tagging",
            status="running", progress_done=1, progress_total=4,
            provider="groq", model="llama-3.3-70b-versatile",
        )
        db.add(job)
        db.commit()
        return transcript.id, job.id
    finally:
        db.close()


def _bump_tagging_job(job_id, **fields):
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


def test_detail_poll_tagging_fingerprint_changes_when_only_tagging_progress_updates(
    page, registered_user, live_server
):
    """Regression test for issue #246: when only tagging_job progress changes,
    the fingerprint must change and the UI must update.
    
    This mirrors test_detail_poll_updates_progress_live_without_rebuilding_segments_then_reveals_completion
    but for tagging_job instead of correction_job.
    """
    username, password = registered_user
    _login(page, username, password)

    transcript_id, job_id = _make_transcript_with_running_tagging(username)

    # Navigate to detail page
    page.evaluate(f"navigate('detail', {transcript_id})")
    page.wait_for_selector("#detail-body", timeout=5000)

    # Tagging job should be visible on the detail page
    # The tagging job widget should show progress
    page.wait_for_selector("#job-tagging", timeout=5000)
    assert "section 1 of 4" in page.locator("#job-tagging").inner_text().lower()

    # Tag the actual DOM node to detect if it gets rebuilt
    page.evaluate("document.querySelector('#job-tagging').__e2eSameNode = true")

    # Simulate the job advancing mid-poll-cycle
    _bump_tagging_job(job_id, progress_done=2)
    
    # Wait for the progress to update
    page.wait_for_function(
        "() => { const el = document.querySelector('#job-tagging'); "
        "return el && el.innerText.toLowerCase().includes('section 2 of 4'); }",
        timeout=6000,
    )

    # Progress text updated live, but on the SAME node — proves this was an
    # innerHTML patch of #job-tagging itself, not a renderDetailBody()
    # rebuild that tore the whole tab down and recreated it.
    assert page.evaluate(
        "document.querySelector('#job-tagging').__e2eSameNode === true"
    )

    # Complete the tagging job
    _bump_tagging_job(job_id, status="completed", progress_done=4)

    # Wait for the job to complete and the widget to be replaced by tags
    page.wait_for_function(
        "() => { const el = document.querySelector('#detail-body'); "
        "return el && el.innerText.toLowerCase().includes('tags'); }",
        timeout=6000,
    )
    # The running-job widget should be gone
    assert page.locator("#job-tagging").count() == 0
