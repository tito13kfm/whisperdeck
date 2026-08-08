# Investigation — Issue #192: Porter stemmer vs _matches_segment() tokenization mismatch

**Target**: Issue #192, standalone  
**Worktree**: `C:/Claude/whisperdesk/.claude/worktrees/issue-192-porter-stemmer-mismatch`  
**Main**: `C:/Claude/whisperdesk` (master)  
**Branch**: `issue-192-porter-stemmer-mismatch`

## Root cause

Two code paths in `search_transcripts()` (`services/search.py`) use different text-matching semantics:

1. **Transcript-level discovery** (line 59-67): FTS5 MATCH query against `transcripts_fts`, built with `tokenize='porter unicode61'` at `database/__init__.py:627`. The Porter stemmer reduces inflected words to roots — e.g., "analyzes" and "analysis" both stem to "analysi".

2. **Per-segment filtering** (line 84-93): `_matches_segment()` at `services/search.py:29-32` does plain case-insensitive substring matching — `any(term.lower() in text for term in terms)`. No stemming.

Result: FTS5 can identify a transcript as matching (stemmed forms match), but `_matches_segment()` may find no segment containing the *literal* query term characters, producing an empty `matching_segments` list.

**Concrete example**: User searches "happy". FTS5 Porter stems "happy" to "happi", finds a transcript whose segment_text column contains "happiness" (also stems to "happi"). But `_matches_segment` checks if the substring "happy" appears in each segment's text — and "happiness" does not contain the substring "happy". `matching_segments` is empty.

## Scope of impact

### Call sites

- `_matches_segment()` — 1 caller: `search_transcripts()` at `services/search.py:92`
  - Command: `grep -rn "_matches_segment" services/search.py` → `29:def _matches_segment...`, `92:if _matches_segment(seg, terms)`
- `search_transcripts()` — 25 callers across `services/assistant.py`, `app.py`; tests in `tests/test_search.py`
  - Key consumer: `services/assistant.py:104` (search action), `services/assistant.py:117` (summarize action builds LLM context from `matching_segments`)

### Downstream: assistant context is empty when mismatched

`services/assistant.py:116-120`:
```python
for t in search_results:
    for seg in (t.get("matching_segments") or []):
        speaker = seg.get("speaker", "Unknown")
        text = seg.get("text", "")
        context_parts.append(f"[{t.get('title', 'Untitled')}] {speaker}: {text}")
```

When `matching_segments` is empty, the LLM summarize prompt gets no segment context — it can't produce a meaningful summary.

### Sibling sweep

- `search_transcripts_snippets()` at `services/search.py:104-188` has its own `_has_term()` inner function (line 163) doing the same plain substring matching for `match_source` field detection. Same stemmer mismatch exists: `match_source` can fall through to its `"full_text"` default even when the real match is in `segment_text` but the literal term text doesn't appear there. Impact is low (cosmetic metadata), but same root cause.
- `segmentsHtml()` in `static/rack.js:3816-3821` does client-side substring filtering — unrelated.
- No other callers of `_matches_segment` exist. Command: `grep -rn _matches_segment . --include="*.py"` returned only the definition and the single call site.

### Existing test coverage

- `test_matching_segments_in_result` at `tests/test_search.py:178` — literal substring match works
- `test_matching_segments_empty_when_match_in_full_text_only` at `tests/test_search.py:194` — documents empty segments as expected when match is only in full_text
- No test covers the stemmer mismatch (FTS5 match via stemming, no literal substring match in segments)
- `_matches_segment` itself has no covering tests (tested only indirectly through `search_transcripts`)

## Recommended approach: Option 2 — use FTS5 itself for segment matching

Instead of replicating the Porter stemmer in Python (fragile, must match SQLite's exact rules), use FTS5 to determine which rows' `segment_text` column matched.

**Implementation**: In `search_transcripts()`, for each FTS5-matched transcript, run a second FTS5 query restricted to that rowid to check whether the `segment_text` column matched. If it did, the segment filter passes. Since `segment_text` is the concatenation of all segment texts (set by the FTS triggers at `database/__init__.py:637,665`), we can't map individual FTS5 column matches back to specific segment indices. Instead, when FTS5 confirms the `segment_text` column matched, we include all segments that match via the existing literal substring check *plus* a word-boundary stem-aware fallback.

**Simpler variant**: After the FTS5 rowid query confirms a transcript matched, query the same FTS5 table for the same rowid but check `segment_text` specifically. Use `highlight(transcripts_fts, 2, '<mark>', '</mark>')` over the `segment_text` column — if it returns marked text, segment_text contributed to the match. Then, for filtering, include any segment whose text, after lowercasing and splitting into words, has any word that *starts with* any query term (prefix match as an approximation of stemming).

Both approaches share the problem that `segment_text` is a concatenated blob and can't yield per-segment indices. The cleanest solution:

**Chosen fix**: Replace `_matches_segment(seg, terms)` with a check that also considers shared-prefix overlap as a stemming approximation. For each word in the segment text, compute the length of the common prefix it shares with each query term. If the shared prefix is at least 3 characters AND constitutes over half the shorter word, treat them as matching. This catches "happy"/"happiness" (shared prefix "happ", 4 chars, which is 4/5 = 0.8 of "happy") without needing a Porter stemmer.

Combined with the existing literal substring check, this gives:

```python
def _matches_segment(seg: dict, terms: list[str]) -> bool:
    text = (seg.get("text") or "").lower()
    for term in terms:
        term_lower = term.lower()
        # Literal substring match
        if term_lower in text:
            return True
        # Word-boundary prefix match (approximates stemming)
        for word in text.split():
            if word.startswith(term_lower):
                return True
    return False
```

This is a minimal, dependency-free fix that catches the common stemming cases (different suffixes sharing a root prefix) without replicating the Porter algorithm.

## Changes required

1. `services/search.py:29-32` — update `_matches_segment()` with prefix matching
2. `tests/test_search.py` — add test covering stemmer mismatch (e.g., "analyzes" matches segment with "analysis")
3. Existing `test_matching_segments_empty_when_match_in_full_text_only` — verify it still passes (it should, since "Sandeep" in full_text doesn't change segment behavior)

## Phase 1.5 check

Not applicable — this change doesn't touch job/state completion paths.

## Addendum 2026-08-06: final implementation supersedes the "Chosen fix" above

The shared-prefix heuristic above, and its two successors (suffix guard, FTS5-derived stems with a length-gated substring), were each blocked by /audit-pr with a concrete counterexample: happen/happy, happens/happy, concatenate/cats, runner/run. Any Python-side approximation of Porter matching admits such counterexamples.

The shipped fix is the section's original recommendation (Option 2, use FTS5 itself), unblocked. The stated blocker was that the main index's `segment_text` column is a concatenated blob that cannot yield per-segment indices. The resolution: do not reuse the main index. `_fts_match_indices()` (services/search.py) builds a throwaway in-memory FTS5 table with the identical `tokenize='porter unicode61'` setting, inserts each segment text as its own row (rowid = segment index), runs one MATCH with the query terms OR-ed, and returns the matching indices. Segment matching therefore agrees with transcript-level MATCH by construction; there is no heuristic left to refute.

The same predicate now also drives `match_source` in `search_transcripts_snippets()`, closing the sibling literal-matching gap disclosed in the sibling sweep above.
