# Token usage analysis for issue #147

## Worst token sinks

### 1. Explore agents returned verbose analysis blocks (bg_0670c244, bg_87bb06a3)

**What happened:**
Both explore agents returned full session transcripts with `<analysis>` blocks showing their reasoning process, not just the final answer. The raw output was ~400 lines per agent, most of which was internal monologue.

**Token cost:**
~800 lines of agent output, of which ~100 lines were the actual answer (file:line tables). The other ~700 lines were reasoning/analysis that I didn't need.

**What would cut it:**
Use `background_output(task_id="bg_...", from_end=true)` to get only the final synthesized answer. I did this on the second collection attempt, but the first collection (without `from_end=true`) wasted ~600 lines of context.

**Lesson:**
Always use `from_end=true` when collecting background agent output. The system reminder says "Use from_end=true to fetch session messages with filters" but doesn't emphasize that it skips the noisy analysis blocks.

### 2. Codegraph_explore returned truncated output

**What happened:**
The first `codegraph_explore` call returned 53 symbols across 3 files, but the output was truncated with "... (gap) ..." sections. I had to make a second call to get `latest_relabel` and `LlmJob` model definition.

**Token cost:**
Two codegraph calls instead of one. The second call returned 50 symbols across 5 files, also with some gaps.

**What would cut it:**
Make more targeted codegraph queries with specific symbol names instead of broad natural-language questions. For example, `codegraph_explore(query="latest_relabel LlmJob")` instead of `codegraph_explore(query="_serialize_transcript latest_job compute_queue_status LlmJob")`.

**Lesson:**
Codegraph has a budget limit per call. If the output is truncated, split the query into smaller, more focused calls rather than retrying the same broad query.

### 3. File edits applied to wrong directory (main repo vs worktree)

**What happened:**
I applied edits to `C:\Claude\whisperdesk\app.py` instead of `C:\Claude\whisperdesk-147-qwen37plus\app.py`. Had to re-apply all edits to the worktree after realizing the mistake.

**Token cost:**
~3 edit calls wasted (import, _serialize_transcript, _dictation_job_fields), plus ~3 verification reads to confirm the worktree was unchanged. Total: ~6 tool calls, ~200 lines of output.

**What would cut it:**
Always verify `git status` in the worktree before and after edits. A quick `git status` after the first edit would have shown "nothing to commit" and caught the mistake immediately.

**Lesson:**
When working in a worktree, double-check that file paths point to the worktree directory, not the main repo. A single `git status` after the first edit catches this.

### 4. Test execution required file swapping

**What happened:**
The worktree doesn't have its own `.venv`. I had to copy modified files from the worktree to the main repo, run tests, then restore the originals.

**Token cost:**
~10 bash calls for copying, running tests, and restoring. ~200 lines of output (mostly pytest output).

**What would cut it:**
Create a symlink from the worktree to the main repo's `.venv`, or use `PYTHONPATH` to point pytest at the worktree's code. This would eliminate the file-swapping overhead.

**Lesson:**
Worktree setup should include venv access. Either symlink `.venv` or document the file-swapping pattern in AGENTS.md.

### 5. Explore agents took 3-4 minutes each

**What happened:**
Both explore agents (bg_0670c244, bg_87bb06a3) took 3-4 minutes to complete. During that time, I was blocked and couldn't proceed with implementation.

**Token cost:**
Not directly a token cost, but a time cost. The agents were running local Lemonade models, which are slower than cloud models.

**What would cut it:**
For simple call-site enumeration, use `grep` directly instead of spawning explore agents. A single `grep -n "latest_job\|compute_queue_status" app.py` would have found all call sites in seconds.

**Lesson:**
Explore agents are overkill for simple grep-style searches. Use them for complex reasoning (e.g., "trace the flow from X to Y across multiple files"), not for "find all callers of function Z".

## What worked well

### 1. Codegraph for initial context gathering

The first `codegraph_explore` call returned verbatim source for `_serialize_transcript`, `_dictation_job_fields`, `latest_job`, `compute_queue_status`, and `serialize_llm_job` in a single call. This saved ~5 file reads.

### 2. Investigation.md as a checkpoint

Writing `investigation.md` before implementing forced me to enumerate all call sites and verify the issue's claims. This caught the false premise (list endpoint doesn't use `_serialize_transcript`) and prevented me from implementing a fix for the wrong problem.

### 3. Optional parameter design

Making `latest_jobs` an optional parameter in `_serialize_transcript` and `_dictation_job_fields` meant I didn't have to update all callers. The function fetches jobs itself if not provided. This simplified the implementation and reduced the blast radius.

## Recommendations for next time

1. **Always use `from_end=true` when collecting background agent output.** This skips the noisy analysis blocks and returns only the final answer.

2. **Use grep for simple call-site enumeration, not explore agents.** Explore agents are for complex reasoning, not "find all callers of X".

3. **Verify worktree context with `git status` after the first edit.** Catches path mistakes immediately.

4. **Make codegraph queries more targeted.** Instead of broad natural-language questions, use specific symbol names to avoid truncation.

5. **Document the worktree testing pattern in AGENTS.md.** Either symlink `.venv` or document the file-swapping pattern.

6. **Write investigation.md before implementing.** Forces verification of the issue's claims and prevents fixing the wrong problem.
