"""Browser-driven e2e tests for the Dump Review tab (issue #287).

Verifies via Playwright on the real served page (rack.min.js via
static/index.html):
1. The `review` tab button appears on a voice_dump transcript's detail page
   and does NOT appear on a meeting transcript's detail page.
2. The tab renders one editable card per seeded draft item (title input,
   body textarea, type select), pre-filled with the exact seeded values.
3. Editing title/body/type and ticking Discard, then Save draft, then a
   full page reload and reopening the tab -- the edits persist exactly.
4. Answering a clarifying question, saving, and confirming the answer text
   is folded into the body and the question is gone; saving a second time
   does not append the answer twice.
5. Finalize (with one item discarded) lands on the Dump notes board with
   the discarded item's title absent and the kept item's title present.
6. The sibling voice_note detail Notes tab (a different surface from the
   Voice Notes board test_voice_dump_board_e2e.py already covers) is
   unaffected by the KIND_TABS refactor -- see test 6's own docstring for
   a pre-existing, unrelated finding surfaced along the way.
7. No console errors during any of the above.

All tests in this file share ONE registered user via a module-scoped
fixture (the same pattern every other tests/e2e/*.py file uses) because
/api/register is rate-limited to 5 requests / 300s per client IP, shared
across the whole live_server pytest session.

Tests 2-5 deliberately share ONE seeded transcript+job across the file (the
same order-dependent pattern test_voice_dump_board_e2e.py uses), via the
module-scoped `dump_transcript_id` fixture: each test builds on the
DOM/data state the previous one left behind, so test order is significant
and asserted values compound across the sequence:
  - test 2 reads back the fixture's untouched seed values.
  - test 3 edits item 0's title, item 1's body+type, discards item 1, saves,
    reloads, and re-asserts those exact values persisted.
  - test 4 answers item 0's clarifying question (item 0's title from test 3
    carries forward), saves twice, and checks the body folds the answer in
    exactly once.
  - test 5 finalizes: item 1 (discarded in test 3) must be absent from the
    board, item 0 (kept) must be present.
Because the fixture is module-scoped rather than a bare shared dict,
running one of tests 3-5 alone (`pytest ...::test_name`) still gets a real
transcript_id -- it just fails the compounding-edit assertions with a
clear diff instead of a bare KeyError, since those edits only exist if
tests 2-4 ran first in the same session.
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
    _ensure_test_user(live_server, "e2e_dumpreview_test_user", "e2e_dumpreview_pass_123")
    return ("e2e_dumpreview_test_user", "e2e_dumpreview_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def _seed_transcript(username, kind, title):
    """Insert a bare Transcript row directly into the same DB the live
    server's `app` module (and thus the browser, over HTTP) reads from.
    live_server imports `app` in-process and runs uvicorn in a background
    thread of THIS interpreter, so app_module.SessionLocal() opens a
    session against the exact engine the running server uses."""
    import app as app_module
    from database import Transcript, User

    session = app_module.SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        assert user is not None, f"test user {username!r} not found -- register before seeding"
        t = Transcript(
            user_id=user.id, title=title, filename="f.mp3",
            status="completed", full_text="x", segments=[], kind=kind,
        )
        session.add(t)
        session.commit()
        return t.id
    finally:
        session.close()


def _seed_voice_dump_job(username, transcript_id, items, provider="groq", model="llama3"):
    """Insert a completed LlmJob(kind='voice_dump') row with the given draft
    items in result_json, using the real key names the job runner writes
    (services/llm_jobs.py): index, type, title, body, structured,
    clarifying_questions. This is what dumpReviewHtml/loadDumpReview reads
    back via GET /api/transcripts/{id}/runs/voice_dump."""
    import app as app_module
    from database import LlmJob, User

    session = app_module.SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        assert user is not None, f"test user {username!r} not found -- register before seeding"
        job = LlmJob(
            user_id=user.id, transcript_id=transcript_id, kind="voice_dump",
            status="completed", provider=provider, model=model,
            result_json={"items": items},
        )
        session.add(job)
        session.commit()
        return job.id
    finally:
        session.close()


def _goto_detail(page, transcript_id, expected_title):
    """navigate('detail', id) fires loadTranscriptDetail() without awaiting
    it, so '#detail-body' existing in the DOM proves nothing about which
    transcript it currently reflects -- it's already there from whatever
    was open before. Wait for the page's title heading (t.title, written by
    renderDetail() only after the fetch resolves) to match this
    transcript's seeded title instead, so navigating between two different
    transcripts in the same test can't read stale DOM from the previous one."""
    page.evaluate(f"navigate('detail', {transcript_id})")
    page.wait_for_function(
        "(title) => { const el = document.querySelector('#page-detail .t-title'); "
        "return el && el.textContent === title; }",
        arg=expected_title,
        timeout=5000,
    )


