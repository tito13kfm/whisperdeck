"""e2e for issue #321: drive relabel undo through real clicks.

tests/test_relabel_undo.py covers the endpoints and the inverse-patch
bookkeeping in isolation. What no test covered is the browser half: select
mode, the retag modal, the "Undo relabel" button appearing and disappearing,
and the two confidence surfaces that a retag is supposed to move -- the
per-line "?" marker and the header's "N uncertain" count, which both read
static/confidence.js's isLowConfidence through the built rack.min.js bundle.
A retag stamps speaker_confidence = USER_ASSIGNED_CONFIDENCE (-1, issue
#305), which that predicate excludes, so one retag-then-undo round trip
exercises the endpoints, the undo button lifecycle, and the confidence
rendering at once.

Uses the plain `page` fixture, not page_no_sw: nothing here intercepts
network traffic, so the service worker stays on the path a real browser
takes.

Assertions read the DOM, never toasts -- toasts self-remove after ~4.2s,
which is a race a slow CI box loses.

Mutation checks:
  - make POST /api/transcripts/{id}/relabel-undo a no-op `return` and the
    first test fails at the post-undo assertions: the speaker stays "Alice"
    and the uncertainty never comes back.
  - drop USER_ASSIGNED_CONFIDENCE from app.py's retag handler (leave the old
    confidence in place) and it fails at the post-retag assertions: the "?"
    and "1 uncertain" survive a manual override.
  - remove the `t.last_relabel` guard on the Undo button in rack.js and the
    pre-retag assertion (no undo button on a fresh transcript) fails.
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
    _ensure_test_user(live_server, "e2e_undo_test", "e2e_undo_pass_123")
    return ("e2e_undo_test", "e2e_undo_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def _seed_transcript(username, title):
    """Three diarized lines, the middle one below the 0.5 low-confidence
    threshold so exactly one "?" marker and a "1 uncertain" chip render. No
    stored audio, so the per-line play/seed controls and the enrolled-voices
    nudge stay out of the way."""
    import app as app_module
    from database import Transcript, User

    session = app_module.SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        assert user is not None, f"test user {username!r} not found -- register before seeding"
        t = Transcript(
            user_id=user.id, title=title, filename="mtg.mp3",
            status="completed", full_text="hello there general", kind="meeting",
            speaker_count=2, diarization_method="pyannote",
            segments=[
                {"start": 0.0, "end": 2.0, "text": "hello there", "speaker": "SPEAKER_00",
                 "speaker_confidence": 0.92},
                {"start": 2.0, "end": 4.0, "text": "general kenobi", "speaker": "SPEAKER_01",
                 "speaker_confidence": 0.3},
                {"start": 4.0, "end": 6.0, "text": "you are bold", "speaker": "SPEAKER_00",
                 "speaker_confidence": 0.88},
            ],
        )
        session.add(t)
        session.commit()
        return t.id
    finally:
        session.close()


def _open_detail(page, transcript_id, title):
    """Wait on the rendered title, not on #detail-body: navigate() does not
    await loadTranscriptDetail, so #detail-body exists (empty, or still
    holding the previous transcript) before the fetch resolves."""
    page.evaluate(f"navigate('detail', {transcript_id})")
    # .t-title is text-transform: uppercase, and innerText reports the
    # rendered casing -- compare case-insensitively rather than hard-coding
    # the transform into the expected string.
    page.wait_for_function(
        "(t) => { const el = document.querySelector('#page-detail .t-title'); "
        "return el && el.innerText.toUpperCase().includes(t.toUpperCase()); }",
        arg=title,
        timeout=10000,
    )


def _collect_errors(page):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    return errors


def _undo_button(page):
    return page.locator("[data-dact='relabel-undo']")


def _uncertain_markers(page):
    """The per-line "?" markers, matched by their title so the assertion
    can't be satisfied by a literal "?" in someone's transcript text."""
    return page.locator("#detail-body span[title^='Low-confidence speaker assignment']")


def test_retag_then_undo_restores_the_speaker_and_its_uncertainty(page, registered_user):
    username, password = registered_user
    _login(page, username, password)
    errors = _collect_errors(page)

    title = "Retag undo check"
    transcript_id = _seed_transcript(username, title)
    _open_detail(page, transcript_id, title)

    # Fresh transcript: nothing to undo, and the seeded low-confidence line
    # is flagged both per line and in the header count.
    assert _undo_button(page).count() == 0
    assert _uncertain_markers(page).count() == 1
    assert "1 uncertain" in page.locator("#page-detail").inner_text()

    page.click("#select-mode-btn")
    page.check("input[data-seg-select='1']")
    page.click("#retag-selected-btn")
    page.fill("#retag-new", "Alice")
    page.click("#retag-go")

    # Retag reloads the detail page; wait for the new label rather than a
    # fixed sleep.
    page.wait_for_function(
        "() => { const el = document.querySelector('#detail-body'); "
        "return el && el.innerText.includes('ALICE'); }",
        timeout=10000,
    )
    body_text = page.locator("#detail-body").inner_text()
    assert "SPEAKER_01" not in body_text
    # A human override is not uncertainty (issue #305): both surfaces clear.
    assert _uncertain_markers(page).count() == 0
    assert "uncertain" not in page.locator("#page-detail").inner_text()
    assert _undo_button(page).count() == 1

    _undo_button(page).click()

    page.wait_for_function(
        "() => { const el = document.querySelector('#detail-body'); "
        "return el && el.innerText.includes('SPEAKER_01'); }",
        timeout=10000,
    )
    restored = page.locator("#detail-body").inner_text()
    assert "ALICE" not in restored
    # The old confidence came back with the label, not just the name.
    assert _uncertain_markers(page).count() == 1
    assert "1 uncertain" in page.locator("#page-detail").inner_text()
    # One history entry, one undo — the button is gone again.
    assert _undo_button(page).count() == 0

    assert errors == []


def test_rename_then_undo_restores_every_line_of_that_speaker(page, registered_user):
    """The other history kind: rename rewrites every line of one speaker
    (and corrected_text), so its inverse has to restore all of them. Driven
    through the speaker label's styledPrompt, which only responds outside
    select mode."""
    username, password = registered_user
    _login(page, username, password)
    errors = _collect_errors(page)

    title = "Rename undo check"
    transcript_id = _seed_transcript(username, title)
    _open_detail(page, transcript_id, title)

    page.click("#detail-body span[data-seg-rename='SPEAKER_00']")
    page.fill("#styled-prompt-input", "Bob")
    page.click("#styled-prompt-ok")

    page.wait_for_function(
        "() => { const el = document.querySelector('#detail-body'); "
        "return el && el.innerText.includes('BOB'); }",
        timeout=10000,
    )
    # Both SPEAKER_00 lines renamed, the SPEAKER_01 line untouched.
    assert page.locator("#detail-body span[data-seg-rename='Bob']").count() == 2
    assert page.locator("#detail-body span[data-seg-rename='SPEAKER_01']").count() == 1
    assert _undo_button(page).count() == 1

    _undo_button(page).click()

    page.wait_for_function(
        "() => { const el = document.querySelector('#detail-body'); "
        "return el && !el.innerText.includes('BOB'); }",
        timeout=10000,
    )
    assert page.locator("#detail-body span[data-seg-rename='SPEAKER_00']").count() == 2
    assert _undo_button(page).count() == 0

    assert errors == []
