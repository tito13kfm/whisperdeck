## PR Audit: #364 fix(search): use shared-prefix heuristic in _matches_segment to approximate Porter stemming   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

---
2026-08-07T02:08:00Z

## PR Audit: #364 fix(search): run FTS5 MATCH over segment texts to align _matches_segment with Porter tokenization   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: APPROVE

### Blocking
- None.

### Should fix
- None.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 18/18 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 0. False [x] found: none. The current self-audit matches commit `b3806d4`, and `verify_self_audit.py` returned `OK`.
- Vacuous / loosened tests: none found. The positive mutation checks fail when `_fts_match_indices()` returns no matches; the negative regression test fails when it returns every row. Specific counterexamples (`happen`, `happens`, `concatenate`/`cats`, `runner`/`run`) are covered.
- Undisclosed scope (diff vs claims): none. The PR body accurately describes the throwaway FTS5 implementation, the `match_source` change, the removed module-level initialization, and the test counts.

### Read scope
- Focused read on `services/search.py`, changed tests in `tests/test_search.py`, `services/assistant.py` caller context, and the snippet path. Full search tests: 61 passed. Full suite: 939 passed, 22 deselected, 1 warning. CI `tests` check also passed in 1m57s. Static scan found no new async reentrancy or newly introduced swallowed exception; the broad `except Exception` at `services/search.py:190` is pre-existing and outside this change.

### Summary
The final implementation removes the heuristic entirely and uses a throwaway FTS5 table with the same `porter unicode61` tokenizer as the main transcript index, one row per segment. This directly resolves the prior counterexamples, shares the predicate with snippet `match_source`, passes the full local and CI suites, and has a verified self-audit.

---
2026-08-07T01:47:39Z

## PR Audit: #364 fix(search): use FTS5 stemmer to align _matches_segment with Porter tokenization   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: BLOCK

### Blocking
- services/search.py:83-84 The length gate does not establish a Porter-stem match, so it still includes unrelated words with the same short stem substring. Failure scenario: a transcript matches query `run` through FTS5, while a segment contains `runner`; `_stem_terms(['run'])` returns `{'run'}`, and `_matches_segment()` accepts `runner` because `run in runner` and `len('runner') <= 2 * len('run')`, even though Porter stems are `run` and `runner`. The assistant then receives unrelated segment context. Fix: compare the Porter stem of each segment word with the query stem, or use FTS5 token matching per segment rather than a substring plus length heuristic. Regression test: `assert _matches_segment({'text': 'runner'}, ['run'], stems={'run'}) is False`.

### Should fix
- [feature] services/search.py:222-234 `search_transcripts_snippets()` still determines `match_source` with literal substring checks, separate from the new stemming-aware path. Failure scenario: query `happy` matches `segment_text` containing `happiness` via FTS5, but `_has_term()` does not recognize that stemmed match and the fallback reports `match_source == 'full_text'` (or another earlier literal column) instead of `segment_text`. Fix: derive the source from the FTS5 column match or share the same stemming-aware predicate.

### Nits
- None.

### Honesty check
- self-audit.md [x] lines verified: 18/18 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 0. False [x] found: line 5 names a shared-prefix heuristic, but the checked-out code implements FTS5-derived stems with a length-gated substring heuristic; line 6 names `test_matches_segment_prefix_approximates_stemming`, which does not exist; line 7 names `test_matching_segments_prefix_match_integration`, which does not exist; line 27 claims 937 passed, while this worktree run produced 938 passed, 22 deselected; line 73 claims no false positives, contradicted by `run` matching `runner`.
- Vacuous / loosened tests: none in the newly added tests. The `cats`/`concatenate` regression only covers the specific long-word collision and does not validate actual Porter-equivalent matching, leaving `run`/`runner` undetected.
- Undisclosed scope (diff vs claims): the implementation changed from the self-audit's described shared-prefix heuristic to a module-level SQLite FTS5 stem lookup and length-gated substring matching. The separate literal `match_source` mismatch remains disclosed in `investigation.md` but is not fixed by this PR.

### Read scope
- Focused read on `services/search.py`, the changed tests in `tests/test_search.py`, `services/assistant.py` caller context, and the existing snippet path. Full `tests/` suite and `tests/test_search.py` were run from the PR worktree. Static scan found no new async reentrancy or newly introduced swallowed exception in the changed path; the broad `except Exception` at `services/search.py:207` is pre-existing and outside this change.

### Summary
The PR now derives stems from FTS5 and the full suite passes, but the replacement matcher is still a substring heuristic rather than a stem comparison. A concrete valid input, query `run` with segment word `runner`, produces an incorrect matching segment, so the PR remains blocked. 

