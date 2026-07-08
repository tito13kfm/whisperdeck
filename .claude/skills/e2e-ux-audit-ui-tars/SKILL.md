---
name: e2e-ux-audit-ui-tars
description: Browser-driven exploratory UX audit of WhisperDeck via the UI-TARS browser MCP (not Playwright) against an isolated local server instance. Drives 6 realistic user journeys end-to-end, judging usability (not just PASS/FAIL), and produces an HTML findings report. Use when asked to audit WhisperDeck's UX with the UI-TARS browser MCP, or when the Playwright MCP is unavailable.
---

# WhisperDeck UX Audit (UI-TARS browser MCP)

Drives 6 realistic user journeys through a real browser against a
throwaway server instance, judging whether the app is usable — not just
whether the backend did what was asked. This complements
`.claude/skills/e2e-regression-http/SKILL.md` (scripted PASS/FAIL backend
regression, HTTP-only) and `.claude/skills/e2e-ux-audit/SKILL.md`
(Playwright MCP version); this skill is specifically for the UI-TARS
browser MCP available in this environment.

Run **inline in this session** — one continuous browser session
across all 6 journeys, not dispatched to subagents. State
(login session, uploaded transcripts, enrolled voices) carries forward
between journeys.

## Why a separate skill?

The original `e2e-ux-audit` skill was written for Claude Code CLI's
Playwright MCP, which supports custom Chromium launch flags such as
`--use-fake-device-for-media-stream`. The UI-TARS browser MCP used here
does **not** expose browser launch flags, so Journey 2 (live capture)
requires a runtime workaround: we inject a mock `MediaStream` via
`browser_evaluate` that returns a silent audio stream, bypassing the
browser-native mic permission dialog that UI-TARS cannot interact with.
This skill captures the real-world steps and workarounds discovered
during live 2026-07-07 runs.

## Prerequisites

Before running, the local machine must have:
- **PowerShell** (Windows) — for server lifecycle, API calls, report generation
- **curl.exe** — for API calls from PowerShell
- **Python 3** — for JSON parsing helpers
- **Lemonade server** running on `http://localhost:13305/v1` with Whisper models
  (Whisper-Tiny, Whisper-Large-v3, Whisper-Large-v3-Turbo) and an LLM
  model (e.g. `gpt-oss-20b-mxfp4-GGUF`)
- Test fixture: `tests/fixtures/e2e_multispeaker.mp3` (5-minute multispeaker recording)

**Known gaps found during 2026-07-07 live run:**
- `builtin` (faster-whisper), `moonshine` providers are not installed — use `local` (Lemonade) for both transcription and LLM
- Diarization backend (`pyannote`) — installed and working as of 2026-07-07. Install with: `pip install -r requirements-diarization.txt`. Requires `HUGGINGFACE_TOKEN` env var for gated pyannote/speaker-diarization-3.1 model. Verified: 6 speakers detected on e2e_multispeaker.mp3.
- **Use non-reasoning LLM models** for correction/summary. Models like `Bonsai-8B-gguf` output directly in `content` field and work correctly. Reasoning/MTP models (e.g. `Qwen3.5-4B-MTP-GGUF`) put their output in `reasoning_content`, leaving `content` empty when token budget is exhausted. A `reasoning_content` fallback was added to `services/correction.py` and `services/transcription.py` to handle this, but non-reasoning models are more reliable.
- `Bonsai-8B-gguf` (1.16 GB) works for correction (~262 t/s) but may produce empty summary fields for long transcripts due to small context window. Use `Qwen3.5-4B-MTP-GGUF` (3.66 GB) with the reasoning_content fallback for longer content.
- Mic permission dialog cannot be automated; Journey 2 injects a mock `MediaStream` via `browser_evaluate` before clicking REC, which bypasses the dialog entirely. A 5-second timeout catches cases where the mock didn't take effect and marks `SKIPPED` instead of locking up.
- The `summarize` API endpoint uses `Form(...)` not JSON body — use `-F` with curl
- The UI-TARS browser window opens very small; permission dialogs may be invisible to the user.
- The context/document endpoint (`/api/transcripts/{id}/context`) expects JSON body, not Form data. Use `Content-Type: application/json` with `curl -H`.
- LLM models available on the test Lemonade server: Whisper-Tiny, Whisper-Large-v3, Whisper-Large-v3-Turbo (transcription), and Bonsai-8B-gguf, Qwen3.5-4B-MTP-GGUF, gpt-oss-20b-mxfp4-GGUF (LLM). Bonsai-8B is the fastest LLM for correction.

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

