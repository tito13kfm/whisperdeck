# Token usage: Issue #175 — wip/ab-deepseek-pure-175

## Agents spawned
- **Orchestrator model**: deepseek-v4-pro (OpenRouter). All work done directly — no subagents spawned.
- **Zero background agents**: investigation used `codegraph_explore` (3 calls), `grep` (3 calls), `read` (6 calls). No explore/librarian/plan/oracle dispatches.
- **No local agents consumed**: total Lemonade VRAM load = 0 for this run.

## Cost drivers
1. **Corrupted edit recovery (2 round-trips)**: The `else:` replacement matched the wrong location in `services/llm_jobs.py` twice. Required re-reading damaged sections and re-editing. ~15% of total turns.
2. **Large file reads**: `services/llm_jobs.py` (698 lines) and `database/__init__.py` (439 lines) read in full or near-full. These are large single files with many symbols.
3. **codegraph_explore truncation**: The first two `codegraph_explore` calls returned truncated source (budget limit). The relevant functions were still visible in the non-truncated portions, so no retries needed.

## What worked well
- `codegraph_explore` with multi-symbol queries (`search_transcripts`, `run_llm_job`, `LlmJob transcript_id`) returned comprehensive context in 3 calls instead of 10+ file reads
- Direct `grep` for sibling sweeps was efficient — `transcript_id.*None` pattern found all relevant call sites in one query
- The plan was detailed enough that no design decisions were needed during implementation
- Batching all edits for a file before re-reading was attempted, but the `else:` collision required a re-read cycle

## Improvement opportunities
- When replacing generic keywords like `else:` in large files, use 5+ lines of context or a unique surrounding pattern
- The `ensure_nullable_llm_job_transcript_id()` migration function verbatim recreates the full SQL schema — alternative: use `SELECT sql FROM sqlite_master` to extract the current CREATE TABLE statement and modify only the targeted column
- Test file was written in one pass and all 24 tests passed on first run (except one test that assumed directory creation failure when it succeeded on Windows — fixed in one edit)
