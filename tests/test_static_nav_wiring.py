"""Nav-registration wiring guard for the SPA shell (issue #286).

Adding a board page to the Signal Rack frontend means declaring the same
page id in four separate places, in two files:

1. a rail button in static/index.html      (`data-nav="<id>"`)
2. a page container in static/index.html   (`<div class="page" id="page-<id>">`)
3. the `PAGES` array in static/rack.js
4. the `loaders` map inside `navigate()` in static/rack.js

Getting this wrong is not a contained failure. `navigate()` does
`PAGES.forEach(p => $('page-' + p).classList.toggle(...))` with no null
check, so a page id listed in `PAGES` without a matching container throws
`Cannot read properties of null` and breaks navigation to *every* page,
not just the new one. A rail button whose target is missing from `PAGES`
silently redirects to the dashboard instead. A missing `loaders` entry
leaves a permanently blank page.

These tests parse the two source files and cross-check all four lists
against each other, for every nav item, so the whole class of mistake is
caught rather than just the one instance that prompted the test. The last
test additionally checks the committed esbuild bundle is in sync, since
index.html loads /static/rack.min.js and not rack.js: editing the source
alone has no runtime effect at all.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"

RAIL_NAV_RE = re.compile(r'class="rail-btn"\s+data-nav="([\w-]+)"')
PAGE_DIV_RE = re.compile(r'<div class="page" id="page-([\w-]+)">')
PAGES_ARRAY_RE = re.compile(r"const PAGES = \[(.*?)\];", re.S)
LOADERS_BLOCK_RE = re.compile(r"const loaders = \{(.*?)\n  \};", re.S)
LOADER_KEY_RE = re.compile(r"^\s{4}([\w]+):\s*(.+?),\s*$", re.M)


def _read(name: str) -> str:
    path = STATIC / name
    assert path.exists(), f"{path} not found"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def wiring():
    """The four declaration lists, parsed out of the two source files."""
    html = _read("index.html")
    js = _read("rack.js")

    pages_match = PAGES_ARRAY_RE.search(js)
    assert pages_match, "could not find the `const PAGES = [...]` array in static/rack.js"
    loaders_match = LOADERS_BLOCK_RE.search(js)
    assert loaders_match, "could not find the `const loaders = {...}` map in static/rack.js"

    data = {
        "rail_targets": RAIL_NAV_RE.findall(html),
        "page_divs": PAGE_DIV_RE.findall(html),
        "pages": re.findall(r"'([\w-]+)'", pages_match.group(1)),
        "loaders": dict(LOADER_KEY_RE.findall(loaders_match.group(1))),
        "js": js,
    }
    # Guard the parsing itself: if a regex silently stops matching after a
    # refactor, every cross-check below would pass vacuously on empty lists.
    for key in ("rail_targets", "page_divs", "pages", "loaders"):
        assert len(data[key]) >= 10, (
            f"parsed only {len(data[key])} entries for {key!r} — the source layout "
            f"changed and this test's regexes need updating, the wiring checks below "
            f"would otherwise pass vacuously"
        )
    return data


def test_every_rail_nav_target_is_a_registered_page(wiring):
    """A rail button pointing at an id missing from `PAGES` silently sends
    the user to the dashboard (navigate() line: `if (!PAGES.includes(page))
    page = 'dashboard';`) — the button looks wired but does nothing."""
    missing = [t for t in wiring["rail_targets"] if t not in wiring["pages"]]
    assert not missing, (
        f"rail buttons target page ids absent from the PAGES array in "
        f"static/rack.js: {missing}"
    )


def test_every_rail_nav_target_has_a_loader(wiring):
    """Without a `loaders` entry, navigate() runs the no-op fallback and the
    page renders as a permanently blank div."""
    missing = [t for t in wiring["rail_targets"] if t not in wiring["loaders"]]
    assert not missing, (
        f"rail buttons target page ids with no entry in the loaders map in "
        f"navigate() (static/rack.js): {missing}"
    )


def test_every_pages_entry_has_a_page_container(wiring):
    """navigate() does `$('page-' + p).classList.toggle(...)` for every id in
    PAGES with no null check. A missing container throws on *every*
    navigation, not just to the new page."""
    missing = [p for p in wiring["pages"] if p not in wiring["page_divs"]]
    assert not missing, (
        f"PAGES entries with no matching <div class=\"page\" id=\"page-...\"> in "
        f"static/index.html: {missing} — navigate() would throw for every page"
    )


def test_every_page_container_is_in_pages(wiring):
    """The reverse direction: an orphan container is dead markup that never
    gets its `active` class toggled."""
    orphans = [d for d in wiring["page_divs"] if d not in wiring["pages"]]
    assert not orphans, (
        f"page containers in static/index.html with no matching PAGES entry: {orphans}"
    )


def test_every_named_loader_function_exists(wiring):
    """A loaders value written as a bare identifier (`voicenotes:
    loadVoiceNotes`) is a ReferenceError at click time if the function was
    never defined or was renamed."""
    missing = []
    for page, value in wiring["loaders"].items():
        if re.fullmatch(r"[A-Za-z_$][\w$]*", value):  # bare identifier, not an arrow fn
            if not re.search(r"function\s+" + re.escape(value) + r"\s*\(", wiring["js"]):
                missing.append((page, value))
    assert not missing, (
        f"loaders map references functions that are not defined in static/rack.js: {missing}"
    )


def test_dump_notes_board_is_registered_at_all_four_points(wiring):
    """Issue #286's own nav item, checked explicitly so a future refactor
    that drops it fails with the feature's name rather than a generic
    count mismatch."""
    assert "dumpnotes" in wiring["rail_targets"], (
        'no rail button with data-nav="dumpnotes" in static/index.html'
    )
    assert "dumpnotes" in wiring["page_divs"], (
        'no <div class="page" id="page-dumpnotes"> in static/index.html'
    )
    assert "dumpnotes" in wiring["pages"], "'dumpnotes' missing from the PAGES array"
    assert wiring["loaders"].get("dumpnotes") == "loadVoiceDumpItems", (
        f"loaders['dumpnotes'] should be loadVoiceDumpItems, got "
        f"{wiring['loaders'].get('dumpnotes')!r}"
    )


def test_committed_bundle_contains_every_page_id(wiring):
    """index.html loads /static/rack.min.js, not rack.js. Editing the source
    without re-running `npm run build:js` leaves the new page unreachable in
    the browser while every source-level check above still passes."""
    minified = _read("rack.min.js")
    missing = [p for p in wiring["pages"] if f'"{p}"' not in minified and f"'{p}'" not in minified]
    assert not missing, (
        f"page ids present in static/rack.js but not in the committed "
        f"static/rack.min.js: {missing} — re-run `npm run build:js` and commit "
        f"the regenerated bundle"
    )
