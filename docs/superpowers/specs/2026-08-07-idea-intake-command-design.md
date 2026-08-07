# /idea intake command — design

## Purpose

A front-end stage for turning a raw idea (bug, feature, or anything else worth
tracking) into a well-shaped GitHub issue, before `/issue` or `/issue-claude`
ever runs. Today, going from "I noticed X" to a filed issue means either
writing the issue by hand or letting `/issue`'s own Phase 0 discover, mid-run,
that the idea is already implemented, already tracked under a different
number, or conflicts with a decision made earlier in the project. `/idea`
moves that discovery earlier and cheaper: it interrogates the idea, challenges
it against known constraints, checks for prior art, and files an issue shaped
so `/issue`'s Phase 0 can trust the dedup work already done instead of
repeating it.

Two parallel ports, matching the existing `/issue` / `/issue-claude` pattern:
one opencode command, one Claude Code command, same phase structure adapted
to each harness's own tools.

## Non-goals

- Not a replacement for `/issue`'s Phase 0 prior-work search. That search
  still runs, unchanged, for any issue that doesn't carry a fresh, self-authored
  `/idea` prior-art check (see "The /issue handoff contract" below) — old
  backlog issues, issues filed by hand, anything untrusted.
- Not a deep investigation. It goes far enough to challenge the idea and
  confirm it isn't already done or already tracked — not far enough to
  enumerate every call site or draft a fix plan. That's still `/issue`'s
  Phase 1 job.
- Not automatic/hook-triggered. Explicit invocation only, same as every other
  command in this family.
- Does not write code or open a PR. Its only durable output is one or more
  filed GitHub issues.
- Does not add a model-tier gate. Unlike `/issue-claude`'s hard Opus
  requirement, `/idea` runs on whatever model the session is already on —
  nothing here edits code, and the deep-reasoning gate belongs to the command
  that actually implements the fix.

## Architecture

Four new tracked files, mirroring the issue-runner pattern:

- `.claude/commands/idea.md` — thin wrapper, no model check. Inlines
  `.claude/idea-runner-prompt.md` via `!cat`, same malformed-invocation
  handling precedent as the other Claude Code commands where relevant (here,
  `$ARGUMENTS` is free-text — the idea itself — not an issue number, so there
  is no number-extraction step; the whole argument, if any, is the seed
  input for Phase 0's intake, and an empty invocation just starts the intake
  dialogue from scratch).
- `.claude/idea-runner-prompt.md` — the Claude-Code-native phase workflow.
  Tracked (like `.claude/issue-runner-prompt.md`, not gitignored).
- `.opencode/command/idea.md` — opencode's thin wrapper, same shape as
  `.opencode/command/issue.md`.
- `.omo/idea-runner-prompt.md` — opencode's phase workflow, adapted to
  opencode's agent-name routing instead of `Agent()` calls, mirroring how
  `.omo/issue-runner-prompt.md` relates to `.claude/issue-runner-prompt.md`
  (parallel ports, not copies — each tuned to its own harness, edited
  independently, not required to stay textually identical).

No changes to `.opencode/command/issue.md`, `.claude/commands/issue-claude.md`,
or the bulk of either issue-runner prompt. The only edit to existing files is
a small, additive change to Phase 0 in both `.claude/issue-runner-prompt.md`
and `.omo/issue-runner-prompt.md` (see "The /issue handoff contract").

## Components

- **Orchestrator** (the invoking session, inline): runs the intake dialogue,
  the challenge check, and final issue drafting/confirmation. These need the
  live conversation with the user and must not be delegated.
- **Delegated search subagents** (fresh, throwaway, one per search): a
  code-prior-art search ("is this already implemented, and if so where") and
  a GitHub-prior-art search (`gh issue list`, `gh pr list --search`,
  `git log --grep`) — the same technique `/issue`'s Phase 0 already uses,
  reused here rather than redesigned. Only their findings return to the
  orchestrator; the search process itself doesn't need to survive.
- **Output**: one or more GitHub issues, created via `gh issue create`, each
  carrying the structured body described below. Nothing else persists —
  no run-artifact directory, no `.omo/runs/` entry (that convention exists
  for `/issue`'s own audit trail; `/idea` produces no code and needs none of
  it).

## Phases

0. **Intake.** Capture the raw idea verbatim (from `$ARGUMENTS` if given,
   otherwise ask). Classify bug / feature / other. Ask follow-up questions
   one at a time until purpose, constraints, and success criteria are clear
   enough to search against. Don't loop indefinitely: after 5 follow-up
   questions without convergence, summarize current understanding and ask
   for an explicit yes/no confirmation instead of continuing open-ended
   interrogation.

