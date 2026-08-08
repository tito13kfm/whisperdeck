# Self-audit — issue #286 (Voice dump: frontend kind picker + board section)

Branch: `worktree-issue-286-voice-dump-board`. Target resolved from tracking issue #261 in Phase 0.

**This workflow runs no independent-model audit pass.** Opencode's `/issue` has one (Oracle, its Phase 3.75); `/issue-claude` does not. Everything below is self-review, plus one Sonnet subagent that wrote and ran the browser tests. Independent review happens separately, via opencode's `/audit-pr` after this PR is opened. Do not read a clean self-audit here as a reviewed change.

---

## Promises made in `investigation.md`

### The API contract as merged (not as the issue described it)

- [x] Board reads `GET /api/voice-dump-items` — delivered, `api('/api/voice-dump-items')` at `static/rack.js:2711`
- [x] Reads envelope key `items`, NOT `voice_dump_items` — delivered, `const items = data.items || [];` at `static/rack.js:2713`; asserted end-to-end by `test_dumpnotes_populated_board_renders_cards_and_order` (two seeded rows render as two cards)
- [x] Renders `title`, body excerpt, `note_type` badge, `transcript_title`, `created_at` — delivered: `typeLabel` at `static/rack.js:2726`, `preview` from `n.body` at `static/rack.js:2727`, `timeAgo(n.created_at)` at `static/rack.js:2734`, `n.title` at `static/rack.js:2736`, `n.transcript_title` at `static/rack.js:2737`
- [x] Most recent first — delivered by consuming the route's own `ORDER BY VoiceDumpItem.created_at.desc()` with no client re-sort; asserted with exact list equality `titles == ["Second noted", "First noted"]` in `test_dumpnotes_populated_board_renders_cards_and_order`
- [x] No status/draft filter exists to express, so none was added — confirmed: `VoiceDumpItem` rows are only created by the finalize route, drafts live in `LlmJob.result_json`

### The kind picker: the MFD Mode wheel, not `startLiveCapture()`

`investigation.md` established that the issue's framing ("value feeds into existing `startLiveCapture()`") points at the wrong function: `startLiveCapture()` takes no kind argument at all. Kind travels via `S.mode` into `startJob()`'s `form.append('kind', S.mode)`. The genuinely unwired control was the MFD "Mode" wheel.

- [x] `mfdCatDefs()` mode option added — delivered, `opts: ['Auto', 'Meeting', 'Dictation', 'Voice Note', 'Voice Dump']` at `static/rack.js:1737`, with the matching `S.mode === 'voice_dump' ? 4` arm on the `idx` ternary at `static/rack.js:1738`
- [x] `mfdNav()` value cycle extended — delivered, `S.mode = ['auto', 'meeting', 'dictation', 'voice_note', 'voice_dump'][newIdx]` at `static/rack.js:1821`
- [x] `mfdSingleSpeaker()` covers the new kind — delivered at `static/rack.js:1723`; without this a voice dump would have shown a live Speakers/diarization wheel that `dictation` and `voice_note` both lock out
- [x] `S.mode` doc comment no longer stale — delivered at `static/rack.js:41`
- [x] `startLiveCapture()` left untouched — confirmed, it is absent from the diff; it never read kind, so "recording with kind voice_dump starts live capture normally" holds by construction
- [x] Mode wheel actually renders the new option in a browser — `test_transcribe_mode_wheel_offers_voice_dump` drives the real `syncTranscribe → renderMfd → renderMfdScreen` pipeline and asserts the Mode row's rendered value `== "Voice Dump"`

### Nav registration: all four points `investigation.md` enumerated

