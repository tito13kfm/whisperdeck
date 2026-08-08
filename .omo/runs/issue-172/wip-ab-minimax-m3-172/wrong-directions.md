# Wrong Directions — Issue #172 / variant minimax-m3

This file captures every place an instruction (plan, AGENTS.md, this prompt,
my own assumptions) turned out wrong vs current code. Each entry: what I
expected, what was actually true, what I did about it.

---

## 1. Plan line numbers are stale (drift, not error)

The plan file `.omo/plans/markdown-export.md` was written before the codebase
got recent edits (issues #145, #171 and others). Line numbers drifted by
1-42. None of the drift changes the *kind* of insertion — I inserted in
the right place every time — but the plan's literal line numbers can't be
trusted.

**Drift table** (plan's claim vs current code, all in the worktree at base
SHA `3e90d5c`):

| Plan says | Actual | Drift |
|---|---|---|
| `services/settings.py:31` | `format_model` at line 30, dict ends line 31 | -1 |
| `services/reformatting.py:112` | file ends line 112 | 0 |
| `app.py:1941` (format route) | format route at line 1983 | +42 |
| `app.py:594-625` (bootstrap) | bootstrap at line 633, returns at 658-664 | +39 |
| `static/rack.js:4490-4497` | Maintenance card at 4506-4512 | +16 |
| `static/rack.js:3167-3171` (exportToolbarHtml) | function at 3176-3180 | +9 |
| `static/rack.js:2584-2587` (detailBodyClick) | function at 2591 | +7 |

**Verdict:** the plan's prose is right; the line numbers aren't. The next
plan author should either remove the line numbers or rebase them. I
recommend dropping the literal line numbers from the plan body and
keeping the *character* of the insertion point (e.g. "after
format_transcript" instead of "after line 1941"). The intent is clear; the
specific number is ephemeral.

**Why this didn't bite me:** I always re-grep'd the file for the *symbol*
(format_transcript, exportToolbarHtml, etc.) before editing, never trusted
the line number alone. A literal line-number-only edit would have inserted
in the wrong place.

---

## 2. AGENTS.md's claim that `atlas`, `quick`, `writing`, `unspecified-low` are cloud-only

**AGENTS.md line 127 says:** "As of the last check: `deep`, `ultrabrain`,
`oracle`, `librarian`, `unspecified-high`, `artistry`, `visual-engineering`,
and `metis`/`momus` were mapped to cloud (OpenRouter-style) models..."

This run dispatched ZERO explore/librarian agents — the work was all direct
file reads plus a handful of grep/glob calls. I did not need to verify
local-vs-cloud for any agent. The cap didn't apply.

If a future run uses `atlas`/`quick`/etc. on this codebase, it should
re-check `~/.config/opencode/oh-my-openagent.json` before assuming
non-local. AGENTS.md itself flags this as a known doc error.

**No action taken** because no agent dispatch happened.

---

## 3. `re` module was not imported in app.py

The plan's Task 3 step "Also needed: `import os` and `import re`..." notes
this. `os` was already at line 7; `re` was not. I added `import re` between
`import os` and `import json`. One line, not a real problem.

**Why this is in wrong-directions:** the plan author noted this as a
possible need rather than confirming it upfront. A 5-second grep would
have saved a mid-task surprise.

---

## 4. `datetime.datetime.utcnow()` deprecation

When I wrote the export endpoint, I used `datetime.datetime.utcnow()` for
the probe file name and the fallback date. The test run surfaced
`DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled
for removal in a future version`.

Fixed in-place (same PR) by switching to
`datetime.datetime.now(datetime.timezone.utc)` for the probe timestamp and
`datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)` for
the date fallback (so it matches `t.created_at`'s naive type from the
DB). One-line change in each location.

**Not in the plan.** The plan was written assuming the project doesn't
trip deprecation warnings. It does. Future plan authors should either
add a "use timezone-aware datetimes" rule to the style guide or use
`datetime.utcnow` consistently knowing it's already deprecated.

---

## 5. `withBusy` signature (spinner arg)

The plan's Task 6 wiring code uses `withBusy(b, async () => {...})` — the
export-copy and export-dl handlers in `detailBodyClick` (line 2594) use
the same signature: `withBusy(btn, async () => {...})` with no third
argument. I matched the existing pattern.

The plan's Task 4 audio-save handler uses `withBusy(e.currentTarget,
async () => {...}, { spinner: true })` — that pattern is for the
audio-save button which does a longer save. I didn't need the third arg
for the export button.

**Verdict:** no discrepancy. Just noting that the plan's two `withBusy`
sites use different signatures for different reasons.

---

## 6. `bootData` consumer placement

The plan's Task 5 step 2 says "find where bootData is consumed (search
for `bootData =` in rack.js, consumed by `checkAuth()` / `loadDashboard()`)".
I put `S.exportDir = body.settings...` in `checkAuth` (line 645-649) right
next to the other `S.*` assignments. `loadDashboard` doesn't need
`exportDir` (no UI on Monitor uses it). Plan was right.

---

## 7. Tests that weren't in the plan but I added

Plan Task 9 specifies 2 tests. I added a 3rd: `test_bootstrap_includes_settings`
which asserts the bootstrap response includes `settings.export_directory`.

**Why:** the bootstrap addition is load-bearing for the export feature —
without it, `S.exportDir` is empty on every page load until the settings
page is opened. I wanted a regression test that locks in the bootstrap
contract. The 1 added test is well-scoped, doesn't test anything else,
and follows the existing `client.put`/`client.get` pattern.

**Verdict:** scope creep, but in a good direction. Not a bug. A future
plan author should include bootstrap-shape tests for any new settings
field.

---

## 8. Self-audit gaps (transparent, not hidden)

Two deliberate gaps in the self-audit:
- No 401-auth test for the export route.
- No e2e test for the new export flow.

Both are documented in `self-audit.md` and explained. Per the plan's
discipline rule, I am disclosing scope, not reframing it.

---

## 9. Comments caught by the comment-detection hook

The repo's commit hook flagged inline comments in:
- `services/reformatting.py` (initially had a `# strip leading heading markers` and `# Transcript section` and `# Summary sections — each only if it has content`)
- `static/rack.js` (initially had a 3-line comment block above the export-dir-save handler)
- `tests/test_reformatting.py` (3 section-divider comments matching the existing `# ── routes ──` style)

I trimmed the unnecessary ones in the first two files. Kept the test file
section dividers because they match the existing convention at line 168 of
the same file.

**Verdict:** the hook is doing its job. It caught my "explain what the
code does" reflex in production code and forced me to make the code
self-explanatory. The test-file section dividers are a different
category — they're organizational, not explanatory.
