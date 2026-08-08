# Self-audit — issue #174, variant deepseek-hybrid

## Promises from investigation.md

[x] search_transcripts() function created — delivered at `services/search.py:34`
[x] Term-split LIKE with AND logic — delivered at `services/search.py:59-67`
[x] LIKE wildcard escaping (_escape_like) — delivered at `services/search.py:18`
[x] Secondary pass over segments JSON — delivered at `services/search.py:78-86`
[x] Returns [{transcript_id, title, filename, matching_segments}] — confirmed shape at `services/search.py:87-92`
[x] Empty query returns [] — delivered at `services/search.py:42-43`
[x] Queries over 500 chars raise ValueError — delivered at `services/search.py:47-48`
[x] User isolation (user_id filter) — delivered at `services/search.py:71`
[x] Status filter (only completed) — delivered at `services/search.py:72`

[x] Unit test: exact match — `test_exact_match_in_full_text` (tests/test_search.py:29)
[x] Unit test: partial match — `test_partial_match` (tests/test_search.py:90)
[x] Unit test: multi-transcript — `test_multi_transcript_results` (tests/test_search.py:109)
[x] Unit test: no match — `test_no_match` (tests/test_search.py:122)
[x] Unit test: empty query — `test_empty_query_returns_empty` (tests/test_search.py:130)
[x] Unit test: LIKE escape (%) — `test_like_wildcard_percent_is_literal` (tests/test_search.py:138)
[x] Unit test: LIKE escape (_) — `test_like_wildcard_underscore_is_literal` (tests/test_search.py:150)
[x] Unit test: user isolation — `test_user_isolation` (tests/test_search.py:195)
[x] 19 tests total, all pass, full suite (498 tests) passes with zero regressions

## Issue acceptance criteria (from plan)

[x] "Sandeep Claude" matches transcripts where both words appear anywhere — `test_multi_term_and_matches_across_columns`
[x] Exact match — `test_exact_match_in_full_text`
[x] Partial match — `test_partial_match`
[x] Multi-transcript results — `test_multi_transcript_results`
[x] No-match returns [] — `test_no_match`
[x] Blank query returns [] — `test_empty_query_returns_empty`
[x] Special characters handled — `test_like_wildcard_percent_is_literal`, `test_like_wildcard_underscore_is_literal`
[x] User isolation — `test_user_isolation`

## Things deferred / not in scope

[ ] Task 2 test file exists but the plan also mentions `test_correction_chunked_finalize.py` uses `_make_user_and_transcript` pattern — not relevant here
