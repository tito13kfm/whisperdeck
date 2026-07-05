---
name: e2e-ux-audit
description: Browser-driven exploratory UX audit of WhisperDeck via Playwright MCP against an isolated local server instance. Drives 6 realistic user journeys end-to-end, judging usability (not just PASS/FAIL), and produces an HTML findings report. Use when asked to audit WhisperDeck's UX, do an exploratory browser pass, or find UI friction beyond backend regression testing.
---

# WhisperDeck UX Audit

Drives 6 realistic user journeys through a real browser against a
throwaway server instance, judging whether the app is usable — not just
whether the backend did what was asked. This complements
`.claude/skills/e2e-test-app/SKILL.md` (scripted PASS/FAIL backend
regression); this skill looks for friction a real user would hit: dead
controls, confusing labels, too many steps, stale UI, missing feedback,
unreachable features.

Run **inline in this session** — one continuous Playwright browser
instance across all 6 journeys, not dispatched to subagents. State
(login session, uploaded transcripts, enrolled voices) carries forward
between journeys.

**Maintenance note:** Journey 4/steps involving summarize/correct/context
apply a workaround for
[issue #2](https://github.com/tito13kfm/whisperdesk/issues/2) (the
`local` provider sends an empty `Authorization: Bearer ` header, which
`httpx` rejects). **Once issue #2 is fixed, remove the placeholder-API-key
step from this skill** — leaving it in after the fix is harmless but
stale.

**Do not re-flag as findings:** the known no-title-rename-UI-control gap
(API-only via `PATCH /api/transcripts/{id}`) is an already-decided,
intentional gap — do not log it as a new finding if encountered again.

## Setup

1. Pick an isolated data dir and port. On Windows PowerShell:

```powershell
$env:WHISPERDECK_DATA_DIR = "$env:TEMP\whisperdeck-uxaudit-$(Get-Random)"
$env:PORT = "9782"
New-Item -ItemType Directory -Force -Path $env:WHISPERDECK_DATA_DIR | Out-Null
```

2. Start the server as a background process from the repo root:

```powershell
$proc = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" `
  -WorkingDirectory (Get-Location) -PassThru -WindowStyle Hidden
```

If `.venv\Scripts\python.exe` doesn't exist, use `python.exe` instead.
Record `$proc.Id` — teardown needs it.

3. Poll until healthy (max 30s):

```powershell
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  try {
    $r = Invoke-RestMethod -Uri "http://localhost:9782/api/health" -TimeoutSec 2
    if ($r) { $ready = $true; break }
  } catch {}
  Start-Sleep -Seconds 1
}
```

If `$ready` is `false` after 30s: report `FAIL: server did not become
healthy` and stop the whole run.

4. Check what's actually available before running journeys:

```powershell
$health = Invoke-RestMethod -Uri "http://localhost:9782/api/health" -TimeoutSec 2
$lemonade_available = $false
try {
  Invoke-RestMethod -Uri "http://localhost:13305/v1/models" -TimeoutSec 2 | Out-Null
  $lemonade_available = $true
} catch {}
```

Use `$health.diarization_backend` and `$lemonade_available` to decide
which journeys run at full fidelity vs. get marked
`SKIPPED(backend unavailable)`.

5. Launch Chromium via the Playwright MCP browser tool with fake media
   device flags, pointed at `http://localhost:9782`:
   - `--use-fake-device-for-media-stream`
   - `--use-file-for-fake-audio-capture=<repo-root>\tests\fixtures\e2e_multispeaker.mp3`
   (If the fixture isn't present, omit this flag — Journey 2 will be
   `SKIPPED(no fixture)`.)

6. Navigate to `http://localhost:9782/` and confirm the page loads
   before starting Journey 1.

7. Give the `local` provider a non-empty placeholder API key, working
   around issue #2 before any LLM-job journey step needs it:

```powershell
Invoke-RestMethod -Uri "http://localhost:9782/api/providers/local" -Method Put `
  -ContentType "application/json" -Body '{"api_key":"not-needed"}'
```

## Findings format

Log a finding the moment friction is noticed, using this exact block:

```
- Journey N, step: <what was being done>
  Type: dead-control | mislabeled | too-many-steps | stale-ui | missing-feedback | unreachable-feature | other
  Severity: blocker | major | minor
  Note: <one-line description of the problem>
  Screenshot: <path or "none">
```

Screenshot rule: capture one **only** when severity is `major` or
`blocker`, or the finding is inherently visual (e.g. a layout glitch).
Save via the Playwright MCP screenshot tool to
`docs/superpowers/e2e-findings/<journey-slug>-<n>.png` (e.g.
`journey2-live-capture-1.png`), where `<n>` increments per screenshot
within that journey. Do not screenshot on every step — only when logging
a finding.

Keep a running list of all findings across all 6 journeys; the Report
section below consumes this list.

## Journey 1: First meeting, cold start

Registers a user, configures the transcription provider, uploads a
recording, and reads the resulting transcript — as a first-time user
would, with no prior knowledge of the app.

1. Navigate to `http://localhost:9782/`. If a login/register form is
   shown, fill username `uxaudit_user`, password `uxaudit_pass_123`,
   submit register.
   - Watch: is it obvious this is a register form vs. login? Is there
     visible guidance (password requirements, confirmation field) or
     does a bad password just silently fail?
