# Claude Code /issue command — design

## Purpose

Recreate opencode's `/issue` workflow (resolve issue, investigate, fix, test,
self-audit, open PR) as a native Claude Code slash command, so `/issue` is
available inside a Claude Code session without opencode running. This is a
parallel, optional path, not a replacement:

- Opencode's `/issue` (`.opencode/command/issue.md` +
  `.omo/issue-runner-prompt.md`) stays exactly as-is, untouched.
- Independent PR audit (Oracle pass, `/audit-pr`) stays on opencode. The
  Claude Code version does not attempt to replicate an independent-model
  review; it explicitly defers that to opencode's existing `/audit-pr`.
- Orchestrator role ("Sisyphus" in opencode's terms) runs on Opus. Cheaper,
  bounded sub-phases (investigation, test-running, mechanical edits) delegate
  to Sonnet or Haiku via the `Agent` tool.

## Non-goals

- Not a background/headless dispatch. Runs interactively in the foreground
  session that invokes it; the user watches phases live and handles
  permission prompts as they occur.
- Not a rewrite of opencode's `/issue`. This is a second implementation of
  the same phase structure, adapted to Claude Code's own tools (`Agent`,
  `EnterWorktree`/`ExitWorktree`) in place of opencode's agent-name/config
  routing and manual `git worktree` commands.
- Does not add a Claude-Code-native audit pass. Independent review continues
  to happen via opencode's `/audit-pr` after the PR is opened, regardless of
  which tool produced it.

## Architecture

Two new tracked files in the whisperdesk repo:

- `.claude/commands/issue.md` — thin wrapper. Extracts the issue number from
  `$ARGUMENTS` (same malformed-invocation handling as opencode's version: if
  extra text follows the number, treat it as an override instruction for
  this run but flag the malformed invocation in the first status update).
  Before doing anything else, checks the active session model; if it is not
  Opus, hard-stops and tells the user to run `/model opus` first, then
  re-invoke. Otherwise `!cat`s the shared runner prompt below, substituting
  the resolved issue number everywhere the prompt says `<N>`.
