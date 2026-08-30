"""Regression for issue #313: #job-tagging container never existed.

`static/rack.js:updateDetailJobStatus` has a `runningContainers` entry for
`{ id: 'job-tagging', job: t.tagging_job }` and tries to patch that container
in place on every poll tick. But `renderDetailBody`'s transcript branch never
emitted a `<div id="job-tagging">` container, so the ticker had nothing to
patch — tagging ran invisibly until the user navigated away and back.

This test asserts the two halves of the contract together via the on-disk
rack.js text (no browser needed):

  1. `renderDetailBody`'s transcript branch must emit `id="job-tagging"`
     coupled to `llmJobActive(t.tagging_job)` and `jobRunningUnit` — otherwise
     the widget never appears on initial render / re-render.

  2. `runningContainers` must list `job-tagging`/`tagging_job` so the micro
     update path has something to advance on poll ticks.

Complement sweep (same commit): `voice_note_job` is the sibling with the same
shape — it had an initial emitter via its dedicated route (`voiceNoteHtml`)
but no `runningContainers` entry and no `job-voice-note` id on that emitter,
so mid-run voice-note progress on its active surface also froze.

Mutation check: replace the added `tagging = llmJobActive(t.tagging_job) …`
line in renderDetailBody with ``, or remove the `job-tagging` entry from
`runningContainers`, and each respective assertion fails.
"""

import re
from pathlib import Path

RACK = Path(__file__).resolve().parents[1] / "static" / "rack.js"
TEXT = RACK.read_text(encoding="utf-8")


def _block(label: str, start: str, end: str | None = None) -> str:
    s = TEXT.index(start)
    e = TEXT.index(end, s) if end else len(TEXT)
    assert s != -1, f"marker {label!r} not found"
    return TEXT[s:e]


def test_render_detail_body_emits_job_tagging_widget():
    # Only the transcript branch should emit this — tagging is kind-agnostic
    # so it lives on the transcript tab, not a kind-gated tab.
    body = _block(
        "renderDetailBody transcript branch",
        "async function renderDetailBody()",
        "} else if (S.detailTab === 'corrected')",
    )
    assert 'id="job-tagging"' in body or "id='job-tagging'" in body, (
        "renderDetailBody's transcript branch must emit a #job-tagging container"
    )
    # Gate must be llmJobActive on tagging_job specifically, not another job
    # and not an unconditional emit — otherwise the container would show even
    # when tagging isn't running.
    assert re.search(r"llmJobActive\s*\(\s*t\.tagging_job\s*\)", body), (
        "transcript branch must gate #job-tagging on llmJobActive(t.tagging_job)"
    )
    assert re.search(r"jobRunningUnit\s*\(\s*t\.tagging_job", body), (
        "transcript branch must render #job-tagging via jobRunningUnit(t.tagging_job, …)"
    )
    # And it must actually be spliced into body.innerHTML
    # (a dead local that is never concatenated is the same as not emitting it).
    assert re.search(r"body\.innerHTML\s*=.*\btagging\b", body, re.DOTALL), (
        "renderDetailBody must splice the tagging widget into body.innerHTML"
    )


def test_running_containers_includes_tagging():
    block = _block(
        "runningContainers",
        "const runningContainers = [",
        "for (const { id: containerId",
    )
    assert "job-tagging" in block and "tagging_job" in block, (
        "runningContainers must include { id: 'job-tagging', job: t.tagging_job }"
    )
    # Label is what jobRunningUnit renders inside the unit — sanity check
    # it didn't drift to a different casing while we're here.
    assert re.search(r"job-tagging.*Tagging", block, re.DOTALL)


def test_complement_voice_note_has_both_halves():
    # runningContainers half
    rc = _block(
        "runningContainers voice_note",
        "const runningContainers = [",
        "for (const { id: containerId",
    )
    assert "job-voice-note" in rc and "voice_note_job" in rc, (
        "complement: runningContainers must include voice_note_job (same class as #313)"
    )
    # Initial-emitter half: voiceNoteHtml must emit id="job-voice-note"
    # so the poll ticker has a live container to patch (parallels the tagging fix).
    assert 'id="job-voice-note"' in TEXT or "id='job-voice-note'" in TEXT, (
        "voiceNoteHtml must emit #job-voice-note so the runningContainers ticker can patch it"
    )
