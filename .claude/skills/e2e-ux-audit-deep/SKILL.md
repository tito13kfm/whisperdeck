---
name: e2e-ux-audit-deep
description: Deep, self-inventorying UX audit of WhisperDeck via Playwright MCP against an isolated local server with a hermetic LLM stub. Runs four passes (A journeys, B full control sweep, C empty/loading/error-state matrix, D partial-failure probes), regenerates the control inventory from static/ each run so new buttons/pages/dials are covered automatically, verifies behavioral findings in source, triages into bug / missing-feature / ui-ux / a11y, and produces an HTML report plus a review-gated GitHub issue set. Use for a thorough pre-release UX/quality dive that goes beyond the 6-journey e2e-ux-audit. For the lighter journeys-only pass, use e2e-ux-audit; for backend-only regression, use e2e-regression-http.
---

# WhisperDeck Deep UX Audit

Finds unexpected-but-not-crashing behavior (dead controls, stale UI, missing
feedback, masked failures, confusing labels, a11y gaps, isolation leaks) and
turns it into filed-able issues. Goes deeper than `e2e-ux-audit`: it drives
**every** interactive control, checks first-load vs post-work states, and
forces failure paths.

Run **inline in one session** with one Playwright browser instance; state
carries across passes. This is a long run (multiple hours). Keep a running
findings list in a durable file from the first finding.

## Design principles (why this skill is shaped this way)

- **Never hardcode the control list.** The app grows buttons/dials/pages. Pass
  B regenerates the inventory from `static/index.html` + `static/rack.js` every
  run (see Pass B), so new controls are swept automatically and removed ones
  don't cause false "missing" reports. If a whole new nav page appears, the
  regenerated inventory includes it; sweep it with the same four lenses.
- **Verify behavioral claims in source before calling something a bug.** A
  screenshot shows *what*; the handler shows *why* and whether it's by-design.
  Every "bug" finding in the report must cite a file:line.
- **Hermetic by default.** The test machine has no local LLM and the isolated
  instance starts with zero API keys. Use the committed stub
  (`scripts/llm_stub.py`); do NOT depend on the operator's real keys
  unless they explicitly ask. (Alternative, if the user prefers real models:
  configure one of their keys in setup instead of the stub, and skip starting
  the stub. Surface this choice; default to the stub.)

## Setup

1. Isolated data dir + port (PowerShell):

```powershell
$env:WHISPERDECK_DATA_DIR = "$env:TEMP\whisperdeck-uxaudit-$(Get-Random)"
$env:PORT = "9782"
New-Item -ItemType Directory -Force -Path $env:WHISPERDECK_DATA_DIR | Out-Null
```

2. Start the server (record the PID for teardown):

```powershell
$proc = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "app.py" `
  -WorkingDirectory (Get-Location) -PassThru -WindowStyle Hidden
