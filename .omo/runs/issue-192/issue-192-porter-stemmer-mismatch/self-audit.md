# Self-Audit — Issue #192 (rewrite: throwaway FTS5 index, replaces all heuristic rounds)

Prior rounds shipped three successive approximations of Porter matching (shared-prefix, suffix guard, FTS5-derived stems with a length-gated substring). /audit-pr refuted each with a counterexample (happen, happens, concatenate, runner). This round removes the approximation entirely: segment texts are indexed into a throwaway in-memory FTS5 table with the same tokenizer as transcripts_fts (porter unicode61) and FTS5 MATCH decides. Sections below describe the current code, not the earlier rounds.

## Deliverables

[x] `_fts_match_indices()` runs FTS5 MATCH over candidate texts with tokenize porter unicode61, identical to transcripts_fts, confirmed at services/search.py:31-67
[x] `_matches_segment()` delegates to `_fts_match_indices()`, no substring or length heuristic remains, confirmed at services/search.py:70-72
[x] `search_transcripts()` batch-matches all segments of a transcript in one FTS5 pass, confirmed at services/search.py:121-136
[x] `search_transcripts_snippets()` match_source uses the same FTS5 predicate (fixes the audit's recurring should-fix: stemmed segment match misattributed to full_text), confirmed at services/search.py:202-218
[x] Module-level SQLite/FTS5 setup removed; connections are created inside the function, so an FTS5-less SQLite build fails at search time, not at import (audit round-4 should-fix), confirmed at services/search.py:51
[x] Unit test `test_matches_segment_porter_semantics`, confirmed at tests/test_search.py:207-226
[x] Regression test `test_matches_segment_rejects_shared_prefix_different_stems` covering every audit counterexample (happen, happens, concatenate/cats, runner/run), confirmed at tests/test_search.py:229-236
[x] Integration test `test_matching_segments_stemmed_integration` (happiness matches, happen excluded), confirmed at tests/test_search.py:239-261
[x] Snippets test `test_snippets_match_source_stemmed_segment_text`, confirmed at tests/test_search.py:388-399

### Mutation check transcripts

Mutation A guts the matcher to no-matches; mutation B inverts it to match-everything. B exists because the false-positive regression test asserts only `is False` and would pass vacuously under A.

[x] `test_matches_segment_porter_semantics` — mutation check:
    ran: C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/test_search.py::test_matches_segment_porter_semantics -q  ->  passed (in 4-test baseline: 4 passed)
    mutated: `_fts_match_indices` body -> `return set()` (mutation A); reran  ->  1 failed
        E   AssertionError: assert False is True
        E    +  where False = _matches_segment({'text': 'The happiness project'}, ['happy'])
    restored: reran full tests/test_search.py  ->  61 passed

[x] `test_matching_segments_stemmed_integration` — mutation check:
    ran: C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/test_search.py::test_matching_segments_stemmed_integration -q  ->  passed (in 4-test baseline: 4 passed)
    mutated: `_fts_match_indices` body -> `return set()` (mutation A); reran  ->  1 failed
        E   assert 0 == 1
        E    +  where 0 = len([])
    restored: reran full tests/test_search.py  ->  61 passed

[x] `test_snippets_match_source_stemmed_segment_text` — mutation check:
    ran: C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/test_search.py::test_snippets_match_source_stemmed_segment_text -q  ->  passed (in 4-test baseline: 4 passed)
    mutated: `_fts_match_indices` body -> `return set()` (mutation A); reran  ->  1 failed
        E   AssertionError: assert 'full_text' == 'segment_text'  (tests/test_search.py:398)
    restored: reran full tests/test_search.py  ->  61 passed

[x] `test_matches_segment_rejects_shared_prefix_different_stems` — mutation check:
    ran: C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/test_search.py::test_matches_segment_rejects_shared_prefix_different_stems -q  ->  passed (in 4-test baseline: 4 passed)
    mutated: `_fts_match_indices` body -> `return set(range(len(texts)))` (mutation B); reran  ->  1 failed
        E   AssertionError: assert True is False
        E    +  where True = _matches_segment({'text': 'it will happen'}, ['happy'])
        FAILED tests/test_search.py::test_matches_segment_rejects_shared_prefix_different_stems
    restored: reran full tests/test_search.py  ->  61 passed

## Full test suite

[x] Full suite: 939 passed, 22 deselected
    `C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/ -q` run from the PR worktree.
    Count delta vs the audit's 938: two heuristic-specific tests removed (`test_matches_segment_with_stemmed_terms`, `test_stem_terms_returns_porter_stems`), three tests added, net +1.

## Six checks

### Value-space exhaustiveness

`_fts_match_indices` receives `texts: list[str]` and `terms: list[str]`.
- Empty texts or empty terms: early return set(), confirmed at services/search.py:48-49
- None segment text: callers coalesce with `or ""`, confirmed at services/search.py:72 and services/search.py:125
- Terms containing double quotes: escaped by doubling in `_quote_fts5_term`, confirmed at services/search.py:23
- Segment dict without "text" key: `seg.get("text") or ""`, covered by `test_matches_segment_porter_semantics` asserting `_matches_segment({}, ["happy"]) is False`

### Boundary cardinality

[x] Single-term and multi-term queries covered: unit tests use single terms; existing `test_matching_segments_in_result` covers a term matching 2 of 3 segments (`pytest tests/test_search.py -q` all green)
[x] Zero-segment transcript: `segments or []` yields empty texts list, early return, confirmed at services/search.py:123-126
[x] Zero-match segment list: `test_matching_segments_empty_when_match_in_full_text_only` still passes unchanged

### Delivery chain

N/A: `git diff --stat` shows services/search.py and tests/test_search.py only, no frontend file, no bundle to rebuild.

### done == total on progress counters

N/A, no progress counters changed (`git diff --stat` shows the two files above).

### Every deferral matched against the issue text

No deferrals. The fix implements the issue's own Option 2 (FTS5 decides segment matches). The previously disclosed match_source literal-matching gap in `search_transcripts_snippets()` is now fixed in this PR rather than deferred.

### Suite count tied to invocation

[x] Full suite: 939 passed, 22 deselected, from `C:/Claude/whisperdesk/.venv/Scripts/python.exe -m pytest tests/ -q` in C:/Claude/whisperdesk/.claude/worktrees/issue-192-porter-stemmer-mismatch

## Issue acceptance criteria

The issue proposes three options; this implements Option 2: FTS5 itself determines segment matches, mapped back to segment indices via a throwaway per-segment index (the concatenated segment_text column of the main index cannot yield indices, which is what stalled the first attempt at Option 2).

[x] `matching_segments` no longer empty when FTS5 matches via Porter stemming and the literal term is absent, confirmed by `test_matching_segments_stemmed_integration` at tests/test_search.py:239-261
[x] Segment matching agrees with the index tokenizer by construction, same `tokenize='porter unicode61'` string, confirmed at services/search.py:55 vs database/__init__.py:715
[x] No false positives from shared prefixes or substrings, confirmed by `test_matches_segment_rejects_shared_prefix_different_stems` at tests/test_search.py:229-236
[x] No new dependencies: sqlite3 is stdlib, confirmed at services/search.py:11
[x] No breaking API changes: `search_transcripts` and `search_transcripts_snippets` signatures and return shapes unchanged; `_matches_segment` dropped its internal-only `stems` parameter (callers: this module and tests only)

## Main checkout check

[x] Main on master: `git -C C:/Claude/whisperdesk rev-parse --abbrev-ref HEAD` -> `master`
[x] Main clean: `git -C C:/Claude/whisperdesk status --porcelain -uall` -> `.omo/runs/` entries only

Independent review: Oracle (Phase 3.75) - APPROVE applied to the abandoned round-1 shared-prefix heuristic only; no in-run Oracle pass covers this rewrite. Independent review of the rewrite happens via /audit-pr after the PR is updated, the same reviewer whose five counterexample rounds this rewrite closes.