1. **Challenge (blocking).** Check the now-clarified idea against known
   constraints: this repo's `CLAUDE.md`/`AGENTS.md`, relevant memory (e.g. a
   decision recorded as deliberately deferred), and a skim of the code area
   the idea touches. If something conflicts, stop, present the conflict
   plainly, and wait for the user to confirm, override, or abandon. No
   conflict found → proceed automatically.

2. **Prior-art search (delegated).** Fire the two search subagents. If either
   turns up a match — code that already does this, or an issue/PR that
   already covers it — report it and ask whether to stop (already
   done/tracked) or proceed anyway (e.g. reopen, extend, or the match is only
   partial). No match on either → proceed.

3. **Shape the deliverable(s).** Decide one issue vs. several using the
   independent-deliverables rule: if the idea bundles pieces that could ship
   and be reviewed separately, split into cross-linked issues; otherwise file
   one. When the split call is genuinely unclear, default to one issue with
   the internal breakdown noted inline rather than guessing wrong. Draft each
   issue with a fixed template:
   - **Problem** — what's wrong or missing, in the user's own terms
   - **Evidence** — file:line citations from Phases 1–2, not paraphrase
   - **Prior-art check** — see contract below
   - **Proposed approach** — explicitly framed as a hypothesis for `/issue`'s
     Phase 1 to verify, not a spec to implement verbatim (same posture the
     issue-runner prompts already take toward an issue's own suggested fix)
   - **Acceptance criteria** — concrete, checkable

4. **Confirm + file.** Show the drafted issue(s) to the user and wait for
   explicit go-ahead before calling `gh issue create` — this is a
   GitHub-visible action and gets confirmed regardless of anything else in
   this spec. Cross-link related issues in each body (`Related: #N`) when
   split. If the user asks for changes instead of confirming, revise the
   draft in place and show it again; only re-run Phase 1 or 2 if the
   requested change alters the idea's scope or the area of code it touches
   enough that the existing challenge/prior-art results no longer apply.

## The `/issue` handoff contract

A dated section, filed only by `/idea`, that both issue-runner prompts learn
to recognize:

```markdown
## Prior-art check (2026-08-07, filed via /idea)
- Code searched: <what/where>, found: <nothing | file:line>
- GitHub searched: <queries run>, found: <nothing | #N (state)>
```

Small, additive edit to Phase 0 in both `.claude/issue-runner-prompt.md` and
`.omo/issue-runner-prompt.md`: before running the existing prior-work search
(`git log` grep + `gh issue list --search`), check whether the target issue's
body contains a `## Prior-art check` heading that is (a) dated within 30 days
and (b) was filed by the repo owner (`gh issue view --json author`, compared
against the configured git user). If both hold, skip the search and log
"trusting /idea's prior-art check from `<date>`" instead. If either fails,
run today's search unchanged — nothing about the existing behavior changes
for issues that don't carry a fresh, self-authored contract.

The 30-day window and the author gate are both deliberate: the window bounds
how stale a "nothing found" claim is allowed to be before `/issue` re-checks
for itself, and the author gate means a non-owner-filed issue (a GitHub
Issues form submission, for instance) can't forge this section to switch off
`/issue`'s own dedup search.

Both runner prompts need this edit together — they're a known
dual-maintenance point (per `.omo/issue-runner-prompt.md`'s own note about
shared examples with `.claude/issue-runner-prompt.md`), and this section adds
a new one.

## Error handling

- `gh` call fails during either search: one retry on a transient error; on
  repeated failure, disclose it in the Prior-art check section ("GitHub
  search failed, code-only") rather than silently proceeding as if nothing
  was searched.
- Idea stays ambiguous after a bounded number of clarifying rounds:
  summarize understanding and ask for explicit confirmation rather than
  interrogating indefinitely.
- User abandons mid-flow: no issue filed, nothing left on disk — `/idea`
  has no run-artifact directory to clean up in the first place.
- Challenge conflict overridden by the user: the override itself is recorded
  in the filed issue's Problem or a dedicated note, so `/issue`'s Phase 1
  doesn't have to rediscover the same conflict from scratch.
- Split judgment is genuinely unclear: default to one issue with the
  breakdown noted inline (see Phase 3), don't force a split that might be
  wrong.

## Testing / validation

No automated tests apply to the command/prompt files themselves — same as
the issue-command precedent, these are orchestration instructions, not
executable code. Validate with a live dry run:

1. Run `/idea` (or `/idea <one-liner>`) with a small real idea and confirm
   it asks sensible follow-ups rather than filing immediately.
2. Run it again against something already fixed or already tracked, and
   confirm Phase 2 catches the match and asks before filing a duplicate.
3. Confirm a filed issue's `## Prior-art check` section is well-formed
   (dated, states what was searched and what was found).
4. Run `/issue` or `/issue-claude` against that filed issue and confirm
   Phase 0 logs "trusting /idea's prior-art check from `<date>`" instead of
   re-running its own search — the actual proof the handoff contract works
   end to end.
