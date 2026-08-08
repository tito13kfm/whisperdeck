# Issue #286 investigation — Voice dump frontend: kind picker + board section

Worktree read: `C:\Claude\whisperdesk\.claude\worktrees\issue-286-voice-dump-board`
Branch: `worktree-issue-286-voice-dump-board`
Worktree HEAD: `290e5f7` (feat: auto-detect worktree root in verify_self_audit.py (#292))

## HEADLINE FINDING: the worktree is stale by exactly one commit — the #285 API the issue depends on is NOT present in the code you'd edit

The tracking issue text says sub-issue #285 ("API endpoints + serialization: rerun, save-draft, finalize, list routes, `voice_dump_job` field") is "ALREADY MERGED to master." That is true of `origin/master`, but **it is not true of this worktree**:

```
$ git merge-base --is-ancestor 6951844 HEAD   # 6951844 = #285 dev commit
ANCESTOR-NO
$ git log --oneline origin/master -3
5207255 feat(api): add voice-dump endpoints and serialization (#285) (#293)
290e5f7 feat: auto-detect worktree root in verify_self_audit.py (#292)   <- worktree HEAD
```

`origin/master` is exactly one commit (`5207255`, authored today 2026-08-02 14:49:04) ahead of this worktree's branch point, and that one commit is entirely the #285 work. Concretely, in the worktree right now:

- `app.py` does not import `VoiceDumpItem` from `database` (grep confirms zero hits for `VoiceDumpItem` in `app.py`).
- There is no `/api/voice-dump-items` route, no `_serialize_voice_dump_item`, no rerun/save-draft/finalize routes — none of it exists in the worktree's `app.py`.
- The `database/__init__.py` `VoiceDumpItem` model (added by #283, which genuinely is in this worktree at `f28e254`) exists, but nothing in `app.py` queries it.

So implementing "wire the frontend to GET /api/voice-dump-items" in this worktree today would produce a 404 for every request, because that endpoint does not exist on this branch. Section 1 below reports the real, merged contract as it exists on origin/master's `5207255` (inspected via `git show`, not by editing the worktree), since that is what the eventual fix needs to target — but whoever picks up sub-issue 4 must first merge/rebase this branch onto origin/master (or cherry-pick `5207255`) before static/rack.js changes will have a backend to talk to.

---

## 1. The actual API contract (as merged to origin/master in 5207255, absent from this worktree)

Five routes were added to app.py by commit 5207255 ("feat(api): add voice-dump endpoints and serialization (#285) (#293)"). The one relevant to issue #286 (board section) is:

### GET /api/voice-dump-items

```python
@app.get("/api/voice-dump-items")
async def list_voice_dump_items(
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List this user's voice dump items across all transcripts, most
    recent first. Each row includes the source transcript's title and
    duration for card rendering."""
    rows = (
        db.query(VoiceDumpItem, Transcript)
        .join(Transcript, VoiceDumpItem.transcript_id == Transcript.id)
        .filter(VoiceDumpItem.user_id == current_user.id)
        .order_by(VoiceDumpItem.created_at.desc(), VoiceDumpItem.id.desc())
        .limit(limit)
        .all()
    )
    return {"items": [
        {
            **_serialize_voice_dump_item(item),
            "transcript_title": t.title or "",
            "transcript_duration_seconds": t.duration_seconds or 0,
            "transcript_status": t.status,
        }
        for item, t in rows
    ]}
```

- Query params: only `limit` (int, default 100). No `offset`, no status filter. There is no pagination beyond a flat limit.
- Response envelope key is `items`, NOT `voice_dump_items`. (Contrast with /api/voice-notes, whose envelope key is `voice_notes`.) loadVoiceDumpItems() must read data.items, not data.voice_dump_items.
- Auth/scoping: filtered by current_user.id exactly like /api/voice-notes; no per-transcript scoping (that is the sibling route GET /api/transcripts/{transcript_id}/voice-dump-items, also added by this commit, which returns {"items": [...]} for one transcript only).
- "finalized vs draft": there is no status field anywhere in this contract. It is implicit in the data model — VoiceDumpItem rows are only ever created by the POST /api/transcripts/{transcript_id}/voice-dump/finalize route (which inserts rows). Drafts live only inside the voice_dump LlmJob.result_json['items'] blob (mutated by POST .../voice-dump/save-draft), never as VoiceDumpItem rows. So GET /api/voice-dump-items is inherently finalized-only — there is no status='draft' row to filter out, because drafts simply do not live in this table.

### Serializer, verbatim (_serialize_voice_dump_item, new in 5207255):

```python
def _serialize_voice_dump_item(item) -> dict:
    if not item:
        return None
    return {
        "id": item.id,
        "transcript_id": item.transcript_id,
        "source_job_id": item.source_job_id,
        "sequence_index": item.sequence_index,
        "note_type": item.note_type,
        "title": item.title or "",
        "body": item.body or "",
        "structured": item.structured or {},
        "model": item.model or "",
        "provider": item.provider or "",
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
```

So the exact field names on each item in the board-list response are:
id, transcript_id, source_job_id, sequence_index, note_type, title, body, structured, model, provider, created_at, plus the three joined fields the list route adds: transcript_title, transcript_duration_seconds, transcript_status.

The field is note_type (matches the issue's assumption) — it is populated from VoiceDumpItem.note_type, a String(16) column (see database/__init__.py:208), and the value vocabulary is services/voice_notes.py's shared NOTE_TYPES = ("todo", "idea", "reminder", "journal", "general") (line 27) — the same tuple VoiceNote.note_type uses. So the issue's claim "reuses NOTE_TYPE_LABELS/NOTE_TYPE_COLORS since the type vocabulary is identical" is correct and verified against the backend classifier code (classify_voice_note in services/voice_notes.py:144-167, which is shared/reused by the voice-dump chain per segment_voice_dump, not a separate bug|idea|todo|reminder vocabulary as the VoiceDumpItem.note_type column comment speculatively suggested — that comment is now stale relative to what actually shipped).

For comparison, the pre-existing, currently-live /api/voice-notes route (this one does exist in the worktree, app.py:2822-2847) has envelope key voice_notes and the identical per-item shape via _serialize_voice_note (app.py:2782-2795) plus the same three joined transcript_* fields. loadVoiceDumpItems() should mirror this shape exactly except for the items vs voice_notes key difference.

---

## 2. Current state of the kind picker(s) in static/rack.js

There are two structurally different "kind pickers" in this file, and only one of them is what issue #286 means by "record-start dropdown." Get this wrong and you wire the wrong control.

### 2a. The bulk-import "Kind" selects (already fully wired, added by #283 — NOT the target of #286)

Two literal select dropdowns exist, one for defaults, one per queued file, both already have voice_dump wired with the exact label the issue quotes:

static/rack.js:2758-2765 (bulk defaults):
```
2757	          <div class="field" style="min-width:100px">
2758	            <label class="t-label">Kind</label>
2759	            <select id="bulk-kind" class="inp">
2760	              <option value="auto" ${S.bulkDefaults.kind === 'auto' ? 'selected' : ''}>Auto</option>
2761	              <option value="meeting" ${S.bulkDefaults.kind === 'meeting' ? 'selected' : ''}>Meeting</option>
2762	              <option value="dictation" ${S.bulkDefaults.kind === 'dictation' ? 'selected' : ''}>Dictation</option>
2763	            <option value="voice_note" ${S.bulkDefaults.kind === 'voice_note' ? 'selected' : ''}>Voice Note</option>
2764	            <option value="voice_dump" ${S.bulkDefaults.kind === 'voice_dump' ? 'selected' : ''}>Audit / stream-of-consciousness dump</option>
2765	          </select>
```

static/rack.js:2821-2827 (per-file row in the batch):
```
2821	      <select class="inp bulk-field" data-bulk-idx="${i}" data-field="kind" style="font-size:11px;width:100px">
2822	        <option value="auto" ${(bf.kind || S.bulkDefaults.kind) === 'auto' ? 'selected' : ''}>Auto</option>
2823	        <option value="meeting" ${(bf.kind || S.bulkDefaults.kind) === 'meeting' ? 'selected' : ''}>Meeting</option>
2824	        <option value="dictation" ${(bf.kind || S.bulkDefaults.kind) === 'dictation' ? 'selected' : ''}>Dictation</option>
2825	        <option value="voice_note" ${(bf.kind || S.bulkDefaults.kind) === 'voice_note' ? 'selected' : ''}>Voice Note</option>
2826	        <option value="voice_dump" ${(bf.kind || S.bulkDefaults.kind) === 'voice_dump' ? 'selected' : ''}>Audit / stream-of-consciousness dump</option>
2827	      </select>
```

Confirmed via git log -p -S"Audit / stream-of-consciousness dump" -- static/rack.js: both lines were added in commit f28e254 "feat: add voice_dump kind plumbing and VoiceDumpItem table (#283) (#288)" — its own message says "- 'Audit / stream-of-consciousness dump' option in both kind pickers". These two are already fully wired end-to-end: the value flows straight into form.append('kind', ...) for the bulk-upload submit path. No work needed here for #286.

### 2b. The MFD "Mode" wheel — the actual record-start / live-capture kind picker (NOT wired — this is the real gap)

This is a custom VFD/wheel-style control (not an HTML select), defined in mfdCatDefs():

static/rack.js:1735-1736:
```
1735	    { key: 'mode', label: 'Mode', desc: 'Meeting: multi-speaker minutes. Dictation: voice notes, no speakers, offers to reformat after. Auto: let the app decide.',
1736	      opts: ['Auto', 'Meeting', 'Dictation', 'Voice Note'], idx: S.mode === 'meeting' ? 1 : S.mode === 'dictation' ? 2 : S.mode === 'voice_note' ? 3 : 0 },
```

Its value-change handler, mfdNav():

static/rack.js:1819:
```
1819	  else if (c.key === 'mode') S.mode = ['auto', 'meeting', 'dictation', 'voice_note'][newIdx];
```

voice_dump is absent from both the opts label array and the idx/value-cycling arrays. There is currently no way to select kind voice_dump through this control at all — cycling the wheel only ever lands on auto | meeting | dictation | voice_note.

S.mode defaults to 'meeting' (static/rack.js:41, with the comment "// meeting | dictation | voice_note — dictation/voice_note skip diarization, unlock their own post-pipeline sets" — itself now stale, doesn't mention voice_dump).

### Trace: how the selected kind reaches startLiveCapture()

The issue's claim ("Value voice_dump feeds into existing startLiveCapture() unchanged") is imprecise about the actual call path — see Section 7. The real flow:

1. openRecModal() (static/rack.js:2419-2436) — its "Start recording" button calls startLiveCapture() (static/rack.js:2434).
2. startLiveCapture() (static/rack.js:2456 onward) takes no kind parameter whatsoever — it just opens mic/display-media streams and starts a MediaRecorder. Kind is never read or used inside this function.
3. On stop, finishLiveCapture() (static/rack.js:2547-2567) loads the recorded blob onto "Deck A" via loadTape(...) — again, no kind involved.
4. The user then presses the actual START button, which calls startJob() (static/rack.js:2255 onward). This is where kind is read and shipped to the backend:
   static/rack.js:2268: form.append('kind', S.mode);

So S.mode (driven by the MFD "Mode" wheel) is what ultimately becomes the kind form field posted with the transcription job — for both a freshly-live-captured tape and a manually-loaded file. startLiveCapture() itself is kind-agnostic and needs zero changes; it is startJob()'s use of S.mode that must be able to carry 'voice_dump', which requires the Mode wheel (mfdCatDefs/mfdNav) to offer it.

### Every site that branches on S.mode / recording kind that would need a voice_dump arm

- static/rack.js:1722 — function mfdSingleSpeaker() { return S.mode === 'dictation' || S.mode === 'voice_note'; } — controls whether diarization is force-disabled and the Speakers wheel shows "N/A" (mfdCatDefs speakers entry, lines 1737-1739, locked: single). A voice-dump capture is presumably also single-speaker/stream-of-consciousness — this needs a || S.mode === 'voice_dump' arm or dumps will show a live (but functionally locked-out) Speakers control inconsistently with dictation/voice_note. This is a product-judgment call, not spelled out anywhere, but it is the direct sibling of the two kinds the issue says share behavior.
- static/rack.js:1736 — the opts/idx pair for the mode MFD category (needs a 5th label + idx branch).
- static/rack.js:1819 — the S.mode = [...][newIdx] cycle array (needs voice_dump appended).
- static/rack.js:41 — the S.mode default/comment (stale, cosmetic only).

### Sites that branch on kind further downstream (detail page) — out of scope per the issue, but part of the "complement rule" sweep:

- static/rack.js:3669-3670 — detail-tab reset logic: if (S.detailTab === 'format' && detailData.kind !== 'dictation') ... / if (S.detailTab === 'notes' && detailData.kind !== 'voice_note') .... No voice_dump arm — correctly out of scope (issue explicitly excludes "the detail page's Dump Review tab (sub-issue 5)").
- static/rack.js:3705-3706 — detailTabsHtml(): if (detailData && detailData.kind === 'dictation') tabs.push('format'); if (detailData && detailData.kind === 'voice_note') tabs.push('notes'); — same, explicitly deferred to sub-issue 5.
- static/rack.js:4682-4686 — const kind = t.kind || 'meeting'; ... const kindLabel = kind === 'voice_note' ? 'Voice note' : (kind.charAt(0).toUpperCase() + kind.slice(1)); — for kind === 'voice_dump' this falls through to the generic capitalize-first-letter branch, producing the label "Voice_dump" (underscore intact, not humanized) wherever the detail page's Mode badge is rendered. Cosmetic bug, not mentioned by the issue, arguably sub-issue-5 territory but worth flagging since it is a silent fallthrough rather than an explicit arm.
- static/rack.js:4875 — the detail page's Mode-toggle button: const newKind = t.kind === 'meeting' ? 'dictation' : t.kind === 'dictation' ? 'voice_note' : 'meeting'; — a 3-state cycle (meeting to dictation to voice_note to meeting). If a transcript's kind is voice_dump, clicking this toggle sends it straight to 'meeting' (falls into the final else branch), silently losing the voice_dump kind. Not in scope per the issue's exclusions, but this is a real behavioral trap for any transcript that already has kind === 'voice_dump' once #285 lets such transcripts exist.
- static/rack.js:3319-3323 — KIND_LABELS (Queue page's job-kind labels: transcription, correction, voice_note, tagging, etc. — note these are LlmJob kind values, a different vocabulary than transcript "kind", but the same word). No voice_dump entry. Falls back to raw string KIND_LABELS[j.kind] || j.kind, so the Queue page would show the raw label "voice_dump" instead of an uppercase "VOICE DUMP"-style label for any voice_dump-kind LLM job in the queue. Not fatal (has a fallback), not explicitly in scope, but is a genuine sibling gap in the same file.

---

## 3. loadVoiceNotes() today, verbatim, with line numbers

static/rack.js:2603-2682:

```js
2603	async function loadVoiceNotes() {
2604	  // The board page: a card grid of recent voice notes, grouped by
2605	  // note_type. Mirrors the loadTranscripts() render shape so the
2606	  // chassis styling picks it up. Click a card to open the source
2607	  // transcript (the user reads/edits the note on the detail Notes
2608	  // tab, not here).
2609	  const root = $('page-voicenotes');
2610	  refreshRailChrome();
2611	  let data;
2612	  try { data = await api('/api/voice-notes'); } catch (e) { toast(e.message, 'error'); return; }
2613	  const notes = data.voice_notes || [];
2614	  if (!notes.length) {
2615	    root.innerHTML =
2616	      '<div class="page-head">' +
2617	        '<h1 class="t-title">Voice notes</h1>' +
2618	        '<div class="page-status page-status--ok">0 notes</div>' +
2619	      '</div>' +
2620	      '<div class="empty-unit">No voice notes yet. Record one in <a href="#" data-nav="transcribe">Transcribe</a> with the mode toggle set to VOICE NOTE.</div>';
2621	    root.querySelector('[data-nav]')?.addEventListener('click', (e) => { e.preventDefault(); navigate('transcribe'); });
2622	    return;
2623	  }
2624	  const cards = notes.map(n => {
2625	    const typeColor = NOTE_TYPE_COLORS[n.note_type] || NOTE_TYPE_COLORS.general;
2626	    const typeLabel = NOTE_TYPE_LABELS[n.note_type] || n.note_type;
2627	    const preview = (n.body || '').slice(0, 220) + ((n.body || '').length > 220 ? '…' : '');
2628	    let structuredBits = '';
       ... (lines 2629-2650: per-note_type structured render bits for todo/reminder/idea/journal — unchanged, omitted here)
2651	    return `
2652	      <div class="unit voice-note-card" data-tid="${n.transcript_id}" style="padding:18px 22px;cursor:pointer">
2653	        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
2654	          <span style="...background:${typeColor};..."></span>
2655	          <span style="...color:${typeColor}">${escapeHtml(typeLabel)}</span>
2656	          <span style="...margin-left:auto">${timeAgo(n.created_at)}</span>
2657	        </div>
2658	        <h3>${escapeHtml(n.title || 'Voice note')}</h3>
2659	        <div>From "${escapeHtml(n.transcript_title || 'transcript')}" · ${formatDur(n.transcript_duration_seconds || 0)}</div>
2660	        ${preview ? '<div>' + escapeHtml(preview) + '</div>' : ''}
2661	        ${structuredBits ? '<div>' + structuredBits + '</div>' : ''}
2662	        <div style="...justify-content:flex-end">
2663	          <button class="btn" data-vnact="discard" data-vnid="${n.id}">Discard</button>
2664	          <button class="btn" data-vnact="open" data-tid="${n.transcript_id}">Open →</button>
2665	        </div>
2666	      </div>`;
2667	  }).join('');
2668	  root.innerHTML = `
2669	    <div class="page-head">
2670	      <h1 class="t-title">Voice notes</h1>
2671	      <div class="page-status page-status--ok">${notes.length} note${notes.length !== 1 ? 's' : ''}</div>
2672	    </div>
2673	    <div class="voice-note-grid">${cards}</div>`;
2674	  root.querySelectorAll('[data-vnact]').forEach(b => b.addEventListener('click', (e) => { ... }));
2675	  root.querySelectorAll('.voice-note-card').forEach(c => c.addEventListener('click', () => {
2676	    navigate('detail', Number(c.dataset.tid));
2677	  }));
2682	}
```

(Style-attribute detail trimmed above for readability in this report; the real file has full inline styles at each of these lines — see the actual file for exact byte-for-byte content.)

discardVoiceNote (static/rack.js:2684-2688) is the delete-button handler; hits DELETE /api/voice-notes/{id} then re-calls loadVoiceNotes(). There is no finalize/save-draft equivalent needed for the board (that is per-item, done at detail-page level, sub-issue 5).

### Where its board HTML "lives"

It is built entirely client-side as a template string injected into root = $('page-voicenotes'). page-voicenotes is an empty placeholder div in static/index.html:116: <div class="page" id="page-voicenotes"></div>. There is no server-rendered/Jinja template involved anywhere — this is a pure SPA (static/index.html + static/rack.js), confirmed: no templates/ directory exists in the repo at all (only static/index.html).

### Registration / when it is called

- PAGES array, static/rack.js:416: const PAGES = ['dashboard', 'transcribe', 'transcripts', 'voicenotes', 'bulk', 'queue', 'costs', 'detail', 'voices', 'files', 'settings', 'assistant']; — a page id must be in this array or navigate() silently redirects to 'dashboard' (static/rack.js:436: if (!PAGES.includes(page)) page = 'dashboard';).
- Loader map inside navigate(), static/rack.js:446-459:
  ```js
  446	  const loaders = {
  447	    dashboard: loadDashboard,
  448	    transcribe: renderTranscribe,
  449	    transcripts: loadTranscripts,
  450	    voicenotes: loadVoiceNotes,
  451	    bulk: loadBulk,
  452	    queue: () => loadQueue({force: true}),
  453	    costs: loadCostsPage,
  454	    detail: () => loadTranscriptDetail(S.detailId),
  455	    voices: loadVoices,
  456	    files: renderFilesPage,
  457	    settings: loadSettingsPage,
  458	    assistant: loadAssistant,
  459	  };
  460	  (loaders[page] || (() => {}))();
  ```
  This is called once, every time navigate('voicenotes') runs (i.e. on every nav-rail click, not cached) — so loadVoiceDumpItems must be registered here under a new page key (e.g. dumpnotes) or it never runs.
- Page div toggling (static/rack.js:441): PAGES.forEach(p => $('page-' + p).classList.toggle('active', p === page)); — this does a raw $('page-' + p) lookup with no null check; if the id is added to PAGES but the matching div id="page-..." is missing from index.html, this throws (Cannot read properties of null) and breaks navigation entirely, not just the new page. This is the single highest-risk wiring mistake for this issue.

### Helpers it reuses that the new function must reuse too (not reinvent)

- escapeHtml() — static/rack.js:161
- timeAgo() — static/rack.js:166
- formatDur() — static/rack.js:181
- NOTE_TYPE_LABELS / NOTE_TYPE_COLORS — static/rack.js:4477-4486 (see below)
- api() (the fetch wrapper — used for all /api/... calls) and toast() (error surfacing) — both used identically in loadVoiceNotes(), same pattern expected for loadVoiceDumpItems().
- navigate('detail', id) for click-through to the source transcript.
- CSS classes .voice-note-grid / .voice-note-card (static/rack.css:753,758,762) are generic enough to reuse verbatim rather than adding new dump-specific classes (nothing dump-specific about their styling).

NOTE_TYPE_LABELS / NOTE_TYPE_COLORS, verbatim (static/rack.js:4477-4486):
```js
4477	const NOTE_TYPE_LABELS = {
4478	  todo: 'Todo', idea: 'Idea', reminder: 'Reminder', journal: 'Journal', general: 'Note',
4479	};
4480	const NOTE_TYPE_COLORS = {
4481	  todo: '#FF8A3D',       // nixie amber
4482	  idea: '#7FE0C8',       // cyan
4483	  reminder: '#FFCB6B',   // yellow
4484	  journal: '#C8A6FF',    // violet
4485	  general: '#A9ACAF',    // neutral
4486	};
```
These constants do exist under exactly these names (the issue's claim is correct) and cover exactly the NOTE_TYPES vocabulary from services/voice_notes.py:27.

---

## 4. Navigation registration checklist — every place a "Dump Notes" nav item must be declared

1. Nav rail button markup, static/index.html:71-73 (mirror of):
   ```html
   <button class="rail-btn" data-nav="voicenotes">
     <span class="led"></span><span class="lbl">Voice notes</span><span class="badge" id="nav-badge-voicenotes"></span>
   </button>
   ```
   A new button class="rail-btn" data-nav="dumpnotes" (or similar id) is needed here. Note the badge span here has an id (nav-badge-voicenotes) that gets populated elsewhere (see item 6) — nav items without a live counter (Bulk, Assistant, Settings — static/index.html:74-76, 89-94) simply omit the id on their span class="badge".
2. Page container div, static/index.html:116: <div class="page" id="page-voicenotes"></div> — a matching <div class="page" id="page-dumpnotes"></div> (or whatever id is chosen) is required, and must exist or navigate()'s unconditioned $('page-' + p) lookup (static/rack.js:441) throws for every page, not just the new one.
3. PAGES array, static/rack.js:416 — new page-id string must be appended (any id not in this list is redirected to dashboard by navigate(), static/rack.js:436).
4. loaders map inside navigate(), static/rack.js:446-459 — new key mapping to loadVoiceDumpItems, or the page renders as a blank/stale div forever.
5. Nav-rail active-state toggling, static/rack.js:442-445:
   ```js
   442	  document.querySelectorAll('.rail-btn').forEach(b => {
   443	    const target = b.dataset.nav;
   444	    b.classList.toggle('active', target === page || (target === 'transcripts' && page === 'detail'));
   446	  });
   ```
   This is generic (keys off data-nav), so no extra registration needed here as long as step 1's data-nav value matches the PAGES/loader key used in steps 2-4 — but it is worth verifying alignment since a mismatch (e.g. data-nav="dumpnotes" vs a loader key dump_notes) is exactly the kind of typo this repo is prone to (per the task brief).
6. Nav badge counter (optional but "mirrors existing Voice Notes nav" implies it): nav-badge-voicenotes is populated in refreshRailChrome() (static/rack.js:429-430) from st.voice_notes, itself returned by the backend's /api/status (app.py:576, voice_notes: voice_note_count). There is no voice_dump_items (or similar) count field on /api/status in either this worktree or on origin/master's 5207255 — the #285 diff did not touch /api/status at all. So a byte-for-byte "mirror" of Voice Notes' badge behavior is not possible without also adding a backend count field (out of scope for both #285 and #286 as merged/described). The safe move is to give the new nav button an empty span class="badge" with no id, matching how Bulk/Assistant/Settings do it, rather than wiring a badge to a field that does not exist.
7. CSS: no #page-voicenotes/data-nav="voicenotes"-specific selectors exist in static/rack.css beyond the generic .page/.rail-btn rules and the reusable .voice-note-grid/.voice-note-card (static/rack.css:753-762) — no CSS registration gap found (see Sibling sweep for detail).
8. Service worker / bundle manifest: static/sw.js precaches /static/rack.min.js and /static/rack.min.css as whole-file entries (static/sw.js:11-12) — it does not enumerate individual pages/routes, so no per-page SW registration is needed. It does need the build artifact regenerated (see Section 5) and ideally a CACHE_VERSION bump (static/sw.js:5) to bust old clients' caches, though that is a general deploy practice rather than something specific to this feature.

---

## 5. Build pipeline — static/rack.js is bundled/minified; the served file is rack.min.js

package.json (verbatim):
```json
"scripts": {
  "test": "npm run test:js",
  "build": "npm run build:js && npm run build:css",
  "build:js": "esbuild static/rack.js --bundle --minify --outfile=static/rack.min.js",
  "build:css": "esbuild static/rack.css --minify --outfile=static/rack.min.css",
  "test:js": "node --test \"tests_js/**/*.test.js\""
},
"devDependencies": { "esbuild": "^0.25.0" }
```

static/index.html:151: script defer src="/static/rack.min.js" — the browser loads the minified bundle, not rack.js directly. Editing static/rack.js alone has zero runtime effect until npm run build:js (or npm run build) is re-run to regenerate the committed static/rack.min.js (and its sourcemap static/rack.min.js.map). All three are checked into the repo (confirmed present: static/rack.min.js, static/rack.min.js.map, static/rack.css to static/rack.min.css). This is the classic "I edited rack.js and nothing changed in the browser" trap — the fix must include running the build and committing the regenerated rack.min.js/rack.min.js.map.

---

## 6. Sibling sweep result

Grepped for every load<Something>Notes/load<Something>Items-style board loader in static/rack.js:
```
function loadPrefs / loadAssistant / loadDashboard / loadDashboardJobs / loadTape /
loadVoiceNotes / loadBulk / loadTranscripts / loadQueue / loadCostsPage /
loadTranscriptDetail / loadVoices / loadSettingsPage
```
None of these is a second, pre-existing "items" board loader that would collide with or need parallel updates for loadVoiceDumpItems — loadVoiceNotes is the only structural sibling, exactly as the issue assumes.

Checked for a shared "kinds shown on the board" registry, a cross-section search/filter, or an "empty-state section count": no such registry exists. PAGES (Section 4) is the only enumeration a new board section must join, and it is purely a navigation/routing list, not a content filter. loadDashboard() and /api/status do build a small set of summary counters (total_transcripts, voice_profiles, voice_notes, etc., app.py:569-580) but this is a fixed, individually-coded dict, not a generic loop over "kinds" — there is no single list to extend for a dump count, only the option to add one more explicit field (out of scope per #285/#286 as merged).

Two genuine sibling gaps found that the issue does not mention (both are single-file, low-risk, and arguably out-of-scope-but-adjacent — see Section 2 for detail):
- KIND_LABELS (static/rack.js:3319-3323, Queue page job-kind labels) has no voice_dump entry — falls back to the raw string, cosmetic only.
- The detail-page Mode-toggle 3-state cycle (static/rack.js:4875) silently resets any voice_dump-kind transcript to 'meeting' if clicked — a real (if rare, since #286 does not add a toggle-kind path from a dump board card) behavioral trap, but explicitly deferred to sub-issue 5 by the issue text ("the detail page's Dump Review tab").

No kind-value allowlist was found on the frontend analogous to the backend's if kind not in (...) guards (app.py:1447-1448, 1543-1544, 2077-2078 — all already include 'voice_dump' in this worktree, since those are part of #283's plumbing, already merged here). The frontend has no equivalent validation list to update.

---

## 7. What the issue's own description gets wrong or omits, vs. merged reality

- Staleness of the "already merged" claim (biggest gap): the issue states #285 is "ALREADY MERGED to master." True of origin/master (5207255), false of this worktree, which branched one commit behind that merge. See the Headline Finding above — this is the single most important discrepancy for whoever picks up the fix.
- Response envelope key: the issue does not specify the JSON envelope key, but it is worth stating explicitly since it is a common copy-paste mistake: the real key is items, not voice_dump_items (which would be the naturally-guessed parallel to voice_notes).
- startLiveCapture() framing is misleading: the issue says "Value voice_dump feeds into existing startLiveCapture() unchanged," implying startLiveCapture() reads/consumes the kind value. In reality startLiveCapture() takes no kind parameter at all and never touches it — kind is read later, at startJob() (static/rack.js:2268, form.append('kind', S.mode)), from S.mode. The actually-unwired control is the MFD "Mode" wheel (mfdCatDefs/mfdNav, Section 2b), not anything inside startLiveCapture() itself. Framing the work as "extend startLiveCapture()" would send an implementer to the wrong function.
- "Extending sub-issue 1's dropdown label into a fully wired option" undersells how much is already done vs. not: the two bulk-import kind selects already have the full voice_dump option (value + label) wired end-to-end since #283 (f28e254) — nothing left to do there. The genuinely-unwired picker is the Mode wheel, a completely different (non-select) widget the issue does not name or point at.
- VoiceDumpItem.note_type column comment is stale: database/__init__.py:208 comments "# bug | idea | todo | reminder (TBD in #284)", suggesting a bespoke vocabulary distinct from VoiceNote. What actually shipped in #284 reuses the existing NOTE_TYPES = ("todo", "idea", "reminder", "journal", "general") from services/voice_notes.py:27 — so the issue's assumption that the vocabulary is "identical" to VoiceNote's is correct, but only despite (not because of) the model's own doc-comment, which nobody updated after #284 landed.
- Nav badge parity is unachievable as literally worded: "Navigation - add 'Dump Notes' nav item (mirrors existing 'Voice Notes' nav)" implies full parity, including the live count badge (nav-badge-voicenotes, sourced from /api/status's voice_notes field). No equivalent backend field exists for voice-dump item counts (confirmed absent from both this worktree's and origin/master's /api/status), so an exact mirror is not possible without additional backend work nobody has scoped. The nav item can mirror the structure (rail button + page container + loader), but not the badge count, without either quietly leaving the badge empty (like Bulk/Assistant/Settings do) or scope-creeping into a new backend field.
- No status/draft-filter parameter exists to express — the issue's endpoint, as merged, has no status concept at all; finalization is structural (a separate table), not a flag. Nothing for the frontend to filter on.

---

## 8. Existing frontend/e2e tests

- tests_js/ contains exactly one test file: tests_js/batch_aggregate.test.js, run via node --test (package.json's test:js script) against static/batch_aggregate.js (a small, DOM-free, unit-testable helper deliberately kept separate from rack.js so it can run in plain Node — see the comment at static/rack.js:3325-3329). There is no committed browser/DOM/e2e test of rack.js's board pages, nav, or any UI rendering — nothing exercises loadVoiceNotes(), page-voicenotes, .voice-note-card, or navigate() in an automated, committed test file. A new loadVoiceDumpItems() has no existing JS test harness pattern to slot into beyond "do not add DOM-coupled logic to batch_aggregate.js-style pure helpers unless it is genuinely DOM-free."
- Backend API tests for the analogous, currently-live feature: tests/test_voice_note_route.py — e.g. test_list_voice_notes_empty (line 142) hits GET /api/voice-notes and asserts on the voice_notes envelope key; test_list_voice_notes_returns_users_own_notes (line 148) asserts per-user scoping. tests/test_voice_dump_route.py (22 integration tests, per the 5207255 commit message) does not exist in this worktree — it lives only on origin/master (added by the same #285 merge this worktree is missing). Once the worktree is rebased/merged past 5207255, that file will already cover the GET /api/voice-dump-items contract from the backend side; a frontend-focused fix for #286 would not need to duplicate that, only add whatever this repo's convention is for UI-level checks (there is none, per above) or rely on manual/Playwright-skill verification.
- No tests/e2e directory exists in the repo at all — searching for it comes up empty; e2e coverage in this codebase is entirely the ad hoc Playwright-MCP-driven skills (e2e-regression-http, e2e-ux-audit, e2e-ux-audit-deep), not committed test files.

Where a new test would belong: since there is no established JS/e2e test convention for board pages, the most consistent-with-repo choice is a Python test in tests/test_voice_dump_route.py (once present via rebase) for the GET /api/voice-dump-items contract (already covered upstream), and no committed frontend test is expected/blocked by precedent — loadVoiceNotes() itself has never had one either.
