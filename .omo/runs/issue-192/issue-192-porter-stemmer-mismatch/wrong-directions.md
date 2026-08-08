# Wrong Directions — Issue #192

## Issue body example is inaccurate

The issue body's example claims FTS5 Porter stems "integration" → "integr" and suggests searching "integr" would match a segment with "integration" but `_matches_segment` would miss it. In reality, "integr" IS a literal substring of "integration", so the old code would have caught this case.

The real Porter stemmer mismatch is for word pairs like "happy"/"happiness" (both stem to "happi", but "happy" is not a substring of "happiness"). The issue description is directionally correct about the problem class but the specific example is wrong.

Fix: update the issue body with a correct example pair like "happy"/"happiness" or "computer"/"computation".

## investigation.md originally recommended a word-boundary prefix match

Initial implementation used `word.startswith(term_lower)` which is directional — it catches "analy" matching "analysis" but NOT "analyzes" matching "analysis" (the term is longer than the word). Switched to shared-prefix ratio heuristic after testing revealed this gap. This correction happened during Phase 2 (implementation), not from a bad investigation — the probe at the top of Phase 2 revealed the wrong test case.
