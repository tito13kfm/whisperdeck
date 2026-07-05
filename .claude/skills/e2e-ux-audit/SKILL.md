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

## Journey 4: Wrap-up-the-meeting flow

Same Lemonade gate as the scripted skill's summarize/correct/context
scenarios; if `$lemonade_available` is `$false`,
`SKIPPED(Lemonade unreachable)`. Uses `$J1_TRANSCRIPT_ID` from Journey 1.

The issue #2 placeholder-API-key workaround was already applied in
Setup step 7 — do not reapply here.

1. On `$J1_TRANSCRIPT_ID`, trigger "Re-run correction" with
   provider `local`, model `gpt-oss-20b-mxfp4-GGUF`. Poll until the job
   reaches a terminal status.
   - Watch: while the job runs, is there any visible indication a
     background job is in progress (spinner, disabled button, status
     text), or does the button just look clickable/idle the whole time?
2. Add a short context document (a sentence or two naming a term/person
   in the transcript) via the "Add context" control.
3. Re-run correction again (same as step 1).
4. Trigger "Summarize" with the same provider/model. Poll until
   terminal status.
   - Watch: once summarize completes, is the summary immediately visible
     without extra navigation, or does the user have to hunt for it?
5. With the correction and summary results visible, look for any
   export, copy, download, or share affordance for either the corrected
   transcript or the summary.
   - Watch: this is genuinely unexplored territory — the scripted skill
     never checked for this. If no such control exists anywhere in the
     detail view, log an `unreachable-feature` finding noting that a
     real user has no way to get their meeting notes out of the app
     short of manually selecting text.

Report: `[PASS|FAIL|SKIPPED(reason)] Journey 4: Wrap-up flow`

Log any findings noticed during steps 1-5 using the Findings format.

## Journey 5: Managing a growing transcript backlog

By this point, Journeys 1-4 have created several transcripts and jobs —
use that accumulated state rather than creating more.

1. Open the transcript list view.
   - Watch: with 3+ transcripts now present (from Journeys 1, 3, 4), is
     there any way to search, sort, or filter, or is it a flat
     unsorted/unlabeled list that would get unwieldy at real-world
     volume? Log a finding if there's no way to distinguish transcripts
     beyond scrolling and reading titles.
2. Open the jobs panel.
   - Watch: does it clearly list the jobs created by earlier journeys
     (upload, diarize, summarize, correct) with distinguishable
     statuses? Is it clear which transcript each job belongs to?
3. Start one throwaway job specifically to cancel here: trigger a
   summarize or correct call on any transcript, then immediately call
   the cancel action on it (`j-cancel` UI control / `POST
   /api/jobs/{job_id}/cancel`) before it completes.
   - Watch: is the cancel control easy to find/use under time pressure
     (i.e., does the user have to hunt for it while the job is racing to
     finish)?
4. Delete one non-essential transcript (e.g. create and immediately
   delete a throwaway upload — do not delete `$J1_TRANSCRIPT_ID`,
   `$J3_TRANSCRIPT_ID`, or `$J3_TRANSCRIPT_ID_2`).
   - Watch: is there a confirmation step before deleting a transcript
     (accidental data loss risk), or does it delete immediately?

Report: `[PASS|FAIL] Journey 5: Backlog management`

Log any findings noticed during steps 1-4 using the Findings format.

## Journey 6: Misconfiguration recovery

Deliberately breaks a provider configuration and checks whether the
resulting failure gives the user a useful signal, or fails silently.
This is the one journey that intentionally exercises the failure path
rather than the happy path.

1. Record the current `local` provider config via `GET
   /api/providers/local` for restoration in step 4.
2. Set the `local` provider's `api_url` to an unreachable address, e.g.
   `http://localhost:1/v1` (`PUT /api/providers/local` with
   `{"api_url": "http://localhost:1/v1"}`).
3. Attempt a summarize or correct call on any existing transcript
   (e.g. `$J1_TRANSCRIPT_ID`) using the `local` provider. Poll until
   terminal status.
   - Watch: does the job reach `failed` with a clear, human-readable
     error surfaced in the UI (toast, inline message), or does it look
     like nothing happened (silent failure) or produce a confusing raw
     error?
   - Watch: does the UI make it easy to figure out *what* to fix (e.g.
     pointing back at the providers panel), or does the user have no
     path forward without already knowing to check provider settings?
4. Restore the `local` provider's original `api_url` from step 1 (`PUT
   /api/providers/local`).
   - Check: a subsequent summarize/correct call on the `local` provider
     succeeds again, confirming the app recovers cleanly once
     configuration is fixed.

Report: `[PASS|FAIL] Journey 6: Misconfiguration recovery`

Log any findings noticed during steps 1-4 using the Findings format.

## Teardown

Run this after all journeys, even if some failed:

```powershell
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $env:WHISPERDECK_DATA_DIR -ErrorAction SilentlyContinue
```

Close the Playwright browser session.

## Report

After Teardown, generate a static HTML report and open it for review.

1. Build the report content: a journey summary table (one row per
   Journey 1-6, columns Journey/Status) followed by all logged findings
   grouped by severity (`blocker` first, then `major`, then `minor`),
   each rendered as a list item with its Journey/step, Type, Note, and
   an `<img>` tag if a Screenshot path was recorded.

2. Write the HTML file. Example structure (fill in actual journey
   statuses and findings collected during the run):

```powershell
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$reportPath = "docs/superpowers/e2e-findings/report-$timestamp.html"

$html = @"
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>WhisperDeck UX Audit Report</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
  th, td { border: 1px solid #ccc; padding: 0.5rem; text-align: left; }
  .PASS { color: #1a7f37; }
  .FAIL { color: #cf222e; }
  .SKIPPED { color: #9a6700; }
  .finding { border-left: 4px solid #ccc; padding: 0.5rem 1rem; margin-bottom: 1rem; }
  .blocker { border-left-color: #cf222e; }
  .major { border-left-color: #bf8700; }
  .minor { border-left-color: #57606a; }
  img { max-width: 100%; border: 1px solid #ddd; margin-top: 0.5rem; }
</style>
</head>
<body>
<h1>WhisperDeck UX Audit Report</h1>
<h2>Journey summary</h2>
<table>
<tr><th>Journey</th><th>Status</th></tr>
<!-- one <tr><td>Journey N: name</td><td class="STATUS">STATUS</td></tr> per journey -->
</table>
<h2>Findings</h2>
<!-- one .finding div per finding, grouped blocker/major/minor, e.g.:
<div class="finding blocker">
  <strong>Journey 2, step: stop recording</strong><br>
  Type: missing-feedback<br>
  Note: No visible indicator during recording; user can't tell it's live.<br>
  <img src="journey2-live-capture-1.png">
</div>
-->
</body>
</html>
"@

Set-Content -Path $reportPath -Value $html -Encoding UTF8
```

3. Open the report in the user's real default browser (not the
   Playwright-controlled instance — it may already be torn down, and
   it's not a full browsing experience anyway):

```powershell
Start-Process (Resolve-Path $reportPath)
```

4. Print the full list of `[PASS|FAIL|SKIPPED]` lines from every
   journey, plus the report file path, as the final output of this
   skill.
