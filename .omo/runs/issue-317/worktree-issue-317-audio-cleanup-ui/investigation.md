# Issue #317 investigation -- "Audio cleanup stage has no UI"

Repo state read: worktree `issue-317-audio-cleanup-ui`, commit `cfa3b78` (2026-08-03 16:10:53 -0400), a fresh checkout of `origin/master`. All line numbers below are current as of that commit, not the issue's (stale) numbers.

## 1. The backend contract -- exact literal keys

### Settings keys read by `services/audio_cleanup.py`, with defaults from `services/settings.py`

All defaults live in `DEFAULT_SETTINGS` at `services/settings.py:13-62`. Every cleanup key is read via `dict.get(key, inline_default)` from inside `audio_cleanup.py`/`app.py`/`queue.py` -- there is no `TypedDict`/schema, just a plain dict.

| Key (verbatim) | Default value | Type | Default declared at | Read at |
|---|---|---|---|---|
| `cleanup_loudnorm_enabled` | `False` | bool | `services/settings.py:40` | `services/audio_cleanup.py:55` |
| `cleanup_loudnorm_target` | `-23.0` | float (LUFS) | `services/settings.py:41` | `services/audio_cleanup.py:80` (inline default `-23.0` repeated there) |
| `cleanup_highpass_enabled` | `False` | bool | `services/settings.py:42` | `services/audio_cleanup.py:56` |
| `cleanup_denoise_enabled` | `False` | bool | `services/settings.py:43` | `services/audio_cleanup.py:57` |
| `cleanup_vad_enabled` | `True` | bool | `services/settings.py:44` | `app.py:1353`, `services/queue.py:426` (NOT read inside `audio_cleanup.py` -- VAD is not implemented there, see below) |
| `cleanup_vad_min_silence_ms` | `100` (ms) | int | `services/settings.py:45` | `app.py:1355`, `services/queue.py:428` |
| `cleanup_vad_threshold` | `0.5` | float (0-1) | `services/settings.py:46` | `app.py:1354`, `services/queue.py:427` |
| `cleanup_hallu_enabled` | `False` | bool | `services/settings.py:47` | `app.py:1372`, `services/queue.py:429` |
| `cleanup_hallu_rep_window` | `3` | int | `services/settings.py:48` | `app.py:1375` (passed to `filter_hallucinations`; `services/queue.py:453` hardcodes `rep_window=3` instead of reading the setting -- see Sibling sweep / Section 5) |
| `cleanup_hallu_logprob_cutoff` | `-2.0` | float | `services/settings.py:49` | `app.py:1376` (queue.py:453 hardcodes `-2.0` too) |
| `cleanup_hallu_no_speech_cutoff` | `0.6` | float (0-1) | `services/settings.py:50` | `app.py:1377` (queue.py:453 hardcodes `0.6` too) |
| `cleanup_demucs_enabled` | `False` | bool | `services/settings.py:51` | `services/audio_cleanup.py:207` inside `cleanup_demucs` -- but `cleanup_demucs` itself is never called from app code (see below) |

