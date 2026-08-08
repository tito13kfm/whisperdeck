## PR Audit: #364 fix(search): use shared-prefix heuristic in _matches_segment to approximate Porter stemming   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: BLOCK

### Blocking
- services/search.py:35-45 The suffix guard still accepts an unrelated Porter non-match, `happens` for query `happy`, because the remainder `ens` contains the one-character suffix `s`. Failure scenario: FTS5 finds a transcript containing `happy` or `happiness`, while another segment contains `happens`; `_matches_segment()` includes that unrelated segment in `matching_segments` and the assistant can receive incorrect context. Fix: do not accept arbitrary one-character suffixes from substring search, or compare actual Porter stems instead. Regression test: `assert _matches_segment({"text": "happens"}, ["happy"]) is False`.

### Should fix
- [feature] services/search.py:211-224 `search_transcripts_snippets()` still uses literal matching for `match_source`, so a stemmed query matching `segment_text` can be reported as `full_text` by the fallback. Failure scenario: query `happy` matches segment text `happiness` without a literal `happy` substring, producing incorrect source metadata. Fix: share the stemming-aware predicate or derive the source from the FTS5 column match.

### Nits
- Static scan found no new async reentrancy or swallowed exception in the changed path. The `asyncio.run()` hit in services/cost.py is the known guarded case, and test-suite hits are synchronous drivers.

### Honesty check
- self-audit.md [x] lines verified: 19/19 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 0. False [x] found: line 73 claims no false positives, but the implementation matches `happens` for `happy`.
- Vacuous / loosened tests: none. The added `happen` regression is meaningful, but it does not cover the closely related `happens` case that still fails.
- Undisclosed scope (diff vs claims): none. The investigation explicitly disclosed the separate literal `match_source` mismatch in `search_transcripts_snippets()`.

### Read scope
- Focused read on services/search.py, the changed hunk and related tests in tests/test_search.py, plus traced callers in services/assistant.py and app.py. Full suite and search tests were run from the PR worktree.

### Summary
The suffix guard fixes the exact `happen` regression, and the PR worktree passes 937 tests with 22 deselected, including 59 search tests. However, the guard's `s` suffix still produces a concrete false positive for `happens` versus `happy`, so the previous blocking issue is not fully resolved.
