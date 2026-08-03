"""Browser-driven e2e tests for the Voice Dump board (issue #286).

Verifies via Playwright on the real served page (rack.min.js via
static/index.html):
1. The 'Dump notes' rail button navigates to #page-dumpnotes.
2. The empty state renders heading, "0 notes" status, and empty-state copy.
3. A populated board renders cards (title, note_type badge, body excerpt,
   source transcript title), the header count, and most-recent-first order.
4. The existing Voice Notes board (loadVoiceNotes, refactored to share
   noteStructuredBits() with the new board) still renders correctly.
5. Navigating to the dump-notes board raises no uncaught JS errors.
6. The transcribe page's Mode wheel can be cycled to Voice Dump through real
   clicks on the wheel controls (no state injection), and the rendered Mode
   row reflects it.
7. Starting a job while the wheel is on Voice Dump posts kind=voice_dump to
   /api/transcribe -- the acceptance criterion from issue #286 ("Recording
   with kind voice_dump starts live capture normally").

All tests in this file share ONE registered user via a module-scoped
fixture (the same pattern every other tests/e2e/*.py file uses) because
/api/register is rate-limited to 5 requests / 300s per client IP
(app.py's `register` route) and live_server (tests/e2e/conftest.py) shares
one real `app` module -- and thus one rate-limiter bucket -- across the
whole pytest session. Registering once here keeps this file's contribution
to that shared budget at 1, matching every sibling file.

Because of the shared user, test ORDER matters and is significant: the
empty-state test runs before any seeding, and the populated-board test
(which asserts an exact "2 notes" count) runs immediately after, before
any other test seeds further voice-dump items.
"""
import datetime
import http.cookiejar
import json
import re
import urllib.error
import urllib.request

import pytest

