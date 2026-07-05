# Design: Browser-driven UX audit skill for WhisperDeck

## Problem

`.claude/skills/e2e-test-app/SKILL.md` (16 scenarios) validates that
backend behavior works, but every scenario was authored/validated via
direct HTTP calls, not a real browser (see that skill's "Validation
status" section). It also only checks scripted PASS/FAIL assertions, not
whether the UI is usable, discoverable, or gives good feedback.

We want a second, separate pass that drives a real browser like an actual
user would, across realistic end-to-end journeys, and surfaces UX
friction as structured findings (not just PASS/FAIL).

## Decisions from brainstorming

- **New skill**, not a section added to `e2e-test-app`: different
  contract (findings-oriented, judgment-heavy) from the scripted
  PASS/FAIL skill.
- **Runs inline** in the main session — one continuous Playwright browser
  session, not subagent-driven-development with parallel implementers.
  Confirmed via a live check that Playwright MCP tools are reachable from
  both the main session and subagents, but exploratory/judgment-heavy
  work with shared session state (login, uploaded transcripts, enrolled
  voices) fits a single continuous session better than parallel dispatch.
- Reuses `e2e-test-app`'s Setup/Teardown blocks (isolated
  `WHISPERDECK_DATA_DIR`, `PORT=9782`, fake-audio-device Chromium flag,
  health-check polling) — duplicated verbatim into the new skill file
  since skills can't include each other, kept word-for-word so the two
  don't drift silently.
- Known bugs already filed are applied as workarounds going in, not
  rediscovered as new findings:
  - Issue #2 (`local` provider empty `Authorization: Bearer ` header
    rejected by httpx) — apply the placeholder-API-key workaround before
    any LLM-job journey step. **This skill needs to be updated (workaround
    removed) once issue #2 is actually fixed** — flag this explicitly in
    the skill file itself as a maintenance note, not just in this spec.
  - No title-rename UI control (API-only) — don't re-flag as a new
    finding; it's already a known, decided-on gap.

## Scope: six user journeys (replaces the 16-scenario structure for this
pass; the scripted skill still owns scenario-level backend regression)

1. **First meeting, cold start** — register/login → configure provider →
   upload a recording → wait → read transcript. Watch for onboarding
   friction: unclear empty states, no progress indicator, confusing first
   screen.
2. **Live capture end-to-end** — start live capture via fake mic device,
   let it run, stop, confirm it lands in the job queue and transitions to
   a transcript. This is genuinely unverified territory (the scripted
   skill has zero live evidence for this scenario) — watch the *recording
   UI itself* (timer, waveform, stop control state), not just poll job
   status afterward.
3. **Voice roster built across meetings** — diarize a multispeaker
   meeting, rename speakers to real names, enroll voice profiles, upload
   a second meeting with overlapping speakers, check whether the app
   surfaces an auto-match/suggestion for already-enrolled voices or
   requires the user to notice and trigger matching manually. Judge
   discoverability, not just whether the API works.
4. **Wrap-up-the-meeting flow** — run correction, add a context doc,
   re-run correction, generate a summary, then look for any export/
   copy/share affordance for the results. New ground: the scripted skill
   never checked whether a way to get output out of the app exists at
   all.
5. **Managing a growing transcript backlog** — jobs panel with several
   concurrent/historical jobs, transcript list with multiple items,
   cancel a stuck job, delete stale transcripts. Watch list-level UX
   (search/sort/empty state) that a single-transcript script never
   exercises.
6. **Misconfiguration recovery** — deliberately set a bad provider
   URL/model, attempt a transcription/LLM job, check whether the failure
   surfaces a useful error to the user or dies silently. Net-new coverage
   of the failure path, not just the happy path.

Journeys run in a fixed order 1→6 in one continuous session/browser
instance, since later journeys depend on state built by earlier ones
(journey 3's enrolled voices feed journey 4's transcript; journey 5 needs
multiple transcripts/jobs already created by 1-4).

## Findings schema

Each finding recorded inline as it's noticed:

```
- Journey N, step: <what was being done>
  Type: dead-control | mislabeled | too-many-steps | stale-ui |
        missing-feedback | unreachable-feature | other
  Severity: blocker | major | minor
  Note: <one-line description of the problem>
  Screenshot: <path> (only for severity >= major, or when the finding is
              inherently visual)
```

Screenshots captured only at the moment a finding is logged (not on every
step), saved to `docs/superpowers/e2e-findings/<journey-slug>-<n>.png`,
to keep token and disk cost down.

## Reporting

End-of-run output has two parts:
1. Journey-level `[PASS|FAIL|SKIPPED(reason)]` line per journey (1-6),
   same convention as the existing scripted skill.
2. Full findings list, grouped by severity (blockers first).

Final step generates a static HTML report at
`docs/superpowers/e2e-findings/report-<timestamp>.html`: journey
PASS/FAIL table at top, findings below grouped by severity, each finding
with an inline `<img>` pointing at its screenshot via a relative path in
the same folder (no base64 embedding — keeps the file small). Simple
inline CSS, no build step, no external dependencies.

After writing the report, the skill opens it in the user's real default
browser (e.g. PowerShell `Start-Process <path>`), not the Playwright-
controlled browser instance — the Playwright session may already be
torn down by teardown, and it isn't a full browsing experience anyway.

## Out of scope

- Replacing or modifying the existing 16-scenario `e2e-test-app` skill's
  content or validation status.
- Automated severity scoring/prioritization beyond the three tiers above.
- CI integration — this remains a manually-invoked skill, same as
  `e2e-test-app`.
