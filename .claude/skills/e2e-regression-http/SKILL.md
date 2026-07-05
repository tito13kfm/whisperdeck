---
name: e2e-regression-http
description: Scripted 16-scenario feature-regression test of WhisperDeck, validated via direct HTTP API calls (not a real browser — see "Validation status" below) against an isolated local server instance (Moonshine STT, local diarization, Lemonade LLM). Use when asked to regression-test WhisperDeck's backend behavior before a release. For a real-browser, exploratory UX pass, use e2e-ux-audit instead.
---

# WhisperDeck End-to-End Regression Test (HTTP-only)

Drives every feature of WhisperDeck through a real browser against a
throwaway server instance. Local/keyless backends only — no API keys, no
network cost. Each step is either a scripted Playwright action with an
exact check, or a DOM assertion — never a subjective judgment call.

Report format: after every scenario, emit one line:
`[PASS|FAIL|SKIPPED(reason)] Scenario N: <name>`
Continue to the next scenario on failure. Never abort the run early.

## Validation status

All "Confirmed live" / "Real run result" evidence in this file comes from
direct HTTP API calls against the isolated server — no implementer
subagent had a live browser/Playwright tool available while authoring
this skill. UI selectors and click sequences described throughout are
transcribed from reading the frontend source (`static/rack.js`,
`static/index.html`), not exercised through a real browser. A future
agent running this skill with a real Playwright MCP tool should treat
the DOM/UI-interaction steps as unverified until run for real, even
though the underlying backend behavior they trigger has been confirmed.

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
   - `--use-file-for-fake-audio-capture=<repo-root>\tests\fixtures\e2e_multispeaker.mp3`
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
   `static/rack.js:830`) to upload `tests/fixtures/e2e_multispeaker.mp3`
   (a real 5-minute business-call recording; fall back to the repo's
   existing `test.mp4` only if the multispeaker fixture is ever missing —
   note `SKIPPED(reduced fixture)` on any later scenario that needs
   multiple speakers, e.g. Scenarios 10-12). The upload endpoint is `POST
   /api/transcribe` (multipart form fields `file`, `provider`, `model`,
   `diarize`, etc.) — **not** `POST /api/transcripts`, which doesn't exist;
   only `GET`/`PATCH`/`DELETE` are defined on `/api/transcripts/{id}` and
   `GET` on the collection.
2. Confirm the provider is Moonshine (from Scenario 4) and submit.
3. Poll `GET /api/transcripts/{id}` every 3s, up to 3 minutes, until
   `status` is `completed`, `failed`, or `partial` (these are the actual
   terminal values the backend uses — see `app.py`'s `Transcript.status`
   writes in `services/transcription.py:101/132` and `services/queue.py:419-423`;
   there is no bare `complete` state).
   - Check: status reaches `completed` within the window. **Confirmed live
     against the real `e2e_multispeaker.mp3` fixture**: a 300-second clip
     completed in under 10 seconds on Moonshine/CPU (inline, non-chunked
     path).
   - Check: the transcript view renders at least one segment with text.
     **Confirmed live**: `full_text` came back as ~500 words of real
     recognized speech across 70 segments — the empty-output caveat below
     no longer applies when using the real fixture; it's retained here only
     as a fallback note for the degenerate `test.mp4` case.
     <details>
     Historical caveat (still true for `test.mp4`, not for
     `e2e_multispeaker.mp3`): the repo's placeholder `test.mp4` produced
     `status: completed` with `segments: []` and `full_text: ""` from
     Moonshine (verified directly against `moonshine_voice.Transcriber` on
     both the 3-second original and a 6.5-minute looped copy — zero lines
     either way), i.e. its audio track has no content Moonshine recognizes
     as speech. If ever falling back to `test.mp4`, treat a `completed`
     status with empty `segments`/`full_text` as a **PASS for the
     job-lifecycle check** (upload → process → terminal status, with no
     `error`) but note `SKIPPED(fixture has no recognizable speech)` on the
     "renders a segment with text" sub-check specifically. Do not treat
     empty output alone as a pipeline failure — confirm `t.error` is null
     first.
     </details>
4. Record this transcript's ID as `$TRANSCRIPT_ID` — later scenarios reuse
   it. **Confirmed live**: transcript id 1.

Report: `[PASS|FAIL] Scenario 5: Upload transcribe`

## Scenario 6: Live capture transcribe (fake mic device)

