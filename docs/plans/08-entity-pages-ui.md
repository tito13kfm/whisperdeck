# Entity pages UI: browsable person/project/decision/action-item views

> One-line status: Planned (design pass done via exploration + Plan-agent design + advisor review). Part 2 of 5 on the meeting-knowledge-layer master tracker (#241). Blocked on part 1 (#245) landing first — this plan assumes `Entity`/`EntityMention` exist exactly as specified there.

## Motivation

Part 1 (#245) gives every account a background job that extracts and deterministically merges people, projects, decisions, and action items into `Entity`/`EntityMention` rows — but with no frontend surface, that data is invisible except through a narrow per-transcript verification route. This plan adds the actual payoff the master issue describes: a "Project X" page listing every meeting that touched it, the decisions made, and the open action items — reachable from a new top-level nav item.

Backend routes + `rack.js`/`index.html` UI only. No schema redesign of part 1's `Entity`/`EntityMention` tables beyond one additive column (see below). Parts 3–5 (topic grouping, graph-decay retrieval, staleness curation) are out of scope.

## Proposed approach

**1. One additive schema change: `Entity.status`.** The master issue's own framing needs a way to tell an open action item from a done one, which part 1's schema doesn't have. Add `status = Column(String(16), nullable=True, default="open")`, meaningful only for `type == "action_item"`, values `open`/`done`, validated at the route (not the column — SQLite has no enum constraint, same reasoning part 1 used for not enum-constraining `Entity.type`). Because part 1 may already be deployed on some installs before this lands, this needs **both** a model-level column (for fresh/co-timed installs) and an `ensure_columns(engine, "entities", {"status": "TEXT DEFAULT 'open'"})` call in `init_db()` (for installs that already ran part 1 alone) — using a SQL-level `DEFAULT 'open'` clause, not bare `TEXT`, matters: this codebase has a prior incident (`docs/superpowers/plans/2026-07-07-queue-audit-llmjob-auto-retry.md`) where `ALTER TABLE ADD COLUMN` without a default backfilled existing rows with `NULL` instead of the intended default. Every read path treats `status` as `entity.status or "open"` regardless, as defense in depth.

**Cross-cutting fix required in #245, not deferred**: `status` is the first user-authored field on `Entity`, and part 1's orphan-auto-delete rule (any zero-mention `Entity` gets deleted) predates it. Without amendment, marking an action item "done" and having it later drop out of extraction would silently delete the user's mark, and a future re-extraction would bring it back as a fresh `status="open"` row. Resolved by amending #245: `delete_orphaned_entities` only deletes when `status IS NULL OR status = 'open'`. See #245's amendment comment and `docs/plans/07-entity-extraction-core.md`'s updated §5 for the full reasoning — this is a decision already made and written into part 1's doc, not an open question for this plan.

**2. Two new backend routes plus one PATCH**, all following the existing scoped/404 ownership pattern (`Model.id == id, Model.user_id == current_user.id`), using `Entity.user_id` directly (no join through `Transcript` needed, since part 1 gave `Entity` its own `user_id` column specifically for this):

- `GET /api/entities?type=&q=&limit=` — list, `type` one of the **five** kinds (400 on anything else; `topic` added during part 3 review — see docs/plans/09-topic-grouping.md), `q` a case-insensitive substring match against `name` (400 over 500 chars, mirroring `list_transcripts`'s existing guard), capped `limit` (default/max ~200), ordered by `updated_at desc`. No server-side offset pagination — this codebase has none anywhere (confirmed by grep), and a capped-but-generous single GET matches the one list-browsing convention that exists (the Transcripts bank list's `?limit=100` + client-side filter).
- `GET /api/entities/{id}` — entity fields plus `meetings` (every transcript that mentions it, via `EntityMention` join) and, for `person`/`project`**/`topic`** types (the `topic` branch added per part 3 — topic pages are explicitly framed as "graph hubs" that should surface co-occurring decisions/action-items), `decisions`/`action_items` via a co-occurrence query: entities of type `decision`/`action_item` sharing at least one `transcript_id` with this entity's mentions. **No entity-to-entity link table needed for this** — part 1's `Entity`+`EntityMention` schema is sufficient for a same-meeting co-occurrence view; a true weighted graph (beyond "showed up in the same room") is part 4's job, not this one. (Extending this same route to *also* surface co-occurring people/projects on a topic's page — the reverse direction — is a genuinely new query shape, not a tuple edit; deferred to part 4 rather than bundled here unpriced.)
- `PATCH /api/entities/{id}` with `{"status": "open"|"done"}` — 400 on any other value, 400 if the target entity isn't `type="action_item"` (status is meaningless elsewhere; validate-then-reject rather than silently accept nonsensical state, same posture as this codebase's existing `kind` validation on transcript updates). No other fields are patchable in this slice — renaming/retyping an entity would fight part 1's deterministic-merge identity, not something a UI click should touch.

