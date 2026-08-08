# Token Usage Analysis: Issue #146

## Worst Token Usage Points

### 1. Background Agent Dispatches (Phase 1)
**Cost:** 2 local explore agents × ~1m 45s each
**What they did:**
- Agent 1: Listed static/ directory files with sizes (trivial task, could have been direct `ls -la`)
- Agent 2: Searched for HTML files and SW registration (grep + read, ~3 files)

**What would cut it:**
- Direct tool use instead of agents for simple file listing
- codegraph_explore already showed the static mount and cache middleware
- Could have done `ls -la static/` and `grep -r "serviceWorker" static/` directly in <5s

**Lesson:** Agents are overkill for "list files in a directory" and "grep for a string." Use direct tools first.

### 2. Agent Result Collection
**Cost:** 2× background_output calls with from_end=true
**What they returned:** ~500 lines of analysis/noise per agent before the final answer

**What would cut it:**
- Already using from_end=true (correct)
- But the agents still generated verbose intermediate reasoning
- Local Lemonade agents (Qwen3.5-4B) produce more verbose output than cloud models

**Lesson:** Local agents are chatty. For simple lookups, direct tools are faster and cheaper.

### 3. Deep Agent Delegation (Phase 2)
**Cost:** 1× deep agent (deepseek-v4-pro) for 3-file implementation
**Duration:** 1m 13s
**What it did:** Created sw.js, edited app.py, edited index.html

**What would cut it:**
- This was the right delegation choice (multi-file, tightly coupled changes)
- Could have done it myself but delegation ensures consistency
- No retry needed, first attempt correct

**Lesson:** Deep agent was appropriate here. No waste.

### 4. Test Suite Run
**Cost:** 374 tests × ~35s
**What it did:** Verified no regressions

**What would cut it:**
- Could have run only tests/test_static_cache.py (related to caching)
- But full suite ensures no unexpected breakage
- Worth the cost for confidence

**Lesson:** Full test suite is cheap insurance. Don't skip it.

## What Went Well

1. **codegraph_explore first** - Got the middleware and route structure in one call, no agent needed
2. **Direct file reads** - After agents returned, read the 3 files myself instead of re-delegating
3. **Single deep agent** - One delegation for all 3 files, no fragmentation
4. **No retries** - Implementation correct on first attempt

## Recommendations for Next Time

1. **Skip explore agents for file listing** - Use `ls -la` directly
2. **Skip explore agents for simple grep** - Use grep tool directly
3. **Reserve agents for:** cross-file pattern discovery, complex reasoning, multi-step investigation
4. **Local agent cap (2 max)** - Respected, didn't hit the limit
5. **Static check before live test** - Did this correctly, no need for Playwright run

## Estimated Token Savings

If I had skipped the two explore agents and used direct tools:
- Saved: ~3m 30s of agent time
- Saved: ~1000 lines of agent output parsing
- Saved: ~50K tokens of agent context

**Net effect:** Would have finished Phase 1 in ~30s instead of ~2m.