def _open_review_tab(page, transcript_id, expected_title):
    _goto_detail(page, transcript_id, expected_title)
    page.wait_for_selector("[data-tab='review']", timeout=5000)
    page.click("[data-tab='review']")
    page.wait_for_selector("[data-dump-item]", timeout=5000)


# Seeded draft items shared by tests 2-5.
_ITEM_0 = {
    "index": 0, "type": "todo", "title": "Buy milk",
    "body": "Get milk from store", "structured": {},
    "clarifying_questions": ["Which store?"],
}
_ITEM_1 = {
    "index": 1, "type": "idea", "title": "App idea",
    "body": "Build a widget", "structured": {},
    "clarifying_questions": [],
}

@pytest.fixture(scope="module")
def dump_transcript_id(registered_user):
    """Seeds the ONE transcript+job that tests 2-5 progressively read and
    mutate (same order-dependent pattern test_voice_dump_board_e2e.py
    uses for its shared user). Module-scoped so it seeds once regardless
    of which test in the sequence requests it first -- which also means a
    single test from 3-5 run in isolation (`pytest ...::test_edit_save_...`
    alone) still gets a real transcript_id instead of a bare KeyError; it
    will instead fail on the compounding-edit assertions with a clear diff
    against the fresh seed values, since those edits only exist if tests
    2-4 actually ran first in the same session."""
    username, _ = registered_user
    transcript_id = _seed_transcript(username, "voice_dump", "Dump review flow")
    _seed_voice_dump_job(username, transcript_id, [_ITEM_0, _ITEM_1])
    return transcript_id


# ── 1. tab visibility by transcript kind ─────────────────────────────────


def test_review_tab_visible_only_for_voice_dump(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    dump_id = _seed_transcript(username, "voice_dump", "Visibility check dump")
    _seed_voice_dump_job(username, dump_id, [_ITEM_1])
    meeting_id = _seed_transcript(username, "meeting", "Visibility check meeting")

    _goto_detail(page, dump_id, "Visibility check dump")
    assert page.locator("[data-tab='review']").count() == 1

    _goto_detail(page, meeting_id, "Visibility check meeting")
    assert page.locator("[data-tab='review']").count() == 0


# ── 2. renders one editable card per seeded item, pre-filled exactly ─────


def test_review_tab_renders_seeded_items(page, registered_user, dump_transcript_id):
    username, password = registered_user
    _login(page, username, password)

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    _open_review_tab(page, dump_transcript_id, "Dump review flow")

    assert page.locator("[data-dump-item]").count() == 2

    card0 = page.locator("[data-dump-item='0']")
    assert card0.locator("[data-dfield='title']").input_value() == "Buy milk"
    assert card0.locator("[data-dfield='body']").input_value() == "Get milk from store"
    assert card0.locator("[data-dfield='type']").input_value() == "todo"
    assert card0.locator("[data-dfield='discarded']").is_checked() is False
    answer0 = card0.locator("[data-dfield='answer'][data-dq='0']")
    assert answer0.count() == 1
    assert answer0.input_value() == ""

    card1 = page.locator("[data-dump-item='1']")
    assert card1.locator("[data-dfield='title']").input_value() == "App idea"
    assert card1.locator("[data-dfield='body']").input_value() == "Build a widget"
    assert card1.locator("[data-dfield='type']").input_value() == "idea"
    assert card1.locator("[data-dfield='answer']").count() == 0

    assert errors == []


# ── 3. edit + save draft + reload -> edits persist ───────────────────────


def test_edit_save_draft_reload_persists(page, registered_user, dump_transcript_id):
    username, password = registered_user
    _login(page, username, password)

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    transcript_id = dump_transcript_id
    _open_review_tab(page, transcript_id, "Dump review flow")

    card0 = page.locator("[data-dump-item='0']")
    card0.locator("[data-dfield='title']").fill("Buy milk and eggs")

    card1 = page.locator("[data-dump-item='1']")
    card1.locator("[data-dfield='body']").fill("Build a mobile widget app")
    card1.locator("[data-dfield='type']").select_option("reminder")
    card1.locator("[data-dfield='discarded']").check()

    page.click("[data-dact='dump-save-draft']")
    page.wait_for_selector("text=Draft saved", timeout=5000)

    page.reload()
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)
    _open_review_tab(page, transcript_id, "Dump review flow")

    card0 = page.locator("[data-dump-item='0']")
    assert card0.locator("[data-dfield='title']").input_value() == "Buy milk and eggs"
    assert card0.locator("[data-dfield='body']").input_value() == "Get milk from store"
    assert card0.locator("[data-dfield='type']").input_value() == "todo"
    assert card0.locator("[data-dfield='discarded']").is_checked() is False

    card1 = page.locator("[data-dump-item='1']")
    assert card1.locator("[data-dfield='title']").input_value() == "App idea"
    assert card1.locator("[data-dfield='body']").input_value() == "Build a mobile widget app"
    assert card1.locator("[data-dfield='type']").input_value() == "reminder"
    assert card1.locator("[data-dfield='discarded']").is_checked() is True

    assert errors == []


