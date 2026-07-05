# Handoff: Browser-driven E2E stress test (next phase)

## What exists today

`.claude/skills/e2e-test-app/SKILL.md` (merged to master, commit `3d4cd28`)
covers 16 feature scenarios for WhisperDeck, but every scenario was
validated via **direct HTTP API calls**, not a real browser. See the
skill's own "## Validation status" section for the disclosure.

## Why it's HTTP-only (and why that's wrong to repeat)

Task 1's implementer subagent reported "no live Playwright/browser tool
is available in this environment." That claim was **never independently
verified** and got copied unchanged into every subsequent task's dispatch
prompt (Tasks 2-6). It was false: Playwright MCP tools
(`mcp__plugin_playwright_playwright__browser_navigate`, `browser_click`,
`browser_snapshot`, `browser_type`, etc.) are available in this session
via `ToolSearch` — confirmed directly. The whole plan ran HTTP-only when
it didn't need to. Don't repeat this mistake: **have the next session's
implementer subagents actually call `ToolSearch` for
`mcp__plugin_playwright_playwright__*` tools and confirm they load**
before assuming a browser isn't available.

## What the user actually wants (this is a materially different bar)

Not just "click through each scripted scenario and check a status code."
The user wants the e2e suite to behave **like a real user actually using
the app**, with enough judgment to *notice problems the app has*, not
just confirm the backend did what was asked. Concretely, a browser-driven
agent should be watching for:

- A button/control that exists in the DOM but isn't wired up (dead click)
- A control that's the right idea but hard to find / mislabeled
- A workflow that requires an unintuitive number of steps
- UI state that doesn't reflect what the backend already did (stale view,
  no refresh, no loading indicator)
- Missing error/success feedback (silent failure, no toast)
- A feature described in scenario text (from the existing SKILL.md) that
  has no reachable UI path at all
- Anything a first-time user would get stuck on

This is closer to an exploratory UX audit than a fixed test script. The
existing 16-scenario list is a good checklist of *what functionality to
exercise*, but the next phase's agent needs standing instructions to
report friction/gaps as findings, not just PASS/FAIL against a fixed
assertion.

## Reusable assets

- **Fixtures:** `tests/fixtures/e2e_multispeaker.mp3` (canonical, copied
  from `O2C_CRP_5min.mp3`, confirmed 6 distinct speakers via pyannote).
  Originals `O2C_CRP_1min/5min/10min/20min.mp3` also present if a
  different length is useful (e.g. faster iteration with the 1min file).
- **Local LLM:** Lemonade at `http://localhost:13305/v1`, model
  `gpt-oss-20b-mxfp4-GGUF`. Requires a placeholder API key (`"not-needed"`
  or similar non-empty string) on the `local` provider config — see known
  bugs below.
- **Isolation pattern:** spawn `app.py` with `WHISPERDECK_DATA_DIR=<temp
  dir>` and `PORT=9782` (see SKILL.md's "## Setup" section) — reuse this,
  don't touch the user's real `data/` dir or port 9781.
- **Known app bugs already filed, don't re-discover/re-fix them:**
  - [Issue #2](https://github.com/tito13kfm/whisperdesk/issues/2): `local`
    provider sends empty `Authorization: Bearer ` header, rejected by
    httpx. Workaround: non-empty placeholder API key.
  - No UI control exists for renaming a transcript title (API-only via
    `PATCH /api/transcripts/{id}`) — found during Task 3 of the prior
    plan. Worth deciding whether this is an intentional gap or something
    to flag in the new stress-test findings.
  - `test.mp4` (original 3-second fixture) produces zero recognizable
    speech from Moonshine — harmless for job-lifecycle-only checks, useless
    for anything content-dependent. Use `e2e_multispeaker.mp3` instead for
    anything that needs real transcript content.

## Suggested next steps for the fresh session

1. Brainstorm (superpowers:brainstorming) the browser-driven test design:
   likely still subagent-driven-development, but each implementer/reviewer
   this time actually drives a real browser via
   `mcp__plugin_playwright_playwright__*` tools against the isolated
   server, following user-like flows rather than a fixed HTTP script.
   Decide: does this replace the existing SKILL.md content, extend it with
   a second "browser-driven" pass, or produce a separate skill entirely?
   (Recommendation to weigh: keep the HTTP-level SKILL.md as a fast
   backend-regression check, and add a new skill or section specifically
   for the exploratory browser/UX pass — they serve different purposes.)
2. Design how "findings" (UX friction, dead controls, missing feedback)
   get reported — probably a structured findings list alongside the
   existing PASS/FAIL/SKIPPED scenario report, not folded into it.
3. Reuse the fixtures, Lemonade config, and isolation pattern above rather
   than rediscovering them.