- [x] Rail button — delivered, `<button class="rail-btn" data-nav="dumpnotes">` at `static/index.html:74`, label `Dump notes`
- [x] Page container — delivered, `<div class="page" id="page-dumpnotes"></div>` at `static/index.html:120`. This was flagged as the highest-risk omission: `navigate()` does `$('page-' + p).classList.toggle(...)` with no null check, so a missing container breaks navigation to *every* page
- [x] `PAGES` array — delivered, `'dumpnotes'` at `static/rack.js:416`
- [x] `loaders` map — delivered, `dumpnotes: loadVoiceDumpItems,` at `static/rack.js:451`
- [x] Active-state toggle needs no change (generic over `data-nav`) — confirmed, and the `data-nav` value matches the `PAGES`/loaders key exactly; `test_dumpnotes_rail_button_navigates` asserts the button gains `active` and `#page-dumpnotes.active` appears

### Sibling sweep items

- [x] `KIND_LABELS` gained `voice_dump: 'VOICE DUMP'` — delivered at `static/rack.js:3391`. In scope because the Mode wheel can now produce a `voice_dump` LLM job, which lands on the Queue page; without it the Queue shows the raw string `voice_dump`
- [x] `renderDetail()`'s `kindLabel` maps `voice_dump` — delivered at `static/rack.js:4757`. In scope because a dump board card navigates straight to that detail page, which would otherwise print the literal `Voice_dump`
- [x] No frontend kind allowlist exists to update — confirmed, the validation lists are server-side (`app.py:1458`, `1538`, `2088`) and already contain `voice_dump` from #283

### Build artifact

- [x] `static/rack.min.js` and `.map` regenerated — delivered. `static/index.html` loads `/static/rack.min.js`, so a source-only edit is invisible in the browser
- [x] Freshness proven, not assumed — a fresh `esbuild static/rack.js --bundle --minify` of the final source is byte-identical to the committed bundle after stripping the trailing `sourceMappingURL` comment (216001 bytes both sides). `static/rack.min.css` rebuilds byte-identical (CSS untouched)

---

## Issue #286's own acceptance criteria, one by one

- [x] **"Recording with kind `voice_dump` starts live capture normally"** — met. `startLiveCapture()` is unmodified and kind-agnostic; the Mode wheel now reaches `voice_dump`, and `startJob()` posts it via the untouched `form.append('kind', S.mode)`. See the `[decision]` line below for what was *not* driven.
- [x] **"Board page shows new 'Dump Notes' section with finalized items"** — met, `loadVoiceDumpItems()` at `static/rack.js:2701`, verified in a real browser by `test_dumpnotes_populated_board_renders_cards_and_order` and `test_dumpnotes_empty_state`.
- [x] **"Existing Voice Notes board section unaffected"** — met, and specifically re-verified because `loadVoiceNotes()` *was* touched (see the refactor line below): `test_voicenotes_board_still_renders_after_refactor` seeds a real `todo` note with `structured.items` and asserts the card and its `Buy milk` structured row both still render.
- [x] **"NOTE_TYPE_LABELS/COLORS reused from existing constants"** — met, `NOTE_TYPE_COLORS[n.note_type]` at `static/rack.js:2725` and `NOTE_TYPE_LABELS[n.note_type]` at `static/rack.js:2726`, resolving against the same `const NOTE_TYPE_LABELS` block at `static/rack.js:4547` that the voice-notes board uses. Asserted at the rendered-badge level (`note_type='general'` renders `Note`, `note_type='todo'` renders `Todo`).

---

## Decisions the issue did not ask for

