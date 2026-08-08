# wrong-directions.md — issue #346, branch issue-346-sisyphus

## issue-runner-prompt.md: `deep` subagent_type no longer exists
The prompt says Phase 1/2 use `deep` as a subagent_type. The tool returns "Unknown agent: deep". 
`deep` only exists as a `category`, mapping to Sisyphus-Junior. Fix: update the prompt to use `category="deep"` 
instead of `subagent_type="deep"`. This is the same fix needed for every phase that says "use deep".

## Verify/workaround:
Used `task(category="deep", ...)` — works.

## issue-runner-prompt.md: `explore` as subagent_type
The prompt says Phase 1 uses `explore` for straightforward lookups. The tool may also reject this as a subagent_type. 
Try `subagent_type="explore"` if needed, otherwise use category routing.
