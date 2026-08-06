# Token usage, issue #340 run

Zero `Agent()` calls. No Sonnet investigate agent, no Fable completion-race
check, no Haiku mechanical edits, no Sonnet test agent.

The run stopped at Phase 0's prior-work check, which is the cheapest step in
the workflow, before any delegation happens. All work was inline on Opus:
four `gh` calls, one `git log`, one `git show`, four `Read` calls, three
`Grep` calls.

This is the intended outcome when Phase 0 catches a duplicate. The whole
Phase 1 through Phase 4 budget, which is where every `Agent()` call lives,
was never spent.
