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

## Scenario 5: Upload transcribe (file path)

1. Use the file input (`static/rack.js:2804`, wired to `#file-input` and
   triggered by the drop zone / `#key-rec`'s sibling upload control around
   `static/rack.js:830`) to upload `tests/fixtures/e2e_multispeaker.<ext>`
   (fall back to the repo's existing `test.mp4` if the multispeaker fixture
   isn't present yet — note `SKIPPED(reduced fixture)` on any later scenario
   that needs multiple speakers, e.g. Scenarios 10-12).
2. Confirm the provider is Moonshine (from Scenario 4) and submit.
3. Poll `GET /api/transcripts/{id}` every 3s, up to 3 minutes, until
   `status` is `completed`, `failed`, or `partial` (these are the actual
   terminal values the backend uses — see `app.py`'s `Transcript.status`
   writes in `services/transcription.py:101/132` and `services/queue.py:419-423`;
   there is no bare `complete` state).
   - Check: status reaches `completed` within the window. On a real local
     server this was observed to complete in well under a second for a
     3-second `test.mp4` (inline, non-chunked path — Moonshine on CPU is
     far faster than real time for a clip this short).
   - Check: the transcript view renders at least one segment with text —
     **caveat observed in a live run**: the repo's placeholder `test.mp4`
     produced `status: completed` with `segments: []` and `full_text: ""`
     from Moonshine (verified directly against `moonshine_voice.Transcriber`
     on both the 3-second original and a 6.5-minute looped copy — zero
     lines either way), i.e. its audio track has no content Moonshine
     recognizes as speech. This is a fixture problem, not a pipeline bug:
     the same `Transcriber` call against a Windows SAPI text-to-speech WAV
     ("The quick brown fox...") produced 2 correct segments in the same
     run, confirming Moonshine itself works. If the empty-output behavior
     on `test.mp4` still holds, treat a `completed` status
     with empty `segments`/`full_text` as a **PASS for the job-lifecycle
     check** (upload → process → terminal status, with no `error`) but note
     `SKIPPED(fixture has no recognizable speech)` on the "renders a
     segment with text" sub-check specifically. Do not treat empty output
     alone as a pipeline failure — confirm `t.error` is null first.
4. Record this transcript's ID as `$TRANSCRIPT_ID` — later scenarios reuse
   it.

Report: `[PASS|FAIL] Scenario 5: Upload transcribe`

## Scenario 6: Live capture transcribe (fake mic device)

Only runs if Setup launched Chromium with
`--use-file-for-fake-audio-capture`; otherwise `SKIPPED(no fixture/fake
device)`.

