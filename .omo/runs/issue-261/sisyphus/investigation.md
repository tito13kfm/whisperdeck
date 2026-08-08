# Investigation: Issue #261 — Voice dump multi-item capture

## Status: investigation-only pass

Issue #261 is a draft feature plan ("Draft plan, not scheduled"). The user asked to investigate and restructure it as a tracking issue, not to implement code. No worktree created; no code changes made.

## Plan doc assumptions vs. current code

The canonical design is `docs/plans/12-voice-dump-multi-item-capture.md` (landed via PR #260). All touchpoints verified against current `origin/master` (10bbc18). Findings:

### Verified accurate

| Plan claim | Current code | Verdict |
|---|---|---|
| `Transcript.kind` is `meeting\|dictation\|voice_note` only | `database/__init__.py:38` — column default `"meeting"`, comment lists three values | Correct, no `voice_dump` exists |
| `VoiceNote` has `UniqueConstraint(transcript_id)` + `uselist=False` | `database/__init__.py:72,171-172` | Correct |
| `VALID_KINDS` needs `voice_dump` added | `services/llm_jobs.py:20-24` — 12 values, no `voice_dump` | Correct |
| `voice_dump` should NOT be in `AUTO_RETRY_KINDS` | `services/llm_jobs.py:35` — currently 10 values including `voice_note` | Correct — plan says exclude it |
| `voice_dump` goes in `IO_KINDS` | `services/llm_jobs.py:42` — 10 values, `voice_note` included | Correct — same pool |
| `_transcript_text = transcript_text_for_prompt` alias | `services/voice_notes.py:25` | Correct — refactor target |
| `structure_voice_note` calls `_transcript_text(transcript)` internally | `services/voice_notes.py:181` | Correct — extraction is clean |
| `enqueue_auto_voice_note` gates on `transcript.kind != "voice_note"` | `services/llm_jobs.py:203` | Correct — same pattern for dump |
| `_dictation_job_fields` serializes `voice_note_job` for `kind == "voice_note"` | `app.py:390-397` — also returns null for other kinds | Correct — need new branch for `voice_dump` |
| `_serialize_transcript` passes `t.kind` through | `app.py:318` | Correct |
| `DASH_STAGE_KINDS` in `rack.js:1214-1219` lists pipeline lights — `voice_note` not among them, plan doesn't propose adding it | Verified — no dash stage light broken | Correct |

### One minor discrepancy

| Plan claim | Current code | Note |
|---|---|---|
| `bulk_defaults.kind` in `services/settings.py` | `services/settings.py` has `DEFAULT_SETTINGS` but no `bulk_defaults` key | `bulk_defaults` appears to be a separate concept (used in bulk import, not the universal defaults). The actual kind validation for bulk import is in `app.py`'s bulk import route. The plan's touchpoint list is a conceptual mapping, not a literal statement of where the constant lives. |

### Sibling sweep

Searched for every `kind == "voice_note"` and `kind != "voice_note"` pattern. Each one that needs a `voice_dump` branch must be updated. Full list:

| File | Location | Pattern | Action |
|---|---|---|---|
| `database/__init__.py:38` | Column comment | `# meeting \| dictation \| voice_note` | Add `\| voice_dump` |
| `services/transcription.py` | Diarization defaults | Kind-based defaults | Add `voice_dump` branch (lightest diarization, like `voice_note`) |
| `services/voice_notes.py:N/A` | Chain dispatch | Only runs for `voice_note` transcripts | Add voice_dump path (separate function) |
| `services/llm_jobs.py:203` | `enqueue_auto_voice_note` | `if transcript.kind != "voice_note": return None` | Need `enqueue_auto_voice_dump` with identical shape |
| `services/queue.py` | Auto-enqueue call site | Calls `enqueue_auto_voice_note` for `voice_note` | Add call for `voice_dump` kind |
| `app.py:390-397` | `_dictation_job_fields` | `voice_note_job` for `kind == "voice_note"` | Add branch for `kind == "voice_dump"` → `voice_dump_job` |
| `app.py` (bulk import) | Kind validation | Allows only certain kinds | Add `"voice_dump"` to allowed list |
| `services/settings.py` | `bulk_defaults.kind` | Lists allowed kind values | Add `"voice_dump"` |
| `static/rack.js` | Kind picker | Dropdown with `meeting`/`dictation`/`voice_note` | Add `"Audit / stream-of-consciousness dump"` option |

**No other sibling `kind == "voice_note"` instances found** that wouldn't need a `voice_dump` branch. Every one listed above is in scope.

### Existing voice_note endpoints (mirror for voice_dump)

| Endpoint | Location | Voice dump mirror |
|---|---|---|
| `GET /api/transcripts/{id}/voice-note` | `app.py:2608` | `GET /api/transcripts/{id}/voice-dump-items` |
| `GET /api/voice-notes` | `app.py:2632` | `GET /api/voice-dump-items` |
| `DELETE /api/voice-notes/{id}` | `app.py:2660` | Not needed (items aren't rows until finalized) |
| `POST /api/transcripts/{id}/voice-note/rerun` | `app.py:2682` | `POST /api/transcripts/{id}/voice-dump/rerun` |

Plus the new operations not in the existing chain: `save-draft` and `finalize`.

### Frontend patterns to reuse (per Phase 3 discipline rule)

The Dump Review tab needs an inline edit UI — the app's first. No existing edit/save/contenteditable exists in `rack.js`. However:

- `NOTE_TYPE_LABELS` and `NOTE_TYPE_COLORS` already exist and can be reused since the type vocabulary is identical
- The existing voice note board section (`loadVoiceNotes`) uses a fetch+render pattern that should be mirrored
- The detail-tab pattern (Notes tab gated on `t.kind === 'voice_note'`) should be mirrored for `t.kind === 'voice_dump'`

## Decomposition into sub-issues

Phase 1 of the plan doc breaks into 5 sub-issues. Each is independent enough for a separate branch/PR, ordered by dependency:

1. **Schema + kind plumbing** — database model + every kind-switch site
2. **Backend chain** — `_structure_from_text` refactor + `segment_voice_dump` + `run_voice_dump_job`
3. **Endpoints + serialization** — all 5 routes + auto-enqueue + `voice_dump_job` serialization field
4. **Frontend: kind picker + board section** — record-type dropdown + new board card section
5. **Frontend: Dump Review tab + inline edit** — new detail tab + editable title/body/type/discard + save-draft button

Issues 4 and 5 can run in parallel once 1-3 are done. Issue 2 depends on 1 (needs `voice_dump` kind in VALID_KINDS). Issue 3 depends on 1+2 (needs the chain to exist and the kind to be validated).

### Out of current scope (deferred phases)

- Phase 2: per-project grounding brief — deferred, needs separate design
- Phase 3: auto-linking notes via entity extraction (#241) — deferred, not designed
- Live-mode (streaming STT, silence-gap, TTS) — separate doc `docs/plans/13-live-conversational-capture.md`
- Auto-created GitHub issues from bugs/feature-ideas — out of scope

## Acceptance criteria (from plan doc verification section)

- [ ] Existing `test_voice_note_chain.py`/`test_llm_jobs.py` still pass unchanged
- [ ] `test_serialize_transcript_contract.py` extended for new kind-gated fields
- [ ] `VALID_KINDS`/`IO_KINDS`/`CPU_KINDS` partition test still passes with `voice_dump` added
- [ ] New tests: segmentation-call parsing, per-item structure+clarify parsing, truncation fallback
- [ ] New route tests (mirror `test_voice_note_route.py` pattern): rerun before/after finalize, save-draft round-trip, finalize with discarded item
- [ ] Real browser pass: record → review → edit → save → finalize → confirm notes appear on board
- [ ] Existing voice_note flow completely unaffected
