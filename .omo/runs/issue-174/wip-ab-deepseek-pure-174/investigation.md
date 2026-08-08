# Issue #174 Investigation — Cross-transcript search service

## Issue summary

Create `services/search.py` with `search_transcripts(db, user_id, query)` for LIKE-based term search across all user transcripts' `full_text`, `corrected_text`, and `segments` JSON.

## Plan reference

`.omo/plans/llm-assistant.md` task 1 — the search service is the first building block of the LLM Assistant feature, with zero upstream dependencies.

## Live codebase findings

### Transcript model (database/__init__.py:31-71)

| Field | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `user_id` | FK→users.id | nullable=True in the schema, but typically non-null |
| `title` | String(255) | |
| `filename` | String(255) | |
| `full_text` | Text | Raw transcript text |
| `corrected_text` | Text, nullable | Post-correction text |
| `segments` | JSON, default=list | `[{start, end, speaker, text}]` |

### No existing search code

`services/` directory has no search functionality. `app.py` has no search endpoint. Clean slate — this is a new file.

### Service layer conventions (from 4 service files)

- `db` (SQLAlchemy Session) is always the first parameter
- `user_id: int` is the second parameter when the function is user-scoped
- Functions are synchronous except for I/O-bound async calls
- Docstrings on public functions
- Return types are type-hinted

### Test patterns (from test_correction_routing.py, test_correction_service.py)

- `db_session` fixture (conftest.py:72): fresh SQLite per test via `init_db()`
- Helper pattern: `_make_user_and_transcript(db_session, segments=None, full_text="...")`
- Segments created as: `[{start, end, speaker, text}]` Python dicts → JSON column
- `client` fixture (conftest.py:87): auto-registered test user + CSRF-token bearing TestClient

### Sibling sweep

No siblings to find — this is greenfield code (new file, new function). Searched services/ and tests/ for any text-search or transcript-filtering functions; none exist. The only prior art that iterates over transcripts is the tape-library listing (app.py GET /api/transcripts), which returns paginated transcript metadata, not full-text search results.

## Design decisions (from the plan)

1. **Query splitting**: whitespace-delimited terms, AND-ed with SQLite LIKE (`LIKE '%term1%' AND LIKE '%term2%'`)
2. **LIKE escaping**: `%` and `_` in user terms must be escaped with `\` before wrapping in `%...%`
3. **Term limit**: reject queries over 500 chars (ValueError, caller returns 400)
4. **Empty query**: returns `[]`
5. **Per-segment pass**: secondary Python-side pass over `segments` JSON — matches if any term appears in segment text
6. **Scope**: user_id only (already required by the `user_id` param)
7. **Return shape**: `[{transcript_id, title, filename, matching_segments: [{speaker, text, start, end}]}]`
8. **Search scope**: `full_text`, `corrected_text`, and `segments` JSON fields
9. **Case-insensitive**: SQLite LIKE is case-insensitive for ASCII (A-Z vs a-z), covers the transcription use case. No ICU extension needed.

## What the issue gets right/wrong

The issue body is sparse (just bullet points matching the plan). The plan itself at `.omo/plans/llm-assistant.md:116-118` is the authoritative spec and is complete. No discrepancies found between the plan and live codebase.

### Plan additions beyond issue body

- 500-char query limit (issue says nothing about length)
- LIKE wildcard escaping (issue says nothing about escaping)
- Return shape includes `matching_segments` with per-segment match data

## Acceptance criteria (from plan)

1. `search_transcripts(db, user_id, "Sandeep")` returns matching segments
2. `search_transcripts(db, user_id, "")` returns `[]`
3. Query over 500 chars raises ValueError
4. "Sandeep Claude" matches transcripts where both words appear anywhere (even different segments)

## Test scope (from plan)

| Test case | Coverage |
|---|---|
| Exact match | ✅ |
| Partial match | ✅ |
| Case-insensitive match | ✅ |
| Multi-transcript results | ✅ |
| No-match returns empty list | ✅ |
| Blank query | ✅ |
| Special characters in query | ✅ (LIKE escape) |
| Segment-level text vs full_text matching | ✅ |
| User isolation | ✅ |
