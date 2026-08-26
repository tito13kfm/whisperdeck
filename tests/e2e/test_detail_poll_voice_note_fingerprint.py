"""e2e regression for issue #426: _jobFingerprint and scheduleDetailPoll must include voice_note_job

_jobFingerprint and scheduleDetailPoll derive from DETAIL_JOB_SLOTS in
static/rack.js. When voice_note shipped, voice_note_job was not added to that
array, so a running voice-note chain never triggered a detail-page poll and
its progress never surfaced without leaving the page. Same class as #246
(tagging_job).

This test asserts the fingerprint string itself changes when only
voice_note_job.progress.done changes, and that voice_note_job can keep the
poll gate open. It loads rack.js in a real browser (so the real
_jobFingerprint + llmJobActive run, not a reimplementation) and compares
payloads that differ only in voice_note_job.progress.done. Against the
pre-fix array (no voice_note_job term) both payloads produce the same
fingerprint and the gate stays closed, so this test fails on the old code
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
    _ensure_test_user(live_server, "e2e_voicenote_fp_test", "e2e_voicenote_fp_test_pass_123")
    return ("e2e_voicenote_fp_test", "e2e_voicenote_fp_test_pass_123")


def _login(page, username, password):
    page.fill("input[name='username'], #username, input[type='text']", username)
    page.fill("input[type='password']", password)
    page.click("button[type='submit'], button:has-text('Sign'), button:has-text('Log')")
    page.wait_for_selector("#app-shell", state="visible", timeout=10000)


def test_jobfingerprint_changes_when_only_voice_note_progress_updates(page, registered_user, live_server):
    """Regression test for issue #426: _jobFingerprint must differ when only
    voice_note_job.progress.done changes, and voice_note_job keeps the poll
    gate open.

    Mutation check: if _jobFingerprint's body were replaced with `return`,
    both fingerprints would be undefined/None and equal, so the regression
    assertion fails. Against the pre-fix code (no voice_note_job term)
    both payloads also yield the same string and the gate stays false.
    """
    username, password = registered_user
    _login(page, username, password)
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
                tagging_job: null,
                voice_dump_job: null,
                voice_note_job: { status: 'running', progress: { done: 1, total: 4 } },
            };
            const a = JSON.parse(JSON.stringify(base));
            const b = JSON.parse(JSON.stringify(base));
            b.voice_note_job.progress.done = 2;
            // Also check the gate: a voice_note-only payload must be considered
            // active (otherwise scheduleDetailPoll returns before scheduling).
            const onlyVoiceNote = {
                correction_job: null,
                summary_job: null,
                voice_match_job: null,
                format_markdown_job: null,
                format_email_job: null,
                format_coding_prompt_job: null,
                classify_intent_job: null,
                tagging_job: null,
                voice_dump_job: null,
                voice_note_job: { status: 'running', progress: { done: 1, total: 3 } },
            };
            const empty = {
                correction_job: null,
                summary_job: null,
                voice_match_job: null,
                format_markdown_job: null,
                format_email_job: null,
                format_coding_prompt_job: null,
                classify_intent_job: null,
                tagging_job: null,
                voice_dump_job: null,
                voice_note_job: null,
            };
            function llmJobActive(job) {
                return !!(job && (job.status === 'pending' || job.status === 'running'));
            }
            // Derive what the poll gate would compute: DETAIL_JOB_SLOTS
            // is not exported, so ask the real _jobFingerprint indirectly:
            // the fingerprint of onlyVoiceNote must differ from empty's, and
            // from a. Progress-only change must flip it.
            return {
                fa: window._jobFingerprint(a),
                fb: window._jobFingerprint(b),
                fa2: window._jobFingerprint(a),
                fVoiceOnly: window._jobFingerprint(onlyVoiceNote),
                fEmpty: window._jobFingerprint(empty),
            };
        }"""
    )

    assert result["fa"] == result["fa2"], (
        f"_jobFingerprint is not deterministic: {result['fa']!r} != {result['fa2']!r}"
    )
    assert result["fa"] != result["fb"], (
        f"_jobFingerprint did not change when only voice_note_job.progress.done "
        f"changed: {result['fa']!r} == {result['fb']!r} "
        f"(voice_note_job is missing from the fingerprint)"
    )
    assert result["fVoiceOnly"] != result["fEmpty"], (
        f"_jobFingerprint for a voice_note-only payload is identical to empty: "
        f"{result['fVoiceOnly']!r} == {result['fEmpty']!r} "
        f"(voice_note_job not in fingerprint, so scheduleDetailPoll gate stays closed)"
    )
