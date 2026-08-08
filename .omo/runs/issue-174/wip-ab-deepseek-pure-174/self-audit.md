# Issue #174 Self-Audit — wip/ab-deepseek-pure-174

## Promises from investigation.md

[x] `services/search.py` with `search_transcripts(db, user_id, query)` — delivered at `services/search.py:32`
[x] Term-split AND with LIKE — implemented at `_split_query()`, `_escape_like()`, SQL `and_(*filters)`
[x] LIKE escape for % and _ — `_escape_like()` at line 16
[x] 500-char query limit — `MAX_QUERY_LENGTH = 500` at line 11, raised as ValueError at line 52
[x] Empty query returns [] — checked at line 47
[x] Secondary Python pass over segments — implemented at lines 70-85
[x] Per-segment match data includes speaker, text, start, end — at lines 78-83
[x] Scope to user_id — filter at line 67

## Test coverage from investigation.md

[x] Exact match — `test_exact_match`
[x] Partial match — `test_partial_match`
[x] Case-insensitive match — `test_case_insensitive`
[x] Multi-transcript results — `test_multi_transcript`
[x] No-match returns empty — `test_no_match`
[x] Blank query — `test_empty_query`
[x] Special characters (LIKE escape) — `test_like_escape_percent`, `test_like_escape_underscore`, `test_like_escape_does_not_wildcard`
[x] Segment-level vs full_text — `test_segment_matching`, `test_segment_no_match`
[x] User isolation — `test_user_isolation`

## Additional tests beyond plan scope

[x] Multi-term AND — `test_multi_term_and`, `test_multi_term_missing_one`
[x] Corrected_text match — `test_corrected_text_match`
[x] NULL corrected_text — `test_corrected_text_null`
[x] Query exactly max length — `test_query_exactly_max_length`
[x] Return shape verification — `test_return_shape`
[x] Multiple segment matches — `test_segment_multiple_match`, `test_multi_term_segment_match`

## Acceptance criteria from plan

[x] "Sandeep" returns matching segments — `test_exact_match` passes
[x] Blank query returns [] — `test_empty_query` passes
[x] Query over 500 chars raises ValueError — `test_query_too_long` passes
[x] "Sandeep Claude" matches transcripts where both words appear anywhere — `test_multi_term_and`, `test_multi_term_segment_match` pass
[x] User A's transcripts don't leak to user B — `test_user_isolation` passes

## Verification

[x] `pytest tests/test_search.py -v` — 21 passed
[x] `pytest tests/ -k "not e2e" -v` — 500 passed, 0 failed