- `.claude/issue-runner-prompt.md` — the shared phase workflow, Claude-Code-
  native, tracked in the repo (unlike opencode's gitignored
  `.omo/issue-runner-prompt.md` copy — there is no local-machine-secret
  reason for this one to stay untracked, and tracking it means the file
  shows up in PR diffs whenever it's tuned).

Neither file touches `.opencode/` or the opencode-only parts of `.omo/`.

Run artifacts (`investigation.md`, `self-audit.md`, `wrong-directions.md`,
`token-usage.md`) are written to `.omo/runs/issue-<N>/<branch>/`, the same
path opencode's `/issue` uses. This is a deliberate exception to "don't
write into `.omo/`": `/audit-pr` reads run artifacts from this exact path
regardless of which tool produced the PR, so the Claude Code version must
write there too for `/audit-pr` to find them. (This does not touch
opencode's own config/prompt files under `.omo/` — only the shared
`runs/` output convention.)

## Orchestrator invocation

The command runs directly in whatever session invokes it — no
subagent-as-orchestrator layer, no background dispatch. The model-check
guard (session must already be on Opus) is the only gate; if it fails, the
command aborts before Phase 0 rather than silently running on the wrong
tier.

Worktree/branch setup uses the native `EnterWorktree` tool (creates a fresh
worktree off `origin/master`, switches the session into it) instead of
opencode's manual `git fetch` / `git worktree add` / `git worktree list`
sequence. Cleanup on completion follows `ExitWorktree`, not raw
`git worktree remove` — consistent with the existing project rule about
never using raw worktree removal on a harness-managed worktree.

The worktree is **not** removed immediately after the PR is pushed. It
persists until the PR is merged (or abandoned), matching opencode's
`/issue` (not `/issueAB`, which self-cleans immediately because AB variants
are throwaway once scored — this command's PRs go through a real review
cycle first).

## Phase-by-phase mapping

| Phase | Execution |
|---|---|
| Model check | inline (Opus); hard-stop if session model isn't Opus |
| Phase 0: resolve real target issue | inline (Opus). Same tracking-issue-vs-standalone resolution and PR-vs-issue-number guard as opencode's prompt |
| Setup: worktree + branch | `EnterWorktree` (fresh off `origin/master`) |
| Phase 1: investigate | `Agent(model: sonnet)` — writes `investigation.md` in the run-artifact directory |
| Phase 1.5: completion-race check | inline (Opus), conditional — only when Phase 1 touched a job/state completion path, same trigger as opencode's prompt |
| Phase 2: fix | inline (Opus) — Complement Rule (every entry point touched, not just the one the issue names), batch edits without re-reading after every single change. Bounded, purely mechanical sub-edits (a rename repeated across files, for example) may be dispatched to `Agent(model: haiku)` |
| Phase 3: test | `Agent(model: sonnet)` — static source-level check first, then live suite/browser-MCP verification where applicable. Any step it can't complete gets reported back verbatim, prefixed `BLOCKED-VERIFICATION:`, and is either completed inline or carried into `self-audit.md` honestly |
| Phase 3.5: self-audit checklist | inline (Opus) — writes `self-audit.md` (one line per promise from `investigation.md` and per acceptance criterion, literal identifiers cited, mutation-check line for every new/changed test), then runs `python scripts/verify_self_audit.py .omo/runs/issue-<N>/<branch>/self-audit.md` (already exists in the repo, confirmed path-agnostic — no `.omo`-specific hardcoding) before proceeding |
| Phase 3.75: Oracle regression pass | **dropped**. `self-audit.md` gets one explicit line noting independent review is deferred to opencode's `/audit-pr`, so self-audit-only is never mistaken for a full review |
| Phase 4: PR | inline (Opus) — opened against `master`, `Closes #<N>` in the body (the real target issue number from Phase 0), no AI-authorship trailers, plain repo writing style |
| Phase 5: self-report | inline — `wrong-directions.md` and `token-usage.md` written as-you-go (not backfilled at the end); `token-usage.md` names every `Agent()` call this run made and which model backed each one. Final report to the user: which issue was actually targeted, the PR link, pointers to all four run-artifact files |

## Error handling

- Model-check failure: hard stop before any phase runs, tell the user to
  `/model opus` and re-invoke.
- Phase 0 PR-not-issue guard: if the given number resolves to a PR rather
  than an issue, stop and report plainly, same as opencode's prompt.
- Tracking issue fully resolved (all referenced issues closed/merged): stop
  and report, don't invent new work.
- `Agent()` returning `null` (subagent skipped or died): treated as a
  blocked verification/step, never silently treated as success.
- Oracle-pass gap: explicitly disclosed in `self-audit.md`, not silently
  absent.
- Any `BLOCKED-VERIFICATION:` from a delegated Sonnet/Haiku phase must be
  resolved inline or carried forward honestly into `self-audit.md` before
  Phase 4.

## Testing / validation of the command itself

No automated tests apply to the command/prompt files directly — they are
orchestration instructions, not executable code. Validation is a live dry
run:

1. Run `/issue <N>` against a small, low-risk real issue, on an Opus
   session, and watch each phase execute.
2. Confirm `Agent()` calls actually launch at the intended model tier —
   check `token-usage.md` after the run for a name-the-model list matching
   the phase mapping above.
3. Confirm `scripts/verify_self_audit.py` runs cleanly against the
   `.omo/runs/issue-<N>/<branch>/self-audit.md` path when invoked from a
   Claude Code session (first real cross-tool invocation of an
   opencode-originated script from this new path).
4. Confirm `EnterWorktree`/`ExitWorktree` and the PR-open flow behave as
   expected, including that the worktree is not cleaned up until the PR
   merges.
