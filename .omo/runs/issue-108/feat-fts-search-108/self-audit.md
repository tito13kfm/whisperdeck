# Self-Audit — Issue #108 FTS Search

## Investigation.md promises

- [x] FTS5 virtual table + triggers in init_db() — delivered at database/__init__.py:468-543
- [x] populate_fts() backfill — delivered at database/__init__.py:410-470
- [x] search_transcripts() rewritten to FTS5 MATCH — delivered at services/search.py:47-98
- [x] search_transcripts_snippets() with snippets — delivered at services/search.py:101-171
- [x] LIKE-specific tests updated for FTS5 tokenizer — delivered at tests/test_search.py:151-180
- [x] New snippet tests (6 total) — delivered at tests/test_search.py:267-309
- [x] New trigger sync tests (4 total) — delivered at tests/test_search.py:312-388
- [x] GET /api/search endpoint — delivered at app.py:1297-1314
- [x] q param on GET /api/transcripts — delivered at app.py:1318-1326
- [x] _build_recent_transcripts() query parameter — delivered at app.py:587-609
- [x] Server-side search for 3+ char queries — delivered at static/rack.js:doServerSearch()
- [x] Search results panel with snippets — delivered at static/rack.js:renderSearchResults()
- [x] Jump-to-segment from search results — delivered at static/rack.js:open-search handler + loadTranscriptDetail _searchJumpQuery
- [x] API endpoint tests (8 new) — delivered at tests/test_search.py:391-449

## Issue acceptance criteria

- [x] GET /api/search?q=hello returns ranked, snippet-bearing results — verified via test_api_search_returns_results
- [x] Tape Library search bar triggers server-side FTS search for >= 3 char queries — doServerSearch() checks S.bankQuery.length >= 3
- [x] Clicking a search result opens transcript scrolled to match — open-search sets _searchJumpQuery, loadTranscriptDetail applies S.query
- [x] FTS index stays in sync: create/update/delete — verified via trigger sync tests
- [x] Existing title/filename client-side filter works for < 3 char queries — renderBankRows unchanged for q.length < 3
- [x] services/assistant.py search path unchanged and functional — imports and call signature verified
- [x] Full test suite green (pytest tests/) — 551 passed, 0 failed
- [x] No new pip dependencies — requirements.txt unchanged

## Oracle review findings (F6)

- [x] Long query ValueError guard — added len check in list_transcripts and search_transcripts_snippets
- [x] snippet() column index -1 for all-column matching — changed from 1 to -1
- [x] XSS in renderSearchResults — snippet HTML escaped with <b> tags preserved
- [x] Empty sanitized query guard — added check in search_transcripts_snippets
- [x] f MATCH alias reverted to transcripts_fts MATCH — alias doesn't work on this SQLite version

## Known deferred items

- [ ] segment_text column not auto-populated on INSERT — triggers populate FTS index but not the transcripts.segment_text column. Non-blocking for search (FTS uses explicit VALUES in triggers). Filed as known limitation.
