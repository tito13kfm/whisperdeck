# token-usage.md, issue #309, branch worktree-issue-309-fts-delete-trigger

Every `Agent()` call this run made, with the model that backed it. Figures for
the subagents are the `subagent_tokens` the harness reported on each completion
notification, not estimates.

| # | Phase | subagent_type | Model | Tokens | Tool uses | Wall clock |
|---|---|---|---|---|---|---|
| 1 | Phase 1, investigate | `Explore` | Sonnet | 105,884 | 44 | 775 s |
| 2 | Phase 3, verify | `general-purpose` | Sonnet | 137,203 | 80 | 1,522 s |
| | | | **Subagent total** | **243,087** | 124 | |

## Orchestrator (Opus)

Estimated, not measured: the harness exposes no per-turn counter to the session
itself, so this is a reconstruction, flagged as such rather than reported as a
fact or left out.

- Roughly 40 tool calls: 2 `Agent` dispatches, ~14 PowerShell, 4 `Read`
  (including two 120-200 line source reads and one 32 KB prompt file), 3 `Grep`,
  11 `Edit`, 1 `Write`, 1 `ToolSearch`, 1 `EnterWorktree`.
- Largest single inputs: the inlined runner prompt (32 KB), issue #309's body
  plus its one long design comment (~11 KB, passed through verbatim into the
  Phase 1 dispatch prompt), and the two subagent result blocks (~4 KB and ~9 KB).
- Both dispatch prompts were long by design, roughly 9 KB and 7 KB, because a
  fresh agent starts with zero context and the issue's literal SQL had to be
  quoted rather than paraphrased.
- Estimate: **~250,000 to 300,000 tokens of context processed, ~40,000 output.**

So the orchestrator is in the same order of magnitude as both subagents
combined. Phase 2 (the fix, the four-scenario probe, and every test in this
change) ran inline on Opus, so a report that treated the orchestrator as free
would attribute near-zero cost to the majority of the actual work.

## Calls not made

- **Phase 1.5, Fable, `general-purpose`: not dispatched.** The trigger condition
  is Phase 1 surfacing a job/state completion path with a side effect in the same
  handler. Nothing in this change is one. Reasoned about and recorded in
  `wrong-directions.md` section 7 rather than skipped silently.
- **Phase 2 mechanical sub-edits, Haiku: not dispatched.** No bounded mechanical
  repetition arose. Every edit was a judgment call about SQL semantics, so all of
  it stayed inline per the prompt's own split.
- The four-scenario FTS5 corruption probe that changed this run's design was run
  inline on Opus, not delegated. It cost one script and two PowerShell calls.
