"""resetDeckState must clear assistant history from memory and sessionStorage.

Issue #304: resetDeckState cleared deck/status/detail state (#54) but left
sessionStorage wd_assistant_history and S.assistantHistory intact, so the next
user on the same tab saw the previous account's assistant answers (which embed
transcript text).

The durable fix is a registry WD_SESSION_KEYS that resetDeckState iterates;
adding a future wd_* key requires only an array edit.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RACK_JS = ROOT / "static" / "rack.js"
RACK_MIN = ROOT / "static" / "rack.min.js"


def _reset_block() -> str:
    src = RACK_JS.read_text(encoding="utf-8")
    start = src.index("function resetDeckState()")
    # next top-level function is showLogin
    end = src.index("\nfunction showLogin()", start)
    return src[start:end]


def _wd_keys_block() -> str:
    src = RACK_JS.read_text(encoding="utf-8")
    start = src.index("const WD_SESSION_KEYS")
    end = src.index("];", start) + 2
    return src[start:end]


def test_wd_session_keys_registry_exists():
    assert "const WD_SESSION_KEYS" in RACK_JS.read_text(encoding="utf-8")
    block = _wd_keys_block()
    assert "wd_assistant_history" in block


def test_reset_clears_assistant_history_in_memory():
    block = _reset_block()
    assert "S.assistantHistory = []" in block, (
        "resetDeckState must reset S.assistantHistory to [] so in-memory "
        "history does not survive showLogin without a page reload"
    )


def test_reset_clears_session_storage_via_registry():
    block = _reset_block()
    assert "WD_SESSION_KEYS" in block, (
        "resetDeckState must iterate WD_SESSION_KEYS so future session keys "
        "are cleared without editing the function body"
    )
    assert "sessionStorage.removeItem" in block
    # must reference the loop variable, not a hardcoded string, to prove registry is used
    assert "removeItem(k)" in block or "removeItem( k" in block


def test_assistant_history_key_reaches_bundle():
    bundle = RACK_MIN.read_text(encoding="utf-8")
    assert "wd_assistant_history" in bundle, (
        "rack.js fix not reflected in committed rack.min.js — run npm run build:js"
    )


def test_reset_clears_via_removeitem_in_bundle():
    bundle = RACK_MIN.read_text(encoding="utf-8")
    assert "removeItem" in bundle