- `[decision]` **Mode wheel label is `Voice Dump`, not the issue's `"Audit / stream-of-consciousness dump"`** — the issue specifies that label for "the record-start dropdown". The two bulk-import `<select>` dropdowns already carry that exact long label (added by #283, unchanged here). The actual record-start control turned out to be a fixed-width VFD wheel whose other options are one or two words; a 38-character option would not fit its readout. The full phrase went into the wheel's `desc` help text at `static/rack.js:1736` instead. Not specified by the issue because the issue did not identify this widget.
- `[decision]` **Nav item has no count badge** — `nav-badge-voicenotes` is fed by `/api/status`'s `voice_notes` field. No equivalent count field exists for voice-dump items on `/api/status`, and #285 did not add one. The new button uses an id-less `<span class="badge">`, matching Bulk/Assistant/Settings. Adding a backend counter was excluded as scope creep into #285's merged surface.
- `[decision]` **Dump cards have no Discard button** — voice-note cards have one because `DELETE /api/voice-notes/{id}` exists. There is no equivalent delete route for `VoiceDumpItem`; discarding happens pre-finalize on the Dump Review tab, which is #287. A button with no endpoint would have been dead UI.
- `[decision]` **Extracted `noteStructuredBits()` rather than duplicating 22 lines** — `static/rack.js:2609`, now called from both `loadVoiceNotes()` (`static/rack.js:2661`) and `loadVoiceDumpItems()` (`static/rack.js:2728`). Both item kinds come out of the same `_structure_from_text` chain, so their `structured` payloads are the same shape; two copies of this renderer would silently drift. This does modify `loadVoiceNotes()`, which the issue's "existing Voice Notes board unaffected" criterion guards, hence the dedicated browser test above.
- `[decision]` **Left the detail-page Mode-toggle 3-state cycle alone** (`static/rack.js:4946`: `t.kind === 'meeting' ? 'dictation' : t.kind === 'dictation' ? 'voice_note' : 'meeting'`). Clicking it on a `voice_dump` transcript silently resets the kind to `meeting`. Real trap, but pre-existing: `voice_dump` transcripts have been creatable via the bulk-import picker since #283, and the issue explicitly defers detail-page work to #287. Flagged rather than fixed.
- `[decision]` **Added a general nav-wiring test, not just a `dumpnotes`-specific one** — `tests/test_static_nav_wiring.py` cross-checks all four registration lists for *every* nav item, not only the new one, because the failure mode is a whole class.

---

## Tests: every new test, with its mutation check

New file `tests/test_static_nav_wiring.py` (7 tests, source-parsing, runs in the default suite):

```
[x] test_every_rail_nav_target_is_a_registered_page — mutation check: removing 'dumpnotes' from PAGES → red? yes
[x] test_every_pages_entry_has_a_page_container — mutation check: deleting the page-dumpnotes div from index.html → red? yes (demonstrated, restored by inverse edit)
[x] test_every_named_loader_function_exists — mutation check: pointing loaders['dumpnotes'] at a nonexistent function name → red? yes (demonstrated, restored by inverse edit)
[x] test_dump_notes_board_is_registered_at_all_four_points — mutation check: both mutations above → red? yes (failed on both)
[x] test_committed_bundle_contains_every_page_id — mutation check: running before the esbuild rebuild → red? yes (observed as the genuine first run: "page ids present in static/rack.js but not in the committed static/rack.min.js: ['dumpnotes']")
[x] test_every_rail_nav_target_has_a_loader / test_every_page_container_is_in_pages — guarded against vacuity by the fixture's own `len(...) >= 10` parse assertion, so a regex that silently stops matching fails loudly instead of passing on empty lists
```

New file `tests/e2e/test_voice_dump_board_e2e.py` (6 tests, `pytest.mark.e2e`, real headless Chromium against a real uvicorn):

```
[x] test_dumpnotes_rail_button_navigates — mutation check: removed 'dumpnotes' from PAGES → red? yes (timeout waiting for #page-dumpnotes.active)
[x] test_dumpnotes_empty_state — mutation check: changed `if (!items.length)` to `if (false)` → red? yes
[x] test_dumpnotes_populated_board_renders_cards_and_order — mutation check: replaced loadVoiceDumpItems() body with `return;` → red? yes
[x] test_voicenotes_board_still_renders_after_refactor — mutation check: changed `n.note_type === 'todo'` to `'xtodo'` inside noteStructuredBits() → red? yes, and only this test went red, confirming it isolates the refactor
[x] test_dumpnotes_navigation_has_no_console_errors — mutation check: injected a ReferenceError after board render → red? yes, failed on `assert errors == []` with the injected error captured
[x] test_transcribe_mode_wheel_offers_voice_dump — mutation check: removed the `S.mode === 'voice_dump' ? 4 :` clause from the mode wheel idx ternary → red? yes ('Auto' == 'Voice Dump' failure)
```

