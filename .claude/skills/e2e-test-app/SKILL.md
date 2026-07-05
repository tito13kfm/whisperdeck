---
name: e2e-test-app
description: Full end-to-end feature test of WhisperDeck via Playwright MCP against an isolated local server instance (Moonshine STT, local diarization, Lemonade LLM). Use when asked to run the e2e test, verify all features work, or regression-test WhisperDeck before a release.
---

# WhisperDeck End-to-End Test

Drives every feature of WhisperDeck through a real browser against a
throwaway server instance. Local/keyless backends only — no API keys, no
network cost. Each step is either a scripted Playwright action with an
exact check, or a DOM assertion — never a subjective judgment call.

Report format: after every scenario, emit one line:
`[PASS|FAIL|SKIPPED(reason)] Scenario N: <name>`
Continue to the next scenario on failure. Never abort the run early.

## Setup

1. Pick an isolated data dir and port. On Windows PowerShell:

```powershell
$env:WHISPERDECK_DATA_DIR = "$env:TEMP\whisperdeck-e2e-$(Get-Random)"
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

If `$ready` is `false` after 30s: report `FAIL: server did not become healthy`
and stop the whole run (this is the one case where aborting is correct —
nothing downstream can run without a server).

4. Check what's actually available before running scenarios:

```powershell
$health = Invoke-RestMethod -Uri "http://localhost:9782/api/health" -TimeoutSec 2
$lemonade_available = $false
try {
  Invoke-RestMethod -Uri "http://localhost:13305/v1/models" -TimeoutSec 2 | Out-Null
  $lemonade_available = $true
} catch {}
```

Record: the `$health` object's `diarization_backend` field shows if a diarization
backend beyond heuristic is installed. The `$lemonade_available` variable shows if
Lemonade (http://localhost:13305/v1/models) is reachable. Use this to decide
which scenarios run at full fidelity vs. get marked
`SKIPPED(backend unavailable)`.

5. Launch Chromium via the Playwright MCP browser tool with fake media
   device flags, pointed at `http://localhost:9782`:
   - `--use-fake-device-for-media-stream`
   - `--use-file-for-fake-audio-capture=<repo-root>\tests\fixtures\e2e_multispeaker.wav`
   (If the fixture isn't present yet, omit this flag — live-capture
   scenario 6 will be `SKIPPED(no fixture)`.)

6. Navigate to `http://localhost:9782/` and confirm the page loads (title
   or root element present) before starting Scenario 1.

## Teardown

Run this after all scenarios, even if some failed:

```powershell
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $env:WHISPERDECK_DATA_DIR -ErrorAction SilentlyContinue
```

Close the Playwright browser session.

## Final report

After teardown, print the full list of `[PASS|FAIL|SKIPPED]` lines from
every scenario as a single summary table.