Important correction to the issue's framing: **VAD is not part of `audio_cleanup.py` at all.** `cleanup_vad_enabled`/`cleanup_vad_threshold`/`cleanup_vad_min_silence_ms` are read only in `app.py` and `services/queue.py`, where they're forwarded as `vad_filter`/`vad_threshold`/`vad_min_silence_duration_ms` kwargs into the transcription provider call (`app.py:1353-1355`, `services/queue.py:426-428`, then `services/queue.py:445/447` `provider.transcribe(..., **vad_settings)`). So VAD (#237) is wired end-to-end already exactly like loudnorm/hallu -- it's a real, working backend path, just with no UI, same as the others. `audio_cleanup.py` has no VAD code whatsoever; the module docstring (`services/audio_cleanup.py:1-8`) describes the intended pipeline order (loudnorm/denoise -> VAD -> chunking -> transcribe -> hallucination filter -> optional Demucs) but VAD's actual implementation lives in the transcription backends, not this module.

### Public function signatures in `services/audio_cleanup.py`

```python
async def cleanup_audio(audio_path: str, output_dir: str, user_settings: dict) -> CleanupResult   # line 41
def filter_hallucinations(segments: list[dict], *, rep_window: int = 3, logprob_cutoff: float = -2.0, no_speech_cutoff: float = 0.6) -> list[dict]   # line 124
async def cleanup_demucs(audio_path: str, output_dir: str, user_settings: dict) -> str   # line 193
```
Non-public helpers: `_ffmpeg_bin()` (line 28), `_ffmpeg_available()` (line 33), `_find_longest_repeat(ngrams: list[tuple]) -> int | None` (line 176), plus the `CleanupResult` dataclass (line 15-21) and `CleanupError` exception (line 24-25).

Call-site status:
- `cleanup_audio` -- called from non-test code: `app.py:1222` (imported `app.py:44`). Live.
- `filter_hallucinations` -- called from non-test code: `app.py:1373` and `services/queue.py:453` (imported `app.py:44` and locally inside `_run_chunk_job` at `services/queue.py:452`). Live.
- `cleanup_demucs` -- **dead**. `app.py:44` imports only `cleanup_audio, filter_hallucinations` -- no `cleanup_demucs`. Grepping the whole repo for `cleanup_demucs` outside `services/audio_cleanup.py` and `services/settings.py` (which only declares its settings key) turns up exactly one other file: `tests/test_audio_cleanup.py` (imports it at line 8, exercises it at lines 191-210). Confirmed dead in production code -- this part of the issue is accurate.
- `_find_longest_repeat` -- private helper, called only from `filter_hallucinations:164` and directly by tests.

## 2. The existing settings pipeline, end to end (bitrate + chunk size as the working example)

1. **Render** -- `static/rack.js:5978` `async function loadSettingsPage()`. It fetches `settings = await api('/api/settings')` (`static/rack.js:5982-5987`, part of a `Promise.all`) and renders the "Audio prep & chunking" card at `static/rack.js:6023-6044`. The bitrate/chunk/parallel-uploads fields are rendered by a single `.map()` over a 3-tuple array at `static/rack.js:6026-6028`:
   ```js
   ${[['audio-bitrate', 'Upload bitrate', 'KBPS', settings.bitrate_kbps],
      ['audio-chunk', 'Split files over', 'MB', settings.chunk_threshold_mb],
      ['audio-parallel', 'Parallel uploads', 'MAX', settings.max_concurrent_chunks]].map(([id, label, unit, val]) => ` ... `)}
   ```
   All three render as plain `<input type="text">` boxes (`static/rack.js:6032`), not `<select>`/number inputs -- the issue's premise that these are "selects" is not quite right, they're free-text boxes parsed with `parseInt` on save.

2. **Boolean control pattern (for reference)** -- `auto_correct` is the one boolean in this same card, rendered as a custom toggle switch at `static/rack.js:6036-6041` (`class="tog ${settings.auto_correct ? 'on' : ''}"`, with `.tog-plate`/`.tog-track`/`.tog-paddle` spans), wired at `static/rack.js:6224-6229` by toggling `.classList.contains('on')` on click. **This is the existing pattern a new boolean cleanup toggle (loudnorm/highpass/denoise/hallu/demucs enabled flags) should reuse** -- there is no native `<input type="checkbox">` anywhere in this settings card.

3. **Save handler** -- `static/rack.js:6230-6242`, bound to `$('audio-save').addEventListener('click', ...)`. Builds:
   ```js
   const body = {
     bitrate_kbps: parseInt($('audio-bitrate').value, 10) || 128,
     chunk_threshold_mb: parseInt($('audio-chunk').value, 10) || 20,
     max_concurrent_chunks: parseInt($('audio-parallel').value, 10) || 4,
     auto_correct: $('audio-autocorrect').classList.contains('on'),
   };
   await api('/api/settings', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
   ```
   `PUT`, JSON body, all four fields sent together every time (not a per-field PATCH) -- a new cleanup card would follow the same "collect everything in this card, PUT the whole object" shape. Other cards in the same page do independent PUTs with a single field (`export_directory` at `static/rack.js:6249-6256`, `hf_token` at `static/rack.js:6171`), so a separate "Save cleanup settings" button doing its own PUT with just the cleanup keys is equally consistent with existing style.

4. **Backend route** -- `app.py:900-902`:
   ```python
   @app.put("/api/settings")
   async def put_settings(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
       return update_user_settings(db, current_user.id, data)
   ```
   No route-level schema/Pydantic model -- `data: dict = Body(...)` accepts anything. **The actual allowlist lives one layer down, in `services/settings.py:143`:**
   ```python
   patch = {key: value for key, value in updates.items() if key in DEFAULT_SETTINGS}
   ```
   This is the single most important fact for this issue: **the settings route does not reject unknown keys with an error -- it silently drops them.** `update_user_settings` (`services/settings.py:126-150`) filters the incoming `updates` dict down to only keys already present in `DEFAULT_SETTINGS` (`services/settings.py:13`), then merges the survivors atomically via `json_patch()` (`services/settings.py:145-148`). No type coercion or validation happens here at all -- whatever JSON value comes in for an allowed key is stored as-is (the frontend does the only coercion, e.g. `parseInt(...)`).

   **Consequence for the fix:** all twelve cleanup keys are *already* present in `DEFAULT_SETTINGS` (`services/settings.py:40-51`, added by #270). So **no backend change is required to accept them** -- `PUT /api/settings` will happily persist any of `cleanup_loudnorm_enabled`, `cleanup_loudnorm_target`, `cleanup_highpass_enabled`, `cleanup_denoise_enabled`, `cleanup_vad_enabled`, `cleanup_vad_min_silence_ms`, `cleanup_vad_threshold`, `cleanup_hallu_enabled`, `cleanup_hallu_rep_window`, `cleanup_hallu_logprob_cutoff`, `cleanup_hallu_no_speech_cutoff`, `cleanup_demucs_enabled` today, right now, if a client sends them. This is confirmed independently by existing tests that already PUT settings keys with no dedicated schema per-key (e.g. `tests/test_correction_inline_and_manual.py:43` `client.put("/api/settings", json={"auto_correct": False})`). The issue's framing that "settings keys can only be changed by editing the database" undersells this: they can already be changed via a raw PUT to `/api/settings` (e.g. curl/devtools) -- it's specifically the *UI* that's missing, not backend key acceptance.

   The one place a NEW key *would* need registering is `DEFAULT_SETTINGS` in `services/settings.py:13-62` itself -- but that registration already happened for all twelve cleanup keys under #270. If the panel design introduces any settings key not already in that dict (e.g. a "run order" toggle for Demucs vs. ffmpeg, floated as an open question in the issue body), *that* new key would need adding to `DEFAULT_SETTINGS` and would silently no-op without it.

5. **Persistence** -- SQLite via `sqlalchemy.text` raw SQL, `UPDATE users SET settings = json_patch(coalesce(settings, '{}'), :patch) WHERE id = :uid` (`services/settings.py:145-148`), inside `User.settings` (a JSON column, no dedicated settings table -- module docstring `services/settings.py:1-6`). Atomic single-statement merge chosen specifically to avoid a read-modify-write race between two settings cards saving close together (documented in the `update_user_settings` docstring, `services/settings.py:126-139`).

6. **Read/default application** -- `get_user_settings(db, user_id)` (`services/settings.py:117-123`): `return {**DEFAULT_SETTINGS, **stored}` -- spreads the stored per-user JSON over the defaults, so any key never set by the user falls back to `DEFAULT_SETTINGS` automatically. `GET /api/settings` (`app.py:895-897`) just calls this and returns the merged dict -- this is what `loadSettingsPage()` uses to prefill the form (`static/rack.js:5982-5983`, `settings.bitrate_kbps` etc. at render time, `static/rack.js:6026-6027`, `6038`).

## 3. Sibling sweep -- things the issue never named

- **A committed, stale-risk esbuild bundle exists and is what actually runs.** `static/index.html:7` loads `/static/rack.min.css`, and `static/index.html:155` loads `/static/rack.min.js` -- **not** `rack.js`/`rack.css` directly. Both `.min.js`/`.min.css` (plus `rack.min.js.map`) are committed files under `static/`. The build step is in `package.json`:
  ```json
  "build": "npm run build:js && npm run build:css",
  "build:js": "esbuild static/rack.js --bundle --minify --sourcemap --outfile=static/rack.min.js",
  "build:css": "esbuild static/rack.css --minify --outfile=static/rack.min.css"
  ```
  **Exact rebuild command for a rack.js-only change: `npm run build:js`** (or `npm run build` to also rebuild CSS if the cleanup panel needs new styles). This is not a hypothetical risk -- `tests/test_static_nav_wiring.py:107-155` (`test_committed_bundle_contains_every_page_id`) and `tests/e2e/test_bundle_globals.py` both exist specifically because a prior issue (#214/#286 per their docstrings) got bitten by exactly this: source-only edits to `rack.js` are invisible at runtime and even to some tests, because `index.html` never references it. **A source-only edit to `rack.js` for the cleanup panel would be genuinely invisible in the browser and would need `npm run build:js` re-run and the resulting `rack.min.js`/`rack.min.js.map` committed alongside it.**

- **Only one settings-rendering panel found.** `loadSettingsPage()` (`static/rack.js:5978`) is the sole "Rear service panel" / global settings page. No second settings page, no mobile-specific variant file (`static/` has only `rack.js`, `batch_aggregate.js`, `dump_review.js`, `sw.js` besides the bundle/HTML -- neither of the other two JS files touches `/api/settings`).

- **A per-job "advanced overrides" panel already exists and is the natural home for the design question the issue raises.** `static/rack.js:1888-1936` (`renderMfdAdvancedScreen`) plus `mfdAdvFieldDefs()` (`static/rack.js:1750-1763`) implement a "Fine Adjust -- per-job overrides" screen (labelled in the UI itself, `static/rack.js:1934`) reachable from the main transcribe page's "MFD" (multi-function display) widget. Its current fields are `speakerCount`, `title` (meeting title), `creativity` (temperature wheel), `context` (pasted context text) -- job-scoped state kept in the in-memory `S` object (`S.advSpeakerCount`, `S.advTitle`, etc.), not persisted to `/api/settings` at all. This is exactly the kind of "per-job advanced section" the issue's design question (global panel vs. per-job advanced section) is asking about -- it already has the UI chrome (wheel controls, text inputs, a field-list navigation model) that a per-job cleanup override could extend, as an alternative to (or supplement of) the global settings-panel route. Nothing in it references any `cleanup_*` key today.

- **Other GET `/api/settings` consumers besides the settings page**, confirming the settings blob is read from several unrelated call sites and none of them touch cleanup keys: `static/rack.js:2775` (bulk-import defaults), `static/rack.js:3082` (saving `bulk_defaults` back), `static/rack.js:5262`/`5276` (summarize/reformat provider+model defaults), `static/rack.js:5457` (rerun-picker correction provider default). None read or write cleanup keys -- confirms the cleanup keys genuinely have zero UI consumers anywhere in the frontend, not just in the obvious settings card.

- **Other backend read sites of cleanup keys beyond `audio_cleanup.py`** (full list, repo-wide grep for `cleanup_`):
  - `services/queue.py:426-429` -- reads `cleanup_vad_enabled`, `cleanup_vad_threshold`, `cleanup_vad_min_silence_ms`, `cleanup_hallu_enabled` for the chunked-job path (mirrors `app.py`'s inline path per the comment at `services/queue.py:417-418`).
  - `services/queue.py:453` -- calls `filter_hallucinations` but with **hardcoded** `rep_window=3, logprob_cutoff=-2.0, no_speech_cutoff=0.6` rather than reading `cleanup_hallu_rep_window`/`cleanup_hallu_logprob_cutoff`/`cleanup_hallu_no_speech_cutoff` from settings -- inconsistent with `app.py:1375-1377`, which does read them. This is a pre-existing backend inconsistency the issue doesn't mention (see Section 5) and worth flagging to whoever builds the panel, since a user who tunes those three dials from the new UI would see them apply for inline (non-chunked) local jobs but silently not apply for chunked jobs.
  - `app.py:1222/1353-1355/1372-1377` as already covered in Sections 1/2.

- **No existing test enumerates the settings route's accepted-key set or the settings UI's control list.** `services/settings.py:143`'s allowlist-by-filtering behavior is exercised incidentally (many tests PUT one key and assert it round-trips, e.g. `tests/test_reformatting.py:466-557`, `tests/test_bulk_import.py:381-412`), but no test asserts "these and only these keys are accepted" or walks `DEFAULT_SETTINGS` to check UI coverage. `tests/test_database_hotwords.py:2-43` imports `DEFAULT_SETTINGS` but only to assert `auto_correct is True`, unrelated to cleanup.

- **Existing boolean-toggle pattern**: covered in Section 2 point 2 above (`static/rack.js:6036-6041`/`6224-6229`, the `.tog`/`.tog-plate`/`.tog-track`/`.tog-paddle` custom switch). This is the shape a new panel should reuse for `cleanup_loudnorm_enabled`, `cleanup_highpass_enabled`, `cleanup_denoise_enabled`, `cleanup_vad_enabled`, `cleanup_hallu_enabled`, and `cleanup_demucs_enabled` (all six cleanup keys are booleans). There is no existing numeric-slider pattern in the codebase for the five numeric cleanup keys (`cleanup_loudnorm_target`, `cleanup_vad_min_silence_ms`, `cleanup_vad_threshold`, `cleanup_hallu_rep_window`, `cleanup_hallu_logprob_cutoff`, `cleanup_hallu_no_speech_cutoff`) beyond the plain `<input type="text">` + `parseInt`/`parseFloat` pattern already used for bitrate/chunk/parallel-uploads (`static/rack.js:6026-6032`).

## 4. e2e / test surface

Test files that touch settings:
- `tests/test_bulk_import.py:381-412` -- PUTs/GETs `bulk_defaults`.
- `tests/test_correction_inline_and_manual.py:43-74`, `tests/test_llm_jobs.py:377-435`, `tests/test_posthoc_reprocess.py` (many lines 54-422), `tests/test_assistant.py:303/344` -- all PUT `auto_correct` or `correction_provider`, unrelated to cleanup UI but exercise the same `PUT /api/settings` route.
- `tests/test_reformatting.py:466-557` -- PUTs/GETs `export_directory`.
- `tests/test_device_token.py`, `tests/test_device_token_auth.py:76` -- device-token sub-routes, not the main settings blob.
- `tests/test_database_hotwords.py:2,43` -- imports `DEFAULT_SETTINGS` for one assertion.
- `tests/test_audio_cleanup.py` (full file, 211 lines) -- unit tests for `cleanup_audio`, `filter_hallucinations`, `_find_longest_repeat`, `cleanup_demucs`; no settings-route or UI coverage at all, purely `services/audio_cleanup.py` in isolation.
- `tests/e2e/test_bundle_globals.py` -- loads the real served `rack.min.js` via `static/index.html`, logs in, asserts `window.navigate`, `window.S`, `window.syncTranscribe`, `window.renderDetail`, `window.curProv`, `window.logout`, `window.api` are present (the `REQUIRED_GLOBALS` list, `tests/e2e/test_bundle_globals.py:60`). Doesn't touch settings directly but is the test that would catch a forgotten bundle rebuild if a cleanup-panel change also touched the `Object.assign(window, {...})` export block.
- `tests/test_static_nav_wiring.py` -- nav-wiring cross-check (four-point registration for SPA pages) plus `test_committed_bundle_contains_every_page_id` (lines 147-155), which parses `rack.min.js` and fails if it's missing a page id present in `rack.js`'s `PAGES` array -- this is the test most likely to catch a stale bundle if the cleanup panel were, hypothetically, its own new page (it won't be, since it's a card inside the existing settings page, so this specific test wouldn't fire -- but it establishes the project's convention of verifying the bundle against the source).

No e2e test currently exercises `#page-settings`, `#audio-save`, or any settings-panel selector directly (no Playwright test navigates to the settings/"Rear service panel" page in this repo as of this commit) -- so there are no existing selector strings (`id`s/`data-*`/labels) inside settings-panel e2e tests that a new cleanup UI would need to keep in sync. The relevant selector conventions to follow, by analogy with the existing card, are plain `id="..."` attributes (`audio-bitrate`, `audio-chunk`, `audio-parallel`, `audio-autocorrect`, `audio-save`), not `data-*` attributes or ARIA labels -- a new cleanup card should follow the same `id`-based convention (e.g. `cleanup-loudnorm-enabled`, `cleanup-loudnorm-target`, `cleanup-save`, etc.) since nothing else in the file uses a different addressing scheme for form controls in this page.

## 5. What the issue gets wrong or omits

- **Cited line numbers are stale**, as expected -- `static/rack.js:5772-5773`/`5978-5979` in the issue vs. `static/rack.js:6026-6028` (render) / `static/rack.js:6230-6242` (save handler) / `static/rack.js:5978` (`loadSettingsPage` function start) in current code. `app.py:1222/1353/1373` are essentially still accurate (current: `1222`, `1353-1355`, `1372-1377` -- off by at most a couple of lines, functionally the same call sites). `services/queue.py:426/453` are exactly accurate.
- **`cleanup_demucs` really is uncalled from non-test code** -- confirmed by direct grep; only `tests/test_audio_cleanup.py` imports it. The issue's claim here is correct.
- **The settings route does not silently drop unknown keys in the sense of an error -- it drops them without complaint, and this needs no backend change for this issue**, because all twelve cleanup keys are already registered in `DEFAULT_SETTINGS`. The issue's claim "settings keys can only be changed by editing the database" is not accurate as stated: they can be changed today via a raw authenticated `PUT /api/settings` request (curl/devtools/any HTTP client) since `services/settings.py:143`'s allowlist already contains them -- the only thing missing is a UI form that sends such a request. This is a meaningful correction because it means the fix is *frontend-only* for the twelve keys that already exist; a backend change is needed only if the panel introduces a brand-new key not in `DEFAULT_SETTINGS` (e.g. any settings key implementing the "Demucs before/after ffmpeg chain" decision floated in the issue, if that becomes a stored toggle rather than a fixed pipeline order).
- **The issue omits that VAD (#237)'s settings live outside `audio_cleanup.py` entirely** (they're forwarded straight into provider `.transcribe()` calls in `app.py`/`services/queue.py`) -- a minor scoping point but relevant if someone reads only `audio_cleanup.py` expecting to find VAD logic there per the issue's `#270` framing.
- **The issue omits a real inconsistency worth fixing alongside the UI**: `services/queue.py:453` hardcodes the hallucination-filter thresholds instead of reading `cleanup_hallu_rep_window`/`cleanup_hallu_logprob_cutoff`/`cleanup_hallu_no_speech_cutoff` from settings, unlike `app.py:1375-1377`. Once a UI lets users tune those three values, this divergence becomes user-visible (chunked jobs would ignore the user's tuning).
- **The issue omits the committed-bundle hazard entirely.** Nothing in the issue mentions that `rack.js` isn't the served file -- `rack.min.js` is -- and that the fix must include a rebuild-and-commit step (`npm run build:js`) or the panel will not appear in the running app at all, despite `rack.js` having the correct code. Given the project already has two tests (`tests/test_static_nav_wiring.py`, `tests/e2e/test_bundle_globals.py`) built specifically around forgetting exactly this, and both exist because of prior real incidents (#214, #286 per their docstrings), this is the single highest-risk gotcha for whoever implements the panel.

## Call sites and entry points in scope for the fix

- `static/rack.js` -- add the cleanup card(s) inside `loadSettingsPage()` (render around `static/rack.js:6023-6045`, reusing the `.tog` boolean pattern at `static/rack.js:6036-6041` and the labeled-input pattern at `static/rack.js:6026-6035`) and a new save handler alongside `static/rack.js:6230-6242` that PUTs the twelve `cleanup_*` keys (all already accepted server-side, no backend change needed for these). If the design answer is "per-job advanced section" instead of/in addition to global settings, the relevant extension point is `mfdAdvFieldDefs()` (`static/rack.js:1750-1763`) and `renderMfdAdvancedScreen()` (`static/rack.js:1888-1936`).
- `static/rack.min.js` / `static/rack.min.js.map` -- **must be regenerated** (`npm run build:js`, or `npm run build` if `rack.css` also changes) and committed, since `static/index.html:155` serves the bundle, not the source file. This is not optional -- skipping it means the change has zero runtime effect.
- `static/rack.css` / `static/rack.min.css` -- only if the new card needs styles beyond what `unit unit--svc`/`t-cap`/`t-label`/`tog*` classes already provide; rebuild via `npm run build:css` if touched.
- `services/settings.py` -- only if the panel introduces a settings key not already in `DEFAULT_SETTINGS` (`services/settings.py:13-62`); all twelve existing `cleanup_*` keys need no change here.
- `services/queue.py:453` -- worth fixing in the same change (or a fast-follow) to read `cleanup_hallu_rep_window`/`cleanup_hallu_logprob_cutoff`/`cleanup_hallu_no_speech_cutoff` from settings instead of hardcoding, so the new dials actually affect chunked jobs consistently with `app.py`.
- No change needed to `app.py` (routes already generic), `services/audio_cleanup.py` (backend already complete per #270), or `tests/test_audio_cleanup.py` (backend-only, unaffected by a UI-only change) -- unless `cleanup_demucs` is wired up as part of closing #239, in which case `app.py:44`'s import line and the upload pipeline around `app.py:1217-1225` would need a new call site for `cleanup_demucs`, plus a new test exercising that call site from `app.py` (today only unit-tested in isolation).
- Test files to extend, not required but recommended given the project's existing conventions: a new `tests/e2e/*` test navigating to the settings page and asserting the new `id`-based controls exist and PUT correctly (no such test currently exists for the settings page at all, per Section 4), and/or a `services/settings.py`-level test asserting the full set of UI-exposed keys matches some enumerable list, to prevent this exact "backend key exists, UI never built" drift from recurring for future settings keys.

## Sibling sweep result

Found, beyond what the issue named:
1. A committed esbuild bundle (`static/rack.min.js`, `static/rack.min.js.map`, `static/rack.min.css`) that `static/index.html` actually serves -- a `rack.js`-only edit is invisible at runtime without `npm run build:js`/`npm run build`. Two existing tests (`tests/test_static_nav_wiring.py`, `tests/e2e/test_bundle_globals.py`) exist specifically because of prior incidents of exactly this mistake.
2. A second, already-built "per-job advanced overrides" UI (`static/rack.js:1750-1936`, the MFD "Fine Adjust" screen) that is a live candidate answer to the issue's own "global panel vs. per-job advanced section" design question -- it isn't wired to any cleanup key today, but its control/navigation model already exists and could host per-job cleanup toggles without inventing new UI chrome.
3. A hardcoded-vs-settings-driven inconsistency in `services/queue.py:453` for the hallucination-filter thresholds, which the issue's own text doesn't mention but which becomes user-visible the moment a UI exposes those three dials.
4. No settings-route or settings-UI test enumerates the full accepted-key set or the full rendered-control set -- so there's currently no automated guard against this "backend key shipped, UI never built" pattern recurring, beyond this issue's manual audit.
5. Confirmed negative findings: no second settings-rendering page, no mobile-specific settings variant, no existing numeric-slider control pattern (only free-text `parseInt`/`parseFloat` inputs), and no e2e test currently touches the settings page or its selectors at all.