If `$ready` is `false` after 30s: report `FAIL: server did not become healthy` and stop.

4. Register a test user and login via curl (to get session cookies):

```powershell
curl.exe -s -X POST "http://localhost:9782/api/register" -H "Content-Type: application/json" -d '{"username":"uxaudit_user","password":"uxaudit_pass_123"}' -c scripts/cookies.txt
curl.exe -s -X POST "http://localhost:9782/api/login" -H "Content-Type: application/json" -d '{"username":"uxaudit_user","password":"uxaudit_pass_123"}' -c scripts/cookies.txt
```

5. Configure the local provider to point at Lemonade (transcription + LLM):

```powershell
curl.exe -s -X PUT "http://localhost:9782/api/providers/local" -H "Content-Type: application/json" -d '{"api_url":"http://localhost:13305/v1","api_key":"not-needed","default_model":"Whisper-Tiny"}' -b scripts/cookies.txt
```

6. Set the user's correction/summary settings to use `local` provider:

```powershell
curl.exe -s -X PUT "http://localhost:9782/api/settings" -H "Content-Type: application/json" -d '{"correction_provider":"local","correction_model":"gpt-oss-20b-mxfp4-GGUF","summary_provider":"local","summary_model":"gpt-oss-20b-mxfp4-GGUF"}' -b scripts/cookies.txt
```

7. Check what's available:

```powershell
$health = Invoke-RestMethod -Uri "http://localhost:9782/api/health" -TimeoutSec 2
$lemonade_available = $false
try {
  Invoke-RestMethod -Uri "http://localhost:13305/v1/models" -TimeoutSec 2 | Out-Null
  $lemonade_available = $true
} catch {}
```

8. Launch the browser via the UI-TARS browser MCP tool, navigating to `http://localhost:9782/`.

9. Set the transcription provider to `local` by running JS in the browser:

```
S.providerIdx = 6;
S.modelIdx = 0;
syncTranscribe();
```

## Journey execution

For each journey, execute the steps below using the UI-TARS browser MCP tools
(`browser_navigate`, `browser_click`, `browser_form_input_fill`,
`browser_get_clickable_elements`, `browser_evaluate`, `browser_screenshot`).
Backend API calls use curl with `-b scripts/cookies.txt`.

Log a finding the moment friction is noticed, using this format:

```
- Journey N, step: <what was being done>
  Type: dead-control | mislabeled | too-many-steps | stale-ui | missing-feedback | unreachable-feature | other
  Severity: blocker | major | minor
  Note: <one-line description>
  Screenshot: <path or "none">
```

Save screenshots only for `major`/`blocker` or inherently visual findings
to `docs/superpowers/e2e-findings/<journey-slug>-<n>.png`.

---

### Journey 1: First meeting, cold start

**Goal:** Register, upload, transcribe, view result.

1. **Login via browser:** On the login form, fill username `uxaudit_user`, password `uxaudit_pass_123`, click "Power on".
2. **Upload a recording via API** (browser file input is not easily automatable with UI-TARS):
   ```
   curl.exe -s -X POST "http://localhost:9782/api/transcribe" -H "Accept: application/json" -b scripts/cookies.txt -F "file=@tests/fixtures/e2e_multispeaker.mp3" -F "diarize=true" -F "provider=local" -F "model=Whisper-Tiny"
   ```
   Record the returned `id` as `$J1_TRANSCRIPT_ID`.
3. **Set provider in browser:** Navigate to Transcribe page, run:
   ```
   S.providerIdx = 6; S.modelIdx = 0; syncTranscribe();
   ```
   Verify the provider label shows "Local / Custom · local · ready" or similar.
4. **View the transcript:** Navigate to Tape library, click the most recent transcript row to expand it, then click "Open transcript".
5. **Observe:** Transcript segments render with speaker labels, timestamps, play buttons, and flag buttons. Corrected tab should show LLM-polished text. Copy and Download .txt buttons are available.
6. **Report:** `[PASS|FAIL] Journey 1: Cold start`

---

### Journey 2: Live capture end-to-end

**Goal:** Record microphone, stop, confirm job.

**Note:** The UI-TARS browser cannot interact with OS/browser permission dialogs.
To work around this, inject a mock `MediaStream` (silent audio) via
`browser_evaluate` **before** clicking REC. This bypasses the permission
dialog entirely. A 5-second timeout after clicking "Start recording" will
fall back to `SKIPPED(mock failed)` if capturing didn't start.