# ── 4. clarifying-question answer folds into body, no double-append ─────


def test_clarifying_answer_folds_into_body_without_duplication(page, registered_user, dump_transcript_id):
    username, password = registered_user
    _login(page, username, password)

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    transcript_id = dump_transcript_id
    _open_review_tab(page, transcript_id, "Dump review flow")

    card0 = page.locator("[data-dump-item='0']")
    # Title carries forward from test 3's edit; the clarifying question is
    # still unanswered at this point.
    assert card0.locator("[data-dfield='title']").input_value() == "Buy milk and eggs"
    answer0 = card0.locator("[data-dfield='answer'][data-dq='0']")
    assert answer0.count() == 1
    answer0.fill("Whole Foods")

    page.click("[data-dact='dump-save-draft']")
    page.wait_for_selector("text=Draft saved", timeout=5000)

    expected_body = "Get milk from store\n\nWhich store?\nWhole Foods"
    card0 = page.locator("[data-dump-item='0']")
    assert card0.locator("[data-dfield='body']").input_value() == expected_body
    assert card0.locator("[data-dfield='answer']").count() == 0

    # Save a second time with no further edits -- the answer must not be
    # appended again.
    page.click("[data-dact='dump-save-draft']")
    page.wait_for_selector("text=Draft saved", timeout=5000)

    page.reload()
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)
    _open_review_tab(page, transcript_id, "Dump review flow")

    card0 = page.locator("[data-dump-item='0']")
    assert card0.locator("[data-dfield='body']").input_value() == expected_body
    assert card0.locator("[data-dfield='answer']").count() == 0

    assert errors == []


# ── 5. finalize -> board has kept item, not the discarded one ───────────


def test_finalize_lands_on_board_without_discarded_item(page, registered_user, dump_transcript_id):
    username, password = registered_user
    _login(page, username, password)

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    transcript_id = dump_transcript_id
    _open_review_tab(page, transcript_id, "Dump review flow")

    page.click("[data-dact='dump-finalize']")
    page.wait_for_selector("#styled-confirm-ok", timeout=5000)
    page.click("#styled-confirm-ok")

    page.wait_for_selector("#page-dumpnotes.active", timeout=5000)
    page.wait_for_selector(".voice-dump-card", timeout=5000)

    # Exact list equality (not membership): this is the only finalize call
    # in the file (test 1's seeded job is never finalized), so exactly one
    # VoiceDumpItem row exists for this user -- item 0, under its
    # test-3-edited title, since item 1 ("App idea") was discarded there.
    titles = page.locator(".voice-dump-card h3").all_inner_texts()
    assert titles == ["Buy milk and eggs"]

    assert errors == []


# ── 6. sibling surface: the existing voice_note detail Notes tab ────────
# The Dump Review tab reuses KIND_TABS, which is also what governs whether
# the Notes tab is offered for a voice_note transcript (detailTabsHtml,
# rack.js:3790-3802) and whether it's reset on navigation
# (loadTranscriptDetail, rack.js:3755-3756). test_voice_dump_board_e2e.py's
# test_voicenotes_board_still_renders_after_refactor only covers the
# separate Voice Notes *board* (#page-voicenotes, fed by GET
# /api/voice-notes) -- a different surface from the detail page's Notes
# *tab* (S.detailTab === 'notes' -> voiceNoteHtml(t), rack.js:5101-5105),
# which nothing in the existing suite drives. Cover it directly here so
# "existing voice_note Notes tab unaffected" is actually exercised.


# ── 5b. reopening the tab after finalize shows the finalized state ──────
# Runs after test 5 and depends on it (module-scoped fixture, file order).
# This covers loadDumpReview's finalized detection, which is the one piece
# of state the backend exposes no flag for: there is no "finalized" marker
# on the LlmJob, so the tab has to cross-check GET /voice-dump-items for a
# row whose source_job_id matches this job's id. The draft items are still
# sitting in job.result_json after finalize, so a tab that only looked at
# result_json would happily offer the already-committed draft for a second
# finalize -- which would insert a duplicate batch of VoiceDumpItem rows.


