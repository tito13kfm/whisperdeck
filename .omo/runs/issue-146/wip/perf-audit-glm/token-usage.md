# Token usage — issue #146

## Decisions made up front (per prompt's "apply what's already known")
- Static source-level contract check before any live server start. No throwaway server-start cycle this run.
- codegraph not used: index lives on main workspace C:/Claude/whisperdesk, worktree is on a branch — codegraph would surface master source, fine for read-only structure but Phase 1 needs current-file reads in the worktree. Used 2 parallel `explore` agents on the worktree files directly instead.
- `from_end=true` will be used when collecting background agent output.

## Where token use was worst
- **Phase 1 explore agents (both bg tasks)**: each took 2-3 min on Lemonade. Each returned ~25-line answer but the framework wrapped each turn in `<analysis>` blocks. `from_end=true` was used on collection, so we only paid for the synthesized answer + a small framing overhead. Total Phase 1 wall clock ~3.5 min (one ran slightly ahead of the other). Without `from_end=true` the bg_dd449895 result alone had ~25 framework-injected analysis blocks (~3-5K tokens of noise). Worth skipping.
- **Investigation.md writeup**: ~1800-token file written by the orchestrator inline. Could have been delegated to a `writing` agent (local, Bonsai-8B) but the orchestrator had the full context hot, so the inline write probably saved tokens vs round-tripping the context to a local agent with a fresh 4-260K-byte context budget. No action.
- **Verification round**: cheap. Static source-level check + 8 pytest tests in 0.47s, no server-start cost. Per the prompt's known-good rule (1), I skipped throwaway server-start/auth/upload cycles entirely. Saved an estimated 5K-20K tokens vs a live-cycle tier.

## What would cut it next time
1. **Two explore agents was right; the prompts could be narrower.** bg_5ffcc975 (frontend assets) read index.html fully AND grepped rack.js for serviceWorker AND read first 30 lines. The "first 30 lines" read added ~600 tokens for information I didn't end up using (I only needed registration point + asset list). Narrower prompt: drop point #5 (rack.js structure).
2. **Skip "stop when" verbose params.** Both prompts had explicit STOP WHEN clauses; the agents followed them but added 50-100 tokens of preamble restating them. Minor.
3. **codegraph not usable in worktree.** The codegraph index lives on the main workspace path; the worktree is on a branch so codegraph would have surfaced master source. I used direct `grep`/`read` for the few quick lookups after the explore agents, which was appropriate (AGENTS.md codegraph rule allows direct read for known files/lines). For the next issue-runner on a similar standalone-issue scope, pure direct-read + grep would likely beat the 2-agent Phase 1 in total wall-clock and tokens.
4. **No agent dispatch for Phase 2**: scope was 4 files (3 created, 1 modified), fully understood from Phase 1. Inline edits saved the ~2-min round-trip that a `deep`/`ultrabrain` category call would have cost.
5. **No agent dispatch for Phase 3**: static check via pytest + reading sw.js source == covered by tests. No browser tier needed (no Playwright MCP detected in this session; the e2e-regression-http skill is a browser flow not a text-reasoning call, and would have been overkill for a 4-file static-asset SW). Called out explicitly per the prompt's "say so rather than silently skip" rule.