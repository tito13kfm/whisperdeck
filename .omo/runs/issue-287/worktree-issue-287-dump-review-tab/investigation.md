# Investigation: Issue #287 — Dump Review tab + inline edit UI

Worktree read from: `C:\Claude\WhisperDeck\.claude\worktrees\issue-287-dump-review-tab` (fresh off origin/master, contains #283/#284/#285/#286).

## AGENTS.md constraints relevant to this change

- **Testing tiers** (AGENTS.md "Testing tiers: match test cost to change blast radius"): this is a UI-visible cross-cutting change (new detail tab, new fetch/save/finalize flow, polling contract). Per the tier rules: (1) unit/integration test for the touched path is mandatory (there is no JS unit test infra for `rack.js` DOM rendering — `tests_js/` only covers pure functions like `computeBatchAggregate`; the real coverage lives in `tests/e2e/*.py` Playwright tests), (2) because this doesn't change a request/response *contract* (the backend endpoints are already merged/tested by #285), the `e2e-regression-http` tier is not strictly required, but since it changes cross-feature flow (new tab, poller, navigate-to-board) a **targeted Playwright check of the new tab flow** is warranted, not a full `e2e-ux-audit`.
- **The Complement Rule**: this issue is exactly the shape the rule warns about — a new enum-gated UI branch (`kind === 'voice_dump'`). Section 6 below (sibling sweep) enumerates every other site that switches on `kind`/`voice_note` that must get a parallel `voice_dump` branch, and section 6b flags that entity rules (`note_type` validation) are **not** enforced server-side today — the rule requires that to exist somewhere, not just in the client dropdown.
- **Worktree hygiene**: not applicable to this investigation (read-only).
- No mutation-testing/build note beyond what's in section 6d (esbuild rebuild required).

---

## 1. The actual backend contract (PR #293 / issue #285)

All in `app.py`.

### `POST /api/transcripts/{transcript_id}/voice-dump/rerun` (app.py:2932)
- Method: **POST**, form-encoded (`provider: str = Form("groq")`, `model: str = Form("")`) — i.e. `FormData`, not JSON.
- 404 if transcript not found/not owned. 400 if `effective_kind(t) != "voice_dump"`. 400 if `t.status not in ("completed", "partial")`. 400 if provider needs a key and none is saved.
- Enqueues `enqueue_llm_job(db, user_id, transcript_id, "voice_dump", provider, model)`.
- Response: `{"job": serialize_llm_job(job)}`.

### `POST /api/transcripts/{transcript_id}/voice-dump/save-draft` (app.py:2960)
- Method: **POST** (issue text says "PATCH" — **wrong**, see §7).
- Body: `await request.json()` — the **raw request body is the item list itself**, e.g. `[{...}, {...}]`. It is **not** wrapped as `{"items": [...]}`. No Pydantic model, no field validation of any kind — whatever JSON array is posted is stored verbatim.
- 404 if transcript not found/not owned. 404 if no `voice_dump` `LlmJob` exists for this transcript (`latest_job(db, transcript_id, "voice_dump")`).
- Effect: `job.result_json = {**job.result_json, "items": items}` (replaces `items` key only, preserves any other keys already on `result_json`), `db.commit()`.
- Response: `{"items": items}` — echoes back exactly what was posted (no re-serialization, no `id` assignment — these are still draft items, not DB rows).
- Confirmed by `tests/test_voice_dump_route.py::test_save_draft_updates_job_result_json` (line 223): `client.post(..., json=new_items)` where `new_items` is a bare list.

### `POST /api/transcripts/{transcript_id}/voice-dump/finalize` (app.py:2987)
- Method: **POST** (issue text says "POST" — correct here, unlike save-draft).
- Body: `items: list[dict] = Body(..., embed=False)` — again the **raw body is the bare JSON array**, not `{"items": [...]}` (FastAPI's `embed=False` with a single `Body` param means the whole payload is the list).
- 404 if transcript not found/not owned. (No 404 if no voice_dump job exists — `source_job_id` simply stays `None` in that case; asymmetric with save-draft's strict 404.)
- Logic: `kept = [it for it in items if not it.get("discarded", False)]` — the discard flag key is **`discarded`** (boolean), not `discard`.
- For each kept item (in original order, `idx` = new sequential index, **not** preserved from the client's own index/order beyond list order):
  ```python
  vdi = VoiceDumpItem(
      user_id=current_user.id, transcript_id=transcript_id,
      source_job_id=source_job_id, sequence_index=idx,
      note_type=item.get("type", "general"),      # <-- client key is "type", column is "note_type"
      title=item.get("title", ""),
      body=item.get("body", ""),
      structured=item.get("structured", {}),
      model=item.get("model", ""),                # per-item model/provider — NOT auto-stamped
      provider=item.get("provider", ""),           # from job.model/job.provider by the backend
  )
  ```
  No validation that `note_type`/`type` is one of the 5 valid values (see §7).
- Response: `{"items": [_serialize_voice_dump_item(vdi) for vdi in created]}` — **fully re-serialized DB rows**, different shape than the draft items (see §2).
- Confirmed by `tests/test_voice_dump_route.py:278` (`test_finalize_inserts_rows_and_filters_discarded`).

### `GET /api/transcripts/{transcript_id}/runs/{kind}` (app.py:2760) — pre-existing, kind-agnostic, includes `"voice_dump"` in its allow-list
- Returns `{"runs": [{"id", "provider", "model", "status", "created_at", "result": j.result_json}, ...]}`, most-recent-job-first (`order_by(LlmJob.id.desc())`).
- **This is the only endpoint that actually returns a voice_dump job's `result_json`** (see §2/§7 — the transcript-detail serializer does not).

### `GET /api/transcripts/{transcript_id}/voice-dump-items` (app.py:3036) — finalized rows for one transcript
### `GET /api/voice-dump-items` (app.py:3058) — finalized rows across all transcripts (already consumed by the board, §5)

---

## 2. The serialized `voice_dump_job` shape the frontend actually receives

**Critical mismatch with the issue's own literal claim.**

`_serialize_transcript` (app.py:354) builds the transcript-detail payload and calls `_dictation_job_fields` (app.py:413) which, for `kind == "voice_dump"` (app.py:450-458), sets:
```python
"voice_dump_job": serialize_llm_job(vd_job) if vd_job else None,
```
`serialize_llm_job` (services/llm_jobs.py:48-70) returns:
```python
{
    "id": job.id, "kind": job.kind, "transcript_id": job.transcript_id,
    "status": job.status,
    "progress": {"done": job.progress_done or 0, "total": job.progress_total or 0},
    "provider": job.provider, "model": job.model, "error": job.error,
    "will_retry": bool(...),
    "created_at": ..., "updated_at": ...,
}
```
**There is no `result_json` key at all.** So the issue's literal spec value `t.voice_dump_job.result_json.items` (quoted verbatim in the task) **does not exist** on the object the frontend gets from `GET /api/transcripts/{id}`. `t.voice_dump_job.result_json` is `undefined` in JS.

To get the items, the frontend must call `GET /api/transcripts/{id}/runs/voice_dump` (§1) and read `runs[0].result.items` (the runs list is ordered newest-first, and the job referenced by `t.voice_dump_job.id` is exactly `runs[0]` since a transcript only ever has one active/latest job of a given kind at a time per `enqueue_llm_job`'s "one active job per transcript+kind" rule). This is the **same pattern `formatHtml()` already uses** for format jobs (`static/rack.js:4655`, see §8).

Per-item field names actually stored in `result_json.items` (from `services/llm_jobs.py:639-646`, confirmed by `tests/test_voice_dump_chain.py:340-344`):
```python
{
    "index": i,                                    # 0-based position, NOT "id"
    "type": result.get("type", "general"),          # one of todo/idea/reminder/journal/general
    "title": result.get("title", ""),
    "body": result.get("body", ""),
    "structured": result.get("structured", {}),
    "clarifying_questions": result.get("clarifying_questions", []),  # array of STRINGS, plural, not "questions"
}
```
No `discard`/`discarded` key exists yet at this stage (per the issue, it's purely a client-side flag added by the UI and only consumed by `finalize`).

**Job status strings**: `job.status` is one of `pending`, `running`, `completed`, `failed`, `cancelled` (from `LlmJob`/`llm_jobs.py` conventions used identically to every other job kind). `"completed"` is what gates showing the review UI; `pending`/`running` gate the "still processing" empty state (mirrors `voiceNoteHtml`'s `inFlight` check).

---

## 3. The LLM segmentation output shape (PR #291 / issue #284)

`services/voice_notes.py`:

- `segment_voice_dump(transcript, ...)` (line 229) → `list[dict]` of `{"span_text": str, "tentative_type": str}`. This is an **intermediate** shape, never exposed to the frontend directly — it's consumed inside the job runner.
- `services.llm_jobs.run_llm_job`'s `voice_dump` branch (services/llm_jobs.py:613-654) calls `segment_voice_dump(...)` then, per span, `_structure_from_text(seg["span_text"], seg["tentative_type"], ..., include_clarifying=True)` (voice_notes.py:170), and assembles the final `items` array shown in §2. This assembled shape **is** what §2 describes and **is** the source of the items the UI edits — confirmed match.
- `NOTE_TYPES = ("todo", "idea", "reminder", "journal", "general")` (voice_notes.py:27) — matches the issue's literal `todo/idea/reminder/journal/general` exactly, no typos, no extra/missing value.
- `structured` schema per type is produced by the same `_structure_from_text`/`_structure_prompt` used by `voice_note`, so the Dump Review tab's per-item type-specific rendering (if any is wanted for a preview) can reuse `noteStructuredBits(n)` (`static/rack.js:2609`), which already switches on `note_type`/`structured` — **but note the key name mismatch**: `noteStructuredBits` reads `n.note_type` (used for finalized rows), while a draft item from `result_json.items` has `type`, not `note_type` (see §7).

---

## 4. Where the detail-tab branch lives in `static/rack.js` — every decision point

`static/rack.js` (6135 lines) is the **only** frontend JS file with detail-tab logic. `static/batch_aggregate.js` (33 lines) is unrelated (pure function for the Queue batch-group header, tested by `tests_js/batch_aggregate.test.js`) — confirmed no sibling tab-rendering file exists.

Every site that currently decides tab identity/visibility for `kind === 'voice_note'` (the closest existing analog), all of which need a parallel `voice_dump`/`'review'` branch:

| # | File:line | What it does |
|---|-----------|---------------|
| 1 | `static/rack.js:3739-3740` (`loadTranscriptDetail`) | Resets `S.detailTab` to `'transcript'` if the sticky tab is `'format'`/`'notes'` but the newly-opened transcript's kind doesn't match. **No `voice_dump`/`'review'` case exists** — opening a voice_dump transcript while `S.detailTab === 'review'` (once that value exists) would never get reset if kind mismatches, since there's no check for it at all. |
| 2 | `static/rack.js:3773-3776` (`detailTabsHtml`) | Builds the tab-button row: `const tabs = ['transcript', 'corrected', 'summary']; if (kind === 'dictation') tabs.push('format'); if (kind === 'voice_note') tabs.push('notes');` — **no `voice_dump` branch pushes a review tab.** |
| 3 | `static/rack.js:4878-4924` (`renderDetailBody`) | The tab-body render switch (`if/else if` chain on `S.detailTab`): `'transcript'` → ... , `'corrected'` → ..., `'format' && kind==='dictation'` → `formatHtml`, `'format'` (mismatched kind) → not-available message, `'notes' && kind==='voice_note'` → `voiceNoteHtml`, `'notes'` (mismatched) → not-available message, else → summary. **No `'review'`/voice_dump branch exists at all** — this is the primary insertion point for the new tab body. |
| 4 | `static/rack.js:4939-4964` (`detailAction`, `act === 'toggle-kind'`) | The kind-cycle button: `meeting → dictation → voice_note → meeting`. **`voice_dump` is not reachable via this cycle at all** (pre-existing gap, inherited from #170 before voice_dump existed; the mode-picker at upload time, §5, is the only way to create a voice_dump transcript). Out of #287's stated scope but worth flagging since a user could reasonably expect the toggle button to reach it. |
| 5 | `static/rack.js:1723,1736-1741,1821` (`mfdSingleSpeaker`, `mfdCatDefs`, `mfdNav`) | The transcribe-time Mode picker already fully supports `voice_dump` (added by #286, §5) — no gap here. |

### Polling / refresh loop (see §6b for full detail) — also part of the "detail tab" decision surface
| # | File:line | Gap |
|---|-----------|---|
| 6 | `static/rack.js:3745-3750` (`_jobFingerprint`) | Does not include `voice_note_job` or `voice_dump_job` in its fingerprint string. |
| 7 | `static/rack.js:3754-3771` (`scheduleDetailPoll`) | The `llmJobActive(...)` guard list that decides whether to even schedule a poll timer omits `voice_note_job`/`voice_dump_job` entirely. |
| 8 | `static/rack.js:4204-4215` (`jobActiveSnapshot`) | Same omission — used to detect a job crossing into/out of "active" between poll ticks. |
| 9 | `static/rack.js:4246-4259` (`updateDetailJobStatus`'s `runningContainers` list) | Omits any `job-voice-note`/`job-voice-dump` container id — there's no live progress patch path for these two kinds; a full `renderDetailBody()` never even gets triggered because `crossed` is computed from `jobActiveSnapshot`, which never differs for these jobs (see §6b). |

---

## 5. What PR #294 (issue #286) already added — do not duplicate

- **Kind picker (mode selector)**: fully done, confirmed in `mfdCatDefs()` (`static/rack.js:1736-1741`) — `Mode` category options are `['Auto', 'Meeting', 'Dictation', 'Voice Note', 'Voice Dump']`, with `S.mode` including `'voice_dump'`, and `mfdNav` (`rack.js:1821`) assigns `S.mode = [...][newIdx]` including `'voice_dump'`. Bulk-import kind `<select>` also has a `voice_dump` `<option>` (`rack.js:2834`, `2896`). `mfdSingleSpeaker()` (`rack.js:1723`) already treats `voice_dump` as single-speaker (skips diarization) alongside `dictation`/`voice_note`. **Fully wired**, covered by `tests/e2e/test_voice_dump_board_e2e.py` tests 6/7 (real wheel clicks + `/api/transcribe` payload assertion).
- **Dump board section**: `loadVoiceDumpItems()` (`static/rack.js:2701-2758`), page id `page-dumpnotes`, nav mapping `dumpnotes: loadVoiceDumpItems` (`rack.js:451`), rail button `button[data-nav='dumpnotes']`. Calls `GET /api/voice-dump-items` (finalized items **only** — `VoiceDumpItem` rows, not job drafts). Its own code comment (`rack.js:2705-2707`) says: *"there is no per-item discard here: reviewing, editing and discarding happen before finalize, on the transcript's own Dump Review tab"* — i.e. it explicitly names and defers to the feature #287 is building, confirming it does not yet exist.
- **`voice_dump_job` reference in `static/`**: **none**. `grep -n "voice_dump_job" static/rack.js` returns zero matches — confirmed the field is never read anywhere in the frontend today. (`voice_note_job` is referenced exactly 3 times, all in `voiceNoteHtml`/its rerun handler — see §8's caveat about that pattern being possibly broken.)

---

## 6. Sibling sweep result

**This sweep found real, concrete gaps — not "nothing else found."**

### 6a. Every place gating on `kind` — checked one-by-one
Full `grep -n "t.kind\s*===\|detailData.kind\s*===\|\.kind ==="` sweep of `static/rack.js`:
- `rack.js:3775-3776` — tab list (§4 #2).
- `rack.js:4806` — `t.kind === 'dictation' ? '' : <Re-diarize buttons>` — **hides Re-diarize for dictation but NOT for voice_note/voice_dump**, even though `mfdSingleSpeaker()` treats all three as single-speaker/no-diarization kinds. Pre-existing gap (predates #287, also affects the already-merged voice_note feature) — flagging since it's the same class of bug the issue's Complement-Rule sweep should catch, but it is **not** part of #287's acceptance criteria; note it, don't silently fix it as part of this ticket.
- `rack.js:4905, 4915` — render switch (§4 #3).
- `rack.js:4946` — toggle-kind cycle (§4 #4).
- No audio-player, title/summary header, or "rerun button list" gates on `kind` beyond what's listed above — `renderDetail()`'s header block (`rack.js:4747-4830`ish) is kind-agnostic aside from the dictation/re-diarize line and the `kindLabel` computation (`rack.js:4757`, already handles `voice_dump` → `"Voice dump"` label — no gap there).

### 6b. Polling/refresh loop — confirmed broken for both voice_note AND (once built) voice_dump
`scheduleDetailPoll()` / `_jobFingerprint()` / `jobActiveSnapshot()` / `updateDetailJobStatus()` (all in `static/rack.js`, see §4 table) collectively decide whether to poll at all and whether to re-render when a job's status crosses a boundary. **None of these four functions reference `voice_note_job` or `voice_dump_job`.** Concretely: if a transcript's *only* active job is `voice_dump` (status `pending`/`running`), `scheduleDetailPoll`'s guard (`rack.js:3757-3759`) evaluates to `false` for every listed job, so **no poll timer is ever scheduled** — the Dump Review tab's "job still running" empty state would never automatically refresh to show items once the job completes; the user would have to navigate away and back. This is a real, load-bearing gap directly relevant to acceptance criterion "Shows empty state when job is still running" (i.e. the empty state needs to stop being empty once the job finishes, and today nothing drives that transition automatically).

### 6c. Rerun endpoint/button for voice_dump
Backend: `POST /api/transcripts/{id}/voice-dump/rerun` exists and works (app.py:2932, §1). Frontend: **not wired**. `grep -n "rerun-voice-dump" static/rack.js` → zero matches (only `rerun-voice-note`, `rack.js:4578, 4633, 4966`). This confirms the issue's own text implicitly assumes rerun is out of scope for #287 (it says the tab renders "while the job is complete" and doesn't mention a rerun button) — but the acceptance criteria never says "no rerun," so this is worth a scoping decision: the sub-issue tracker (#285 body per the task prompt) does mention rerun as part of #285's backend scope, and it's fully backend-ready; whether the Dump Review tab should also add a "Rerun chain" button (mirroring `voiceNoteHtml`'s pattern, `rack.js:4578,4633`) is a real open question for the implementer, not a hard requirement from #287's stated acceptance criteria.

### 6d. Build system — `rack.js` is NOT served raw
`static/index.html:155` → `<script defer src="/static/rack.min.js"></script>` — the browser loads the **bundled/minified** file, not `static/rack.js` directly. `package.json` build scripts:
```json
"build": "npm run build:js && npm run build:css",
"build:js": "esbuild static/rack.js --bundle --minify --outfile=static/rack.min.js",
```
Committed output paths: `static/rack.min.js` and `static/rack.min.js.map` (both present in the repo tree). **Any edit to `static/rack.js` requires running `npm run build:js` (or `npm run build`) and committing the regenerated `static/rack.min.js`/`.map`**, or the live app will keep serving stale JS. All of the `tests/e2e/*.py` Playwright tests load the real served page, i.e. they exercise `rack.min.js`, not `rack.js` — so a source-only edit without rebuilding will make e2e tests pass/fail against the wrong code silently.

### 6e. Existing tests that select on detail-tab text/roles
- `tests/e2e/test_detail_poll_partial_update.py`, `tests/e2e/test_detail_poll_tagging_fingerprint.py`, `tests/e2e/test_detail_rapid_clicks.py` — exercise `data-tab` buttons and `#detail-body` but none currently reference `voice_dump` or a `'review'` tab (confirmed via `grep -rln "voice_dump\|data-tab" tests/e2e`).
- `tests/e2e/test_voice_dump_board_e2e.py` (full file reviewed, §5) — covers the **board** page and the **mode picker**, not the Dump Review tab. It reuses one shared registered user per file and is order-dependent (documented in its own docstring) — a new Dump-Review-tab e2e test file should follow the same one-shared-user-per-file convention rather than register a second user, to respect the `/api/register` rate limit shared across the whole `live_server` pytest session (noted explicitly in that file's docstring).
- No test currently asserts `.mfd-row` "Mode" text, tab labels, or `data-tab='review'` — safe to add a new tab name without breaking an existing selector, **provided** the new tab's `data-tab` value doesn't collide with `'transcript'|'corrected'|'summary'|'format'|'notes'`.

---

## 7. What the issue's own approach gets wrong or omits

1. **HTTP method for save-draft is wrong in the issue.** Issue says "PATCH `save-draft` endpoint". The real route is `@app.post(...)` (app.py:2960) — **POST**, not PATCH.
2. **Request body shape is a bare array, not an envelope.** Both `save-draft` and `finalize` expect the raw POST body to *be* the item list (`[...]`), not `{"items": [...]}`. The issue's phrase "with current item list" is consistent with this, but an implementer skimming the issue could easily wrap it — verified via `tests/test_voice_dump_route.py`'s `client.post(url, json=new_items)` where `new_items` is a bare `list`.
3. **`t.voice_dump_job.result_json.items` does not exist on the wire.** This is the single biggest issue-vs-reality mismatch (§2). `serialize_llm_job` never emits `result_json`. The frontend must instead call `GET /api/transcripts/{id}/runs/voice_dump` and use `runs[0].result.items` — following the exact pattern `formatHtml()` already uses (`rack.js:4655`, §8) for other job kinds, **not** the pattern `voiceNoteHtml()`'s own code comment claims to use (which appears to be based on a false assumption — see the caveat in §8).
4. **Per-item field names differ between draft and finalized shapes**, in a way the issue never calls out:
   - Draft item (`result_json.items[i]`, from `services/llm_jobs.py:639-646`): keys `index`, `type`, `title`, `body`, `structured`, `clarifying_questions`.
   - Finalized item (`_serialize_voice_dump_item`, app.py:2810): keys `id`, `transcript_id`, `source_job_id`, `sequence_index`, `note_type` (not `type`!), `title`, `body`, `structured`, `model`, `provider`, `created_at`.
   - The discard flag key the backend actually reads is **`discarded`** (boolean), not "discard" as loosely implied by the issue's "Discard checkbox" phrasing.
   - Draft items have **no `id`** — only a 0-based `index`. The frontend must key its editable rows off array position/`index`, not an `id` that doesn't exist yet.
5. **"Navigate to the new dump-items list" — the issue doesn't name the actual route.** It is client-side page `dumpnotes` (`navigate('dumpnotes')` → `loadVoiceDumpItems()` → `GET /api/voice-dump-items`, §5), already built by #286. Not a server route the client "navigates to" in the URL sense — it's an SPA page switch.
6. **Type values are correct** — `todo/idea/reminder/journal/general` exactly matches `NOTE_TYPES` in `services/voice_notes.py:27`. No discrepancy here (the one place the issue is fully right). Note, though, that the `VoiceDumpItem.note_type` DB column comment (`database/__init__.py:208`) is **stale**, still saying `"bug | idea | todo | reminder (TBD in #284)"` — a leftover from before #284 settled the real enum; harmless but worth not copying that comment's vocabulary into new code/docs.
7. **No server-side enforcement of the type enum at finalize time.** `finalize` does `item.get("type", "general")` with zero validation against `NOTE_TYPES` — any string the client sends becomes the stored `note_type`. Per AGENTS.md's Complement Rule ("Enforce entity rules server-side. A rule that only lives in the client... does not exist"), a dropdown limited to the 5 valid values is not sufficient by itself if that's the only thing preventing a bad value — this is a pre-existing backend gap from #285, out of #287's frontend-only file scope, but worth flagging since it directly touches the "type dropdown" acceptance criterion.
8. **The issue doesn't address the "already finalized" gating condition its own acceptance criteria requires.** Criterion 1 says the tab renders "while the job is complete and draft items haven't been finalized," but there is no boolean flag anywhere for "this job's draft was already finalized." The only way to determine that client-side is to fetch `GET /api/transcripts/{id}/voice-dump-items` and check whether any row's `source_job_id` equals `t.voice_dump_job.id` — that endpoint returns **all** finalized items for the transcript (not scoped by job), so a re-run job (new `voice_dump_job.id`) would need this same cross-check to correctly show a *fresh* Draft Review UI even though older finalized items exist from a previous job run. The issue is silent on this; the implementer needs to design it.
9. **Per-item `model`/`provider` are not auto-populated by the backend at finalize** — if attribution matters for the finalized rows, the client must copy `job.model`/`job.provider` into each item's `model`/`provider` fields itself before calling finalize; the backend does not stamp them from the job context.
10. **`finalize` has no 404 if the voice_dump job is missing**, unlike `save-draft`'s strict 404 — an asymmetry worth being aware of when writing error-handling code (a Finalize call could silently succeed with `source_job_id: null` if called with no job).

---

## 8. Existing patterns to reuse (house style — copy these, don't invent new ones)

**The issue's claim "no contenteditable/edit pattern exists" is correct** — confirmed no `contenteditable`, no existing `<input>`/`<textarea>` two-way-binding pattern, and no "dirty flag"/"isDirty" state anywhere in `static/rack.js` (`grep` for `dirty|isDirty|hasUnsaved` → zero matches). This genuinely is the first inline-edit UI in the app.

**(i) DOM construction via template literals** — e.g. `loadVoiceDumpItems()`'s card grid (`rack.js:2724-2744`):
```js
const cards = items.map(n => {
  ...
  return `
    <div class="unit voice-note-card voice-dump-card" data-tid="${n.transcript_id}" ...>
      ...
    </div>`;
}).join('');
root.innerHTML = `<div class="page-head">...</div><div class="voice-note-grid">${cards}</div>`;
```
Same idiom is used throughout `renderDetailBody()`'s branches (`rack.js:4881-4923`) and `formatHtml()` (`rack.js:4639-4677`) — build an HTML string (often via `.map(...).join('')` over an array), assign wholesale to `.innerHTML`, then re-bind listeners.

**(ii) Binding button handlers after render** — the universal idiom is a `data-*` attribute + a post-assignment `querySelectorAll` pass:
```js
body.querySelectorAll('[data-dact]').forEach(b => b.addEventListener('click', () => detailAction(b.dataset.dact, b)));
```
(`rack.js:4902`, `4908`; also the card-grid variant `data-vnact`/`data-vdact` at `rack.js:2685-2692`, `2751-2757`). New Dump Review actions (save-draft, finalize, discard checkbox, type select, per-question inputs) should route through `detailAction(act, btn)`'s existing `if (act === '...')` chain (`rack.js:4926` onward), following the same `data-dact="save-draft"` / `data-dact="finalize-voice-dump"` convention, rather than inventing separate event wiring.

**(iii) Fetch calls / auth / error handling** — the single `api(path, opts)` helper (`rack.js:234-266`) is the only entry point used everywhere: it attaches `X-CSRF-Token` for mutating methods, retries once on a rotated-CSRF 403, handles 401 by showing the login screen, and throws `Error(detail)` on non-2xx (parsed from `{detail}`/`{error}` JSON). Example call site:
```js
await api('/api/transcripts/' + t.id + '/voice-note/rerun', { method: 'POST', body: form });
```
(`rack.js:4976`). For JSON-array bodies (save-draft/finalize), the equivalent call is:
```js
await api('/api/transcripts/' + t.id + '/voice-dump/save-draft', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(items),   // bare array, NOT { items }
});
```
matching the `toggle-kind` PATCH call's header pattern (`rack.js:4948-4952`).

**(iv) State-tracking / dirty-flag pattern** — **none exists**; this is new ground, per the issue's own (correct) claim. The closest analog is `detailData` (`rack.js:3683`) as the single source of truth for the open transcript, refreshed wholesale by `loadTranscriptDetail`/`scheduleDetailPoll`, and `S.detailTab` (`rack.js` global, e.g. set at `4852`) tracking which tab is open. A Dump Review implementation will need its own local draft-items array (likely a new module-level `let dumpDraftItems = null;` mirroring `detailData`'s style) since there's no existing per-tab local-edit-state convention to copy.

**(v) Empty-state rendering** — the consistent idiom is a `<div class="empty-unit">...</div>`:
```js
if (!job || !job.result_json) {
  return '<div class="empty-unit">No voice-note result yet. The chain runs automatically after transcription completes.</div>';
}
```
(`rack.js:4581-4582`; also `rack.js:4919` `'Not available for non-voice-note transcripts'`, and the board's `rack.js:2714-2721` no-items empty state with a `data-nav` link back to `transcribe`). The Dump Review tab's "job still running" state should follow `voiceNoteHtml()`'s `inFlight` branch shape (`rack.js:4565-4573`) — **but note the caveat below** about not blindly copying its data-source assumption.

**Caveat on reuse — do not copy `voiceNoteHtml`'s `result_json` read verbatim.** `voiceNoteHtml()` (`rack.js:4558-4564`) contains this comment:
> "The chain writes to a VoiceNote row AND a LlmJob result_json. The serializer already exposes voice_note_job.result_json, so we read from there to avoid a follow-up /voice-note fetch — same shape either way..."

This claim is **false** against current code: `serialize_llm_job` (services/llm_jobs.py:48-70) does not include `result_json` (§2), and no test (`tests/test_voice_note_route.py`, `tests/test_serialize_transcript_contract.py`) ever asserts `detail["voice_note_job"]["result_json"]` is present. Confirmed via full `git log -S "result_json"` history of `services/llm_jobs.py` — `serialize_llm_job` never had a `result_json` key added. In practice this likely means `job.result_json` is `undefined` in the browser for a completed `voice_note_job`, and `voiceNoteHtml()`'s `if (!job || !job.result_json)` branch (`rack.js:4581`) fires even when the chain succeeded, showing "No voice-note result yet" for a completed job. This is a pre-existing latent bug outside #287's scope (introduced in #170, unrelated to #283-286) — **not** something to fix here, but the Dump Review tab must **not** copy this exact "trust the comment, read `t.voice_dump_job.result_json` directly" approach. Use the proven-working `GET /api/transcripts/{id}/runs/{kind}` pattern from `formatHtml()` (`rack.js:4654-4657`) instead:
```js
const runs = (await api('/api/transcripts/' + t.id + '/runs/' + target.kind)).runs;
const latest = runs.find(r => r.status === 'completed');
const text = (latest && latest.result && latest.result.text) || '';
```

---

## Call sites / entry points in scope for the fix

Frontend (`static/rack.js` — must rebuild `static/rack.min.js`/`.js.map` via `npm run build:js` after editing, §6d):

1. `rack.js:3739-3740` — `loadTranscriptDetail`: add a reset case for the review tab when kind mismatches (parallel to the existing `format`/`notes` checks).
2. `rack.js:3773-3776` — `detailTabsHtml`: push a `'review'` (or similarly-named) tab when `detailData.kind === 'voice_dump'`.
3. `rack.js:4878-4924` — `renderDetailBody`: add the `S.detailTab === 'review' && t.kind === 'voice_dump'` branch (and the mismatched-kind fallback branch), calling a new `dumpReviewHtml(t)` function (new, modeled on `voiceNoteHtml`/`formatHtml` but sourcing items via `GET /api/transcripts/{id}/runs/voice_dump`, §7 item 3/§8 caveat).
4. `rack.js:4926` onward (`detailAction`) — add `save-draft`/`finalize-voice-dump` (and optionally `rerun-voice-dump`, §6c) action handlers, following the existing `if (act === '...')` chain style and `api()` usage (§8-iii).
5. New module-level draft-state variable (mirroring `detailData`, §8-iv) to hold the in-progress edited item array plus the client-only `discarded` flags and appended clarifying-question answers, independent of `detailData` (which stays the read-only server snapshot).
6. `rack.js:3745-3750` (`_jobFingerprint`), `rack.js:3754-3771` (`scheduleDetailPoll`), `rack.js:4204-4215` (`jobActiveSnapshot`), `rack.js:4246-4259` (`updateDetailJobStatus`) — must all learn about `voice_dump_job` (and, while touching this, arguably `voice_note_job` too, though that's pre-existing scope creep) so the "job still running" empty state actually transitions to the item-review UI once the job completes, per §6b.
7. `rack.js:2701-2758` (`loadVoiceDumpItems`) — navigation target after Finalize (`navigate('dumpnotes')`), no changes needed here, just the call site from the new Finalize handler.

Backend (already merged by #285/#293, #284/#291, #283/#288 — **no changes needed** for #287 per its stated file scope of `static/rack.js` only, but relevant contract surface the frontend must match exactly):
- `app.py:2932` `rerun_voice_dump_chain`
- `app.py:2960` `save_voice_dump_draft`
- `app.py:2987` `finalize_voice_dump`
- `app.py:2760` `transcript_runs` (the actual source of `result_json.items`)
- `services/llm_jobs.py:48` `serialize_llm_job` (confirms no `result_json` field)
- `services/llm_jobs.py:613-654` (`voice_dump` job runner — item shape source of truth)
- `services/voice_notes.py:27` `NOTE_TYPES`, `:229` `segment_voice_dump`, `:170` `_structure_from_text`