**3. Two new pages in `rack.js`/`index.html`: `entities` (list) and `entity-detail`.** Adding a page here is mechanical and well-precedented — the routing layer is a flat `PAGES` array plus a `loaders` object-literal lookup in `navigate()`, not scattered conditionals; the only per-page cost is one nav button + one page-div mount in `index.html`, one `PAGES` entry, and one `loaders` entry in `rack.js`.

- **List page** (`loadEntities`/`renderEntitiesBody`): a type-filter tab strip (`All | People | Projects | Decisions | Action items | Topics` — `Topics` added per part 3), reusing the same LED-topped tab-button visual language the transcript-detail page already established, rather than four permanently-visible sections (too much scroll for a first cut with sparse data) or one flat mixed list (the four types have too different a "name" shape — short noun vs. full sentence — to read well interleaved). **Fetch once, filter twice, client-side**: one `GET /api/entities` with no `type` param on page load, cached in a module-level `entityListCache`, with both the type-tab and the free-text query filtering that single cached array in memory — mirroring the Transcripts bank list's `bankListCache`/`S.bankQuery` pattern exactly, so switching tabs never re-fetches and a typed query always filters the same data regardless of which tab is active. (An earlier draft of this plan had the type tab re-fetch from the server while the query filtered client-side — cut during review because it means the same query string can filter two different underlying datasets depending on which tab is active, a confusing and unnecessary boundary once a single fetch is cheap enough to filter twice in memory.) Each card shows type, name, meeting count, and (for action items) a status chip — no click-to-toggle on the list page itself, that lives only on the detail page. Clicking a card calls `navigate('entity-detail', id)`.
- **Detail page** (`loadEntityDetail`/`renderEntityDetail`/`renderEntityDetailBody`): mirrors the transcript-detail page's shape (one fetch, a small tabs array tracked on `S`, a body-mount switched by active tab) but deliberately drops the job-polling machinery (`scheduleDetailPoll`/`_jobFingerprint`/`detailLoadGen`) that pattern also carries — entity detail data never changes from a background job on this page, only from navigation or an explicit PATCH-then-refetch. Tabs: `meetings` always; `decisions`/`action-items` for `person`/`project`**/`topic`** types (the `topic` case added per part 3 — a paired edit with the route-level tuple above; editing one without the other leaves the API returning data a tab never renders, or a tab rendering against a 400) (falls back off a stale tab the same way the transcript-detail page already handles kind-specific tabs). A meeting row click calls `navigate('detail', transcriptId)` — the one required click-through back into existing UI. An action-item row gets a "Mark done"/"✓ Done" toggle button calling the new `PATCH` route, then a full re-fetch of the detail payload (simplest correct refresh, since toggling one item can change the same co-occurrence lists the page is already showing).

**4. `entities_job` / transcript-detail wiring — deliberately not part of this plan.** Part 1's doc flagged this as a part-2 decision point, but on review it doesn't belong here: nothing this plan builds consumes the transcript-detail payload (`/api/entities` and `/api/entities/{id}` are the only routes these pages call), so there's no consumer-driven reason to add a field to `_serialize_transcript`. Cut entirely; left for whoever eventually wants live job-progress chrome on the transcript-detail page to scope as its own small slice. The pre-existing, unrelated `tagging_job`/`_jobFingerprint` bug this surfaced is filed separately as #246 rather than bundled into this feature's diff.

## Code touchpoints (files + symbols, no line numbers)

