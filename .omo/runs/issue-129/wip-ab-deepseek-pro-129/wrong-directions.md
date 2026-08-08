# Wrong Directions — Issue #129 (deepseek-pro variant)

## 1. `explore-hard` agent does not exist

**Source**: AGENTS.md model table + issue-runner-prompt.md both refer to `explore-hard` as a distinct agent. The prompt says: "`explore` — straightforward lookups. `explore-hard` — anything requiring actual reasoning."

**Reality**: Calling `task(subagent_type="explore-hard", ...)` fails with "Unknown agent: 'explore-hard'." The available agents list includes `explore` but not `explore-hard`. 

**Impact**: Had to fall back to `explore` for both Phase 1 agents. The sibling-sweep agent was asked to do reasoning work (analyze race conditions across patterns) that `explore-hard` would have been better suited for.

**Recommended fix**: Either register `explore-hard` as a subagent_type (it exists as a concept in AGENTS.md but not in the agent registry), or update AGENTS.md and the issue-runner-prompt.md to remove references to `explore-hard` and describe how to get reasoning-quality results from the `explore` agent alone.

## 2. `scout` IS in the available agent list (contrary to AGENTS.md)

**Source**: AGENTS.md says "Earlier versions of this doc referred to scout and plan as separate agents; those keys don't exist in the current config."

**Reality**: The error message for unknown agents lists `scout` as an available agent: "Available agents: ... scout ..."

**Impact**: None (didn't try to use scout). But AGENTS.md's claim is outdated.

**Recommended fix**: Update AGENTS.md to reflect the current state — `scout` exists but `explore-hard` doesn't (if that's the config's actual state). Or vice versa if the config is what needs updating.

## 3. Issue line number (2338) is stale

**Source**: Issue body says "File: static/rack.js:2338 loadTranscriptDetail()"

**Reality**: `loadTranscriptDetail()` definition is at line 2372 in current code (38-line drift).

**Impact**: Minimal — codegraph_explore found it immediately by name. But confirms the prompt's warning: "Issue bodies in this tracking system have a track record of being stale or incomplete."

**Recommended fix**: None (this is expected drift). The prompt already warns about this.

## 4. Non-awaiting callers of loadTranscriptDetail are fire-and-forget

**Observation**: 6 of 14 call sites call `loadTranscriptDetail()` without `await` (lines 437, 3409, 3415, 3693, 3722, 3733). If the guard triggers (S.detailId !== id), the function returns silently — but since the caller didn't await, it can't observe the return anyway.

**Is this a problem?** No. The fire-and-forget callers are either:
- The navigation path (line 437) where `loadTranscriptDetail` is always the latest intent
- Job-start actions where the user just queued a background job — the detail page will poll and refresh anyway

The guard just prevents the stale response from corrupting `detailData`, which is exactly what we want.
