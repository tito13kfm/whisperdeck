# Feature: search

## Sources consulted
- `app.py:1852-1866` (/api/search), `684-699` (_build_recent_transcripts), `108-113` (init_db call site)
- `services/search.py` full file (1-229)
- `database/__init__.py:492-641` (populate_fts, cleanup_fts_orphans), `643-835` (init_db incl. FTS DDL/triggers 704-810)
- `services/assistant.py:11,104` (second consumer of search_transcripts)
- Grep of `Transcript(` writes across repo

## Concrete findings
- Query path: `app.py:1852` validates q non-empty (1861-1862) and <=500 chars (1863-1864) -> `services/search.py:146 search_transcripts_snippets`. That function re-validates (155-160, dead code given app.py's gate) -> `_sanitize_fts5_query` (26-28) -> `_quote_fts5_term` per token, AND-joined (20-23) -> single SQL: `transcripts_fts MATCH :q` joined to transcripts, filtered by user_id/status='completed', ordered by rank, `snippet()` for highlights (167-189). Exception (malformed FTS5 syntax) swallowed -> `[]` (190-191). Per row, `_fts_match_indices` (31-66) builds a throwaway in-memory FTS5 table to attribute which column matched (porter-stemmed match attribution, issue #192) -> match_source (207-217).
- `search_transcripts` (74-143, used by /api/transcripts?q= and services/assistant.py:104) shares sanitize/MATCH pattern but raises ValueError on overlong query instead of swallowing, returns per-segment matches not snippets.
- **Index maintenance is NOT per-write Python calls.** `app.py:113` calls `init_db()` once at startup. Inside: `transcripts_fts` virtual table created (722-732, external-content mode, porter unicode61), then three SQL triggers (insert 734-742, update 769-782 unconditionally DROP+CREATE to fix stale bodies from #206/#309, delete 801-810) fire automatically on any ORM insert/update/delete against `transcripts` — this is what keeps the index in sync, decoupled from any feature's Python code. `cleanup_fts_orphans` (558-640, runs first) then `populate_fts` (492-552, runs second) are one-time startup backfill/repair jobs, not per-write hooks. Both idempotent no-ops once caught up.
- Non-test call sites that create/mutate Transcript rows (thus fire triggers): `services/transcription.py`, `services/queue.py`, plus correction/relabel/diarization update paths via ORM commits.

## Mermaid flowchart

```mermaid
flowchart TD
    A["GET /api/search<br/>app.py:1852"] --> B{"q empty/blank?<br/>app.py:1861"}
    B -- yes --> B1["HTTPException 400<br/>app.py:1862"]
    B -- no --> C{"len(q) > 500?<br/>app.py:1863"}
    C -- yes --> C1["HTTPException 400<br/>app.py:1864"]
    C -- no --> D["search_transcripts_snippets<br/>services/search.py:146"]

    D --> E{"query.strip() empty?<br/>services/search.py:155-157"}
    E -- yes --> RET0["return []<br/>services/search.py:157"]
    E -- no --> F{"len(query) > 500?<br/>services/search.py:159-160"}
    F -- yes --> RET0
    F -- no --> G["_sanitize_fts5_query<br/>services/search.py:162,26-28"]
    G --> G1["_quote_fts5_term per token, AND-joined<br/>services/search.py:20-23"]
    G1 --> H{"fts5_query blank?<br/>services/search.py:163-164"}
    H -- yes --> RET0
    H -- no --> I["SELECT ... FROM transcripts_fts f<br/>JOIN transcripts t ON t.id=f.rowid<br/>WHERE transcripts_fts MATCH :q<br/>AND user_id, status='completed'<br/>ORDER BY rank<br/>services/search.py:167-189"]
    I -. reads .-> IDX[("transcripts_fts<br/>virtual table")]
    I -- exception --> RET1["return [] (except swallowed)<br/>services/search.py:190-191"]
    I -- rows --> J["for each row: gather title/full_text/<br/>corrected_text/segment_text<br/>services/search.py:207-212"]
    J --> K["_fts_match_indices:<br/>build in-memory FTS5 table, MATCH terms<br/>services/search.py:31-66"]
    K --> L["pick match_source = first hit column<br/>services/search.py:213-217"]
    L --> M["append {transcript_id, rank, title,<br/>filename, created_at, snippet, match_source}<br/>services/search.py:219-227"]
    M --> N["return results list<br/>services/search.py:229"]
    N --> O["JSON {results, total}<br/>app.py:1865-1866"]

    subgraph INIT["Startup: index setup (runs once, app.py:113 -> init_db)"]
        P["init_db()<br/>database/__init__.py:643"] --> Q["CREATE VIRTUAL TABLE transcripts_fts<br/>(content='transcripts', porter unicode61)<br/>database/__init__.py:722-732"]
        Q --> R["CREATE TRIGGER trg_transcripts_fts_insert/<br/>_update/_delete<br/>database/__init__.py:734-810"]
        R --> S["cleanup_fts_orphans(engine)<br/>database/__init__.py:558, called at 814"]
        S --> T["populate_fts(engine)<br/>database/__init__.py:492, called at 815"]
    end
    T -. backfills missing rows into .-> IDX
    S -. removes orphaned entries from .-> IDX

    subgraph RUNTIME["Runtime: index sync (per write, not per search request)"]
        V["Transcript row INSERT/UPDATE/DELETE + commit<br/>(services/transcription.py, services/queue.py,<br/>correction, relabel, delete-transcript paths)"] -. fires .-> W["trg_transcripts_fts_insert /<br/>trg_transcripts_fts_update /<br/>trg_transcripts_fts_delete<br/>database/__init__.py:734,769,801"]
    end
    W -. writes .-> IDX
```

## External dependencies
- Only `database/__init__.py:init_db()` calls `populate_fts`/`cleanup_fts_orphans`, called once from app.py:113 at process startup — no request-time or per-write code path calls either directly.
- Real sync mechanism: the three SQL triggers, fired by SQLite itself on any write to `transcripts`, regardless of which feature performed it.
- Other consumers: `services/assistant.py:104` (search_transcripts for AI assistant lookup), `app.py:684-699 _build_recent_transcripts` (search_transcripts for /api/transcripts?q=).

## Confidence and gaps
High confidence, all line numbers verified against source. Did not trace into services/transcription.py or services/queue.py for the exact db.commit() line that fires triggers — grep-confirmed as non-test Transcript-mutating files, sufficient to identify trigger callers but not line-exact for that specific commit. Did not deep-read tests/test_search.py.