- `database/__init__.py`: `Entity.status` column (model-level); `ensure_columns(engine, "entities", {"status": "TEXT DEFAULT 'open'"})` in `init_db()`.
- `services/entities.py` (part 1's module): `delete_orphaned_entities`'s zero-mention check gains the `status IS NULL OR status = 'open'` exemption (this is written into #245's plan directly, not a part-2 patch onto already-shipped code).
- `app.py`: `GET /api/entities`, `GET /api/entities/{id}` (co-occurrence query), `PATCH /api/entities/{id}`; a `_serialize_entity` helper.
- `static/rack.js`: `PAGES` array; `loaders` object; `S.entityId`/`S.entityTab`/`S.entityQuery`/`S.entityType` fields (added to the logout/account-switch state-reset block too — this codebase has a specific prior bug here, issue #54, cross-account state leakage); module-level `entityListCache`/`entityDetailData`; `navigate()`'s data-stash line and rail-active-toggle line, both gaining an `entity-detail`/`entities` case; `loadEntities`/`renderEntitiesBody`; `loadEntityDetail`/`renderEntityDetail`/`renderEntityDetailBody`.
- `static/index.html`: one `<button class="rail-btn" data-nav="entities">` in the rail; two page-div mounts, `#page-entities` and `#page-entity-detail`. These must land in the same change as the `PAGES` array edit — `navigate()`'s per-page `$('page-' + p)` lookup dereferences on every navigation if a `PAGES` entry has no matching mount.
- Build step: `npm run build:js` (esbuild bundles `rack.js` → the `rack.min.js` that `index.html` actually loads) is a required step before any of this is visible at runtime, not an assumed side effect of editing the source file.

## Data model / schema changes

```python
# database/__init__.py — additive column on part 1's Entity model
status = Column(String(16), nullable=True, default="open")  # action_item-only: open | done
```

Plus the `ensure_columns` call in `init_db()` as described above. No other schema changes — `EntityMention` is untouched, and the co-occurrence view in `GET /api/entities/{id}` is computed at read time from existing rows, not a new table.

## Research notes / decisions made during design review

- **List-page fetch strategy** — resolved in favor of one unfiltered `GET /api/entities` cached client-side, with both the type-tab and the free-text query filtering that same cached array (see Proposed approach §3). An earlier draft had the type tab re-fetching server-side while the query filtered client-side; cut for the reason described there.
- **`status` vs. part 1's orphan-delete rule** — resolved in favor of amending #245's `delete_orphaned_entities` with a status-aware exemption, rather than either accepting the data-loss path or dropping `status` from `Entity` entirely. See Proposed approach §1.
- **`entities_job` transcript-detail wiring** — resolved by cutting it from this plan entirely (see Proposed approach §4), rather than folding it in as a "sixth deliverable" the way an earlier draft did. The pre-existing `tagging_job` fingerprint bug this surfaced is issue #246, filed separately.
- **PATCH precedent** — the existing partial-update route to mirror is `update_transcript` (`app.py`), not `/api/settings` (which is a whole-object `PUT`, not a partial `PATCH` — checked directly rather than assumed).
- **No entity-to-entity link table** — confirmed part 1's `Entity`+`EntityMention` schema is sufficient for the co-occurrence query this plan needs (decisions/action-items sharing a transcript with a person/project). A weighted, multi-hop graph is part 4's concern, not this one.

## Open questions

- Whether the entities-list rail button needs a live count badge (matching Queue/Costs/Voices' existing badge convention) — skipped for this first cut (needs `/api/status`-style wiring with no clear payoff yet), but worth reconsidering once real usage exists.
- Whether "Mark done" should have any undo/history, or whether a plain toggle is enough for a first cut — plain toggle chosen here; revisit only if it becomes a real user complaint.

## Rough phasing / checklist

**Schema (amends #245, blocked on it landing)**
- [ ] `database/__init__.py`: `Entity.status` column (model-level)
- [ ] `database/__init__.py`: `ensure_columns(engine, "entities", {"status": "TEXT DEFAULT 'open'"})`
- [ ] `services/entities.py`: confirm/land the `status`-aware exemption in `delete_orphaned_entities` (per #245's amendment)

**Backend routes**
- [ ] `app.py`: `GET /api/entities` (type/q filter, capped limit, `_serialize_entity`)
- [ ] `app.py`: `GET /api/entities/{id}` (meetings join + co-occurrence query, `.distinct()`)
- [ ] `app.py`: `PATCH /api/entities/{id}` (status-only, type-gated, 400/404 validation)

**rack.js / index.html**
- [ ] `index.html`: nav button + two page-div mounts
- [ ] `rack.js`: `PAGES`, `loaders`, `S` fields (incl. logout-reset block), module-level caches, `navigate()` touch points
- [ ] `rack.js`: `loadEntities`/`renderEntitiesBody` (type-tab strip + client-side query filter over one cached fetch)
- [ ] `rack.js`: `loadEntityDetail`/`renderEntityDetail`/`renderEntityDetailBody` (meetings/decisions/action-items tabs, status toggle, click-through to `detail`)
- [ ] `npm run build:js`

**Tests**
- [ ] `tests/test_entities_routes.py` (new): list (empty/filtered/400s), detail (shape, co-occurrence correctness with an overlapping-vs-non-overlapping decision fixture, 404 scoping), PATCH (valid toggle, 400s, 404 scoping), brand-new-user empty-list shape
- [ ] `tests/e2e/test_entities_ui_e2e.py` (new, `pytest.mark.e2e`, same skeleton as `tests/e2e/test_costs_ui_e2e.py`): nav click reveals the page; empty state with zero entities and no console errors; a DB-seeded entity+mention renders a card, click navigates to detail, meetings tab shows the transcript; meeting-row click-through lands on the transcript detail page; action-item status toggle flips without a stuck busy state
- [ ] Check `tests/e2e/test_bundle_globals.py` for a fixed page/global list that needs `entities`/`entity-detail` added

## Testing considerations

- Route tests need no browser — list/detail/PATCH logic, the co-occurrence query, and validation are all provable at the HTTP/DB layer, same tiering part 1 used.
- This slice adds a brand-new nav item and two new pages — exactly the class of change this project already treats as needing at least a scripted browser pass (precedent: `tests/e2e/test_costs_ui_e2e.py`, written for the structurally identical "new nav button + new dashboard page" addition in issue #210). `navigate()`'s page-swapping and the boot-time rail-button wiring are the kind of "looks right in the diff, breaks at runtime" failure route tests can't catch, since they never load `index.html`/the bundled JS.
- Seed entities directly at the DB layer for e2e fixtures rather than waiting on a real LLM job to run — there's no LLM call in this slice at all, so there's nothing to stub, just fixture data to insert.
