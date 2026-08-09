---
description: "Run the full issue workflow end to end (resolve, investigate, fix, test, PR). Works on a standalone issue or a tracking issue (auto-picks the next open item). Usage: /issue <issue-or-tracking-number>. Pass ONLY the number, nothing else on the line, anything past the first token gets treated as part of the number and breaks resolution. Send any override/special instructions as a separate follow-up message after this one."
argument-hint: <issue-number-only-nothing-else>
agent: sisyphus
---

<command-instruction>
Target issue number given: $ARGUMENTS

If the text above contains anything beyond a single number (extra words,
an override, a sentence), that's a misuse of this command, the caller put
override text on the same line instead of sending it as a follow-up
message. Extract the leading number as the real target, treat everything
else as a special instruction for this run and apply it, but flag in your
first status update that the invocation was malformed and the fix is to
send overrides as a separate message next time.

Canonical workflow (single source of truth lives at `.omo/issue-runner-prompt.md`,
inlined below so this command never drifts out of sync with it):

!`cat "$(git rev-parse --path-format=absolute --git-common-dir)/../.omo/issue-runner-prompt.md"`

Everywhere the text above says `<N>` or refers to "the issue number you were
given", substitute the leading number extracted above. Start at Phase 0.
</command-instruction>
