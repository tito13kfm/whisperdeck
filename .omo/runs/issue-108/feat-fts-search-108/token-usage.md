# Token Usage — Issue #108 FTS Search

## Approach

All 10 tasks implemented directly by Sisyphus (no subagent delegation for implementation). The plan at `.omo/plans/issue-108-fts-search.md` was a complete, unambiguous specification with exact code patterns — per the delegation exception, implementing directly was the correct choice over dispatching agents.

## Sub-sessions spawned

| Agent | Model | Cloud/Local | Purpose | Cost share |
|-------|-------|-------------|---------|-----------|
| oracle (ses_054aebf8bffeyxdAW0XqQYfaJF) | meta/muse-spark-1.1 | Cloud | F6 regression review of full diff | ~1 call |

No other subagents spawned. All investigation was done through codegraph_explore (pre-indexed, zero token cost for lookups) and direct Read calls.

## Token usage notes

- **codegraph_explore**: 5 calls for initial source survey. Efficient — returned verbatim source with call graphs in single round-trips. No budget truncation gaps encountered.
- **Direct Read**: ~12 calls for targeted file sections (test files, specific function ranges). Reasonable.
- **Oracle**: 1 call with full file contents + diff (~745 lines). Worth the ~$0.03 cost for catching 6 findings.
- **Global search (grep)**: 3 calls for import/caller lookups. Could have used codegraph for these but grep was faster for simple text matches.

## What would cut tokens next time

1. All exploration fits within codegraph — no explore agents needed for this size project (104 files indexed).
2. The FTS5 contentless-vs-external-content confusion cost ~5 round-trips of trial-and-error. A quick SQLite docs check before implementing would have caught this.
3. The test-debug cycle for trigger behavior used ~3 temporary scripts. Building a reusable FTS5 test helper would eliminate these.
