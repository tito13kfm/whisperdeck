"""Every audio-cleanup setting the backend reads must be reachable from the UI.

Issue #317: #270 shipped the whole audio-cleanup backend plus twelve
DEFAULT_SETTINGS keys, and no UI to set any of them. `PUT /api/settings` had
accepted them the whole time (services/settings.py filters incoming keys
against DEFAULT_SETTINGS), so the keys were reachable only by hand-writing an
HTTP request. Nothing failed, nothing warned, and four issues sat open for a
month reading as unbuilt features.

This test is the guard against that recurring: a cleanup key that exists in
DEFAULT_SETTINGS but in no settings control fails here.

It also catches a stale bundle. static/index.html loads static/rack.min.js,
never static/rack.js, so a source-only edit is invisible in the browser (see
tests/test_static_nav_wiring.py, which exists for the same reason). Asserting
against the committed bundle means forgetting `npm run build:js` fails here.
"""
from pathlib import Path

import pytest

from services.settings import DEFAULT_SETTINGS

ROOT = Path(__file__).resolve().parent.parent
RACK_JS = ROOT / "static" / "rack.js"
RACK_MIN_JS = ROOT / "static" / "rack.min.js"

# cleanup_demucs_enabled is intentionally not exposed. cleanup_demucs() in
# services/audio_cleanup.py is written and unit-tested but called from no
# production code path, so a toggle for it would persist a value that changes
# nothing. Issue #239 owns wiring it up, along with the consent flow for its
# multi-GB model download. Wiring Demucs up means adding a control AND
# deleting it from this set.
NOT_YET_EXPOSED = {"cleanup_demucs_enabled"}


def _cleanup_keys() -> set[str]:
    return {k for k in DEFAULT_SETTINGS if k.startswith("cleanup_")}


def _cleanup_fields_block() -> str:
    """The CLEANUP_FIELDS registry literal from static/rack.js.

    Sliced out rather than searching the whole file, so a key that only appears
    in a comment or an unrelated code path can't satisfy the coverage check --
    the registry is what actually drives the render, the toggle wiring, and the
    save payload.
    """
    source = RACK_JS.read_text(encoding="utf-8")
    start = source.index("const CLEANUP_FIELDS = [")
    end = source.index("\n];", start)
    return source[start:end]


def test_there_are_cleanup_keys_to_check():
    """Guards the two tests below against silently passing on an empty set if
    the keys are ever renamed off the cleanup_ prefix."""
    assert len(_cleanup_keys()) >= 12


@pytest.mark.parametrize("key", sorted(_cleanup_keys() - NOT_YET_EXPOSED))
def test_cleanup_key_has_a_settings_control(key):
    assert key in _cleanup_fields_block(), (
        f"{key} is in DEFAULT_SETTINGS but not in CLEANUP_FIELDS in "
        f"static/rack.js, so it cannot be set from the app. Add a control for "
        f"it, or add it to NOT_YET_EXPOSED in this test with the issue that "
        f"owns it."
    )


@pytest.mark.parametrize("key", sorted(_cleanup_keys() - NOT_YET_EXPOSED))
def test_cleanup_key_reaches_the_committed_bundle(key):
    """String literals survive esbuild --minify (only identifiers are
    mangled), so the settings key appears verbatim in the bundle."""
    bundle = RACK_MIN_JS.read_text(encoding="utf-8")
    assert key in bundle, (
        f"{key} is in static/rack.js but not in the committed "
        f"static/rack.min.js, which is the file static/index.html actually "
        f"loads. Run `npm run build:js` and commit the result."
    )


def test_unexposed_cleanup_keys_really_are_absent():
    """Mirror of the tests above: a key listed as not-yet-exposed must not be in
    the registry, or the exclusion list is lying about what the UI offers."""
    block = _cleanup_fields_block()
    for key in NOT_YET_EXPOSED:
        assert key in DEFAULT_SETTINGS, f"{key} is excluded but no longer exists"
        assert key not in block, (
            f"{key} now has a control in CLEANUP_FIELDS, so it is exposed after "
            f"all. Remove it from NOT_YET_EXPOSED."
        )
