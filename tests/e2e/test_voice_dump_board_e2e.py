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
6. The transcribe page's Mode wheel offers/renders "Voice Dump".

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


# ── 6. Mode wheel offers Voice Dump ──────────────────────────────────────


def test_transcribe_mode_wheel_offers_voice_dump(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    page.locator("button[data-nav='transcribe']").click()
    page.wait_for_selector("#page-transcribe.active", timeout=5000)
    page.wait_for_selector("#mfd-leftcol .mfd-btn", timeout=5000)

    # window.S and window.syncTranscribe are exposed globals (see
    # test_bundle_globals.py). Setting S.mode and calling the real
    # syncTranscribe() re-renders the MFD screen through the real pipeline
    # (syncTranscribe -> renderMfd -> renderMfdScreen), so this reads the
    # actual rendered DOM (via .textContent, unaffected by the CSS
    # text-transform on .mfd-label/.mfd-value), not internal state.
    page.evaluate("() => { window.S.mode = 'voice_dump'; window.syncTranscribe(); }")

    rows = page.evaluate(
        """() => Array.from(document.querySelectorAll('#mfd-screen .mfd-row')).map(r => ({
            label: r.querySelector('.mfd-label')?.textContent,
            value: r.querySelector('.mfd-value')?.textContent,
        }))"""
    )
    mode_rows = [r for r in rows if r["label"] == "Mode"]
    assert len(mode_rows) == 1, f"expected exactly one Mode row, got {rows}"
    assert mode_rows[0]["value"] == "Voice Dump"
