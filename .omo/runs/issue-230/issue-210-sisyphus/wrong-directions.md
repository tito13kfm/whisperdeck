# Wrong directions

## #230 is a PR, not an issue

`/issue 230` was invoked, but #230 is an open PR (GitHub shares issue/PR
numbering), not a standalone issue. `gh issue view 230` returns the PR's
info. The real feature issue is #210, which PR #230 closes.

**Recommended fix to the prompt:** Phase 0 should check `gh pr view <N>` in
addition to `gh issue view <N>`. If the number resolves to a PR, the workflow
should switch to audit/verify mode rather than create-a-new-PR mode. The
current Phase 0 logic only distinguishes tracking issues from standalone
issues, not PRs from issues.
