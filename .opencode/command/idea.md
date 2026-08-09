---
description: "Interrogate a raw idea (bug, feature, or anything else worth tracking), challenge it against existing design/decisions, search code and GitHub for prior art, and file GitHub issue(s) shaped for /issue's Phase 0 to trust. Usage: /idea <optional one-line idea>, or just /idea to start the intake dialogue from scratch."
argument-hint: <optional-one-line-idea>
agent: sisyphus
---

<command-instruction>
Idea given (may be empty): $ARGUMENTS

Canonical workflow (single source of truth lives at `.omo/idea-runner-prompt.md`,
inlined below so this command never drifts out of sync with it):

!`cat "$(git rev-parse --show-toplevel)/.omo/idea-runner-prompt.md"`

If the idea given above is non-empty, treat it as the seed input for
Phase 0's intake and start there. If empty, start Phase 0 by asking the
user what the idea is. Start at Phase 0 either way.
</command-instruction>