def test_reopening_tab_after_finalize_shows_finalized_state(page, registered_user, dump_transcript_id):
    username, password = registered_user
    _login(page, username, password)

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    _goto_detail(page, dump_transcript_id, "Dump review flow")
    page.wait_for_selector("[data-tab='review']", timeout=5000)
    page.click("[data-tab='review']")
    # The finalized branch renders no editable cards, so wait on its own
    # affordance rather than _open_review_tab's [data-dump-item].
    page.wait_for_selector("[data-dact='open-dumpnotes']", timeout=5000)

    assert page.locator("[data-dump-item]").count() == 0, \
        "an already-finalized dump must not offer the editable draft again"
    assert page.locator("[data-dact='dump-finalize']").count() == 0, \
        "no second Finalize button -- that would duplicate VoiceDumpItem rows"
    assert page.locator("[data-dact='dump-save-draft']").count() == 0

    # One row was finalized in test 5 (item 1 was discarded), so the count
    # is the finalized-row count, not the 2 items still in result_json.
    body_text = page.locator("#detail-body").inner_text().lower()
    assert "finalized" in body_text
    assert "1 note" in body_text

    page.click("[data-dact='open-dumpnotes']")
    page.wait_for_selector("#page-dumpnotes.active", timeout=5000)
    titles = page.locator(".voice-dump-card h3").all_inner_texts()
    assert titles == ["Buy milk and eggs"]

    assert errors == []


def _seed_voice_note_row(username, transcript_id, note_type, title, body, structured):
    """Mirrors the real chain (services/llm_jobs.py's job.kind ==
    'voice_note' branch): a VoiceNote row plus a completed LlmJob whose
    result_json carries the identical payload."""
    import app as app_module
    from database import LlmJob, User, VoiceNote

    session = app_module.SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        assert user is not None, f"test user {username!r} not found -- register before seeding"
        session.add(VoiceNote(
            user_id=user.id, transcript_id=transcript_id, note_type=note_type,
            title=title, body=body, structured=structured,
            model="llama3", provider="groq",
        ))
        session.add(LlmJob(
            user_id=user.id, transcript_id=transcript_id, kind="voice_note",
            status="completed", provider="groq", model="llama3",
            result_json={"type": note_type, "title": title, "body": body, "structured": structured},
        ))
        session.commit()
    finally:
        session.close()


def test_voice_note_detail_tab_unaffected_by_kind_tabs_refactor(page, registered_user):
    username, password = registered_user
    _login(page, username, password)

    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    transcript_id = _seed_transcript(username, "voice_note", "Notes tab check")
    _seed_voice_note_row(
        username, transcript_id, "todo", "Grocery run",
        "Need milk and eggs.", {"items": [{"text": "Buy milk", "priority": "high"}]},
    )

    _goto_detail(page, transcript_id, "Notes tab check")
    # KIND_TABS gates 'notes' onto voice_note transcripts, never 'review'.
    assert page.locator("[data-tab='notes']").count() == 1
    assert page.locator("[data-tab='review']").count() == 0

    page.click("[data-tab='notes']")
    page.wait_for_selector("#detail-body .empty-unit, #detail-body h2", timeout=5000)
    # .empty-unit has text-transform: uppercase in CSS -- lower-case before
    # comparing, matching this repo's existing e2e convention (see
    # test_voice_dump_board_e2e.py's _badge_label / .lower() usage).
    body_text = page.locator("#detail-body").inner_text().lower()

    # This is NOT the "Not available for non-voice-note transcripts"
    # kind-mismatch fallback (that would indicate KIND_TABS regressed) --
    # it's voiceNoteHtml()'s own "no result yet" empty state (rack.js:4600).
    # Confirmed pre-existing and unrelated to this branch: voiceNoteHtml
    # reads job.result_json directly off the transcript payload, but
    # serialize_llm_job() (services/llm_jobs.py:48-70) never includes a
    # result_json key -- the same gap dumpReviewHtml avoids by fetching
    # GET /runs/voice_dump instead (see loadDumpReview's comment). This
    # bug predates and is untouched by this branch (git diff never touches
    # voiceNoteHtml, lines 4577-4656) -- reported to the calling agent as
    # an out-of-scope, pre-existing bug, not fixed here per the "do not
    # modify services/ or its frontend contract" scope of this task. The
    # assertion below pins today's actual (broken) behavior so a real fix
    # elsewhere shows up here as an intentional test update, not a silent
    # behavior change slipping through.
    assert "not available" not in body_text
    assert "no voice-note result yet" in body_text
    assert errors == []
