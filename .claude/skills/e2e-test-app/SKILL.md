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

## Scenario 1: Auth (register / login / logout / session persistence)

1. Navigate to `http://localhost:9782/`. If a login/register form is shown,
   fill username `e2e_test_user`, password `e2e_test_pass_123`, submit
   register.
   - Check: `GET http://localhost:9782/api/me` (via browser fetch or
     Playwright network inspection) returns 200 with
     `"username": "e2e_test_user"`.
2. Reload the page.
   - Check: still authenticated — no login form shown, `/api/me` still
     200. This is the session-persistence check.
3. Trigger logout (find and click the logout control in the UI).
   - Check: `/api/me` now returns 401, login form is shown again.
4. Log back in with the same credentials for the rest of the run.
   - Check: `/api/me` 200 again with the same username.

Report: `[PASS|FAIL] Scenario 1: Auth`

## Scenario 2: Settings (view / update / persists)

1. Open the settings panel in the UI.
   - Check: current settings load without error (values populate, no
     error toast).
2. Change one setting (e.g. a numeric field like max concurrent chunks) to
   a new value and save.
   - Check: a success indicator appears (toast, or field reflects saved
     state).
3. Reload the page and reopen settings.
   - Check: the changed value is still the new value, not reverted to
     default.

Report: `[PASS|FAIL] Scenario 2: Settings`

## Scenario 3: Hotwords (add / list / delete / dedup)

1. Open the hotwords panel.
2. Add a hotword, e.g. `Kubernetes`.
   - Check: it now appears in the hotwords list.
3. Add the same word again in a different case, e.g. `kubernetes`.
   - Check: the list still shows only one entry for it (case-insensitive
     dedup) — confirms the fix at `services/hotwords.py`'s
     `add_hotword()` still holds.
4. Delete the hotword.
   - Check: it no longer appears in the list.

Report: `[PASS|FAIL] Scenario 3: Hotwords`

## Scenario 4: Providers panel (switch / save config / models load)

1. Open the providers/services panel.
   - Check: Moonshine appears in the provider list.
2. Configure the local LLM provider: set provider `local`, `api_url` to
   `http://localhost:13305/v1`, model `gpt-oss-20b-mxfp4-GGUF`. Save.
   - Check: save succeeds (no error toast); reload the panel and confirm
     the saved `api_url` and model persisted.
3. Select Moonshine as the active transcription provider (default anyway,
   but explicitly confirm) and check its model list loads without error.

Report: `[PASS|FAIL] Scenario 4: Providers`

If `http://localhost:13305/v1/models` was unreachable in Setup step 4,
still attempt step 2 (saving config doesn't require the server to be up)
but note `SKIPPED(Lemonade unreachable, config saved but unverified live)`
instead of a hard PASS on the "loads without error" sub-check.

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
