# wrong-directions.md -- issue #284

## 1. Explorer agent (bg_b33fddd6) was unreliable

The `openrouter/nvidia/nemotron-3-super-120b-a12b:free` explorer agent was verbose and task-abandoned — it emitted scrolling analysis but never returned a synthesized answer. Switched to `codegraph_explore` for codebase traversal and the `deep` agent for implementation.

Recommendation: Avoid nemotron explorer for codebase tasks. Use codegraph + deep implementer directly.

## 2. Investigation.md spec suggested `feature_name="VoiceDump"` but `_generate` hardcodes `"VoiceNote"`

The sibling sweep in investigation.md noted: "`_generate` takes `feature_name` param — need to pass `"VoiceDump"` so resolve_model routes correctly." But the earlier phase 0 confirmation said "No settings changes needed (reuses existing format_provider/format_model pattern)."

The implementation kept `feature_name="VoiceNote"` hardcoded in `_generate`, which is correct for now — no `"VoiceDump"` key exists in resolve_model settings. When someone adds VoiceDump-specific model settings later, `_generate` will need to accept a `feature_name` parameter.

Recommendation: Update investigation.md sibling sweep to note that `feature_name="VoiceDump"` is aspirational, not required for this issue.

## 3. Investigation.md suggested `include_clarifying` param for `_structure_from_text`

The investigation ended with: "The safer approach is to add a separate clarifying_questions key in the voice_dump path only, by having `_structure_from_text` optionally accept an `include_clarifying` param that extends the prompt."

The implementation chose the simpler approach: `clarifying_questions: []` is a hardcoded empty list in the dispatch. #285 fills these in during finalization. Adding `include_clarifying` now would be dead code until #285 lands.

Recommendation: Strike the `include_clarifying` suggestion from investigation.md — it over-architects for a feature that does not exist yet.

## 4. verify_self_audit.py build checks fail in worktree

Same as #283: fresh worktrees have no `node_modules` (gitignored). `esbuild` is not available in the worktree environment. The build check fails for both JS and CSS. Both builds succeed from the main checkout using `npx esbuild`. This change does not touch any static files, so the build check is irrelevant here.

Recommendation: Same as #283 — have verify_self_audit.py detect missing esbuild and skip gracefully for backend-only changes.