```
(Fall back to `python.exe` if `.venv` is absent.)

3. Poll health — **budget ~60s, not 30s** (cold start can exceed 30s):

```powershell
$ready = $false
for ($i=0; $i -lt 60; $i++) { try { if (Invoke-RestMethod http://localhost:9782/api/health -TimeoutSec 2){$ready=$true;break} } catch {}; Start-Sleep 1 }
```
If not ready after 60s: `FAIL: server did not become healthy`, stop.

4. Start the hermetic LLM stub (record its PID too):

```powershell
$env:STUB_DELAY = "8"   # slow enough to see progress UI + race cancel
$stub = Start-Process -FilePath ".venv\Scripts\python.exe" `
  -ArgumentList "scripts\llm_stub.py" -PassThru -WindowStyle Hidden
Start-Sleep 2
Invoke-RestMethod http://localhost:13305/v1/models -TimeoutSec 5   # confirm up
```

5. Check real backends (record for SKIPPED reasons): `GET /api/health`
   (`diarization_backend`, `voice_id_backend`). Lemonade/real LLM is NOT
   required — the stub replaces it.

6. Launch Chromium via Playwright MCP at `http://localhost:9782`, with fake
   media flags if the MCP honors them:
   - `--use-fake-device-for-media-stream`
   - `--use-file-for-fake-audio-capture=<repo>\tests\fixtures\e2e_multispeaker.mp3`
   The Playwright MCP here usually does NOT expose launch args, so plan for a
   manual mic-Allow in Pass A Journey 2 (see Pitfalls).

7. **Provider + settings config — do this from *inside the browser page*, via
   the app's own `api()` helper, NOT via out-of-band HTTP.** Register the audit
   user through the UI first (so the browser holds a valid cached CSRF token),
   then in `browser_evaluate`:

```js
await api('/api/providers/local',     {method:'PUT', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({api_key:'not-needed', api_url:'http://localhost:13305/v1', default_model:'gpt-oss-20b-mxfp4-GGUF'})});
await api('/api/providers/local_llm', {method:'PUT', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({api_key:'not-needed', api_url:'http://localhost:13305/v1', default_model:'gpt-oss-20b-mxfp4-GGUF'})});
await api('/api/settings',            {method:'PUT', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({correction_provider:'local', correction_model:'gpt-oss-20b-mxfp4-GGUF'})});
```
   Configure BOTH `local` and `local_llm` — different features default to
   different provider ids (correction/summary have used `local_llm`;
   re-transcribe/context have used `local`). Settings are **per-user**, so
   re-apply after registering any new account.

## Pitfalls (READ FIRST — these cost real time)

- **Never call `GET /api/csrf-token` yourself (curl, PowerShell, or in-page
  fetch).** It rotates the session's server-side token on every call
  (`generate_csrf_token` overwrites it), silently invalidating the token the
  frontend cached at boot → every subsequent UI mutation 403s. Mutate only via
  the page's own `api()` (it sends the cached token). If you break it, reload
  the page to re-boot a fresh token. (This same mechanism is a real product
  bug — see report F1 — but during the audit it's a self-inflicted trap.)
- **All pages stay mounted in the DOM.** `document.querySelector('h1')` and
  bare `[data-tid]`/`[data-seg-*]` selectors match hidden pages too. Scope to
  the active page: `document.querySelector('[id^="page-"].active')`, and scope
  row queries to their container (e.g. `#bank-rows details[data-tid]`).
- **Modal state is `#modal-overlay.classList.contains('open')`, NOT the
  presence of `[role="dialog"]`.** `#modal-box` always exists in the DOM
  (emptied when closed). Checking for the dialog element gives false "modal
  still open" readings.
- **Toasts auto-remove after 4.2s.** Snapshotting between tool calls misses
  them. Instrument at click time inside a single `browser_evaluate`: click,
  then poll `#toast-wrap .toast` every ~500ms-1s and accumulate unique texts.
- **Marking clips / renaming re-renders the segment list**, invalidating
  element refs. Click via fresh `document.querySelector` calls one at a time,
  not cached ref arrays.
- **Pickers/model lists populate async** (~1-2s blank). Wait/poll before
  asserting their options.
- **Playwright MCP has no launch-arg control here** → `getUserMedia` pops a
  real OS mic dialog Playwright can't see; the call hangs. Do NOT retry in a
  loop (re-hangs the session). Pause, ask the operator to click Allow, wait for
  "continue"; or skip Journey 2.
- **The LLM stub must return summary JSON** or the summary pipeline fails on a
  parse error (`transcription.py` expects `{short_summary,key_points,
  action_items,decisions}`). The committed stub already does this when the
  prompt contains "json". If you swap in a real model, this is moot.
- **Local jobs finish fast** (Moonshine tiny ~9s, stub 8s). For the cancel
  race, submit then immediately navigate to Queue and cancel; `STUB_DELAY=8`
  gives a comfortable window.

## Findings format

Keep a durable running list from the first finding (write to
`docs/superpowers/e2e-findings/findings-<timestamp>.md` or the scratchpad — but
the HTML report is the durable deliverable). Per finding:

```
- Pass/Journey, step: <what was being done>
  Category: bug | missing-feature | ui-ux | a11y | data-isolation | product
  Type: dead-control | mislabeled | too-many-steps | stale-ui | missing-feedback
        | masked-failure | unreachable-feature | quality | other
  Severity: blocker | major | minor
  Note: <one line>
  Source: <file:line if behavioral — REQUIRED for any "bug">
  Screenshot: <path or none>   # only for major/blocker or inherently-visual
```

Screenshots → `docs/superpowers/e2e-findings/<slug>-<n>.png`, only on
major/blocker or visual glitches.

## Pass A — Six user journeys

Run journeys 1-6 exactly as in `.claude/skills/e2e-ux-audit/SKILL.md` (cold
start; live capture; voice roster across meetings; wrap-up correct/context/
summarize/export; backlog manage/search/sort/cancel/delete; misconfiguration
recovery). Differences for this deep run:
- Use the stub for all LLM steps (correction/summary/context).
- Journey 2: attempt fake-device; if the real mic dialog blocks, pause for a
  manual Allow (see Pitfalls), then continue.
- Journey 6: break a provider by pointing its `api_url` at
  `http://localhost:1/v1` via the page's `api()`, trigger correct/summarize,
  confirm the failure surfaces, then restore.
- Do NOT re-flag the known intentional gaps: no-title-rename-UI (rename lives
  in Tape library rows / `PATCH /api/transcripts/{id}`); issue #2 empty-Bearer
  workaround (placeholder key set in setup).

## Pass B — Full control sweep (self-inventorying)

1. **Regenerate the control inventory** from source this run (do not reuse a
   saved list). Dispatch a read-only investigator over `static/index.html` and
   `static/rack.js` to emit a table of every interactive control: nav items,
   static buttons, dynamically-generated buttons in template literals (modals,
   row actions `data-act`/`data-jact`/`data-dact`, tab keys, segment controls
   `data-seg-*`, voice/roster/file/export controls), toggles/knobs (`ctl-*`),
   inputs with side effects (search boxes, sort dropdowns, file inputs, drop
   zones), and keyboard shortcuts — grouped by page/panel/modal, noting
   conditional visibility. This auto-covers newly added controls and drops
   removed ones.
2. Drive each control and judge with four lenses:
   1. Click → visible response within ~1s? (else `missing-feedback`)
   2. Label communicates the action? (else `mislabeled`)
   3. Disabled controls explain themselves (title/tooltip); is a dead-looking
      control actually dead? (`dead-control`)
   4. After the action, is the next step obvious?
3. For any control whose effect is a backend mutation, confirm the request is
   actually sent and honored (network + source) — this is how F8 (dead
   Auto-correct toggle) was caught: the toggle changed only a client var and
   was never sent to `/api/transcribe`.
4. Controls the inventory lists but the current build no longer renders →
   note as removed, not as findings.

## Pass C — Empty / loading / error-state matrix

1. Register a fresh zero-data account. Screenshot/inspect **every** nav
   destination's first-load state — confirm each shows a real empty state with
   a CTA (this is where the genuine "blank page on first load" concern is
   judged; distinguish it from stale-snapshot bugs).
2. After doing work, return to the landing page (Monitor) and re-check: does it
   reflect the new data, or show a stale session-start snapshot? (F2.)
3. Log out and log in as a different account: is client state (deck status,
   last-opened transcript, badges) cleared, or does it leak across users? (F16.)
4. Check cross-user data scoping on any shared surface (e.g. Files) — verify in
   source whether lists filter by `current_user.id`. (F17.)
5. Mid-job loading states: is there a busy affordance while long ops run? (F4.)

## Pass D — Partial-failure probes

1. Break the LLM provider (`api_url` → `http://localhost:1/v1`) and exercise
   each LLM feature (correction, summary, context/term-extraction). Confirm
   each surfaces the failure; watch for **masked failures** where an error is
   reported as success (F19: context extraction returned "No new terms found"
   on a dead provider because `extract_hotwords_from_doc` swallows all
   exceptions). Restore the provider after.
2. Compound actions (rename→enroll, upload→diarize, correct-with-context):
   force a later step to fail and confirm the UI still reflects every earlier
   committed step (the shared-try/catch swallow pattern; local memory
   observation #10). Verify handler structure in source.
3. CSRF/session edge: confirm behavior when a mutation's token is stale (see
   F1) — the app should recover, not silently no-op.

## Triage (present for review — DO NOT file yet)

1. Classify every finding into the operator's buckets: **bug /
   missing-feature / ui-ux / a11y** (plus data-isolation, product-decision).
   Be honest about severity — cosmetic items (console noise, label
   inconsistency) are minor, not major.
2. Dedup against closed issues #4-#9 and open #38: an echo of a closed issue is
   a **regression**, framed as such, not a new report.
3. Group by **fix surface** so one PR/issue can close a cluster (e.g. all
   "no busy affordance" items together; keep distinct-root items separate even
   under one theme).
4. Cross-link to existing issues where relevant (e.g. voice-match quality →
   #38; orphan scoping → local observation #11).
5. Write the HTML report to
   `docs/superpowers/e2e-findings/report-<timestamp>.html` (same format as prior
   reports: journey table, findings grouped by category, regressions-checked,
   positives). This is the durable deliverable — write it BEFORE proposing
   issues so nothing is lost if the session drops.
6. Present the proposed issue set (titles, labels, bodies, grouping) to the
   operator and STOP. Filing is outward-facing and gated on explicit approval;
   do not run `gh issue create` until told to.

## Teardown (ALWAYS run, even on failure)

```powershell
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue   # server
Stop-Process -Id $stub.Id -Force -ErrorAction SilentlyContinue   # LLM stub
Remove-Item -Recurse -Force $env:WHISPERDECK_DATA_DIR -ErrorAction SilentlyContinue
```
Also:
- Close the Playwright browser session.
- If any stub PID was lost, kill leftover listeners on 13305:
  `Get-NetTCPConnection -LocalPort 13305 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }`
- **Delete scratch files this run created** (control-inventory dumps, findings
  markdown in scratchpad, saved `.yml` snapshots, ad-hoc `.playwright-mcp/`
  artifacts under the repo). KEEP the committed deliverables:
  `docs/superpowers/e2e-findings/report-*.html` and the screenshots it
  references. If the run wrote stray files into the repo root (e.g. a snapshot
  `.yml`), remove them so `git status` is clean apart from the intended report.
- Do NOT delete `scripts/llm_stub.py` or `tests/fixtures/e2e_multispeaker.mp3`
  — those are permanent fixtures.

## Report

Print the final `[PASS|FAIL|SKIPPED]` line per journey, the counts per
category/severity, the report file path, and the proposed-issues summary as the
skill's final output.
