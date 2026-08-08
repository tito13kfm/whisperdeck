# Investigation — issue #174 (search service), variant deepseek-hybrid

Date: 2026-07-27
Plan ref: `.omo/plans/llm-assistant.md` Sub-issue 1, Tasks 1-2

## 1. Files/functions referenced

### Database model (database/__init__.py)
- `Transcript` (line 31): columns `id`, `user_id`, `title`, `filename`, `full_text` (Text), `corrected_text` (Text, nullable), `segments` (JSON, default=list), `status`, `created_at`, etc.
- `LlmJob.transcript_id` is `nullable=False` (line 100) — not relevant to this sub-issue, that's Task 5's concern
- `User` (line 17): columns `id`, `username`, etc.

### Existing service patterns (services/correction.py, services/hotwords.py)
- Functions take unannotated `db` parameter (SQLAlchemy session), no type annotation
- Import models from `database` package directly: `from database import Transcript, User`
- Return typed results: `list[dict]`, `HotwordEntry`, `str`, etc.
- Error handling: either non-fatal (catch + set error field + return status) or fatal (let exception propagate)
- No existing search/LIKE functionality anywhere in services/

### Existing test patterns (tests/conftest.py, tests/test_correction_service.py)
- `db_session` fixture: fresh SQLite per test via `init_db(str(tmp_path / "test.db"))`
- Helper functions create User + Transcript inline (not fixtures)
- Tests are flat functions, no classes
- Assertions use `db_session.refresh(obj)` then check fields
- No need for mocking in search tests (no LLM/HTTP calls)

### Settings (services/settings.py)
- `export_directory` already exists in DEFAULT_SETTINGS at line 31 — pre-existing, not needed for this sub-issue
- Generic validation: `patch = {key: value for key, value in updates.items() if key in DEFAULT_SETTINGS}` handles all keys

## 2. Call sites / entry points in scope

`search_transcripts(db, user_id, query)` is a NEW function — no existing call sites. Future callers will be:
- `services/assistant.py` → `execute_plan()` (Task 4)
- Potential future: API endpoint for direct search

No siblings to sweep — this is the first search function. No existing search/LIKE patterns to fix elsewhere.

## 3. Sibling sweep

Grep for `LIKE`, `ilike`, `contains`, `search` in services/*.py: zero matches (only a regex search in tagging.py, unrelated).
No other search functions exist. No existing call sites to update.

Sweep for `segments` JSON access: `_transcript_lines()` in correction.py accesses segments but via ORM attribute directly — unrelated.

Conclusion: no siblings missed. This is a greenfield function.

## 4. Issue vs actual code

The issue says: "Create `search_transcripts(db, user_id, query)` — splits query into terms, ANDs with escaped LIKE, secondary pass over segments JSON"

The plan specifies:
- LIKE wildcards `%` and `_` escaped
- Query split into whitespace-delimited terms, AND-ed
- Secondary pass over segments JSON for per-segment matching
- Returns `[{transcript_id, title, filename, matching_segments: [{speaker, text, start, end}]}]`
- Reject queries over 500 chars (ValueError)
- Empty query returns []
- User isolation via `user_id` filter

No discrepancies between issue and plan. Plan is complete and unambiguous.

## 5. Acceptance criteria from plan

- [ ] "Sandeep Claude" matches transcripts where both words appear anywhere (even in different segments)
- [ ] Exact match returns matching segments
- [ ] Partial match works
- [ ] Multi-transcript results
- [ ] No-match returns empty list
- [ ] Blank query returns []
- [ ] Special characters (LIKE wildcards) don't break search
- [ ] User isolation (user A's transcripts don't leak to user B)
- [ ] Queries over 500 chars raise ValueError

## 6. Implementation plan (exact from plan)

1. `services/search.py`: `search_transcripts(db, user_id, query)` — ~80 lines
2. `tests/test_search.py`: unit tests — ~80 lines