1. **Navigate to Transcribe** page (click "Transcribe" nav button).
2. **Take a screenshot** of the current page so the user can see the window size.
3. **Inject mock MediaStream** (silent audio oscillator) to bypass mic permission:
   ```js
   if (typeof window._mockStream === 'undefined' || !window._mockStream.active) {
     try {
       const mockCtx = new (window.AudioContext || window.webkitAudioContext)();
       const mockDest = mockCtx.createMediaStreamDestination();
       const mockOsc = mockCtx.createOscillator();
       mockOsc.frequency.value = 0; // silence
       mockOsc.connect(mockDest);
       mockOsc.start();
       window._mockStream = mockDest.stream;
       window._mockCtx = mockCtx;
     } catch(e) { /* AudioContext not available */ }
   }
   navigator.mediaDevices.getUserMedia = () => window._mockStream && window._mockStream.active
     ? Promise.resolve(window._mockStream)
     : Promise.reject(new DOMException('Mock unavailable', 'NotAllowedError'));
   navigator.mediaDevices.getDisplayMedia = () => Promise.reject(new DOMException('No display', 'NotAllowedError'));
   ```
4. **Click Rec button** (the red circle button, id `key-rec`).
5. **Confirm modal:** A "Start a live capture?" modal appears. Click "● Start recording".
6. **Wait up to 5 seconds** for `S.capturing` to become `true`. Poll every 500ms:
   ```js
   await new Promise(r => { let i=0; const t=setInterval(()=>{ if(S.capturing || i++>10){ clearInterval(t); r(); } },500); });
   ```
   If `S.capturing` is still `false` after 5s, mark `SKIPPED(mock failed)`.
7. **Observe recording state:** The UI should show "● REC — mic (L) + system (R) — press ● to stop". The "Input scope" indicator should show "LIVE".
8. **Wait ~3 seconds** for recording to accumulate (silent audio frames are still recorded).
9. **Stop recording:** Click the Rec button again (id `key-rec`). The state transition (`onstop`) loads the silent WebM into Deck A.
10. **Verify capture:** Deck A should show the recording filename (e.g. `live_capture_2027.webm`) with file size.
11. **Set provider:** Navigate to Tape library, then back to Transcribe. Run:
    ```
    S.providerIdx = 6; S.modelIdx = 0; syncTranscribe();
    ```
    Then click the Play button to start transcription of the captured file.
12. **Poll** `GET http://localhost:9782/api/jobs` until the transcription completes or fails.
13. **Report:** `[PASS|FAIL|SKIPPED(reason)] Journey 2: Live capture`

---

### Journey 3: Voice roster built across meetings

**Note:** Requires diarization backend (`pyannote`). If the health check shows `diarization_backend: false`, mark `SKIPPED(backend unavailable)`.

1. **Upload multispeaker fixture** with diarization enabled:
   ```
   curl.exe -s -X POST "http://localhost:9782/api/transcribe" -H "Accept: application/json" -b scripts/cookies.txt -F "file=@tests/fixtures/e2e_multispeaker.mp3" -F "diarize=true" -F "provider=local" -F "model=Whisper-Tiny"
   ```
2. Record the transcript ID as `$J3_TRANSCRIPT_ID`.
3. Open the transcript detail view in the browser.
4. Try renaming a speaker to `Alice` via the speaker label.
5. Flag a segment as a voice seed (click the ◈ button on a segment).
6. Click "Enroll marked clips" to create a voice profile.
7. Check the Voice roster (nav button) — verify the profile appears.
8. Upload the same fixture again as `$J3_TRANSCRIPT_ID_2`.
9. Check for voice match prompts in the transcript detail view.
10. **Report:** `[PASS|FAIL|SKIPPED(reason)] Journey 3: Voice roster`

---

### Journey 4: Wrap-up-the-meeting flow

**Goal:** Correct, summarize, context, and export a transcript.

**Note:** Requires Lemonade LLM to be running. If `$lemonade_available` is `false`, mark `SKIPPED(Lemonade unreachable)`.

1. **Re-run correction** on `$J1_TRANSCRIPT_ID`:
   ```
   curl.exe -s -X POST "http://localhost:9782/api/transcripts/$J1_TRANSCRIPT_ID/correct" -b scripts/cookies.txt -F "provider=local" -F "model=gpt-oss-20b-mxfp4-GGUF"
   ```