Every mutation probe was reverted with the exact inverse edit (never `git checkout` / `git stash` / `git restore`, which would have wiped the rest of the uncommitted work), `rack.min.js` was rebuilt after each revert, and the final source was grepped for probe residue: zero matches for `thisFunctionDoesNotExist`, `xtodo`, `loadVoiceDumpItemsRenamed`, `if (false)`.

## Full-suite runs (not just the new files)

- `pytest -q` (default suite, e2e deselected): **792 passed, 14 deselected** — same 792 as before this change, +6 deselected from the new e2e file.
- `node --test "tests_js/**/*.test.js"`: **8 passed, 0 failed**.
- `pytest tests/e2e/test_voice_dump_board_e2e.py -m e2e -q`: **6 passed**.
- Pre-existing e2e files re-run individually to confirm the new rail button broke no selectors: `test_browser_smoke.py` **2 passed**, `test_costs_ui_e2e.py` **1 passed**, `test_bundle_globals.py` **1 passed**. These three are the ones that select on the nav rail (`.rail-btn`, `button[data-nav='costs']`, `button[data-nav='queue']`); none use positional `nth-child` selectors, so the inserted button does not shift them.

## BLOCKED-VERIFICATION carried forward from Phase 3

The Phase 3 agent reported one:

```
BLOCKED-VERIFICATION: pytest tests/e2e -m e2e -q (the FULL e2e directory in one invocation)
→ urllib.error.HTTPError: HTTP Error 429: Too Many Requests — 6 passed, 8 errors
```

Cause: `/api/register` is rate-limited to 5 requests per 300s per client IP, and `tests/e2e/conftest.py`'s session-scoped `live_server` shares one in-process `app` module, so one rate-limiter bucket serves every e2e module. Each e2e file registers one user in a module-scoped fixture; past the fifth file, registration 429s.

**Pre-existing, not introduced here** — the agent reproduced the same pattern with the 7 original files and this run's new file excluded entirely (6 passed, 2 errors). Independently re-checked above by running the nav-sensitive files one invocation per file, all green.

**Not a CI risk**: `.github/workflows/verify.yml` runs `scripts/verify.sh`, which runs a plain `pytest` (e2e is deselected by marker) plus `npm test`. The full-e2e-directory invocation is not part of the gate.

