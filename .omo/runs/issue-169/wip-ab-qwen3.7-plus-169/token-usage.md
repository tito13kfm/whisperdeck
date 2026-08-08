# Token Usage - Issue #169 (qwen3.7-plus variant)

## Agent Dispatches

### Phase 1: Investigation (2 local agents, at cap)
1. **bg_ff215971** - explore agent (local, Lemonade Qwen3.5-4B) - 5m 40s
   - Task: Investigate LlmJob queue and classify_intent
   - Result: Complete architecture map with file paths, line numbers, call graph
   - Token cost: ~50K input, ~15K output (estimated)

2. **bg_e0365a4c** - explore agent (local, Lemonade Qwen3.5-4B) - 38m 20s (aborted)
   - Task: Investigate transcript model and UI surfaces
   - Result: Aborted due to timeout
   - Token cost: ~100K input, ~5K output (estimated, wasted)

3. **bg_79d5cd03** - explore agent (local, Lemonade Qwen3.5-4B) - 14m 52s
   - Task: Re-fire transcript/UI investigation (narrower scope)
   - Result: Complete transcript model, UI surfaces, format templates
   - Token cost: ~80K input, ~20K output (estimated)

**Lesson**: Second agent aborted on first try (broad scope). Re-fired with same prompt, succeeded. Total local agent time: ~59 minutes. Should have scoped more narrowly from the start.

### Phase 2: Planning (1 cloud agent)
4. **ses_05e123502ffebWUCPnW0nufJNO** - plan agent (cloud, opencode-go/deepseek-v4-pro) - 3m 4s
   - Task: Create detailed implementation plan
   - Result: 5-task plan with dependency graph, parallel execution waves, exact file changes
   - Token cost: ~30K input, ~25K output (estimated)
   - Note: Plan agent used deepseek-v4-pro, not qwen3.7-plus (parent model)

### Wave 1: Implementation (2 cloud agents in parallel)
5. **bg_18a3081c** - Sisyphus-Junior (cloud, unspecified-high category) - 2m 12s
   - Task: Database schema + settings
   - Model: Unknown (unspecified-high category, likely cloud)
   - Result: Added voice_note_json column, VALID_KINDS, settings defaults
   - Token cost: ~40K input, ~10K output (estimated)

6. **bg_a46566ef** - Sisyphus-Junior (cloud, deep category) - 5m 34s
   - Task: LLM prompts + service functions
   - Model: Unknown (deep category, likely cloud)
   - Result: Implemented classify_note_type, structure_note, enqueue_auto_voice_note, run_llm_job branches
   - Token cost: ~60K input, ~20K output (estimated)

### Wave 2: Implementation (1 cloud agent)
7. **bg_10c54318** - Sisyphus-Junior (cloud, deep category) - 6m 42s
   - Task: Backend endpoints + integration
   - Model: Unknown (deep category, likely cloud)
   - Result: Added voice_note kind validation, diarize override, serializer, voice-notes endpoints, gating
   - Token cost: ~80K input, ~30K output (estimated)

### Wave 3: Implementation (2 cloud agents in parallel)
8. **bg_01b3ec6a** - Sisyphus-Junior (cloud, unspecified-high category) - 5m 46s
   - Task: Voice-note test suite
   - Model: Unknown (unspecified-high category, likely cloud)
   - Result: Added 28 tests across test_reformatting.py, test_voice_notes.py, test_llm_jobs.py
   - Token cost: ~70K input, ~25K output (estimated)

9. **bg_b28c3f81** - Sisyphus-Junior (cloud, visual-engineering category) - 7m 20s
   - Task: Voice-note board frontend UI
   - Model: Unknown (visual-engineering category, likely cloud)
   - Result: Added voice-notes board to rack.js with navigation, cards, filtering, polling
   - Token cost: ~90K input, ~35K output (estimated)

## Total Estimated Token Cost
- Local agents (Lemonade): ~230K input, ~40K output (3 agents, 1 aborted)
- Cloud agents (OpenRouter): ~500K input, ~180K output (6 agents)
- Total: ~730K input, ~220K output = ~950K tokens

## Where Token Usage Was Worst

1. **Phase 1 investigation**: 59 minutes of local agent time, 1 aborted agent. Should have scoped transcript/UI investigation more narrowly from the start.

2. **Wave 3 frontend**: 7m 20s, largest single agent. Vanilla JS with inline HTML templates is verbose. Could have delegated smaller chunks (nav item, card rendering, polling separately).

3. **Wave 2 backend**: 6m 42s. Touched many files (app.py, queue.py, serializer, endpoints, gating). Could have split into "serializer + endpoints" and "gating + queue integration" as two parallel agents.

## What Would Cut Token Usage Next Time

1. **Scope Phase 1 agents more narrowly**: Instead of "investigate transcript model and UI surfaces", split into "read database/__init__.py Transcript model" and "read static/rack.js format tab rendering". Two narrower agents finish faster than one broad agent that times out.

2. **Don't re-fire aborted agents with same prompt**: If an agent aborts, the prompt was too broad. Rewrite it narrower before re-firing.

3. **Split Wave 3 frontend into smaller chunks**: Nav item + badge (1 agent), card rendering (1 agent), detail view + polling (1 agent). Three parallel agents finish faster than one monolithic frontend agent.

4. **Use codegraph_explore more in Phase 1**: Could have replaced 1-2 explore agents with direct codegraph queries for known files (database/__init__.py, services/llm_jobs.py).

5. **Skip plan agent for well-scoped features**: The plan agent took 3 minutes and produced a good plan, but the investigation already had enough detail to decompose directly. For features with clear scope (new kind, new endpoints, new UI), skip plan and go straight to implementation.

## Transparency: Model Routing

- **Parent model**: opencode-go/qwen3.7-plus (this session)
- **Plan agent**: opencode-go/deepseek-v4-pro (via category: unknown)
- **All other agents**: Model unknown (categories: unspecified-high, deep, visual-engineering). These are cloud-billed via OpenRouter, not local Lemonade.

Per AGENTS.md, category-to-model mapping is in ~/.config/opencode/oh-my-openagent.json and changes often. Did not hardcode model names in prompts per instructions.

## Cost Breakdown (Estimated)

- Local agents (Lemonade): Free (runs on local GPU)
- Cloud agents (OpenRouter): ~$0.50-1.00 estimated (6 agents, ~680K tokens total at ~$1-2/M tokens average)
- Exact cost: Check OpenRouter usage panel (not readable from this session)
