"""e2e regression for issue #311: voice_match's per-speaker similarity
summary must actually reach the rendered detail page, not just the
serializer.

services/llm_jobs.py now builds a `result_json` for a completed voice_match
job (threshold/considered/matched/skipped/degraded/unmatchable/speakers),
app.py opts the voice_match_job field into `include_result=True` so it's the
one *_job field on the transcript-detail payload that carries a "result"
key, and static/rack.js's voiceMatchSummaryUnit() renders that result as a
headline plus one percentage chip per speaker (amber for anything whose
minimum similarity sits within LOW_MATCH_MARGIN of the threshold). None of
that is exercised anywhere else at the browser layer -- the Python tests in
tests/test_voice_match_job.py check the job/serializer in isolation, not
what actually lands in the DOM through the built static/rack.min.js bundle.

This test seeds a real transcript (so the GET /api/transcripts/{id} response
has a genuine, complete shape) and intercepts that one response via
page.route to graft on a synthetic, fully-controlled voice_match_job --
route.fetch() first gets the real backend response so every other field
(kind, status, tags, segments, ...) stays realistic, and only voice_match_job
is replaced. page_no_sw is required: static/sw.js intercepts /api/* at the
service-worker level and answers with its own re-issued fetch, which bypasses
a page-level page.route handler entirely (see conftest.py's page_no_sw
docstring) -- using the plain `page` fixture here would silently serve the
real (job-less) response instead of the stub.

Mutation check (see PR description): removing the `vmDone` insertion in
renderDetailBody (static/rack.js), or the include_result plumbing, or the
job.result_json assignment in services/llm_jobs.py, all independently make
this fail; a passing run today is not proof any one of those exists on its
own, but the frontend piece is what only this test (not the Python unit
tests) actually exercises.
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
    _ensure_test_user(live_server, "e2e_vm_summary_test", "e2e_vm_summary_pass_123")
    return ("e2e_vm_summary_test", "e2e_vm_summary_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def _seed_transcript(username):
    """A plain completed 'meeting' transcript with no stored audio -- has_audio
    is False, so renderDetailBody's "N enrolled voices might match" nudge
    never fires its /api/voices fetch, keeping this test's only network
    round trip the one GET this test actually stubs."""
    import app as app_module
    from database import Transcript, User

    session = app_module.SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        assert user is not None, f"test user {username!r} not found -- register before seeding"
        t = Transcript(
            user_id=user.id, title="Voice match summary check", filename="f.mp3",
            status="completed", full_text="x", kind="meeting",
            segments=[
                {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "Alice"},
                {"start": 1.0, "end": 2.0, "text": "there", "speaker": "Alice"},
                {"start": 2.0, "end": 3.0, "text": "bye", "speaker": "Bob"},
            ],
        )
        session.add(t)
        session.commit()
        return t.id
    finally:
        session.close()


# The synthetic voice_match_job grafted onto the real detail payload. Bob's
# min_similarity (0.67) sits below threshold(0.65) + LOW_MATCH_MARGIN(0.05)
# = 0.70, so his chip is the amber/thin one; Alice's (0.9) is comfortably
# clear of it.
_STUBBED_RESULT = {
    "threshold": 0.65,
    "considered": 3,
    "matched": 3,
    "skipped": 0,
    "degraded": 0,
    "unmatchable": 0,
    "speakers": [
        {"name": "Alice", "segments": 2, "min_similarity": 0.9, "mean_similarity": 0.9, "max_similarity": 0.9},
        {"name": "Bob", "segments": 1, "min_similarity": 0.67, "mean_similarity": 0.67, "max_similarity": 0.67},
    ],
}


def test_voice_match_summary_unit_renders_headline_and_speaker_percentages(page_no_sw, registered_user):
    page = page_no_sw
    username, password = registered_user
    _login(page, username, password)

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    transcript_id = _seed_transcript(username)

    def _stub_detail(route):
        # Fetch the real response first so every field this test doesn't
        # care about (kind, status, segments, tags, ...) stays genuine --
        # only voice_match_job is swapped for the synthetic one under test.
        response = route.fetch()
        data = response.json()
        data["voice_match_job"] = {
            "id": 1,
            "kind": "voice_match",
            "transcript_id": transcript_id,
            "status": "completed",
            "progress": {"done": 3, "total": 3},
            "provider": "",
            "model": "",
            "error": None,
            "will_retry": False,
            "created_at": None,
            "updated_at": None,
            "result": _STUBBED_RESULT,
        }
        route.fulfill(status=response.status, headers=response.headers, body=json.dumps(data))

    page.route(f"**/api/transcripts/{transcript_id}", _stub_detail)

    page.evaluate(f"navigate('detail', {transcript_id})")
    page.wait_for_function(
        "() => { const el = document.querySelector('#detail-body'); "
        "return el && el.innerText.includes('matched at'); }",
        timeout=5000,
    )

    body_text = page.locator("#detail-body").inner_text()

    # Headline: "<matched> of <considered> line(s) matched at <threshold%> or better"
    assert "3 of 3 lines matched at 65% or better" in body_text

    # Both speakers' chips, with real percentage strings -- catches a
    # similarityPct() formatting regression (e.g. a stray decimal or a
    # fraction left unmultiplied by 100) that a mere "chip count" assertion
    # would miss.
    assert "Alice" in body_text
    assert "90%" in body_text
    assert "Bob" in body_text
    assert "67%" in body_text

    assert errors == []