VERDICT: BLOCK

### Blocking
- services/search.py:54 The shared-prefix rule treats unrelated words such as `happen` and query term `happy` as a segment match. Failure scenario: a transcript is found for `happy` and a segment contains `happen`, so `matching_segments` includes that segment and can feed incorrect context to the assistant. Fix: use a Porter-compatible token/stem comparison or narrow the approximation to stem pairs validated against the tokenizer. Regression test: `assert _matches_segment({"text": "happen"}, ["happy"]) is False`.

### Should fix
- [feature] services/search.py:187-200 `search_transcripts_snippets()` retains literal matching for `match_source`, so a stemmed query matching `segment_text` can still be reported as `full_text` by the fallback. Failure scenario: query `happy` matches segment text `happiness` without a literal `happy` substring, producing the wrong source metadata. Fix: share the same stemming-aware matching predicate or derive the source from the FTS5 column match.

### Nits
- Static scan found no new async reentrancy or swallowed exception in the changed path. The runtime `asyncio.run()` hit in `services/cost.py` is the known guarded case, and test-suite hits are synchronous test drivers.

### Honesty check
- self-audit.md [x] lines verified: 19/19 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 0. False [x] found: line 73 claims no false positives, but the implementation matches `happen` for `happy`.
- Vacuous / loosened tests: none for the two new tests. The integration test asserts the exact segment text and both mutation checks are supported by the test shape.
- Undisclosed scope (diff vs claims): none. The investigation explicitly disclosed the separate literal `match_source` mismatch in `search_transcripts_snippets()`.

### Read scope
- Focused read on services/search.py, the changed hunk and related tests in tests/test_search.py, plus traced callers in services/assistant.py and app.py (cost guard).

### Summary
The PR's tests pass, including 937 tests with 22 deselected and 59 search tests, but the heuristic is broader than Porter stemming and creates incorrect segment matches on valid input. The false-positive acceptance claim is therefore not supported by the implementation, so this audit blocks the PR.

---
2026-08-07T01:30:00Z

## PR Audit: #364 fix(search): use shared-prefix heuristic in _matches_segment to approximate Porter stemming   (reviewer: GPT-5.6 Luna, independent third family)
Reviewer slug: gpt-5.6-luna

VERDICT: BLOCK

### Blocking
- services/search.py:97 The rewrite appends Porter stems to a literal substring matcher without token boundaries, so a stem can match an unrelated longer word. Failure scenario: query `cats` finds a transcript through FTS5, a segment contains `concatenate`, and the derived stem `cat` makes `_matches_segment()` include that segment in `matching_segments`, producing incorrect assistant context. Fix: compare tokenized segment words against stems, or use FTS5 to match each segment token rather than substring-searching stem text. Regression test: `assert _matches_segment({"text": "concatenate"}, ["cats", "cat"]) is False`.

### Should fix
- [robustness] services/search.py:42-63 The module-level SQLite connection and FTS5 schema are initialized during import, so environments with SQLite built without FTS5 fail importing the entire search service instead of degrading locally. Failure scenario: deployment uses a Python SQLite build without FTS5, and importing services.search raises OperationalError before the app starts. Fix: fail with an explicit startup diagnostic or provide a safe fallback.

### Nits
- services/search.py:190 retains an existing broad `except Exception` that returns an empty snippet result; it is outside the newly added stem path and was not introduced by this PR.

### Honesty check
- self-audit.md [x] lines verified: 18/18 (counted per the Phase 2 definition: lines opening with [x], excluding [ ] and [decision]). Open [ ] items: 0. False [x] found: lines 5-7 cite stale locations from the previous implementation; line 73 claims no false positives, but `cat` matches `concatenate` through substring search.
- Vacuous / loosened tests: none in the new tests, but the new tests cover `happen` and `happens`, not stem substring collisions such as `cats`/`concatenate`.
- Undisclosed scope (diff vs claims): the claim "zero false positives possible" is contradicted by the actual literal substring path; the PR body also still describes the old heuristic rather than the checked-out rewrite.

### Read scope
- Focused read on services/search.py and tests/test_search.py, plus traced search callers in services/assistant.py and app.py. Full suite and search tests were run from the PR worktree.

### Summary
The FTS5 stem derivation itself returns the expected Porter stems, and the worktree passes 938 tests with 22 deselected, including 60 search tests. However, appending stems to substring matching does not eliminate false positives, so the rewrite does not meet its stated correctness claim.

---
2026-08-07T01:05:00Z

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