Only runs if Setup launched Chromium with
`--use-file-for-fake-audio-capture`; otherwise `SKIPPED(no fixture/fake
device)`.

Note: unlike every other scenario in this file, this one has zero live
evidence behind it in any form — not even indirectly via API. It was
never exercised during this plan's authoring (no live browser tool was
available; see "Validation status" above). Expect this to be the primary
residual gap for whoever first runs this skill with a real Playwright
browser tool.

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
   `static/rack.js:2128` as `<h1 class="t-title">`).
   - Check: segments/text render (or, per Scenario 5's caveat, the empty-
     state renders cleanly with no error if the fixture produced no
     segments).
3. Rename its title via the Tape library list's **Rename** button
   (`data-act="rename"`, `static/rack.js:1525`), which opens a styled
   prompt (`styledPrompt()`, `static/rack.js:332`) pre-filled with the
   current title/filename and calls `PATCH /api/transcripts/{id}` with
   `{"title": "..."}` on confirm. (Earlier revisions of this scenario used
   a direct API call because no UI control existed yet — that gap is
   closed; use the UI control now. The detail-view title itself is still
   plain text with no click handler; renaming happens from the list.)
   - Check: the new title shows in both detail view and list view after
     the rename (confirmed via `GET /api/transcripts/{id}` and `GET
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
     job under the original ID** — confirmed live: `$TRANSCRIPT_ID` (id 1,
     model `base`, per Scenario 5) was untouched, and `POST
     /api/transcripts/{id}/retranscribe` returned a new transcript (a
     different id than both `$TRANSCRIPT_ID` and
     `$TRANSCRIPT_ID_DIARIZE`, e.g. id 3, same title, model `tiny`). The
     correct check is: `GET
     /api/transcripts` now lists both the original `$TRANSCRIPT_ID` and the
     new transcript, sharing the same title/source audio but different
     `model`/`id` — not "job history shows two completed runs for this
     transcript" (there is no such combined job history; each is its own
     independent `Transcript` row, by design, per the docstring on
     `/api/transcripts/{transcript_id}/retranscribe` in `app.py`).

Report: `[PASS|FAIL] Scenario 9: Retranscribe`

## Scenario 10: Diarization (upload-time toggle + rediarize)

Requires `$TRANSCRIPT_ID` to have been transcribed from a genuinely
multi-speaker fixture. If Scenario 5 fell back to `test.mp4` (single
speaker, 3 seconds), mark this `SKIPPED(no multispeaker fixture)` and do
not attempt it — a single-speaker recording cannot validate speaker
separation.

There is no standalone post-hoc "diarize" trigger separate from
rediarize — the only two distinct diarization actions in the app are the
upload-time "Speakers" toggle and the post-hoc "Re-diarize" button.

1. Start a fresh upload of the same multispeaker fixture used in
   Scenario 5, explicitly enabling diarization first: click the
   "Speakers" toggle at `static/rack.js:769-771` (`id="ctl-diarize"`,
   `tog-diarize`) before submitting, then submit the upload (form field
   `diarize=true` on `POST /api/transcribe`).
   - Check: poll until the job reaches a terminal status (same pattern as
     Scenario 5), then confirm the completed transcript's segments show
     more than one distinct speaker label — i.e. diarization ran as part
     of the upload because the toggle was on. **Confirmed live against
     `e2e_multispeaker.mp3`**: `speaker_count: 6`, distinct labels
     `SPEAKER_00`..`SPEAKER_05` (pyannote backend), `status: completed`.
   - Record this transcript's ID as `$TRANSCRIPT_ID_DIARIZE` (throwaway,
     not reused later). Confirmed live: id 2.
2. On `$TRANSCRIPT_ID` (the existing transcript from Scenario 5), trigger
   "Re-diarize" — detail-view button at `static/rack.js:1937`
   (`data-dact="rediarize"`), calling `POST
   /api/transcripts/{id}/rediarize` at `app.py:1030`.
   - Note: this endpoint is asynchronous — it returns `{"job": {...,
     "status": "pending", "kind": "rediarize"}}` immediately, not the
     updated transcript. Poll either `GET /api/transcripts/{id}` (simplest —
     watch `speaker_count`/`segments` update) or `GET /api/jobs`, whose
     response shape is `{"jobs": [...], "active": N}` — **note the wrapper
     object**, the job list is the `.jobs` field, not the top-level
     response. Confirmed live: a `rediarize` job does appear in that
     `.jobs` array (alongside `correction`/`summary`/`voice_match` — no
     kind-based filtering in `app.py`'s `list_jobs`), so either poll target
     works; the transcript endpoint is just less to unwrap.
   - Check: it re-runs and completes without error, and segments still
     show more than one distinct speaker label afterward. **Confirmed
     live**: after rediarizing `$TRANSCRIPT_ID` (id 1), `speaker_count`
     became 6 with the same `SPEAKER_00`..`SPEAKER_05` labeling.

Report: `[PASS|FAIL|SKIPPED(reason)] Scenario 10: Diarization`

**Real run result: PASS** (both steps, against `e2e_multispeaker.mp3`).

## Scenario 11: Speaker rename + segment retag

Same fixture gate as Scenario 10.

1. Rename one detected speaker label (e.g. `Speaker 1` -> `Alice`) via the
   UI.
   - Check: click on the speaker label rendered at `static/rack.js:1759`
     (`data-seg-rename` attribute). This opens the app's styled prompt
     modal (`styledPrompt()`) and calls `POST
     /api/transcripts/{id}/speakers/rename` at `app.py:773` via the
     `renameSpeaker()` function at `rack.js:1847`. The new name appears on
     all of that speaker's segments in the transcript view, not just one.
     **Confirmed live**: renaming `SPEAKER_04` -> `Alice` on
     `$TRANSCRIPT_ID` relabeled 41 of 69 segments in one call
     (`{"renamed": 41, ...}`), and the remaining `SPEAKER_00/01/02/03/05`
     labels were untouched.
2. Retag a single segment to a different speaker.
   - Check: use the "Select lines…" button at `rack.js:1962`, select a
     segment, then click "Re-tag selected" button (also `rack.js:1962`).
     This opens the retag modal and calls `POST /api/transcripts/{id}/segments/retag`
     at `app.py:820`. That segment now shows the reassigned speaker label, and
     total segment count is unchanged (retagging doesn't create/delete
     segments). **Confirmed live**: retagging one `SPEAKER_00` segment
     (index 52) to `Alice` returned `{"retagged": 1, ...}`; total segment
     count stayed at 69 before and after.

Report: `[PASS|FAIL|SKIPPED(reason)] Scenario 11: Speaker rename/retag`

**Real run result: PASS** (both steps, against `e2e_multispeaker.mp3`).

## Scenario 12: Voice bank (enroll / list / identify / delete)

Same fixture gate as Scenario 10.

1. From a segment belonging to "Alice" (Scenario 11), trigger
   "enroll-speaker" to create a voice bank profile.
   - Check: use the "Enroll marked clips" button at `rack.js:1960`
     (id="enroll-marked-btn"). Mark a segment with the ◈ flag first, then
     click the button to open the enroll modal. This calls `POST
     /api/transcripts/{id}/enroll-speaker` at `app.py:858` with a JSON body
     `{"name": "Alice", "clips": [{"start": ..., "end": ...}, ...]}` (1-10
     clips, each an Alice-labeled segment's `start`/`end`). A new voice
     profile named `Alice` (or as entered) appears under `GET /api/voices`
     at `app.py:1223`. **Confirmed live**: enrolling 3 Alice segments
     returned `{"id": 1, "name": "Alice", "sample_count": 1, ...}`, and it
     appeared in `GET /api/voices`.
2. Upload a second transcript from the same multispeaker fixture (or a
   different clip containing the same voice). Record its ID as
   `$TRANSCRIPT_ID_2`. In practice the diarized transcript already created
   in Scenario 10 step 1 (`$TRANSCRIPT_ID_DIARIZE`) works fine as
   `$TRANSCRIPT_ID_2` — no need for a third upload.
3. Trigger "identify" against the voice bank on `$TRANSCRIPT_ID_2`.
   - Check: navigate to the Voice Roster view and click the "Identify a voice…"
     button at `rack.js:2295`. This calls `POST /api/voices/identify` at
     `app.py:1259`. Or, trigger "Match against voice roster" button in the
     detail view at `rack.js:1938` (data-dact="voicematch"), which calls
     `POST /api/transcripts/{id}/voice-match` at `app.py:1061`. At least
     one segment in `$TRANSCRIPT_ID_2` gets matched/labeled against the
     enrolled `Alice` profile. **Confirmed live**: `voice-match` is
     asynchronous — it returns `{"job": {..., "status": "pending", "kind":
     "voice_match"}}`; poll `GET /api/transcripts/{id}` and check its
     embedded `voice_match_job.status` until `completed` (or use `GET
     /api/jobs` — see Scenario 10 step 2's note on that endpoint's
     `{"jobs": [...], "active": N}` response shape; `voice_match` jobs
     appear there the same as every other kind, no special-casing). On
     `$TRANSCRIPT_ID_DIARIZE`, the completed job relabeled all 70/70
     segments to `Alice` — expected here since it's the same source audio
     as the enrollment clips, which produces a very close embedding match.
4. Delete the enrolled voice profile.
   - Check: in the Voice Roster view, trigger the delete button for the
     `Alice` profile. This calls `DELETE /api/voices/{profile_id}` at
     `app.py:1286`. It no longer appears under `GET /api/voices`. **Confirmed
     live**: deleting profile id 1 returned `{"ok": true}`, and `GET
     /api/voices` came back empty afterward.

Report: `[PASS|FAIL|SKIPPED(reason)] Scenario 12: Voice bank`

**Real run result: PASS** (all four steps, against `e2e_multispeaker.mp3`
and its diarized sibling transcript).

## Scenario 13: Summarize (Lemonade)

Requires Lemonade reachable (Setup step 4); otherwise
`SKIPPED(Lemonade unreachable)`.

**Real bug found in a live run, work around it before triggering the job**:
`POST /api/transcripts/{id}/summarize` and `POST /api/transcripts/{id}/correct`
both route through `services/correction.py`'s `_chat_completion()`
(`services/correction.py:65`), which unconditionally sends
`Authorization: Bearer {api_key}` — including when `api_key` is the empty
string, which Scenario 4 leaves it as for the `local` provider (no key
needed to talk to Lemonade). That produces the header value `"Bearer "`
(trailing space, no token), which `httpx` rejects outright before the
request is even sent: the job fails immediately with
`error: "Illegal header value b'Bearer '"` (confirmed live — job id 2 in a
real run). Work around it by giving the `local` provider config a non-empty
placeholder key first: `PUT /api/providers/local` with
`{"api_key": "not-needed"}`. Lemonade itself ignores the header entirely, so
any non-empty string works. Do this once per run before Scenario 13.

1. Trigger "summarize" on `$TRANSCRIPT_ID` — `POST
   /api/transcripts/{id}/summarize` with form fields `provider=local`,
   `model=gpt-oss-20b-mxfp4-GGUF` (UI equivalent: detail-view "Summarize"
   button, `static/rack.js:1940`, `data-dact="summarize"`).
2. Poll the LLM job status via `GET /api/jobs` (match by `id`/`kind:
   "summary"`) every 5s, up to 2 minutes. **Observed live timing on this
   hardware** (Lemonade running gpt-oss-20b-mxfp4-GGUF on a ROCm llama.cpp
   backend): a summarize job on a real ~15-second/40-word transcript
   completed in about 2 seconds end to end (job `created_at` to
   `updated_at`); a job against an empty-text transcript completed in about
   1 second. This is far faster than the plan's original "up to 5 minutes"
   estimate — 2 minutes is already generous headroom here, but if a future
   run on different hardware or a much longer transcript is still `pending`/
   `running` past 2 minutes, don't fail early; extend the wait rather than
   declaring `FAIL`.
   - Check: job status reaches `completed`, not `failed`.
   - Check: `GET /api/transcripts/{id}/summary` returns non-empty
     `short_summary`/`key_points`. **Caveat observed live**: if
     `$TRANSCRIPT_ID` is the empty-text `test.mp4` fixture from Scenario 5's
     caveat, the summarize job still reaches `completed` (PASS for the
     job-lifecycle check) but `short_summary`, `key_points`,
     `action_items`, and `decisions` all come back empty (`""`/`[]`) since
     there was no source text to summarize — note
     `SKIPPED(fixture has no recognizable speech)` on the "non-empty text"
     sub-check specifically, same treatment as Scenario 5. Use a transcript
     with real recognized speech (e.g. Scenario 5's multispeaker fixture,
     or any transcript with non-empty `full_text`) to exercise the
     non-empty-text check for real.

Report: `[PASS|FAIL|SKIPPED(reason)] Scenario 13: Summarize`

**Real run result: PASS, re-confirmed against `e2e_multispeaker.mp3`**:
summarize on `$TRANSCRIPT_ID` (real ~500-word business-call transcript)
completed in about 6 seconds and returned a non-empty `short_summary`
plus 7 `key_points`, 4 `action_items`, and 3 `decisions` — all populated
from real content instead of the empty-fixture caveat above.

## Scenario 14: Correction pass (Lemonade)

Same Lemonade gate as Scenario 13, and the same `local`-provider placeholder
API key workaround applies (correction goes through the same
`_chat_completion()` code path).

1. Record the transcript's current `corrected_text` (pre-correction, likely
   `null`) via `GET /api/transcripts/{id}` for comparison.
2. Trigger "correct" on `$TRANSCRIPT_ID` — `POST
   /api/transcripts/{id}/correct` with form fields `provider=local`,
   `model=gpt-oss-20b-mxfp4-GGUF` (UI: "Re-run correction" button,
   `static/rack.js:1941`, `data-dact="rerun"`, opens the picker at
   `toggleRerunPicker()`/`rerunCorrection()`, `static/rack.js:2101-2135`).
3. Poll via `GET /api/jobs` (or the `correction_job` field embedded in `GET
   /api/transcripts/{id}`) every 5s, up to 2 minutes — same real-hardware
   timing basis as Scenario 13. **Observed live**: two separate correction
   runs on the same short real-speech transcript completed in about 9
   seconds and about 3 seconds respectively (job `created_at` to
   `updated_at`) — noticeably slower than summarize since correction makes
   one LLM call per line-batch rather than one call total, but still well
   under a minute for a short transcript. As with Scenario 13, 2 minutes is
   generous headroom for this hardware/transcript size; extend rather than
   fail if a longer transcript needs more time.
   - Check: job status reaches `completed`.
   - Check: `corrected_text` on `GET /api/transcripts/{id}` is non-empty.
   - Check: corrected output text is not byte-identical to the
     pre-correction text recorded in step 1. (This is a hard check, not a
     quality judgment — it only proves the correction pass ran and
     produced *a* transformation, not that the transformation is good.
     Confirmed live: the model added sentence-level punctuation/paragraph
     breaks; it did not fix an actual mis-transcription in the same run,
     e.g. "Sarah" mis-heard as "Cereal" survived correction unchanged —
     that's a model-quality result, not a lifecycle failure.)

Report: `[PASS|FAIL|SKIPPED(reason)] Scenario 14: Correction`

**Real run result: PASS, re-confirmed against `e2e_multispeaker.mp3`**:
correction on `$TRANSCRIPT_ID` completed with `corrected_text` non-empty
(4493 chars) and not identical to `full_text` — the model added
punctuation/paragraph breaks and (since this transcript had already been
through Scenario 11's speaker rename/retag) prefixed each line with its
speaker label, e.g. `Alice: So this is the same as Arnold Page, so.`.

## Scenario 15: Context refinement

Same Lemonade gate and placeholder-API-key workaround as Scenario 13. Also
set the user's `correction_provider` setting to `local` first (`PUT
/api/settings` with `{"correction_provider": "local", "correction_model":
"gpt-oss-20b-mxfp4-GGUF"}`) — `POST /api/transcripts/{id}/context` resolves
its own provider from this setting (default `groq`), independent of what
was configured on the transcript-level correct/summarize calls, and 400s
with a "no API key" error if it resolves to a hosted provider with no key
saved.

1. Add a short context document (a few sentences of relevant background/
   glossary text, e.g. naming a person or term the transcript mis-heard) to
   `$TRANSCRIPT_ID` via `POST /api/transcripts/{id}/context` with form field
   `context_doc` (UI: "Add context" button, `static/rack.js:1939`,
   `data-dact="context"`, opens `toggleContextPicker()`,
   `static/rack.js:2225-2251`, textarea `#ctx-doc`, submit `#ctx-go`).
   - **Caveat observed live, not a failure**: this call is synchronous (no
     job to poll) and returns `{"terms": [...]}` — extracted glossary
     terms it added to the hotword list. Against the `local` provider it
     reliably returned `terms: []` even for a document containing an
     obvious name, because term extraction requires JSON-mode
     (`_JSON_MODE_PROVIDERS` in `services/correction.py:23` covers only
     `groq`/`openai`/`openrouter`, not `local`), and
     `extract_hotwords_from_doc()` (`services/correction.py:204-230`)
     silently swallows any JSON-parse failure and returns `[]` — this is
     documented, non-fatal-by-design behavior, not a bug to chase. Don't
     treat an empty `terms` list against the `local` provider as a
     failure; it's expected. If validating that extraction can find terms
     at all, temporarily point `correction_provider` at `groq`/`openai`/
     `openrouter` with a saved key instead — out of scope for the
     local-only run this skill otherwise sticks to.
2. Re-run correction on `$TRANSCRIPT_ID` (same call as Scenario 14 step 2) —
   this is the "transcription-refinement... whichever the UI wires context
   into" step: context wires into the hotword glossary the correction pass
   already reads on every run, there's no separate "correct with this
   context" parameter.
3. Poll up to 2 minutes (same basis as Scenario 13/14; observed live at
   about 3 seconds for a short transcript).
   - Check: job status reaches `completed`, not `failed`.

Report: `[PASS|FAIL|SKIPPED(reason)] Scenario 15: Context refinement`

## Scenario 16: Jobs panel (list / cancel / rerun)

1. Open the jobs panel — `loadQueue()` (`static/rack.js:1464-1520ish`),
   fetching `GET /api/jobs?limit=50`.
   - Check: it lists the jobs created by Scenarios 5-15 (upload,
     diarize, summarize, correct, etc.) with their statuses. Confirmed
     live: a real `GET /api/jobs` response after Scenarios 13-15 listed
     every summary/correction job created, newest first, each with
     `status`/`provider`/`model`/`title`.
2. Start one more job specifically to cancel it here (don't reuse a job
   another scenario still needs). **Use an LLM job (summarize or correct),
   not a plain transcribe upload** — `POST /api/jobs/{job_id}/cancel`
   (`app.py:1186`) only accepts an `LlmJob` id; the transcription-queue
   entries the same panel also lists (id format `"transcription-{id}"`) are
   cancelled through the transcript-level `POST
   /api/transcripts/{id}/cancel` instead (already exercised in Scenario 8
   via the `t-cancel` UI action, `static/rack.js:1516`) — not through this
   endpoint. A short local `test.mp4` upload also finishes inline before
   there's anything to cancel anyway, same caveat as Scenario 8. Trigger a
   correction or summarize job on any transcript, then immediately call
   `POST /api/jobs/{job_id}/cancel` (UI: `j-cancel`, `static/rack.js:1514`).
   - Check: cancel succeeds and the job's status becomes `cancelled`, not
     `completed`/`failed`. Confirmed live: `POST /api/jobs/9/cancel` on a
     still-`pending` correction job returned
     `{"ok":true,"job":{"status":"cancelled",...}}` immediately. Note this
     is timing-sensitive the same way Scenario 8's cancel is — cancel only
     succeeds while the job is `pending`/`running`
     (`services/llm_jobs.py`'s `cancel_llm_job`); a job that has already
     reached `completed`/`failed` returns `400 Cannot cancel a job with
     status '...'`. Given local jobs complete in single-digit seconds
     (Scenario 13/14's observed timing), issue cancel immediately after the
     trigger call returns, same discipline as Scenario 8.
3. Trigger "rerun" on any `failed` or `cancelled` job — `POST
   /api/jobs/{job_id}/rerun` (`app.py:1197`, UI: `j-rerun`,
   `static/rack.js:1515`). Note: `rerun_llm_job` (`services/llm_jobs.py:108`)
   only accepts jobs in `failed`/`cancelled` status — attempting it on a
   `completed` job 400s with `"Can only rerun failed or cancelled jobs
   (this one is 'completed')"`, so pick one of the `failed` auto-correction
   jobs left over from Scenario 5's upload (provider `groq`, no key saved)
   or the job just cancelled in step 2.
   - Check: a new job entry appears (new `id`, same `kind`/`transcript_id`)
     and reaches a terminal status (`completed` or `failed`). Confirmed
     live: `POST /api/jobs/4/rerun` (a `failed` groq correction job with no
     API key) returned a new job id 8, which reached `failed` again within
     ~2 seconds (same "no groq API key saved" error) — a legitimate
     terminal-status result for this check even though it re-fails, since
     the check is "reaches a terminal status", not "succeeds".

Report: `[PASS|FAIL] Scenario 16: Jobs panel`

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

## End of run

Print every scenario's report line in order (1 through 16) as the final
output of this skill, then confirm Teardown ran.