Recorded in `wrong-directions.md` as a follow-up (reset the rate-limiter bucket in the `live_server` fixture, as `tests/conftest.py`'s `client` fixture already does, or share one registered user across e2e modules).

Also observed and out of scope: `tests/e2e/test_detail_rapid_clicks.py::test_rapid_clicks_show_last_clicked_even_when_first_response_is_slow` fails even when run alone (`Page.evaluate: TypeError: Cannot read properties of undefined (reading 'resolve')`). Unrelated to #286, predates this branch, untouched here.

## Phase 1.5

Not triggered, and not silently skipped. The completion-race check is mandatory only when Phase 1 surfaces code that marks a job/task/state `"completed"` and then fires a further side effect in the same handler. This change is frontend-only: nav registration, one board loader, MFD wheel options, two label maps. Nothing in scope writes job state. No Fable call was made, and the Fable budget was not spent elsewhere.

## Mechanical checker

`python scripts/verify_self_audit.py .omo/runs/issue-286/worktree-issue-286-voice-dump-board/self-audit.md` was run before opening the PR. Verbatim output:

```
Auto-detected repo root: C:\Claude\whisperdesk\.claude\worktrees\issue-286-voice-dump-board
1 blocking finding(s), 0 advisory:

- STALE BUILD [build:js]: static/rack.min.js does not match a fresh build of static/rack.js
  (sizes: committed=216039b, fresh=216002b). Run `npm run build:js` (or the parent `build`
  script) and commit the result.
```

Zero citation findings: every `file:line` in this document matched a literal identifier at that location. `build:css` passed clean.

The one BUILD finding is the pre-existing condition described in `wrong-directions.md` §4, and the byte counts prove it: 216039 − 216002 = **37 bytes**, exactly `\n//# sourceMappingURL=rack.min.js.map`. The checker rebuilds with `package.json`'s declared `build:js` command, which omits `--sourcemap`; the committed artifact has always carried that comment. Nothing about this change causes it, and no rebuild can clear it while the script and the artifact disagree. Discussed further in `wrong-directions.md` §4 (`package.json`'s `build:js` omits `--sourcemap` while the committed artifact carries a `sourceMappingURL` comment, so the checker's byte-diff can never match for `rack.min.js` regardless of the change under review). Bundle freshness was instead proven directly, byte-for-byte modulo that one comment line, as recorded above.

---

# Round 2: response to the independent `/audit-pr` review (GPT-5.6 Luna)

The audit returned **BLOCK**: 1 blocking, 1 should-fix, 1 nit; honesty check clean (37/37 `[x]` lines verified, no false claims, no vacuous tests). All three findings were verified against the code before acting, and all three are accepted.

## Blocking: service worker serves the old bundle

- [x] **Accepted and verified.** `static/sw.js`'s fetch handler is cache-first for everything outside `/api/`, `activate` purges only caches whose name differs from `CACHE_NAME`, and `CACHE_NAME` derives from `CACHE_VERSION`. With `sw.js` unchanged the browser never sees a new worker, so `install` never re-fetches the precache list and existing clients keep the previous `rack.min.js` indefinitely. The feature would have been invisible to precisely the users who already had the app installed. Full reasoning in `wrong-directions.md` §6.
- [x] **Not a one-PR slip**: `git log 9d59417..HEAD -- static/rack.min.js` counts **17 commits** that changed the bundle since the last `CACHE_VERSION` bump. Every one shipped a bundle installed clients could not see.
- [x] `CACHE_VERSION` bumped to `'v3'` — `static/sw.js:11`
- [x] **Root cause fixed, not just the symptom.** The reviewer asked for a bump plus a test asserting `CACHE_VERSION != 'v2'`. A bump alone repeats a pattern that has now been missed 17 times, and that assertion hardcodes the literal it checks, so it goes stale at the next bump and then passes vacuously forever. Instead `sw_build_fingerprint()` (`app.py:3496`) hashes the precached first-party assets (`rack.min.js`, `rack.min.css`, `index.html`) and the `@app.get("/sw.js")` route (`app.py:3522`) appends a 12-hex fingerprint to whatever literal is on disk. A changed bundle therefore always yields a changed worker script, which installs, re-precaches under a new cache name, and purges the old one. No human step remains.
- `[decision]` **Kept the hand-maintained literal alongside the fingerprint** rather than replacing it. It stays useful for forcing invalidation on a change the asset bytes do not capture, such as editing the caching strategy inside `sw.js` itself. Not specified by the reviewer.
- `[decision]` **Scope added beyond issue #286**: this touches `app.py` and `static/sw.js`, neither named in the issue. Justified because the issue's own acceptance criterion ("Board page shows new Dump notes section") is false for existing users without it, but it is scope the issue did not ask for and is called out here as such.

### Tests for the blocking fix — `tests/test_service_worker.py` (3 new, in the default suite)

```
[x] test_sw_cache_version_includes_build_fingerprint — mutation check: dropped the "-" + sw_build_fingerprint() term from the /sw.js route's substitution → red? yes
[x] test_sw_fingerprint_changes_when_the_bundle_changes — mutation check: same → red? yes. Uses a monkeypatched BASE_DIR over a tmp_path static tree, so it changes "the bundle" without touching repo files
[x] test_sw_fingerprint_is_stable_for_unchanged_assets — mutation check: same → red? yes. Guards the opposite failure the reviewer's suggested assertion would not have caught: a timestamp- or random-derived version satisfies "it changed" while busting the cache on every single request
```

All three went red together on the one mutation (3 failed, 5 passed), and green again after the inverse edit restored it: **8 passed**. No `git checkout`/`git stash` used.

## Should-fix: mode-picker test injected state instead of driving the wheel

- [x] **Accepted.** The original `test_transcribe_mode_wheel_offers_voice_dump` set `window.S.mode = 'voice_dump'` and then asserted the render. That proves the renderer handles the value, not that a user can reach it or that `startJob()` posts it, so a regression in `mfdNav()`'s cycle array or in `form.append('kind', S.mode)` could have passed it while the acceptance criterion failed. Replaced with a test that clicks the real Mode category button and wheel chevrons, and intercepts `POST /api/transcribe` to assert the multipart `kind` field. Outcome, mutation checks, and whether the real START button could be driven are recorded in the round-2 test section below.

### Tests for the should-fix — `tests/e2e/test_voice_dump_board_e2e.py`

The old injection-based test was replaced by two that drive real UI:

```
[x] test_transcribe_mode_wheel_cycles_via_real_clicks — mutation check: removed 'voice_dump' from mfdNav()'s mode cycle array (static/rack.js:1821) → red? yes (stuck at 'meeting')
[x] test_transcribe_start_posts_kind_voice_dump — mutation check: changed form.append('kind', S.mode) to append 'meeting' in startJob() → red? yes (intercepted_kinds == ['meeting'] vs expected ['voice_dump'])
```

Both probes were restored by the exact inverse edit with a bundle rebuild after each, no `git checkout`/`stash`/`restore`. `static/rack.js` was grepped for probe residue: zero matches.

The wheel is driven only through real clicks (`#mfd-leftcol .mfd-btn[data-cat='mode']`, then `#mfd-btn-down`, then `#mfd-btn-ok`); `window.S.mode` is read to decide when to stop and to assert, never assigned. The file is loaded through the real `#file-input`, and the real `#key-play-a` START button is clicked. Vacuity guard: the test asserts `intercepted_kinds == ["voice_dump"]`, so a request that never fires fails rather than passing silently.

- `[decision]` **`startJob` added to `static/rack.js`'s `Object.assign(window, {...})` test-hook block** — a product-source change made for test portability, not required on this machine. `curProv().ready` is `True` here (moonshine's `check_health()` passes), so the real START button path is the one actually exercised; the export only backs a documented fallback for a machine where no provider is ready and the button is therefore disabled. Kept because that block is explicitly the repo's "supported test-hook surface" (added by #214) and `INSTALL.md` documents optional providers, so provider readiness genuinely varies per machine. It is currently a dead path on this machine, which is why it is disclosed rather than presented as covered.

## Nit: imprecise comment

- [x] **Accepted and fixed.** `static/rack.js:41` said "all three single-speaker kinds" while sitting above a five-value list. Now reads `auto | meeting | dictation | voice_note | voice_dump — the last three are single-speaker: they skip diarization and unlock their own post-pipeline sets`.

## On the audit's "undisclosed scope" note

The audit observed that the PR body describes a frontend-only change and confirmed the diff contains only the six claimed files, with the API/LLM files inherited from merged PR #293 rather than part of #294. That is correct and is not a finding against this PR. Note that the round-2 fix above changes that: `#294` now also touches `app.py` and `static/sw.js`, disclosed in the PR body and in the `[decision]` line above.

## Main checkout cleanliness

`git -C C:\Claude\whisperdesk status --porcelain` — clean (the `.omo/runs/` report files are gitignored).
