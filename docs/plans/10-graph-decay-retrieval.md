# Graph-decay retrieval: one-hop entity-graph expansion for search

> One-line status: Planned (design pass done via exploration + Plan-agent design + advisor review, which caught a real bug in the merge algorithm before it shipped as pseudocode). Part 4 of 5 on the meeting-knowledge-layer master tracker (#241). Depends on part 1's `Entity`/`EntityMention` schema (#245, unmerged). Does not depend on parts 2/3 landing first, and makes zero changes to their surfaces.

## Scope verdict, up front

This ships small: one pure function, one DB-query function, one composition wrapper, one opt-in query parameter on the existing `GET /api/search` route, and one pinning test for an existing implicit behavior (FTS5 bm25's sign convention) that this plan is the first thing to actually depend on. No new module, no `rack.js` changes, no `services/assistant.py` changes, no topic-page UI changes. Each of those exclusions is a decision made below, not an oversight — issue #241's "graph hub" language is broader than what this part actually needs to build to satisfy it.

## Motivation

From #241's "Later phase" section: "a search hit on an entity page pulls in linked meetings and decisions at a decayed score." From the topic-grouping comment (#248): "topic pages... become graph hubs for the retrieval work in #242." Part 2 (#247) already built the *zero-hop* version of this (same-transcript co-occurrence: a topic's decisions/action-items from meetings that directly mention it). This part is the *one-hop, cross-transcript* version: a search hit on transcript A, where A mentions entity X, should also surface transcript B — which never matched the search query at all, but also mentions X — at a reduced (decayed) relevance score.

## Existing system this plugs into

`services/search.py` has two structurally different functions. `search_transcripts()` (used by `services/assistant.py` and the Tape Library list view) has no ranking, no order guarantee, and no score field — SQLAlchemy's `.filter(.in_(ids))` doesn't preserve the FTS5 match order, and nothing has ever needed it to until now. `search_transcripts_snippets()` (used only by `GET /api/search`) does rank, via FTS5's implicit bm25 auxiliary `rank` column, `ORDER BY rank ASC`. **Load-bearing gotcha, confirmed not assumed**: SQLite FTS5 bm25 scores are negative, more-negative-is-better — any decay arithmetic must operate on `-rank`, never the raw signed value, or the math runs backwards.

## Proposed approach

**1. The primitive lives in `services/search.py`, not a new module.** Its correctness is contingent on the bm25 sign convention that already lives there, and `services/retrieval.py` is a name #242 (still unplanned) will plausibly want for its own, larger module later — not pre-claiming it keeps that door open.

**2. Two functions, pure/DB split, mirroring part 1's own `plan_merge`/`apply_extraction` precedent:**

- `merge_expansion(seeds, expanded)` — pure, no DB. Dedupes by `transcript_id`: a seed (direct hit) always wins over any decayed value regardless of numeric comparison; among multiple decayed paths to the same non-seed transcript, keeps the max, never the sum (summing would let a heavily-connected hub transcript outrank a genuine direct hit). **Implementation note carried from review**: an earlier draft's dedup loop had a `continue`-then-unreachable-comparison bug that made "seed always wins" true only by accident of control flow, not by an explicit check — the real implementation must make the seed-wins rule an explicit condition (e.g. keep seeds and expanded results in two separate dicts, decide precedence once at the very end), not rely on early-return ordering to produce the right answer by coincidence. Test `test_merge_expansion_seed_always_wins` needs to actually exercise a case where a decayed score is numerically *higher* than a seed's real score, or it can pass against the buggy version too.
- `expand_by_entity_graph(db, user_id, seeds, decay=0.65)` — the two-hop walk. `seeds`: `[{"transcript_id", "score"}]`, score positive/higher-is-better (caller's job to sign-normalize; this function never touches bm25 directly). Exactly three batched queries, no per-seed loop:
  1. Hop 1 (transcript → entity): `EntityMention.transcript_id IN (seed_ids)` joined to `Entity`, filtered `Entity.user_id == user_id` (tenant scoping — `EntityMention` itself has no `user_id`) and `Entity.type IN (person, project, topic)` (decision/action_item excluded — their exact-match dedup policy means they rarely re-share a transcript, and excluding them shrinks fan-out for free).
  2. Degree check: one batched `COUNT(*) GROUP BY entity_id` over only the hop-1 candidate ids (not the whole table) — entities above `_MAX_ENTITY_DEGREE` (default 50) are dropped as hop targets before hop 2 runs. This is the on-the-fly substitute for the `Entity.mention_count` column part 1 deliberately didn't build.
  3. Hop 2 (entity → other transcripts): `EntityMention.entity_id IN (surviving_ids)` joined to `Transcript`, filtered `Transcript.user_id == user_id` (tenant scoping again — hop 2's join target is `Transcript`, not `Entity`, so this is a *second*, independent tenant check, not a repeat of hop 1's) and `Transcript.status == "completed"`, excluding transcript ids already in the seed set.
  - Three additional caps, all named constants: `_MAX_EXPANSION_SEEDS = 5` (only the top-N seed hits get expanded from at all — see the fixed-anchor decision below), `_MAX_ENTITIES_PER_SEED = 10` (per seed, ordered by `mention_count` desc), `_MAX_TRANSCRIPTS_PER_ENTITY = 10` (per entity, ordered by `created_at` desc).

**3. `_MAX_EXPANSION_SEEDS` is a fixed anchor, deliberately not scaled to `limit`.** A `GET /api/search?limit=100&expand=1` call still only expands from the top 5 hits, not the top 100 — expansion cost and graph fan-out stay bounded regardless of how many results the caller asked for. The alternative (scaling the seed cap with `limit`) would make expansion cost unbounded as `limit` grows toward its existing 100 cap; a fixed anchor is deliberately chosen over "asking for more results means proportionally more graph work." Stated here explicitly so a future reader doesn't need to guess which reading was intended.

**4. `_EXPANSION_DECAY_DEFAULT = 0.65`** (GrayBox's number), a plain module constant — **not** wired into `services/settings.py`. Unlike `entity_dedup_threshold`/`topic_dedup_threshold` (which directly change visible merge behavior a power user might want to tune), a graph-decay factor has no user-facing frame of reference; making it settings-configurable now would be speculative surface area with no one to use it. Additive to expose later if that changes.

**5. Integration: one new composition function, `search_transcripts_snippets_expanded`, plus one new opt-in query param on the existing route.** `search_transcripts_snippets` itself is untouched — its existing tests, contract, and (non-)callers stay exactly as they are. The new function calls it, sign-normalizes (`score = -rank`), calls `expand_by_entity_graph`, and hydrates minimal metadata (title/filename/created_at, empty snippet, null rank) for any transcript that's graph-only. `GET /api/search` gains `expand: bool = Query(False)`:

```python
if expand:
    results = search_transcripts_snippets_expanded(db, current_user.id, q.strip(), limit=limit)
else:
    results = search_transcripts_snippets(db, current_user.id, q.strip(), limit=limit)
```

Default-off is deliberate, not just cautious: `rack.js` already renders `/api/search` results today (the Tape Library search bar), and a graph-expanded row has no real snippet (it never matched the query text). Turning expansion on unconditionally would silently change what a shipped surface displays with no renderer ready for `match_source: "graph"` rows. `expand=False` by default means this plan changes nothing about today's default response shape — purely additive, and whoever builds the UI for it (part of #242's eventual scope, or a smaller follow-up) flips the switch when there's a renderer.

**6. `search_transcripts`/`services/assistant.py`: zero changes.** Not "probably wouldn't help" — structurally can't participate: the primitive's contract requires a positive, higher-is-better score per seed, and `search_transcripts` produces none at all. Fixing that function's total lack of ranking is a different, unscoped problem (already tracked as a general gap, see #246's neighboring "known but unrelated" precedent) — not taken on here.

**7. Topic pages as "graph hubs": no UI change from this part.** Two things that could plausibly mean "build this in part 4," both declined with reasons:
   - Reverse-direction same-meeting co-occurrence (people/projects sharing a meeting with a topic, the mirror of part 2's existing decisions/action-items view) — this is still *zero-hop*, not this part's *one-hop* concern, and has now been deferred by parts 2, 3, and 4 in a row pointing at "the next part." Rather than defer a fourth time, filed directly as **#249** with no part assignment — pick up whenever, referenced here so it has a real home instead of dying in a doc.
   - A genuinely new one-hop browsing view on the entity-detail page itself ("meetings connected through shared people/projects") — declined. Read in context, the issue's "graph hub" language is about topic pages being a good *anchor point* for #242's future retrieval, not a request for new detail-page UI. Building it would mean new `app.py` query shapes, a new `rack.js` tab, and `npm run build:js` — none of which trace back to a consumer this plan or #241's stated scope actually needs yet.

## The #242 boundary

**Delivered here, for #242 to consume once it gets its own `/plan` pass**: `expand_by_entity_graph` (retrieval-method-agnostic by construction — any future seed-scoring mechanism, e.g. semantic embedding similarity, can call it as long as it produces positive/higher-is-better seed scores), `merge_expansion` (independently reusable if #242 ever needs to merge more than two ranked sources), and one working precedent (`search_transcripts_snippets_expanded` + `?expand=1`) to follow or diverge from with reasoning.

**Explicitly #242's problem, not touched here**: LLM answer synthesis, citation/provenance formatting for graph-derived (not directly-matched) results, the semantic-embedding retrieval phase #242's placeholder body already sketches, any decision to default `expand=1` on or build a UI for it, and module naming for #242's own retrieval code.

## Code touchpoints (files + symbols, no line numbers)

- `services/search.py`: `merge_expansion`, `expand_by_entity_graph`, `search_transcripts_snippets_expanded`, module constants (`_EXPANSION_DECAY_DEFAULT`, `_HOP_ELIGIBLE_TYPES`, `_MAX_EXPANSION_SEEDS`, `_MAX_ENTITIES_PER_SEED`, `_MAX_TRANSCRIPTS_PER_ENTITY`, `_MAX_ENTITY_DEGREE`).
- `app.py`: `GET /api/search` gains `expand: bool = Query(False)`.
- `tests/test_search.py`: new bm25-sign pinning test (see below), landed ahead of/alongside this part since it pins existing, previously-unpinned behavior this part is the first thing to actually depend on. New route-level tests for `expand=1`/`expand` omitted.
- `tests/test_search_expansion.py` (new): pure `merge_expansion` tests, DB-fixture two-hop/tenant/cap tests.

## Open questions

None blocking. One forward note: if `expand=1` is ever exposed in `rack.js`'s search UI, that slice will need to render `match_source: "graph"` rows distinctly (no real snippet, a "related via {entity name}" label instead) — not this part's problem, but worth remembering when that slice gets planned so it doesn't rediscover the distinction from scratch.

## Rough phasing / checklist

**Prerequisite fix, lands first**
- [ ] `tests/test_search.py`: bm25 sign/ordering pinning test. **Use 3+ transcripts with the search term absent from at least one** — a 2-document corpus risks bm25's IDF term going non-negative (a term appearing in most/all documents can score non-negative under bm25's formula), which would make the pinning test itself flaky rather than a reliable pin.

**Primitive**
- [ ] `services/search.py`: `merge_expansion` (explicit seed-precedence logic, not order-dependent-by-accident — see the bug called out in Proposed approach §2), `expand_by_entity_graph` (three batched queries, both tenant checks, degree cutoff, three fan-out caps)
- [ ] Unit tests (pure): seed always wins even against a numerically higher decayed score, max-not-sum across multiple entity paths, sort + `match_source` tagging, empty-input cases

**Integration**
- [ ] `services/search.py`: `search_transcripts_snippets_expanded`
- [ ] `app.py`: `expand` query param on `GET /api/search`, default `False`
- [ ] DB-fixture tests: two-hop correctness, tenant isolation at hop 1 AND hop 2 (two distinct code paths, two distinct tests), incomplete-transcript exclusion, decision/action_item entities produce no edge, each fan-out cap, degree-cutoff exclusion
- [ ] Route tests: `expand` omitted/false leaves today's response byte-identical (proves the additive-only claim), `expand=1` surfaces a `match_source: "graph"` row with the expected shape

**Filed, not scheduled**
- [x] #249 (reverse-direction co-occurrence on the entity-detail page) — filed during this part's design, not assigned to any of the five tracked parts

## Testing considerations

Most of this is genuinely unit-testable with zero DB (`merge_expansion`'s pure logic), unlike the initial assumption that a DB-query-shaped feature would be mostly untestable without one — worth noting since it means the pure-function split is doing real work here, not just following part 1's pattern for its own sake. The DB-fixture tests seed `Entity`/`EntityMention` directly, no LLM call anywhere in this part to stub. No `rack.js`/browser test needed — this part ships zero frontend surface.