2. After registering, look at the very first screen shown.
   - Watch: is it clear what to do next (e.g. an obvious "upload" or
     "start recording" affordance), or does the screen look empty/blank
     with no call to action? Log a finding if a first-time user would be
     stuck here.
3. Configure the transcription provider: open the providers/services
   panel, confirm Moonshine is available and selected. If not already
   the default, select it.
   - Watch: does the panel make it clear which provider is currently
     active vs. just available?
4. Upload `tests/fixtures/e2e_multispeaker.mp3` via the file input /
   drop zone and submit.
   - Watch: is there a visible progress indicator between submit and
     completion, or does the UI look frozen/unchanged during processing?
5. Poll `GET http://localhost:9782/api/transcripts/{id}` every 3s, up to
   3 minutes, until `status` is `completed`, `failed`, or `partial`.
   Record the ID as `$J1_TRANSCRIPT_ID`.
   - Watch: once complete, does the UI update on its own (e.g.
     auto-navigate to the transcript, live status change) or does the
     user have to manually refresh/re-navigate to see the result?
6. Open the completed transcript's detail view.
   - Watch: does it render segments/text clearly? Is it obvious how to
     get back to the list of all transcripts?

Report: `[PASS|FAIL] Journey 1: Cold start`

Log any findings noticed during steps 1-6 using the Findings format.

## Journey 2: Live capture end-to-end

Only runs if Setup launched Chromium with
`--use-file-for-fake-audio-capture`; otherwise
`SKIPPED(no fixture/fake device)`.

This is the least-verified path in the app — the scripted
`e2e-test-app` skill has zero live evidence for it. Pay close attention
to the recording UI itself, not just the resulting job status.

1. Trigger the "Live capture" control (`#key-rec` in `static/rack.js`).
   - Watch: does the button's visual state change immediately (e.g. to
     a "recording" look) or is there a delay/no feedback at all?
2. Grant the (fake) microphone permission prompt if one appears.
   - Watch: if a permission prompt appears, is it clear to the user
     what's being requested and why?
3. While "recording" (5-10 seconds), observe the UI.
   - Watch: is there a visible timer, waveform, or other live indicator
     that recording is actually happening? A control with no feedback
     during an active recording is a strong candidate for a
     `missing-feedback` finding.
4. Stop recording via the same control (its title/state should toggle,
   per `static/rack.js`'s toggle logic).
   - Watch: is it obvious the recording stopped (e.g. button reverts,
     a "processing" state appears) or does the UI look unchanged?
5. Confirm a new transcription job is submitted and reaches a terminal
   status (`completed`, `failed`, or `partial`) using the same poll
   pattern as Journey 1.
   - Watch: does the new transcript appear in the list/UI automatically,
     or does the user have to manually navigate to find it?

Report: `[PASS|FAIL|SKIPPED(reason)] Journey 2: Live capture`

Log any findings noticed during steps 1-5 using the Findings format.

## Journey 3: Voice roster built across meetings

Same fixture gate as Journey 1 (requires `tests/fixtures/e2e_multispeaker.mp3`);
if missing, `SKIPPED(no multispeaker fixture)`.

Tests whether the voice bank is a real, discoverable UX loop across
multiple meetings — not just whether the underlying API calls succeed.

1. Upload `tests/fixtures/e2e_multispeaker.mp3` with diarization enabled
   (the "Speakers" toggle before submitting). Poll until terminal
   status. Record as `$J3_TRANSCRIPT_ID`.
2. Rename one detected speaker to a real name, e.g. `Alice`, via the
   speaker-rename UI control.
   - Watch: is the rename control easy to find (does the user have to
     already know to click directly on a speaker label)?
3. Enroll a voice profile for `Alice` using the "Enroll marked clips"
   flow (mark a segment, then enroll).
   - Watch: is it clear afterward that the enrollment succeeded (e.g.
     confirmation, or the profile visibly appearing in a voice roster
     view)?
4. Upload the same fixture a second time (simulating a second meeting
   with the same speakers), without pre-labeling anyone. Poll until
   terminal status. Record as `$J3_TRANSCRIPT_ID_2`.
5. Without being told which button to press, look at the transcript
   detail view and the diarization result for `$J3_TRANSCRIPT_ID_2`.
   - Watch: does the UI proactively suggest or highlight a possible
     match against the enrolled `Alice` profile (e.g. a badge, a
     suggested label, an unprompted notification)? Or does the user
     have to already know to seek out and trigger a separate
     "Identify"/"Match" action?
   - This is the core judgment call of this journey: if there is no
     visible cue that voice matching is available at all, log an
     `unreachable-feature` or `missing-feedback` finding (choose based
     on whether the control exists but is unadvertised, vs. genuinely
     absent from this view).
6. Trigger the match/identify action explicitly (if not already
   surfaced in step 5) and confirm segments in `$J3_TRANSCRIPT_ID_2` get
   labeled against the enrolled `Alice` profile.
7. Delete the enrolled `Alice` voice profile via the Voice Roster view.
   - Watch: is there a confirmation step before deleting (accidental
     deletion risk), or does it delete immediately on click?

Report: `[PASS|FAIL|SKIPPED(reason)] Journey 3: Voice roster`

Log any findings noticed during steps 1-7 using the Findings format.

## Teardown

Run this after all journeys, even if some failed:

```powershell
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $env:WHISPERDECK_DATA_DIR -ErrorAction SilentlyContinue
```

Close the Playwright browser session.
