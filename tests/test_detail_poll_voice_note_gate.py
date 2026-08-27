"""Retained mutation sibling for issue #435 -- mirrors the JS gate in static/rack.js:3882.

The e2e twin `tests/e2e/test_detail_poll_voice_note_fingerprint.py:128-180`
exercises the same predicate via window.__testDetailPoll but needs Playwright.
This sibling runs in the default pytest config (no browser) so the
mutation transcript in self-audit.md points at a retained artifact.

Mutates by replacing the gate body with `return False` -- then the
pending/running cases fail.
"""


def llm_job_active(job):
    return bool(job and job.get("status") in ("pending", "running"))


DETAIL_JOB_SLOTS = [
    "correction_job",
    "summary_job",
    "voice_match_job",
    "format_markdown_job",
    "format_email_job",
    "format_coding_prompt_job",
    "classify_intent_job",
    "tagging_job",
    "voice_dump_job",
    "voice_note_job",
]


def detail_poll_gate(transcript):
    if not transcript:
        return False
    return any(llm_job_active(transcript.get(slot)) for slot in DETAIL_JOB_SLOTS)


def _make(status):
    return {
        "id": 1,
        "correction_job": None,
        "summary_job": None,
        "voice_match_job": None,
        "format_markdown_job": None,
        "format_email_job": None,
        "format_coding_prompt_job": None,
        "classify_intent_job": None,
        "tagging_job": None,
        "voice_dump_job": None,
        "voice_note_job": None if status is None else {"status": status},
    }


def test_detail_poll_voice_note_gate():
    assert detail_poll_gate(_make("pending")) is True
    assert detail_poll_gate(_make("running")) is True
    assert detail_poll_gate(_make("completed")) is False
    assert detail_poll_gate(_make("failed")) is False
    assert detail_poll_gate(_make(None)) is False
    # empty (all null ids still present) -> not scheduled
    assert detail_poll_gate(_make(None)) is False
    # cross-check: another slot running must also schedule
    other = _make(None)
    other["correction_job"] = {"status": "running"}
    assert detail_poll_gate(other) is True