1. Trigger the "Live capture" control — `#key-rec` (`static/rack.js:686`
   defines the deck key, `static/rack.js:833` wires its click handler,
   `static/rack.js:975` toggles its title between "Live capture — asks
   before recording" and "Stop recording").
2. Grant the (fake) microphone permission prompt if one appears.
3. Let it run 5-10 seconds, then stop recording.
4. Confirm a new transcription job is submitted and reaches a terminal
   status (`completed`, `failed`, or `partial`) using the same poll pattern
   as Scenario 5.

Report: `[PASS|FAIL|SKIPPED(reason)] Scenario 6: Live capture`

## Scenario 7: Transcript list / detail / rename / delete

1. Open the transcript list view.
   - Check: `$TRANSCRIPT_ID` from Scenario 5 appears in the list (`GET
     /api/transcripts`; rendered per-row around `static/rack.js:1380`).
2. Open its detail view (`GET /api/transcripts/{id}`; header rendered at
   `static/rack.js:1933` as `<h1 class="t-title">`).
   - Check: segments/text render (or, per Scenario 5's caveat, the empty-
     state renders cleanly with no error if the fixture produced no
     segments).
3. Rename its title. **Verified via direct API call, not the UI**: the
   detail view's title (`static/rack.js:1933`) is plain text with no click
   handler, and `static/rack.js` has no PATCH call anywhere — the only
   in-app rename affordance is the *speaker* rename `window.prompt` at
   `static/rack.js:1724`, which is unrelated. Since `PATCH
   /api/transcripts/{id}` with `{"title": "..."}` exists and works
   (confirmed live: title updated and persisted), exercise it directly via
   HTTP rather than a UI control, and note
   `SKIPPED(no title-rename UI control exists — verified via API only)`
   on the UI-specific portion of this check.
   - Check: the new title shows in both detail view and list view after
     reload (both confirmed live via `GET /api/transcripts/{id}` and `GET
     /api/transcripts`).
4. Create a second throwaway transcript (repeat Scenario 5's upload with
   `test.mp4`) purely to delete it here, so Scenario 5's main transcript
   survives for later scenarios.
   - Check: after deleting the throwaway one (`DELETE
     /api/transcripts/{id}`), it no longer appears in the list;
     `$TRANSCRIPT_ID` still does.

Report: `[PASS|FAIL] Scenario 7: Transcript CRUD`

## Scenario 8: Cancel + resume a running job; retry failed chunks

Note: a 3-second `test.mp4` finishes inline before there's anything to
cancel (`#tx-cancel`, `static/rack.js:743/1003-1005`, is disabled unless
`S.runningId` is set — "Quick local jobs can't be cancelled — this
finishes on its own"). To get a real cancellable job, use a longer local
recording (e.g. several minutes) so it takes the chunked path
(`local_chunked` in `app.py`'s `_run_transcription_pipeline`, chunk size
driven by `LOCAL_CHUNK_SECONDS = 300`). In a live run this was done by
concatenating `test.mp4` into a ~6.5-minute file, producing a 2-chunk job.

1. Start a new upload transcribe job long enough to be chunked (third audio
   submission).
2. Immediately trigger cancel on it — list-view button
   `data-act="cancel"` (`static/rack.js:1370/1410`) or detail-view
   `data-dact`/`t-cancel` equivalents (`static/rack.js:1451/1516`), which
   both call `POST /api/transcripts/{id}/cancel` — before it reaches a
   terminal status. This is timing-sensitive: cancel must land while the
   job is still `processing` with pending chunks, or it returns `400
   Cannot cancel a transcript with status 'completed'` (observed live on a
   first attempt where cancel was issued a few seconds late, after both
   chunks had already finished — Moonshine on this fixture processes each
   chunk in well under a second). Issue cancel immediately after the
   upload response returns.
   - Check: job status becomes `cancelled` (confirmed live:
     `{"ok":true,"cancelled":2}`, transcript status `cancelled`).
3. Trigger resume on the same job — `data-act="resume"`
   (`static/rack.js:1371/1411`) / `t-resume` (`static/rack.js:1452/1517`),
   calling `POST /api/transcripts/{id}/resume`.
   - Check: it proceeds again and reaches `completed` (confirmed live:
     resumed 2 chunks, transcript reached `completed` in about 15 seconds)
     or `failed`/`partial`, in which case try "retry failed chunks" next.
4. If any chunk shows `failed` status at any point, trigger "retry failed
   chunks" — `data-act="retry"` (`static/rack.js:1412`) / `t-retry`
   (`static/rack.js:1518`), calling `POST
   /api/transcripts/{id}/retry-failed-chunks` — and confirm those chunks
   re-run. No chunk failed naturally in the live verification run (the
   endpoint was exercised once anyway on a fully-succeeded job and
   correctly returned `{"ok":true,"retried":0}`); the reset-to-pending
   logic itself is implemented at `services/queue.py:260-277`.

Report: `[PASS|FAIL] Scenario 8: Cancel/resume/retry`

## Scenario 9: Retranscribe with a different provider

1. On `$TRANSCRIPT_ID`, trigger "Re-transcribe" — detail-view button at
   `static/rack.js:1936`, opening the picker built in
   `toggleRetranscribePicker()` (`static/rack.js:2144-2186`) — and select a
   different available local provider/model if one exists (e.g. re-run
   Moonshine at a different model size, since Groq/OpenAI are out of scope
   per the local-only constraint). The picker itself says "Creates a new
   transcript — this one stays untouched" (`static/rack.js:2159`).
2. Poll until the new transcript's status is `completed` (or `failed`/
   `partial`).
   - Check: **retranscribe creates a brand-new transcript row, not a second
     job under the original ID** — confirmed live: `$TRANSCRIPT_ID` (e.g.
     id 2, model `base`) was untouched, and `POST
     /api/transcripts/{id}/retranscribe` returned a new transcript (id 5,
     same title, model `tiny`). The correct check is: `GET
     /api/transcripts` now lists both the original `$TRANSCRIPT_ID` and the
     new transcript, sharing the same title/source audio but different
     `model`/`id` — not "job history shows two completed runs for this
     transcript" (there is no such combined job history; each is its own
     independent `Transcript` row, by design, per the docstring on
     `/api/transcripts/{transcript_id}/retranscribe` in `app.py`).

Report: `[PASS|FAIL] Scenario 9: Retranscribe`

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
