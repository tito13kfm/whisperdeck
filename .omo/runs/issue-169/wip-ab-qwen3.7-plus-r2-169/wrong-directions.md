# Wrong Directions: Issue #169

## Agent Configuration

**Issue**: AGENTS.md line 127 lists `atlas`, `quick`, `writing`, and `unspecified-low` as OpenRouter-only, not subject to local cap.

**Reality**: Config at `~/.config/opencode/oh-my-openagent.json` maps all four to local Lemonade models:
- `quick`: `lemonade/Qwen3-0.6B-GGUF`
- `writing`: `lemonade/Bonsai-8B-gguf`
- `unspecified-low`: `lemonade/Qwen3-0.6B-GGUF`
- `atlas`: `lemonade/Qwen3.5-4B-MTP-GGUF`

**Fix**: Update AGENTS.md to reflect these are local and subject to the 2-agent cap.

## Agent Names

**Issue**: AGENTS.md model table names `scout` and `plan` as distinct agents.

**Reality**: Config only defines `explore` and `explore-hard`. No `scout` or `plan` keys exist.

**Fix**: Remove `scout` and `plan` from AGENTS.md agent table, or add them to config if needed.

## Frontend Agent Edits

**Issue**: visual-engineering agent claimed to make frontend changes but edits didn't persist in worktree.

**Reality**: Agent output showed successful edits, but `git status` showed no changes to static/ files.

**Fix**: Implemented frontend changes directly in orchestrator. Root cause unclear (agent sandboxing? worktree sync issue?). Log for future reference: when delegating frontend work, verify edits with `git diff` immediately after agent completes, not after multiple agents finish.

## Test Coverage

**Issue**: Investigation promised comprehensive test coverage.

**Reality**: Existing tests pass, but no new unit tests for `services/voice_notes.py` functions.

**Fix**: Add tests for `classify_voice_note()` and `structure_voice_note()` in a future commit. The queue/handler flow is covered by existing LLM job tests, but the prompt construction and JSON parsing are untested.