2. **Add context document** — send a short sentence naming a term/person:
   ```
   curl.exe -s -X POST "http://localhost:9782/api/transcripts/$J1_TRANSCRIPT_ID/context" -b scripts/cookies.txt -H "Content-Type: application/json" -d '{"document":"The product is called ERPNext and the client is ACME Corp."}'
   ```
3. **Re-run correction** again (same as step 1).
4. **Trigger summary:**
   ```
   curl.exe -s -X POST "http://localhost:9782/api/transcripts/$J1_TRANSCRIPT_ID/summarize" -b scripts/cookies.txt -F "provider=local" -F "model=gpt-oss-20b-mxfp4-GGUF"
   ```
5. **Poll** until the summary job completes (check `GET /api/jobs`).
6. **View results in browser:** Open the transcript, check the corrected tab and summary tab.
7. **Test export:** Click Copy and Download .txt on the transcript tab — verify content is non-empty.
8. **Check summary structure** via API:
   ```
   curl.exe -s "http://localhost:9782/api/transcripts/$J1_TRANSCRIPT_ID/runs/summary" -b scripts/cookies.txt
   ```
   Expected: `short_summary`, `key_points`, `action_items`, `decisions` fields.
9. **Report:** `[PASS|FAIL|SKIPPED(reason)] Journey 4: Wrap-up flow`

---

### Journey 5: Managing a growing transcript backlog

**Goal:** Search, sort, cancel a job, delete a transcript.

1. **Navigate to Tape library** — verify search box, sort dropdown, transcript rows.
2. **Test search:** Type a partial title in the search box, verify the list narrows.
3. **Test sort:** Change the sort dropdown, verify rows reorder.
4. **Open Queue** — verify jobs are listed with clear statuses and transcript names.
5. **Cancel a running job:** If any job is running, find and click its cancel control. If no jobs are running, start a correction or summary, then immediately cancel it.
6. **Delete a throwaway transcript:** Create a throwaway upload, then delete it via the Delete button in the Tape library. Verify a confirmation step exists before deletion.
7. **Report:** `[PASS|FAIL] Journey 5: Backlog management`

---

### Journey 6: Misconfiguration recovery

**Goal:** Break a provider config, verify error handling, restore.

1. **Save current local provider config:**
   ```
   curl.exe -s "http://localhost:9782/api/providers/local" -b scripts/cookies.txt
   ```
2. **Break the provider:**
   ```
   curl.exe -s -X PUT "http://localhost:9782/api/providers/local" -H "Content-Type: application/json" -d '{"api_url":"http://localhost:1/v1"}' -b scripts/cookies.txt
   ```
3. **Attempt a correction** (which will fail):
   ```
   curl.exe -s -X POST "http://localhost:9782/api/transcripts/5/correct" -b scripts/cookies.txt -F "provider=local" -F "model=gpt-oss-20b-mxfp4-GGUF"
   ```
4. **Poll jobs** and check if the error is surfaced clearly.
5. **Restore the provider:**
   ```
   curl.exe -s -X PUT "http://localhost:9782/api/providers/local" -H "Content-Type: application/json" -d '{"api_url":"http://localhost:13305/v1","default_model":"Whisper-Tiny"}' -b scripts/cookies.txt
   ```
6. **Verify recovery:** Run a correction or summary and confirm it completes.
7. **Report:** `[PASS|FAIL] Journey 6: Misconfiguration recovery`

---

## Teardown

Run this after all journeys, even if some failed:

```powershell
# Kill the server process
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue

# Remove the isolated data directory
Remove-Item -Recurse -Force $env:WHISPERDECK_DATA_DIR -ErrorAction SilentlyContinue
```

Close the browser session.

## Report

After Teardown, generate a static HTML report and open it for review.

1. Collect all journey status lines and findings logged during the run.
2. Write the HTML report to `docs/superpowers/e2e-findings/report-<timestamp>.html`.
3. Open it via `Start-Process (Resolve-Path $reportPath)`.
4. Print the full list of `[PASS|FAIL|SKIPPED]` lines and report path as final output.

**Known issues for 2026-07-07 run:**
- Journey 2 requires mocking the `getUserMedia` API via `browser_evaluate` before clicking REC; the browser-native permission dialog cannot be automated
- Journey 3 requires pyannote diarization backend (not installed)
- Journey 6 error handling: broken provider may still show "completed" if retry mechanism retries after restoration
- `summary_text` field on transcript is null — summary is stored in `runs/summary` result_json
- UI-TARS `browser_screenshot` may time out; use `browser_get_text` as fallback
