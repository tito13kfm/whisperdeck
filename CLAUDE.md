# WhisperDeck — Claude Code instructions

Read `AGENTS.md` and follow **The Complement Rule** section — it exists because three PRs in a row shipped guards that missed sibling entry points. The Serena Usage and Advisor Escalation sections of AGENTS.md are Opencode-specific; ignore them in Claude Code (use your own configured tools).

## Git and PR hygiene

- Never advertise AI authorship: no `Co-Authored-By: Claude` trailers, no "Generated with Claude Code" footers, no session links in commit messages or PR bodies. Commit as the repository's configured git user.
- Never commit directly to `master`. Branch, open a PR, merge only after review and green CI.
- Delete the feature branch when merging a PR (default `gh pr merge` behavior; do not pass `--delete-branch=false`).
- **The main checkout `C:/Claude/whisperdesk` stays on `master`. Never run `git checkout`, `git switch`, or `git checkout -b` there.** All branch work happens in a worktree: use `EnterWorktree`, or `git worktree add .claude/worktrees/<name> -b <name> origin/master`. This applies to every session, not just `/issue-claude` runs.
  Why: run artifacts are written to `<main>/.omo/runs/`, and a feature branch checked out in the main checkout hides every file that branch predates. It has happened twice. The second time a just-merged `docs/` file and the tracked `.omo/issue-runner-prompt.md` vanished from disk, which reads exactly like data loss even though nothing was lost; the first time it went unnoticed for two days. A `post-checkout` hook warns when it happens, and `scripts/verify_self_audit.py` blocks on it.
  If you find the main checkout already on another branch, do not switch it back blind — another session may have uncommitted work there. Check `git -C <main> status` first, and say what you found.
- **`EnterWorktree` does not fetch, and does not necessarily branch off `origin/master`.** It can base a new worktree on whatever the main checkout currently has checked out. Always `git fetch origin`, then verify: `git rev-parse HEAD` against `git rev-parse origin/master`, and reset or rebase if they differ.

## Hook setup (once per clone)

```
git config core.hooksPath .githooks
```

Shared across every worktree via `.git/config`. Currently provides the `post-checkout` warning above.

## Writing style (commits, PRs, docs, comments)

- No em or en dashes; use commas, periods, parentheses, colons.
- Write plainly. Avoid AI-typical phrasing ("not just X but Y", adjective stacks, "robust/seamless/leverage/delve").

## Testing

- Match test cost to change blast radius; see AGENTS.md "Testing tiers" — don't run full browser e2e for every small change.
- Any user-visible UI change (text, control, label, role) changes what e2e tests select by; grep the e2e/test directories for the old text or role and update selectors in the same change.
- A green local run proves only the layer you ran. Do not claim behavior works from unit tests alone when the change has a runtime surface; drive the affected flow (scope the check to the changed flow, not the full suite).
