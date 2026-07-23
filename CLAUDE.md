# WhisperDeck — Claude Code instructions

Read `AGENTS.md` and follow **The Complement Rule** section — it exists because three PRs in a row shipped guards that missed sibling entry points. The Serena Usage and Advisor Escalation sections of AGENTS.md are Opencode-specific; ignore them in Claude Code (use your own configured tools).

## Git and PR hygiene

- Never advertise AI authorship: no `Co-Authored-By: Claude` trailers, no "Generated with Claude Code" footers, no session links in commit messages or PR bodies. Commit as the repository's configured git user.
- Never commit directly to `master`. Branch, open a PR, merge only after review and green CI.
- Delete the feature branch when merging a PR (default `gh pr merge` behavior; do not pass `--delete-branch=false`).

## Writing style (commits, PRs, docs, comments)

- No em or en dashes; use commas, periods, parentheses, colons.
- Write plainly. Avoid AI-typical phrasing ("not just X but Y", adjective stacks, "robust/seamless/leverage/delve").

## Testing

- Match test cost to change blast radius; see AGENTS.md "Testing tiers" — don't run full browser e2e for every small change.
- Any user-visible UI change (text, control, label, role) changes what e2e tests select by; grep the e2e/test directories for the old text or role and update selectors in the same change.
- A green local run proves only the layer you ran. Do not claim behavior works from unit tests alone when the change has a runtime surface; drive the affected flow (scope the check to the changed flow, not the full suite).
