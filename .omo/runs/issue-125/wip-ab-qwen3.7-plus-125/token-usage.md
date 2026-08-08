# Token Usage - Issue #125 (qwen3.7-plus variant)

## Agent Dispatches

### Phase 1 (Investigation) - Local Lemonade agents (explore)

1. **Find register route and create_user** (bg_7db8ad2c)
   - Agent: explore (local Lemonade, Qwen3.5-4B-MTP-GGUF)
   - Duration: 2m 56s
   - Purpose: Read app.py register route and services/auth.py create_user function
   - Result: Found register at app.py:403-422, create_user at auth.py:44-56

2. **Find all create_user callers** (bg_18666a5c)
   - Agent: explore (local Lemonade, Qwen3.5-4B-MTP-GGUF)
   - Duration: 3m 9s
   - Purpose: Enumerate all call sites of create_user
   - Result: Found 2 call sites (app.py:419, auth.py:75)

3. **Find IntegrityError handlers** (bg_210ec314)
   - Agent: explore (local Lemonade, Qwen3.5-4B-MTP-GGUF)
   - Duration: 7m 44s
   - Purpose: Search for existing IntegrityError exception handlers
   - Result: None found in project source

4. **Find check-then-insert patterns** (bg_b64bf181)
   - Agent: explore (local Lemonade, Qwen3.5-4B-MTP-GGUF)
   - Duration: 2m 55s
   - Purpose: Find sibling SELECT-then-INSERT patterns (Complement Rule sweep)
   - Result: Found 4 siblings (hotwords, voice_id, llm_jobs, summarize)

**Total local agent time:** ~16 minutes (4 agents, max 2 concurrent)

### Phase 2 (Implementation) - Direct

No agent delegation. Trivial single-file change (add import, wrap one call in try/except). Implemented directly per delegation exception for mechanical transcription of complete plan.

### Phase 3 (Testing) - Direct

No agent delegation. Ran pytest directly.

## Token Waste

1. **Agent name error:** Tried `explore-hard` agent, got "Unknown agent" error. Had to retry with `explore`. Wasted one agent dispatch cycle (~30s).

2. **Test file location confusion:** Initially tried to run tests from worktree, but worktree has no .venv. Had to apply fix to main repo's app.py to run tests. This was necessary but adds complexity.

3. **Explore agent verbosity:** Agents spent significant time on file discovery (glob failures, PowerShell errors) before finding target files. Prompt could be more specific about file paths.

## What Would Cut Token Usage

1. **Use codegraph_explore first.** Investigation could have been done in 1-2 codegraph calls instead of 4 explore agents. Codegraph returns verbatim source with line numbers, no file discovery overhead.

2. **Specify file paths in agent prompts.** Instead of "find create_user in app.py", say "read app.py lines 400-430 and services/auth.py lines 40-60". Eliminates search overhead.

3. **Skip sibling sweep for simple fixes.** The 4th agent (check-then-insert patterns) found siblings but they're out of scope for this issue. Could have skipped and noted "siblings exist but separate issue" in investigation.md.

## Cost Estimate

- 4 local explore agents: ~0 cost (Lemonade is free, runs on local GPU)
- Direct implementation: minimal tokens
- Direct testing: minimal tokens
- **Total:** Low cost, mostly local GPU time (~16 minutes)