from database import utcnow_naive

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
    _ensure_test_user(live_server, "e2e_dumpboard_test_user", "e2e_dumpboard_pass_123")
    return ("e2e_dumpboard_test_user", "e2e_dumpboard_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def _seed_voice_dump_items(username, rows):
    """Insert Transcript + VoiceDumpItem rows directly into the same DB the
    live server's `app` module (and thus the browser, over HTTP) reads from.

    live_server imports `app` in-process and runs uvicorn in a background
    thread of THIS interpreter, so app_module.SessionLocal() opens a session
    against the exact engine the running server uses -- there is no second
    database to keep in sync. `rows` is a list of dicts with keys note_type,
    title, body, created_at (a naive UTC datetime), transcript_title.
    """
    import app as app_module
    from database import Transcript, User, VoiceDumpItem

    session = app_module.SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        assert user is not None, f"test user {username!r} not found -- register before seeding"
        for r in rows:
            t = Transcript(
                user_id=user.id, title=r["transcript_title"], filename="f.mp3",
                status="completed", full_text="x", segments=[], kind="voice_dump",
            )
            session.add(t)
            session.flush()
            session.add(VoiceDumpItem(
                user_id=user.id, transcript_id=t.id, sequence_index=0,
                note_type=r["note_type"], title=r["title"], body=r["body"],
                structured=r.get("structured", {}), model="llama3", provider="groq",
                created_at=r["created_at"],
            ))
        session.commit()
    finally:
        session.close()


def _seed_voice_note(username, note_type, title, body, structured, transcript_title):
    """Same idea as _seed_voice_dump_items but for the VoiceNote table (the
    existing Voice Notes board), so test 4 can drive loadVoiceNotes() through
    its non-empty (noteStructuredBits-calling) path, not just the empty
    state."""
    import app as app_module
    from database import Transcript, User, VoiceNote

    session = app_module.SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        assert user is not None, f"test user {username!r} not found -- register before seeding"
        t = Transcript(
            user_id=user.id, title=transcript_title, filename="f.mp3",
            status="completed", full_text="x", segments=[], kind="voice_note",
        )
        session.add(t)
        session.flush()
        session.add(VoiceNote(
            user_id=user.id, transcript_id=t.id, note_type=note_type,
            title=title, body=body, structured=structured,
            model="llama3", provider="groq",
        ))
        session.commit()
    finally:
        session.close()


def _badge_label(card_locator):
    """The note_type badge's rendered text (NOTE_TYPE_LABELS[n.note_type]).

    The header row (the card's first child div) holds 3 spans in DOM order:
    a color dot (no text), the type-label badge, and the "time ago" stamp.
    The badge span has `text-transform: uppercase` in CSS, so its rendered
    inner_text() comes back upper-cased regardless of the label's actual
    casing -- lower-case before comparing, matching this repo's existing
    e2e convention (see test_costs_ui_e2e.py's `page_text.lower()`).
    """
    return card_locator.locator("div:first-child span").nth(1).inner_text().strip().lower()


# ── 1. rail button navigation ───────────────────────────────────────────


def test_dumpnotes_rail_button_navigates(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    btn = page.locator("button[data-nav='dumpnotes']")
    assert btn.is_visible()
    btn.click()

    page.wait_for_selector("#page-dumpnotes.active", timeout=5000)
    assert "active" in (btn.get_attribute("class") or "")


# ── 2. empty state (must run before any test seeds voice-dump items) ────


def test_dumpnotes_empty_state(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    page.locator("button[data-nav='dumpnotes']").click()
    page.wait_for_selector("#page-dumpnotes.active", timeout=5000)

    text = page.locator("#page-dumpnotes").inner_text().lower()
    assert "dump notes" in text
    assert "0 notes" in text
    assert "no dump notes yet" in text


# ── 3. populated board: cards, badges, count, most-recent-first order ───
# Must run right after the empty-state test, before any other test seeds
# further voice-dump items for this shared user, so "2 notes" is exact.


def test_dumpnotes_populated_board_renders_cards_and_order(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    older = utcnow_naive() - datetime.timedelta(hours=1)
    newer = utcnow_naive()
    _seed_voice_dump_items(username, [
        {
            "note_type": "todo", "title": "First noted",
            "body": "Do this first thing please and thanks so much for it.",
            "transcript_title": "Recording Alpha", "created_at": older,
        },
        {
            "note_type": "general", "title": "Second noted",
            "body": "This is the second general note body content here today.",
            "transcript_title": "Recording Beta", "created_at": newer,
        },
    ])

    page.locator("button[data-nav='dumpnotes']").click()
    page.wait_for_selector("#page-dumpnotes.active", timeout=5000)
    page.wait_for_selector(".voice-dump-card", timeout=5000)

    # header count
    status_text = page.locator("#page-dumpnotes .page-status").inner_text().lower()
    assert status_text == "2 notes"

    cards = page.locator(".voice-dump-card")
    assert cards.count() == 2

    # most-recent-first ordering -- exact list equality, not membership
    titles = page.locator(".voice-dump-card h3").all_inner_texts()
    assert titles == ["Second noted", "First noted"]

    newer_card = cards.nth(0)
    older_card = cards.nth(1)

    assert newer_card.locator("h3").inner_text() == "Second noted"
    assert _badge_label(newer_card) == "note"  # NOTE_TYPE_LABELS.general == 'Note'
    assert "Recording Beta" in newer_card.inner_text()
    assert "second general note body" in newer_card.inner_text()

    assert older_card.locator("h3").inner_text() == "First noted"
    assert _badge_label(older_card) == "todo"  # NOTE_TYPE_LABELS.todo == 'Todo'
    assert "Recording Alpha" in older_card.inner_text()
    assert "first thing please" in older_card.inner_text()


# ── 4. existing Voice Notes board unaffected by the refactor ────────────


def test_voicenotes_board_still_renders_after_refactor(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    # A real todo-type note with structured.items so loadVoiceNotes() runs
    # through the same noteStructuredBits() branch the refactor moved out
    # of loadVoiceNotes() and into a shared helper.
    _seed_voice_note(
        username, "todo", "Grocery run",
        "Need to pick up a few things on the way home tonight.",
        {"items": [{"text": "Buy milk", "priority": "high"}]},
        "Errand recording",
    )

    page.locator("button[data-nav='voicenotes']").click()
    page.wait_for_selector("#page-voicenotes.active", timeout=5000)
    page.wait_for_selector(".voice-note-card", timeout=5000)

    text = page.locator("#page-voicenotes").inner_text().lower()
    assert "voice notes" in text
    assert "1 note" in text
    assert "grocery run" in text
    assert "buy milk" in text  # rendered by noteStructuredBits()'s todo branch


# ── 5. no console errors during dump-notes navigation ────────────────────


def test_dumpnotes_navigation_has_no_console_errors(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    _seed_voice_dump_items(username, [
        {
            "note_type": "idea", "title": "Ship it",
            "body": "Ship the dump board once it looks right.",
            "transcript_title": "Console check recording",
            "created_at": utcnow_naive(),
        },
    ])

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    page.locator("button[data-nav='dumpnotes']").click()
    page.wait_for_selector("#page-dumpnotes.active", timeout=5000)
    page.wait_for_selector(".voice-dump-card", timeout=5000)

    assert errors == []


# ── 6/7. Mode wheel real-click cycling, and startJob() posts the kind ────
#
# The mode picker is a custom VFD "MFD" wheel (static/rack.js), not an HTML
# <select>. Clicking the category button (data-cat='mode') enters wheel-edit
# mode; Up/Down (#mfd-btn-up / #mfd-btn-down) call mfdNav(dir), which is the
# only code path that ever assigns S.mode. OK (#mfd-btn-ok) exits edit mode
# back to the row-list ("browse") view, where .mfd-row/.mfd-label/.mfd-value
# are rendered again (while editing, the screen instead shows a single
# prev/current/next wheel, with no .mfd-row for "Mode").

_MODE_ORDER = ["auto", "meeting", "dictation", "voice_note", "voice_dump"]


def _cycle_mode_via_clicks(page, target):
    """Drive the real Mode wheel to `target` using only real clicks on the
    category button and the Up/Down nav buttons -- never by assigning
    window.S.mode directly. window.S.mode is read back here only to decide
    when to stop clicking (and by the caller, only to assert), never to set
    it.
    """
    assert target in _MODE_ORDER
    mode_btn = page.locator("#mfd-leftcol .mfd-btn[data-cat='mode']")
    assert mode_btn.count() == 1, "expected exactly one Mode category button"
    mode_btn.click()  # mfdOnCatClick('mode') -- enters wheel-edit mode

    def current_mode():
        return page.evaluate("() => window.S.mode")

    # Don't assume a starting index -- read it and click Down until it
    # lands, capped at one full cycle so a broken wrap can't hang the test.
    for _ in range(len(_MODE_ORDER)):
        if current_mode() == target:
            break
        page.locator("#mfd-btn-down").click()
    assert current_mode() == target, (
        f"real wheel clicks never reached {target!r} via mfdNav's Down button, "
        f"stuck at {current_mode()!r}"
    )

    page.locator("#mfd-btn-ok").click()  # mfdOnOk -- confirm, exit edit mode


def test_transcribe_mode_wheel_cycles_via_real_clicks(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    page.locator("button[data-nav='transcribe']").click()
    page.wait_for_selector("#page-transcribe.active", timeout=5000)
    page.wait_for_selector("#mfd-leftcol .mfd-btn", timeout=5000)

    _cycle_mode_via_clicks(page, "voice_dump")

    # Read the rendered DOM via .textContent (unaffected by the CSS
    # text-transform on .mfd-label/.mfd-value), not internal state.
    rows = page.evaluate(
        """() => Array.from(document.querySelectorAll('#mfd-screen .mfd-row')).map(r => ({
            label: r.querySelector('.mfd-label')?.textContent,
            value: r.querySelector('.mfd-value')?.textContent,
        }))"""
    )
    mode_rows = [r for r in rows if r["label"] == "Mode"]
    assert len(mode_rows) == 1, f"expected exactly one Mode row, got {rows}"
    assert mode_rows[0]["value"] == "Voice Dump"
    # Assertion only -- S.mode was never assigned directly by this test.
    assert page.evaluate("() => window.S.mode") == "voice_dump"


def test_transcribe_start_posts_kind_voice_dump(browser, live_server, registered_user, tmp_path):
    """The acceptance criterion from issue #286: recording with kind
    voice_dump starts live capture (here, a file-based job) normally --
    i.e. the wheel's selection actually reaches the /api/transcribe
    request as `kind=voice_dump`, not just the rendered Mode row.

    Uses its own browser context (service_workers='block') instead of the
    shared `page` fixture: app.py's sw.js registers a service worker that
    intercepts every /api/* fetch and reissues it from the *service
    worker's* own scope (`e.respondWith(fetch(e.request)...)`), not the
    page's. page.route() only patches requests the page itself makes, so
    with the service worker active it never sees the /api/transcribe
    call -- confirmed empirically (route handler simply never fired, while
    the request still hit the real backend and came back with no doneId,
    i.e. a real failed transcription of the fake wav bytes). Blocking
    service workers for this context's requests is the fix.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
    page = ctx.new_page()
    page.goto(live_server + "/")
    try:
        username, password = registered_user
        _login(page, username, password)

        page.locator("button[data-nav='transcribe']").click()
        page.wait_for_selector("#page-transcribe.active", timeout=5000)
        page.wait_for_selector("#mfd-leftcol .mfd-btn", timeout=5000)

        _cycle_mode_via_clicks(page, "voice_dump")

        # Load a tiny real file through the real, visible-to-the-app file
        # input (the same node wireTranscribeDrop()'s change-listener wires
        # up) -- never write to S.tapeFile/S.tapeLoaded directly.
        audio_path = tmp_path / "tiny.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 256)
        page.locator("#file-input").set_input_files(str(audio_path))
        page.wait_for_function("() => window.S.tapeLoaded === true", timeout=5000)

        # Intercept the upload: assert the real multipart body carries
        # kind=voice_dump, record that fact in `intercepted_kinds`, and
        # fulfill with a minimal stub so no real transcription pipeline
        # runs. Also stub the immediately-following GET
        # /api/transcripts/<id> poll as already "completed" so
        # startJob()'s poll loop returns on its first iteration instead of
        # hitting the real (nonexistent) transcript row.
        intercepted_kinds = []

        def handle_transcribe_post(route):
            body = (route.request.post_data_buffer or b"").decode("utf-8", errors="replace")
            match = re.search(r'name="kind"\r\n\r\n([^\r\n]*)\r\n', body)
            intercepted_kinds.append(match.group(1) if match else None)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"id": 999999, "status": "completed", "duration_seconds": 1}),
            )

        def handle_transcript_get(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"id": 999999, "status": "completed", "duration_seconds": 1}),
            )

        page.route("**/api/transcribe", handle_transcribe_post)
        page.route(re.compile(r".*/api/transcripts/\d+$"), handle_transcript_get)

        # curProv().ready gates BOTH the real START button (#key-play-a is
        # disabled otherwise -- see rack.js's `playKey.disabled = !canStart`)
        # and startJob()'s own early-return guard. Investigate rather than
        # assume. In this environment moonshine's check_health() actually
        # passes (it's installed) even though neither faster-whisper nor
        # any hosted-provider API key is configured for this fresh test
        # user, so curProv() (whichever provider ensureProviders() picked
        # as firstReady) reports ready=True and the real button is not
        # disabled -- confirmed below rather than assumed.
        ready = page.evaluate("() => window.curProv().ready")
        with page.expect_request("**/api/transcribe", timeout=5000):
            if ready:
                page.locator("#key-play-a").click()
            else:
                # BLOCKED-VERIFICATION: no provider is ready in this test
                # environment, so the real START button stays disabled and
                # a real click could never reach startJob(). Confirm
                # that's really why (not a UI/guard mismatch), then fall
                # back to calling the real startJob() directly -- exposed
                # on window in rack.js's existing test-hook Object.assign
                # block for exactly this -- after marking the current
                # provider ready so startJob()'s *other* guard doesn't
                # also block it. tapeLoaded and tapeFile were still set
                # through the real file input above, not this evaluate
                # call, and S.mode was still set through real wheel clicks
                # above, not this evaluate call.
                assert page.locator("#key-play-a").is_disabled(), (
                    "curProv().ready is False but #key-play-a isn't disabled -- "
                    "guard/UI mismatch, not the expected no-ready-provider case"
                )
                page.evaluate("() => { window.S.providers[window.S.providerIdx].ready = true; }")
                page.evaluate("() => window.startJob()")

        assert intercepted_kinds == ["voice_dump"], (
            f"expected exactly one intercepted /api/transcribe request carrying "
            f"kind=voice_dump, got {intercepted_kinds}"
        )
    finally:
        ctx.close()
