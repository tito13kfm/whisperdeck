"""e2e regression for issue #246: _jobFingerprint must include tagging_job

The _jobFingerprint function in static/rack.js builds a poll-comparison string
from the LLM job fields. When tagging shipped, tagging_job was not included in
that fingerprint, so when only tagging's status/progress changed the fingerprint
string stayed the same and updateDetailJobStatus / re-render never fired for a
mid-run tagging progress change.

Per issue #246's acceptance criterion, this test asserts the fingerprint string
itself changes when only tagging_job.progress.done changes. It loads rack.js
in a real browser (so the real _jobFingerprint runs, not a reimplementation)
and compares the fingerprints of two payloads that differ only in
tagging_job.progress.done. Against the pre-fix fingerprint (no tagging_job
term) both payloads produce the same string, so this test fails on the old code
and passes on the fixed code.
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


def test_jobfingerprint_changes_when_only_tagging_progress_updates(page, registered_user, live_server):
    """Regression test for issue #246: _jobFingerprint must differ when only
    tagging_job.progress.done changes.

    Constructs two payloads identical except for tagging_job.progress.done and
    asserts the real _jobFingerprint (loaded from rack.js and exported on window
    for test access) produces different strings. Also anchors that the function
    is deterministic: the same payload twice yields the same fingerprint.

    Mutation check: if _jobFingerprint's body were replaced with `return`, both
    fingerprints would be undefined and equal, so the regression assertion fails.
    Against the pre-fix code (no tagging_job term in the fingerprint) both
    payloads also yield the same string, so the assertion fails there too.
    """
    username, password = registered_user
    _login(page, username, password)

    # rack.js is loaded once #app-shell is visible; _jobFingerprint is exported
    # on window for test access.
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)

    result = page.evaluate(
        """() => {
            const base = {
                correction_job: null,
                summary_job: null,
                voice_match_job: null,
                format_markdown_job: null,
                format_email_job: null,
                format_coding_prompt_job: null,
                classify_intent_job: null,
                tagging_job: { status: 'running', progress: { done: 1, total: 4 } },
            };
            const a = JSON.parse(JSON.stringify(base));
            const b = JSON.parse(JSON.stringify(base));
            b.tagging_job.progress.done = 2;
            return {
                fa: window._jobFingerprint(a),
                fb: window._jobFingerprint(b),
                fa2: window._jobFingerprint(a),
            };
        }"""
    )

    # Determinism anchor: identical payloads yield identical fingerprints.
    assert result["fa"] == result["fa2"], (
        f"_jobFingerprint is not deterministic for the same payload: "
        f"{result['fa']!r} != {result['fa2']!r}"
    )

    # Regression assertion: changing only tagging_job.progress.done must change
    # the fingerprint. With the pre-fix fingerprint (no tagging_job term) fa and
    # fb are equal, so this fails on the old code.
    assert result["fa"] != result["fb"], (
        f"_jobFingerprint did not change when only tagging_job.progress.done "
        f"changed: {result['fa']!r} == {result['fb']!r} "
        f"(tagging_job is missing from the fingerprint)"
    )
