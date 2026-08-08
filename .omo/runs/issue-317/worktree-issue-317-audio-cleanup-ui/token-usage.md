# token-usage.md — issue #317, branch `worktree-issue-317-audio-cleanup-ui`

Orchestrator: Opus 5, inline for Phase 0, Phase 2 (all of it, no mechanical
sub-edits were delegated), Phase 3's browser drive, Phase 3.5, Phase 4, Phase 5.

Delegated `Agent()` calls this run, in order:

| # | Phase | Model | `subagent_type` | Purpose |
|---|---|---|---|---|
| 1 | Phase 1 (investigate) | **Sonnet** | `Explore` (read-only) | Traced the settings round trip end to end, enumerated the twelve literal `cleanup_*` keys with defaults and read sites, ran the sibling sweep, found the committed-bundle hazard. Wrote `investigation.md`. 48 tool uses. |
| 2 | Phase 1.5 (completion-race check) | **Fable** | `general-purpose` | The mandatory second opinion on whether a completion path guards only on `"cancelled"`. Scoped to `_run_chunk_job` and `_finalize_if_done` in `services/queue.py`. Found a real pre-existing bug (see `self-audit.md`). One call, no retry needed. 8 tool uses. |

No Haiku call was made: Phase 2 had no bounded purely-mechanical sub-edit to
delegate. The `rack.js` change is one coherent panel plus its wiring, which is
judgment work, and the `services/queue.py` change is three lines. Splitting
either across an agent boundary would have cost more than it saved.

No `fork` subagent was used, per the workflow: a fork ignores the `model`
override, and both delegated phases needed a specific model.

Advisor calls (not `Agent()`, the separate reviewer tool): 1, at the
approach-commit gate, before any code was written. It cut a planned server-side
bounds registry as out-of-scope, blocked the `services/queue.py` edit pending a
mirror-path `full_text` verification (which came back clean), flagged that two
`.tog` idioms exist in `rack.js` and the CSS-driven one was correct for new
controls, and called for a no-op bundle rebuild before any source edit to prove
the esbuild version matched. All four were acted on.
