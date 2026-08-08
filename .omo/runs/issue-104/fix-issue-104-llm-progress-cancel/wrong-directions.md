# Wrong Directions — Issue #104

No significant wrong directions encountered. The issue's root cause diagnosis was accurate, and the Option A fix direction was correct. The only finding:

## Issue text understated scope
The issue's "How to reproduce" section says "Check /api/jobs response" but the stale progress also affects transcript detail serialization (every transcript serialization embeds job objects via serialize_llm_job), the Queue screen, and the cancel endpoint's immediate response. The fix at the data layer (cancel_llm_job + _finish) covers all of these end-to-end, which is the right approach.

## AGENTS.md stale: local agent list
AGENTS.md's "Agents that do NOT need Lemonade" table lists `atlas` and `writing` as local — per the live config this is still accurate. No discrepancy.

Recommend fix: none needed, this was a clean one.

## Oracle model routing note
The oracle agent was dispatched as a subagent with `meta/muse-spark-1.1` backing it. The issue-runner-prompt says oracle costs ~$0.02 — this is probably accurate for the actual model used.
