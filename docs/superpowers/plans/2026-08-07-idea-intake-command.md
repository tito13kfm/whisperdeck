# /idea Intake Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-port `/idea` command (Claude Code + opencode) that interrogates a raw idea, challenges it against existing design/decisions, searches code and GitHub for prior art, and files GitHub issue(s) carrying a dated `## Prior-art check` section — then teach both `/issue`-family Phase 0 implementations to trust that section (skip their own re-search) when it's fresh and self-authored.

**Architecture:** Same shape as the existing `/issue` / `/issue-claude` pair: a thin per-harness command wrapper that `!cat`s a tracked runner-prompt body. Five phases (Intake → Challenge → Prior-art search → Shape deliverable(s) → Confirm+file), all inline in the invoking session except the two prior-art searches, which delegate to fresh throwaway agents. Full behavioral spec: `docs/superpowers/specs/2026-08-07-idea-intake-command-design.md`.

**Tech Stack:** Markdown command/prompt files (Claude Code slash-command frontmatter + `!cat` inlining; opencode command frontmatter + `!cat` inlining), `gh` CLI, `git log`.

## Global Constraints

- These are orchestration prompt files, not executable code. There is no unit-test suite for them — "testing" means the live dry runs specified in each task and the end-to-end validation in Task 5, exactly as the spec's own "Testing / validation" section describes and as `.claude/issue-runner-prompt.md`/`.omo/issue-runner-prompt.md` were validated. Do not invent a pytest suite for markdown files.
- `/idea` has no model-tier gate (spec, Non-goals). Do not add an Opus check to the Claude Code wrapper.
- `/idea` writes no run-artifact directory and touches no code. Its only durable output is GitHub issues created via `gh issue create`, always after explicit user confirmation (spec, Phase 4).
- The two ports are parallel, not copies — adapt to each harness's own idiom (Claude Code: `Agent()` calls, `TaskCreate`/`TaskUpdate`; opencode: named agents routed through `oh-my-openagent.json`, `todowrite`) rather than producing textually identical files. This matches how `.claude/issue-runner-prompt.md` and `.omo/issue-runner-prompt.md` already relate.
- Any time external text (the idea's own free-text seed, later a GitHub issue/PR body found during search) is passed into a delegated agent prompt, wrap it and label it as data, not instructions — same convention `.claude/issue-runner-prompt.md:208-211` and its opencode counterpart already use.
- Exact issue-body template and Prior-art check block are fully specified in the design spec — do not improvise a different format; Task 3/4 depend on the exact heading text `## Prior-art check`.

---

### Task 1: Claude Code `/idea` command

**Files:**
- Create: `.claude/commands/idea.md`
- Create: `.claude/idea-runner-prompt.md`

**Interfaces:**
- Produces: a filed GitHub issue whose body contains a `## Prior-art check (<YYYY-MM-DD>, filed via /idea)` section in exactly this format (Task 3 depends on this exact heading text and the dated-parenthetical format):
  ```markdown
  ## Prior-art check (2026-08-07, filed via /idea)
  - Code searched: <what/where>, found: <nothing | file:line>
  - GitHub searched: <queries run>, found: <nothing | #N (state)>
  ```
- Consumes: nothing from other tasks (standalone).

- [ ] **Step 1: Write the command wrapper**

Create `.claude/commands/idea.md`, following the exact pattern of
`.claude/commands/issue-claude.md` but with no model-check gate and
free-text `$ARGUMENTS` instead of a single issue number:

```markdown
---
description: "Interrogate a raw idea (bug, feature, or anything else worth tracking), challenge it against existing design/decisions, search code and GitHub for prior art, and file GitHub issue(s) shaped for /issue-claude's Phase 0 to trust. Usage: /idea <optional one-line idea>, or just /idea to start the intake dialogue from scratch. No model requirement."
argument-hint: <optional-one-line-idea>
---

<command-instruction>
Idea given (may be empty): $ARGUMENTS

Canonical workflow (single source of truth lives at
`.claude/idea-runner-prompt.md`, inlined below so this command never
drifts out of sync with it):

!`cat "$(git rev-parse --path-format=absolute --git-common-dir)/../.claude/idea-runner-prompt.md"`

If the idea given above is non-empty, treat it as the seed input for
Phase 0's intake and start there. If empty, start Phase 0 by asking the
user what the idea is. Start at Phase 0 either way.
</command-instruction>
```

- [ ] **Step 2: Write the runner-prompt body**

Create `.claude/idea-runner-prompt.md`. Required section structure, in
order (mirror `.claude/issue-runner-prompt.md`'s heading/prose style —
numbered steps under each phase, imperative voice, no placeholders):

1. `# Claude Code Idea Runner` (title, matching `.claude/issue-runner-prompt.md:1`'s convention)
2. `## Phase 0: intake` — expand the spec's Phase 0 bullet
   (design spec, "Phases" section, item 0) into full prose: capture
   `$ARGUMENTS` verbatim as the seed if non-empty, else ask; classify
   bug/feature/other; ask follow-up questions **one at a time**; hard
   stop at 5 follow-up rounds without convergence — summarize
   understanding and ask for explicit yes/no confirmation instead of
   continuing.
3. `## Phase 1: challenge (blocking)` — expand spec Phase 1: check the
   clarified idea against this repo's `CLAUDE.md`/`AGENTS.md`, relevant
   memory, and a skim of the touched code area. Conflict found → stop,
   present it plainly, wait for the user to confirm/override/abandon.
   No conflict → proceed automatically. State explicitly: if the user
   overrides, record the override verbatim for Phase 3 to carry into
   the filed issue (spec, Error handling).
4. `## Phase 2: prior-art search (delegated)` — two fresh `Agent()`
   calls (`subagent_type: "Explore"`, read-only), never `fork` (same
   rule as `.claude/issue-runner-prompt.md:194-200`, "Fresh agent,
   never fork, whenever a specific model matters" — here it matters
   because a fresh agent must not see the live conversation's framing
   bias when searching): one for code prior-art ("is this already
   implemented, and where"), one for GitHub prior-art (`gh issue list
   --search`, `gh pr list --search`, `git log --oneline --grep`). Wrap
   the idea's own text when handing it to each delegated agent, same
   convention as `.claude/issue-runner-prompt.md:208-211`. Match found
   by either → report it, ask whether to stop or proceed anyway. Both
   findings (or lack thereof) get recorded verbatim for Phase 3's
   Prior-art check section — do not paraphrase what was searched or
   found.
5. `## Phase 3: shape the deliverable(s)` — expand spec Phase 3:
   independent-deliverables split rule (bundle → cross-linked issues;
   unclear → one issue with the breakdown noted inline, never guess a
   split). Each issue drafted with this exact template:
   ```markdown
   ## Problem
   <what's wrong or missing, in the user's own terms>

   ## Evidence
   <file:line citations from Phase 1/2, not paraphrase>

   ## Prior-art check (<YYYY-MM-DD>, filed via /idea)
   - Code searched: <what/where>, found: <nothing | file:line>
   - GitHub searched: <queries run>, found: <nothing | #N (state)>

   ## Proposed approach
   <a hypothesis for /issue-claude's Phase 1 to verify, not a spec to
   implement verbatim>

   ## Acceptance criteria
   <concrete, checkable list>
   ```
   If Phase 1 recorded a user override, add it under `## Problem` as a
   one-line note (`Note: overrides a known conflict with <X>, see
   below`) followed by what the conflict was.
6. `## Phase 4: confirm + file` — show the drafted issue(s) in full,
   wait for explicit go-ahead before any `gh issue create` call (this
   is a GitHub-visible action). On requested changes: revise in place
   and show again; only re-run Phase 1 or 2 if the change alters scope
   or the touched code area enough that their results no longer apply
   (spec, Phase 4). On confirmation: `gh issue create` per issue,
   `Related: #N` cross-links when split. Report the filed issue
   number(s)/URL(s) back to the user as the final message.
7. `## Error handling` — port verbatim, adapted to this workflow, the
   five bullets from the design spec's "Error handling" section
   (`gh` retry-then-disclose, bounded clarification rounds, clean
   abandon, override disclosure, default-to-one-issue-on-unclear-split).

- [ ] **Step 3: Live dry run (Claude Code)**

Run `/idea test: a throwaway idea for validating this command, e.g. add
a --dry-run flag to the export script` in a Claude Code session. Confirm:
Phase 0 asks at least one follow-up question rather than filing
immediately; Phase 2 actually dispatches two `Agent()` calls (observe
the tool-call log directly — `/idea` has no `token-usage.md`, per
Global Constraints); Phase 4 shows a fully-formed draft and stops for
confirmation. At the
confirmation prompt, answer "abort, this was a dry run" and confirm no
`gh issue create` was called (`gh issue list --search "dry-run flag for
export script"` returns nothing new).

- [ ] **Step 4: Commit**

```bash
git add .claude/commands/idea.md .claude/idea-runner-prompt.md
git commit -m "feat: add Claude Code /idea intake command"
```

---

### Task 2: Opencode `/idea` command

**Files:**
- Create: `.opencode/command/idea.md`
- Create: `.omo/idea-runner-prompt.md`

**Interfaces:**
- Produces: same `## Prior-art check (<date>, filed via /idea)` contract as Task 1 — Task 4 depends on this exact heading text.
- Consumes: nothing from Task 1 (independent port of the same spec, not a copy).

- [ ] **Step 1: Write the command wrapper**

Create `.opencode/command/idea.md`, following the exact pattern of
`.opencode/command/issue.md`:

```markdown
---
description: "Interrogate a raw idea (bug, feature, or anything else worth tracking), challenge it against existing design/decisions, search code and GitHub for prior art, and file GitHub issue(s) shaped for /issue's Phase 0 to trust. Usage: /idea <optional one-line idea>, or just /idea to start the intake dialogue from scratch."
argument-hint: <optional-one-line-idea>
agent: sisyphus
---

<command-instruction>
Idea given (may be empty): $ARGUMENTS

Canonical workflow (single source of truth lives at `.omo/idea-runner-prompt.md`,
inlined below so this command never drifts out of sync with it):

!`cat "C:/Claude/whisperdesk/.omo/idea-runner-prompt.md"`

If the idea given above is non-empty, treat it as the seed input for
Phase 0's intake and start there. If empty, start Phase 0 by asking the
user what the idea is. Start at Phase 0 either way.
</command-instruction>
```

Reuses the `sisyphus` agent identity (same as `/issue`) — this repo has
no lighter-weight orchestrator agent defined, and `/idea` needs the same
judgment-call capacity `/issue`'s orchestrator has for the Challenge
phase.

- [ ] **Step 2: Write the runner-prompt body**

Create `.omo/idea-runner-prompt.md`. Same seven-section structure as
Task 1 Step 2, ported to opencode idiom per
`.omo/issue-runner-prompt.md`'s own conventions: named-agent dispatch
(`explore` for the code-prior-art search, `explore` or `deep` for the
GitHub-prior-art search — not hardcoded model names, matching
`.omo/issue-runner-prompt.md`'s "call agents by name, not model" rule),
`todowrite` if a task list is needed, and the same untrusted-text-wrap
convention `.omo/issue-runner-prompt.md` already applies elsewhere. The
Phase 3 issue template, Phase 4 confirm-then-file behavior, and Error
handling bullets are identical in substance to Task 1 (the spec doesn't
differ by harness there) — only the *mechanics* of Phase 2's delegation
differ.

- [ ] **Step 3: Live dry run (opencode)**

Same dry run as Task 1 Step 3, run through opencode's `/idea` instead.
Confirm the same four checkpoints (follow-up question asked, two
searches dispatched, Phase 4 shows a draft and stops, abort leaves no
new GitHub issue).

- [ ] **Step 4: Commit**

```bash
git add .opencode/command/idea.md .omo/idea-runner-prompt.md
git commit -m "feat: add opencode /idea intake command"
```

---

### Task 3: Phase 0 handoff contract — Claude Code

**Files:**
- Modify: `.claude/issue-runner-prompt.md` (Phase 0 section, starts at line 13 per current file)

**Interfaces:**
- Consumes: the exact `## Prior-art check (<YYYY-MM-DD>, filed via /idea)` heading format Task 1 produces.
- Produces: a Phase 0 that either logs `trusting /idea's prior-art check from <date>` and skips its own search, or falls back to the existing search unchanged — this is what Task 5 verifies end-to-end.

- [ ] **Step 1: Add the trust check to Phase 0**

In `.claude/issue-runner-prompt.md`, in the Phase 0 section (currently
starting at line 13), immediately before the existing "Search for prior
work landed under a different issue number" step, insert:

```markdown
**Check for a fresh, self-authored prior-art check first.** If the
target issue's body contains a `## Prior-art check (<date>, filed via
/idea)` heading: parse `<date>`. If it is within the last 30 days AND
`gh issue view <N> --json author --jq .author.login` matches the
repository's configured git user, trust it — log "trusting /idea's
prior-art check from `<date>`" and skip the prior-work search below
entirely. Otherwise (missing, stale, or not self-authored), run the
search below unchanged; a non-owner-filed issue cannot switch off dedup
by forging this section.
```

This is additive only — do not remove or restructure the existing
prior-work search step that follows it.

- [ ] **Step 2: Self-check**

Read the edited Phase 0 section back and confirm: the new paragraph
comes before the existing search step (so the skip actually short-circuits
it, rather than running both unconditionally); the heading text matched
(`## Prior-art check (<date>, filed via /idea)`) is character-for-character
what Task 1 Step 2's template produces; nothing else in Phase 0 changed.

- [ ] **Step 3: Commit**

```bash
git add .claude/issue-runner-prompt.md
git commit -m "feat: trust /idea's prior-art check in Claude Code issue Phase 0"
```

---

### Task 4: Phase 0 handoff contract — opencode

**Files:**
- Modify: `.omo/issue-runner-prompt.md` (Phase 0 section)

**Interfaces:**
- Same contract as Task 3, ported to opencode's Phase 0 (which has the identical "Check AGENTS.md's testing tiers"-style prior-work search step, per the earlier read of this file).

- [ ] **Step 1: Add the same trust check**

Apply the identical addition as Task 3 Step 1, in `.omo/issue-runner-prompt.md`'s
Phase 0 section, immediately before its prior-work search step. Same
exact paragraph text (this one paragraph is intentionally identical
across both files — the contract is harness-agnostic even though the
rest of the two prompts diverge).

- [ ] **Step 2: Self-check**

Same checks as Task 3 Step 2, against this file.

- [ ] **Step 3: Commit**

```bash
git add .omo/issue-runner-prompt.md
git commit -m "feat: trust /idea's prior-art check in opencode issue Phase 0"
```

---

### Task 5: End-to-end validation

**Files:** none (validation only; may produce a fix commit if something's broken)

**Interfaces:**
- Consumes: all of Tasks 1–4.
- Produces: proof the full loop works, or a list of concrete fixes if it doesn't.

- [ ] **Step 1: File a real small idea via `/idea`**

Pick one genuinely small, real idea (bug or minor feature) worth
tracking. Run `/idea` (Claude Code or opencode, your choice) through to
a filed issue. Confirm the filed issue's body has a well-formed
`## Prior-art check (<date>, filed via /idea)` section: dated today,
states what was searched, states what was/wasn't found.

- [ ] **Step 2: Confirm dedup catches a known duplicate**

Run `/idea` again with an idea you know is already fixed or already
tracked (pick any closed/merged issue from `gh issue list --state
closed --limit 5`, describe its symptom as if new). Confirm Phase 2
reports the match and asks before filing, rather than filing a
duplicate silently.

- [ ] **Step 3: Confirm the handoff actually works**

Run `/issue <N>` or `/issue-claude <N>` against the issue filed in Step
1. Confirm Phase 0's first status update (or `investigation.md`)
contains the line `trusting /idea's prior-art check from <date>` rather
than re-running the prior-work search. If it instead runs the full
search, that's a bug in Task 3 or 4 — fix the parsing/matching logic in
the relevant runner-prompt file and re-run this step.

- [ ] **Step 4: Clean up the test issue**

If Step 2's duplicate-test idea produced a throwaway issue, close it
with a one-line comment explaining it was a `/idea` dedup test. Do not
close the real issue from Step 1 — that one stays open, feeding into
normal `/issue` work.

- [ ] **Step 5: Final commit (if fixes were needed)**

```bash
git add -A
git commit -m "fix: correct /idea prior-art handoff based on end-to-end test"
```

If no fixes were needed, skip this step — Tasks 1–4's commits already
cover everything.
