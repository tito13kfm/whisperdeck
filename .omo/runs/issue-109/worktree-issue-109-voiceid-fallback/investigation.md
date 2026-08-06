# Investigation: Issue #109 -- "voice_id: silent MFCC fallback makes voice_match silently match nothing"

Repo read from: `C:\Claude\WhisperDeck\.claude\worktrees\issue-109-voiceid-fallback`
Report written to: `C:\Claude\WhisperDeck\.omo\runs\issue-109\worktree-issue-109-voiceid-fallback\investigation.md`

All line numbers below are from the current worktree, not the issue body (which is stale in places -- noted where relevant).

---

## 1. Structure of `services/voice_id.py` (current, 403 lines)

`VoiceIdentificationService` (class starts line 23):

- `__init__` (26-32): picks backend once via `_detect_backend()`, caches `self._backend`, `self._classifier`, `self._pyannote_inference`, and `self._last_backend_error`.
- `_detect_backend()` (34-57): tries `speechbrain` -> `pyannote.audio` -> `librosa` -> `"none"`, in that priority order. This is a one-time, process-lifetime choice, not per-call.
- `backend_name` property (59-67): human label for `self._backend` (used in error messages and health surfacing).
- `enroll()` (69-112): entry point for creating a *new* profile from a sample. Calls `_extract_embedding()` (78), raises `ValueError` if `result is None` (79-92), otherwise calls `_ensure_clip_compatible()` (109) then `_persist_clip()` (110).
- `add_clip()` (114-140): entry point for adding an additional clip to an *existing* profile. Same shape as `enroll()`: calls `_extract_embedding()` (129), raises `ValueError` on `None` (130-135), then `_ensure_clip_compatible()` (138) + `_persist_clip()` (140).
- `_ensure_clip_compatible()` (142-163): shared guard used by both `enroll()` and `add_clip()` -- rejects a clip whose `embedding_model` differs from existing clips on the profile (147-154), or whose embedding length differs from existing clips (155-163). This is the **only** place with an explicit "mismatched model" guard, and it is a hard `raise ValueError`, not a silent skip.
- `_persist_clip()` (165-184): writes the `VoiceClip` row, commits, calls `_recompute_profile_embedding()`.
- `_recompute_profile_embedding()` (206-219): means the clips' embeddings, updates `profile.embedding_model` to the latest clip's model (215-217).
- `identify()` (221-250): **the function the issue is about**. Calls `_extract_embedding()` (222); if `None`, returns `[]` (223-224, no error/signal). Otherwise loops enrolled profiles (232-247): skips profiles with no embedding (233-234), **skips profiles whose `embedding_model` differs from the probe's model via a bare `continue`** (235-236 -- this is the line the issue calls out), skips dimension mismatches (237-239), else computes cosine similarity and keeps it if `>= threshold`.
- `list_profiles()`, `delete_profile()`, `remove_clip()` -- unrelated CRUD, not embedding-path.
- `_extract_embedding()` (299-312): **the function the issue names**. Dispatches on `self._backend`:
  - `"speechbrain"` (300-304): tries `_embed_speechbrain()`; if it returns `None`, silently calls `_mfcc_fallback()` (304).
  - `"pyannote"` (305-309): tries `_embed_pyannote()`; same silent `_mfcc_fallback()` on failure (309).
  - `"librosa_mfcc"` (310-311): MFCC is already the primary/only backend, so this isn't a "fallback" in this branch, it's just the selected backend.
  - `"none"` (312): returns `None`.
- `_mfcc_fallback()` (314-316): thin wrapper -- calls `_embed_mfcc()` and labels the result `"MFCC fingerprint (librosa)"`.
- `_get_classifier()` / `_embed_speechbrain()` (318-348), `_get_pyannote_inference()` / `_embed_pyannote()` (350-378), `_embed_mfcc()` (380-392): the three backend implementations. All three wrap their body in `try/except Exception` and return `None` on failure, additionally stashing a message in `self._last_backend_error` (347, 377, 390-391).
- `_cosine_similarity()` (394-400): static helper.
- Module-level singleton: `voice_id_service = VoiceIdentificationService()` (403).

**Issue accuracy check on file structure:** the issue cites `services/voice_id.py:299-312` for `_extract_embedding()` -- that is **exactly correct** in the current worktree (299-312, unchanged). The issue's `identify()` line reference ("line 235-236" for the model-mismatch `continue`) is also correct verbatim in the current code. The dimension figures (speechbrain 192-dim, pyannote 256-dim, MFCC 20-dim) are believable given `n_mfcc=20` at line 385, but I did not verify the exact speechbrain/pyannote output dimensionality against upstream model docs (external pretrained models, not observable from this repo). The core structural claims in the issue are accurate.

---

## 2. Every caller of `_extract_embedding()`, `_mfcc_fallback()`, and `identify()`

Grepped the whole repo for the three function names (`*.py` only; JS bundle matches for unrelated `.identify` methods excluded).

### `_extract_embedding()` callers (all inside `services/voice_id.py` itself -- no caller reaches into it from outside the class)
| Caller | Line | What it does with `None` | Affected by a fallback-behavior change? |
|---|---|---|---|
| `enroll()` | `services/voice_id.py:78` | Raises `ValueError` with a descriptive message (79-92) mentioning `self.backend_name` and `self._last_backend_error` | Yes -- if fix (b) makes `_extract_embedding()` return `None` more often, `enroll()` already has a correct, user-facing error path. No regression risk; it already does the "loud failure" behavior the issue wants for `identify()`. |
| `add_clip()` | `services/voice_id.py:129` | Same -- raises `ValueError` (130-135) | Same as above -- already loud. |
| `identify()` | `services/voice_id.py:222` | Returns `[]` silently (223-224) | Yes -- this is the exact bug. No error, no signal; `identify()` treats "couldn't even extract a probe embedding" identically to "extracted fine, nobody matched". |

`_mfcc_fallback()` (services/voice_id.py:314) is called only from inside `_extract_embedding()` itself (lines 304, 309) -- no external callers.

### `identify()` callers (outside `voice_id.py`)
| Caller | File:line | What it does with the result | Impact of changing fallback behavior |
|---|---|---|---|
| `POST /api/voices/identify` route | `app.py:3381` (handler starts `app.py:3363`) | Returns `{"matches": matches, "total_profiles": ..., "backend": voice_id_service._backend}` (3383-3387) directly -- no distinction today between "0 matches because no one matched" and "0 matches because the probe used a different embedding model or extraction failed". | If `identify()` gains a way to signal "extraction failed" vs "genuinely no match", this route benefits directly -- today a caller gets an empty list with zero explanation either way. |
| voice_match job's `_identify()` closure | `services/llm_jobs.py:729` (called via `loop.run_in_executor` at 731, inside the per-segment loop 715-743) | If `matches` is truthy, relabels the segment (737-739); if falsy (empty list, whether "no match" or "extraction failed") the segment is left alone **and it is NOT counted in `skipped`** (712, 741) -- `skipped` only increments on an *exception* from `extract_clips_concat`/`identify` (740-741 `except Exception: skipped += 1`). | This is the crux of the bug for the voice_match job. A silent MFCC fallback causing `identify()` to return `[]` for every segment produces a job that completes with `skipped == 0`, `error is None`, `status == "completed"` -- exactly the "job completes successfully with 0 relabeled segments, no error" the issue describes. |

### Everything else in the sweep (not real "callers" of the 3 named functions but relevant context)
- `tests/test_voice_id.py:62,75,88` call `_extract_embedding()` directly to unit-test the fallback chain (see section 6).
- `tests/test_voice_id.py:387,405,423,442` call `identify()` directly to unit-test the model-mismatch/dimension-mismatch skip behavior (see section 6 -- this already has direct test coverage for the exact "skip" mechanism the issue is about).
- `tests/test_voice_match_job.py:87` docstring references `identify()` running for real through the executor (not a call site itself, just documents the design).

**Conclusion for section 2:** `enroll()` and `add_clip()` already surface extraction failure loudly (`ValueError`). Only `identify()` (and, downstream, the voice_match job that consumes it) swallows the failure silently. Any fix must not touch `enroll()`/`add_clip()`'s `_extract_embedding()` contract (they already do the right thing) -- it should be scoped to `identify()` and the voice_match job's consumption of its result.

---

## 3. The voice_match job (`services/llm_jobs.py`)

### Location
- Kind registered in `VALID_KINDS` at `services/llm_jobs.py:21`, in `CPU_KINDS` (not `AUTO_RETRY_KINDS`) at line 43 (comment 25-34 explains why: local CPU-bound compute, deterministic failures, no auto-retry).
- Enqueue route: `app.py:2735-2761` (`POST /api/transcripts/{transcript_id}/voice-match`), gated to `effective_kind(t) == "meeting"` (2748-2757) and requiring stored audio (2758-2759). Enqueues via `enqueue_llm_job(db, current_user.id, transcript_id, "voice_match", "", "")` (2760).
- Execution: `services/llm_jobs.py:690-752`, inside `run_llm_job()`'s big `if/elif` dispatch (job kinds).

### How it calls into voice_id
1. Precondition checks (691-705): backend must not be `"none"` (691-693, `_finish(..., "failed", "No voice embedding backend available")`), audio file must exist (694-696), and at least one `VoiceProfile` with `embedding is not None` must exist for this user (697-705, else fails with `"No enrolled voices with clips -- add a clip to a roster profile first"`).
2. Per-segment loop (714-743): for each segment, extracts a clip via `extract_clips_concat()` (717-720), then calls `voice_id_service.identify(db, job.user_id, clip_path, threshold=0.65, hf_token=...)` off the event loop via `loop.run_in_executor()` (727-731, comment explains why -- sync CPU-bound call shouldn't block the loop).
3. If `identify()` raises (from either `extract_clips_concat` or the executor call), the whole segment is caught by `except Exception: skipped += 1` (740-741) -- but if `identify()` returns normally with `[]` (no exception), nothing is caught, nothing is counted.
4. If `matches` truthy, relabels segment `i` in `new_segments` (738-739) and appends to `changed`.
5. After the loop: if any segments changed, calls `record_relabel(db, transcript, "voice_match", changed, ...)` (745-747) to record an undo-able relabel. Always writes `transcript.segments = new_segments` (748) and commits (750).
6. Finish: `error = f"{skipped} segment(s) skipped (extraction/embedding failed)" if skipped else None` (751), then `_finish(db, job, "completed", error)` (752) -- status is always `"completed"` here, never `"failed"`, regardless of how many segments were skipped or matched.

### `result_json` / result payload shape
The voice_match branch (690-752) never sets `job.result_json` at all -- unlike every other job kind (`correction` sets `{"corrected_text": ...}` at 379, `rediarize` sets `{"segments": merged}` at 685, etc.). So there is no structured payload describing what changed; the only place segment changes are visible is `transcript.segments` itself (via `record_relabel`'s diff) and the queue-screen `job.error` string.

Every `result_json =` assignment site in `services/llm_jobs.py`, for context:
```
379:  job.result_json = {"corrected_text": transcript.corrected_text}         # correction
421-424: job.result_json = {...summary fields...}                            # summary
445:  job.result_json = {"text": text}                                       # voice_notes chain step
458:  job.result_json = {"format": label}                                    # format_*
501:  job.result_json = {"kind": ..., "confidence": ..., "accepted": ...}     # classify_intent
541:  job.result_json = {"tags": tags}                                       # tagging
602:  job.result_json = {...}                                                # voice_dump/voice_note chain
649:  job.result_json = {"items": items}                                     # voice_dump items
685:  job.result_json = {"segments": merged}                                 # rediarize
819:  job.result_json = {"user_request": ..., **result}                      # assistant
```
voice_match has no entry in this list -- it is the one job kind with no `result_json` payload.

### `serialize_llm_job()` shape (`services/llm_jobs.py:48-70`)
```python
{
    "id": job.id,
    "kind": job.kind,
    "transcript_id": job.transcript_id,
    "status": job.status,
    "progress": {"done": job.progress_done or 0, "total": job.progress_total or 0},
    "provider": job.provider,
    "model": job.model,
    "error": job.error,
    "will_retry": bool(...),
    "created_at": ...,
    "updated_at": ...,
}
```
Note: `result_json` is not included in `serialize_llm_job()` at all -- it's a DB-only field (`database/__init__.py:121`, `LlmJob.result_json`, comment: "output snapshot for history/diff"), exposed separately through the run-history/diff endpoints (`app.py:2764`, `/api/transcripts/{id}/runs/{kind}`), not through the main transcript payload's `voice_match_job` field. So even if voice_match started setting `result_json`, the Queue screen / detail page's `t.voice_match_job` object (built from `serialize_llm_job`, per `app.py:399`) would not see it without a separate fetch to the run-history endpoint.

### Skip / match / error counting
- `skipped` (int, local var, line 712): incremented only on an exception during per-segment extraction or identification (740-741). Not incremented when `identify()` returns `[]` cleanly (e.g. due to model mismatch or a silently-degraded probe embedding).
- `changed` (list of `(index, old_speaker)` tuples, line 713): appended only on a confident match (737-739); drives `record_relabel()`'s diff and the undo feature.
- No separate "matched" or "no-match" counter exists -- only `changed` (relabeled count, derivable as `len(changed)`) and `skipped` (extraction/identify exceptions only).

### Status values / precedent for "completed but degraded"
- `TERMINAL_LLM_STATUSES = ("completed", "failed", "cancelled")` (`services/llm_jobs.py:18`) -- `LlmJob.status` has no `"partial"` value. `"partial"` exists only on `Transcript.status` (`database/__init__.py:52` comment, used throughout `services/queue.py` for chunked transcription, e.g. lines 538, 577, 611) -- a completely different subsystem/model (`TranscriptionJob`/`Transcript`, not `LlmJob`). The frontend's `jobStatusView()` (`static/rack.js:3360-3373`) does have a `case 'partial':` branch (367) with amber styling, but the callers that feed it (`static/rack.js:3420-3468`, iterating `txs`) are transcription-chunk jobs, not `LlmJob` rows -- `LlmJob`/voice_match can never reach that branch given `TERMINAL_LLM_STATUSES`.
- The actual existing "completed but degraded" precedent for `LlmJob` is exactly what voice_match itself already does for its skip counter: set `status = "completed"` and a non-null `error` string (line 751-752). The frontend renders this by coloring the job's meta line red whenever `j.error` is truthy, independent of status (`static/rack.js:3479` / `3509`: `color:${j.error ? 'var(--red)' : 'var(--label-dim)'}`), and by running `job.error` through `humanizeJobError()` (`static/rack.js:214-224`) for display (3473/3502). This is on the Queue screen rendering (`static/rack.js:3449-3520`-ish, batch-entry/unit job cards) -- it is not shown on the transcript detail page (see section 4).

**In short:** the precedent pattern for "completed but degraded" in this codebase, for `LlmJob` specifically, is: `status="completed"` + non-empty `error` string, rendered in red on the Queue screen only. There is no dedicated `warnings` field or `"partial"` `LlmJob` status anywhere in the codebase today.

---

## 4. Frontend consumer (`static/rack.js`)

- The voice_match job object reaches the frontend as `t.voice_match_job` (built server-side by `app.py:399`: `"voice_match_job": serialize_llm_job(jobs_map[(t.id, "voice_match")]) if (t.id, "voice_match") in jobs_map else None`).
- Detail page (`renderDetailBody`, `static/rack.js:5067` onward): only reads `t.voice_match_job` to decide whether to show a running spinner unit -- `llmJobActive(t.voice_match_job) ? '<div id="job-voice-match">' + jobRunningUnit(t.voice_match_job, 'Voice match') + '</div>' : ''` (line 5071). Once the job is `"completed"`, `vm` becomes `''` and nothing about the job (no error, no skip count, no "0 matched") is rendered on the detail page -- the only visible effect is that `transcript.segments` may or may not have changed (silently, if it changed at all).
- `jobRunningUnit()` (`static/rack.js:4287-4299`) only ever shows "running"/"queued" text plus provider/model -- it has no code path for showing `job.error` (that field isn't even read inside this function).
- The only place `job.error` is rendered anywhere in the UI is the Queue screen cards (`static/rack.js:3473`, `3479`, `3502`, `3509`), which live on a separate page from the transcript detail view and require the user to expand/find that specific job row.
- `runVoiceMatch()` (`static/rack.js:5569-5577`): fires the POST, shows a generic `'Matching against voice roster...'` info toast, then reloads detail data (5573-5575) -- it does not poll to completion or show a completion toast/summary at all (unlike, e.g., the batch-transcription code which does show completion toasts with failure counts, `static/rack.js:3440-3445`).
- `hasUnlabeledSpeakers(t)` nudge (`static/rack.js:5073-5082`) offers a "Match now" button when there are enrolled voices and unlabeled speakers, but has no awareness of backend/model compatibility.

**What the renderer would need to show a new warning field:**
1. `serialize_llm_job()` (`services/llm_jobs.py:48-70`) would need to add the new field (e.g. `warning`, or reuse `error`) to its returned dict -- right now it does not pass through `result_json` at all, so a `result_json`-only warning would be invisible to the frontend's `t.voice_match_job` without also touching `serialize_llm_job()`.
2. `renderDetailBody()` (`static/rack.js:5067+`) would need a new branch for `t.voice_match_job` when `!llmJobActive(...)` (i.e. after `vm` currently goes to `''`) -- today there is no "just completed" summary unit at all on the detail page for voice_match, so this isn't just "add the warning to an existing unit", it's "add a first completed-state unit" for this job kind on this page. The Queue screen's job-error rendering (`static/rack.js:3473`/`3479`) is the closest existing pattern to copy (red-tinted meta line + `humanizeJobError()`).
3. `runVoiceMatch()` (`static/rack.js:5569-5577`) does not poll for completion, so even with a warning field added server-side, the toast at match-kickoff time can't reflect it -- the user would have to reopen/refresh the detail page (which re-fetches `t.voice_match_job`) or visit the Queue screen to see it, unless polling is added.

---

## 5. Sibling sweep (mandatory)

### a. Other `try/except` in `voice_id.py` that swallow an exception and substitute a degraded result
- `_embed_speechbrain()` (330-348): `except Exception as e: self._last_backend_error = f"speechbrain: {e}"; return None` (346-348) -- not itself silent (caller sees `None` and either falls back or, in enroll/add_clip, raises), but see the stale-state bug below.
- `_embed_pyannote()` (360-378): same shape (376-378).
- `_embed_mfcc()` (380-392): same shape (389-392), with an extra guard: `if not self._last_backend_error: self._last_backend_error = ...` (390-391).
- `remove_clip()` (186-204) and `delete_profile()` (282-297): `try: os.remove(clip.audio_path) except OSError: pass` (198-200, 290-293) -- silently ignores a missing/undeletable file on disk. Unrelated to embeddings, low severity, but is a second, independent "swallow and continue" pattern in the same file worth naming for completeness.

**New defect found during the sweep -- stale `_last_backend_error` (not mentioned in the issue):** `self._last_backend_error` (`services/voice_id.py:32`) is set on failure (347, 377, 391) but never reset to `None` on a subsequent success. Because `voice_id_service` is a process-wide singleton (`services/voice_id.py:403`) shared by every request and by the voice_match job's executor thread, a stale error message from one unrelated failed call (e.g. a bad pyannote token used once) will keep being reported in `enroll()`'s/`add_clip()`'s `ValueError` messages (`services/voice_id.py:87`, `131`) for every subsequent, unrelated failure until the process restarts or another failure overwrites it. This is a correctness/UX bug adjacent to the issue (misleading error text) but distinct from the silent-fallback bug -- flagging it as a sibling finding, not proposing a fix for it here since it's outside the issue's scope.

### b. Other places where an embedding model mismatch is handled with a bare `continue` or silent skip
- `identify()` line 235-236: the one the issue names (`if profile.embedding_model and profile.embedding_model != probe_model: continue`).
- `identify()` line 233-234: `if profile.embedding is None: continue` -- not a model-mismatch, but the same silent-skip shape (profile has no clips at all yet).
- `identify()` line 238-239: `if len(stored) != len(probe_embedding): continue` -- dimension-mismatch skip, same shape, catches legacy `embedding_model IS NULL` rows whose stored vector length happens to differ from the probe.
- No other file in the repo re-implements this comparison -- `identify()` is the only consumer that compares `embedding_model` values across rows (confirmed by the caller grep in section 2; `_ensure_clip_compatible()` in the same file, `services/voice_id.py:142-163`, is the other embedding_model comparison, but it raises loudly rather than silently skipping -- see next point).

### c. Does `enroll()` also use the same fallback? (the mirror-image bug)
Yes. `enroll()` (`services/voice_id.py:69-112`) calls the exact same `_extract_embedding()` (78) that silently falls back to MFCC. If `self._backend == "speechbrain"` and `_embed_speechbrain()` fails for this particular clip (corrupted audio, e.g.) but `_embed_mfcc()` succeeds, `_extract_embedding()` returns `(mfcc_embedding, "MFCC fingerprint (librosa)")` -- not `None` -- so `enroll()`'s `if result is None:` guard (79) never fires, no `ValueError` is raised, and the profile is silently persisted with `embedding_model = "MFCC fingerprint (librosa)"` (`services/voice_id.py:100-102`, 217) while every other profile in the system (and the default backend) is speechbrain. From then on, this profile is invisible to `identify()` for any probe that itself doesn't also fall back to MFCC (line 235-236 skip) -- a profile "enrolled but permanently unmatchable" until the user notices and re-enrolls, with zero indication at enroll time that anything degraded. This is a real, confirmed instance of the "mirror-image" bug hypothesized in the task description, and it means a fix scoped only to `identify()` (issue's option (b)) would still leave this enroll-side half of the bug in place -- `enroll()` needs the same "don't silently accept a fallback embedding" treatment, or a warning surfaced back through the `/api/voices/enroll` response, for the fix to be complete.

`add_clip()` (`services/voice_id.py:114-140`) has the identical structure and the identical exposure (129-136) -- same finding applies there, and it's arguably worse: `add_clip()` is exactly how the roster page adds more clips to an already-established, e.g., speechbrain profile; a single bad clip whose speechbrain extraction fails would either (a) get silently absorbed as an MFCC clip if it's the very first clip on the profile, or (b) get rejected loudly by `_ensure_clip_compatible()`'s model-mismatch guard (147-154) if the profile already has a speechbrain clip on it. So case (b) is already "protected" by an unrelated guard -- it's specifically the first clip on a profile (via either `enroll()` or `add_clip()` on a brand-new profile) that can silently land on MFCC with no guard at all.

### d. Dimension-mismatch / model-mismatch guards elsewhere -- grep for `embedding_model`, `dim`, `192`, `256`, `20`
- `embedding_model` appears in: `database/__init__.py` (column definitions for `VoiceProfile`/`VoiceClip`), `services/voice_id.py` (as mapped above: `_ensure_clip_compatible` raises, `identify()` skips, `_recompute_profile_embedding()` derives the latest), `app.py` (surfacing it in `/api/voices` list responses and the enroll/add_clip routes), and `tests/test_voice_id.py` (extensively, see section 6). No other module touches `embedding_model`.
- Literal dims `192`/`256`/`20`: only `20` appears in `services/voice_id.py:385` (`n_mfcc=20`, the MFCC feature count). Neither `192` nor `256` appears anywhere in the Python source -- the issue's cited speechbrain-192/pyannote-256 dimensionality is not encoded or asserted anywhere in this codebase; the only dimension check present is the generic `len(embedding)` comparison in `_ensure_clip_compatible()` (155-163) and the generic `len(stored) != len(probe_embedding)` check in `identify()` (238-239) -- neither hardcodes specific model dimensions, they just compare whatever two vectors happen to be, which is the right design (it already generalizes to a fourth future backend without new constants).

---

## 6. Existing test coverage

### `tests/test_voice_id.py` (656 lines) -- the file most relevant to any fix here
Covers, with fixture/mock patterns worth reusing:
- Fallback chain unit tests, calling `_extract_embedding()` directly on a bare `VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))` instance with `svc._backend` set manually and `monkeypatch.setattr(svc, "_embed_speechbrain"/"_embed_mfcc"/"_embed_pyannote", lambda path: ...)`:
  - `test_extract_embedding_falls_back_to_mfcc_when_speechbrain_fails` (56-67) -- already directly tests the exact fallback path the issue is about, asserting `model_id == "MFCC fingerprint (librosa)"` when `_embed_speechbrain` returns `None`.
  - `test_extract_embedding_speechbrain_success_reports_speechbrain_model` (70-79).
  - `test_extract_embedding_librosa_backend_reports_mfcc_model` (82-92).
- `identify()` model/dimension-mismatch skip tests -- already directly test the exact "silent skip" behavior the issue reports as a bug:
  - `test_identify_skips_profiles_with_no_embedding` (378-389).
  - `test_identify_skips_profile_with_different_embedding_model` (392-407) -- constructs a `VoiceProfile` with `embedding_model="pyannote/wespeaker-voxceleb-resnet34-LM"`, monkeypatches `_extract_embedding` to return a speechbrain-labeled probe, asserts `results == []`. This test currently encodes the "bug" as correct/expected behavior -- any fix that changes `identify()`'s return contract on model mismatch must update or extend this test rather than break it.
  - `test_identify_still_matches_legacy_profile_with_no_recorded_model` (410-426).
  - `test_identify_dim_mismatch_guard_still_skips_same_label_different_length` (429-444).
- Enroll/add_clip cross-model guard tests: `test_add_clip_raises_when_embedding_model_differs_from_existing_clips` (272-290), `test_enroll_rejects_cross_model_clip_on_existing_profile` (580-601), `test_add_clip_rejects_dim_mismatch_with_legacy_null_model_clip` (604-626) -- these all monkeypatch `svc._extract_embedding` directly (never touching the real fallback chain) to inject a specific `(embedding, model_id)` tuple, so they'd be unaffected by a change to the internal fallback logic as long as `_extract_embedding()`'s external contract (`Optional[tuple[np.ndarray, str]]`) is preserved.
- Fixture/mock shape used throughout this file (reusable pattern for a new test):
  - `_test_user(db_session)` (14-20) -- get-or-create a `User` row.
  - `_svc(tmp_path)` (23-26) -- `VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))` with `svc._backend = "speechbrain"` forced.
  - `_profile(db_session, user_id, name="Alice")` (212-216) -- bare `VoiceProfile` row, no clips.
  - Route-level tests use the shared `client`/`db_session` fixtures from `tests/conftest.py` (e.g. `test_enroll_route_passes_hf_token_from_user_settings`, 524-544) with `monkeypatch.setattr("app.voice_id_service.enroll", fake_enroll)` to stub the service at the route boundary.

### `tests/test_voice_match_job.py` (282 lines) -- the job itself
- Fixtures: `_NoCloseSession` (12-19, wraps `db_session` so `run_llm_job`'s `db.close()` doesn't tear down the shared test session), `_user(db_session, name="matcher")` (22-26), `_enrolled_profile(db_session, user, name="Alice")` (29-36, a `VoiceProfile` with `embedding=[0.1, 0.2, 0.3]`, `embedding_model="test"`, `sample_count=1`), `_transcript_with_segments(db_session, user, tmp_path, segments)` (39-46, writes a fake audio file and a `Transcript` row with `status="completed"`, given `segments`).
- Job execution pattern: `job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")`, then manually `job.status = "running"; db_session.commit()`, then `asyncio.run(run_llm_job(factory, job.id, transcription_service=None))` where `factory = lambda: _NoCloseSession(db_session)`. Extraction and identify are stubbed with `patch("services.llm_jobs.extract_clips_concat", fake_extract)` and `patch("services.llm_jobs.voice_id_service.identify", fake_identify)`.
- Directly relevant existing tests:
  - `test_voice_match_relabels_confident_segments_only` (49-83) -- asserts a segment with no confident match (`fake_identify` returns `[]`) is left untouched, and the job still ends `status == "completed"`. This is functionally the same code path a silent-MFCC-fallback bug would take (identify returns `[]` for an unrelated reason) -- the test doesn't distinguish "genuinely no match" from "extraction degraded", which is precisely the ambiguity the issue is about.
  - `test_voice_match_skips_segment_on_extraction_failure_without_failing_job` (175-206) -- the one existing test that asserts on the `error` field's skip-count message (`assert "1 segment" in job.error` at 205, comment: `# skip count surfaced even though status is completed`) -- this is the precedent to extend/copy for a new "voice model fallback" warning.
  - `test_voice_match_runs_real_identify_through_executor` (86-111) -- the only test that exercises real `identify()` (not stubbed) through the actual `run_in_executor` wrapper, only stubbing `_extract_embedding`; this would be the natural place to add a test that stubs `_extract_embedding` to simulate a fallback (e.g. return an MFCC-labeled tuple while the enrolled profile is `"test"`/speechbrain-labeled) and assert on whatever new signal a fix adds.
  - `test_voice_match_passes_hf_token_from_user_settings` (251-281) -- shows the pattern for asserting on values `identify()` is called with.

### `db_session` / `client` fixtures (`tests/conftest.py`)
- `db_session(tmp_path)` (72-83): fresh sqlite file per test via `init_db()`, session closed/engine disposed in a `finally`.
- `client(db_session)` (86-...): `TestClient` wired to `db_session` via `app.dependency_overrides[get_db]`, with rate limiter reset and (per the docstring) a logged-in test user via cookies -- does not use `with TestClient(app)` to avoid triggering the app's lifespan/queue worker.

No test file directly named for "mfcc" beyond `test_voice_id.py` (the fallback tests live there under generic names); no dedicated "voice model mismatch warning" tests exist yet anywhere.

---

## 7. Evaluating the issues two proposed fixes

### (a) Propagate the fallback as a warning to the jobs result_json so the UI can show it
- Feasible, but incomplete as literally stated. As shown in section 3, the voice_match job branch never sets result_json at all today, and, more importantly, serialize_llm_job() (services/llm_jobs.py:48-70) does not include result_json in the dict it returns, and result_json is not part of t.voice_match_job as consumed by the frontend (app.py:399). So propagate to result_json alone would be invisible to the UI unless (1) serialize_llm_job() is also changed to include the new field (or a lighter-weight boolean/string sibling of error), and (2) renderDetailBody() gains a new completed-state unit for voice_match (section 4, today there is none at all, active-only).
- A more targeted, lower-risk version of (a) that fits the codebases existing precedent (section 3): extend the already-used job.error string (currently "N segment(s) skipped..." at services/llm_jobs.py:751) to also report a probe/profile embedding backend mismatch count, e.g. tallying how many segments were compared against a probe whose embedding model differed from every enrolled profiles model. This reuses the exact status=completed plus non-null error pattern that already exists and is already rendered (red meta line, Queue screen only), no new frontend work required for that channel, though the detail-page gap in section 4 would remain unless also addressed.
- This requires identify() itself to expose which embedding model the probe actually used and whether it matches, right now identify()s return value (list[dict]) carries no such metadata; the job would need identify() to return something richer than a bare list, or a second call/property to inspect probe_model after the fact.

### (b) Do not fall back during identify(), return None and let the caller handle it
- The issue's framing here is a bit imprecise: identify() does not currently return None under any circumstance, it always returns a list (possibly empty), even when _extract_embedding() itself returns None (line 223-224: if result is None: return []). So "have identify() return None" would be a breaking change to identify()'s type signature -- every caller (app.py:3381, services/llm_jobs.py:729) currently treats the result as iterable (if matches: truthy-check works fine on None too, actually -- if None: is falsy, so the voice_match job's if matches: check at line 737 would still work unchanged). The /api/voices/identify route (app.py:3383) returns {"matches": matches, ...} directly -- returning None there would serialize as "matches": null instead of "matches": [], a visible API contract change for any existing consumer of that endpoint.
- More precisely, (b) as intended by the issue is really about not silently falling back to MFCC inside _extract_embedding() when called from the identify() path -- i.e. distinguish "the probe embedding extraction degraded to a different model than what is enrolled" from "extraction succeeded, no match". Implemented at the _extract_embedding() level, this would need to be conditional per-caller (a new parameter or a caller-side check), because sections 2/5c show enroll()/add_clip() also call _extract_embedding() and currently rely on MFCC-as-last-resort succeeding to enroll speakers at all when speechbrain/pyannote are not installed (_detect_backend()'s librosa_mfcc branch, services/voice_id.py:52-56) -- for a system with only librosa installed, disabling the fallback entirely would break enrollment/add_clip for that whole class of installs, not just fix identify(). The fix therefore cannot be "delete _mfcc_fallback() calls" -- it must be "surface that a fallback occurred" so the caller (identify(), the voice_match job, and per section 5c enroll()/add_clip() too) can decide whether that is acceptable.
- They should be combined, and extended to cover enroll()/add_clip() too (section 5c), not just identify(): the cleanest single change is to make _extract_embedding() (or a thin wrapper) report whether the result came from a fallback (e.g. return a 3-tuple embedding, model_id, used_fallback bool, or keep the fallback model_id itself as the signal -- since model_id already differs, "did the model actually used differ from the backends primary model for self._backend" is derivable without a new field). Then:
  - identify() can compare the probe's fallback status against whether any enrolled profile shares its model, and return that as metadata (not None -- a structured dict, or an added key alongside the existing matches list) so both the /api/voices/identify route and the voice_match job can surface a warning distinct from "no results / not skipped".
  - The voice_match job (services/llm_jobs.py:690-752) should count "probe used a fallback embedding model relative to the roster's model" as a form of "skipped-with-reason", folding it into (or alongside) the existing skipped/error reporting at line 751 -- reusing the established "completed plus non-null error" precedent (section 3) rather than inventing a new job status.
  - enroll()/add_clip() should raise (or at minimum warn in the response, app.py:3352-3358/route for add_clip) when the first clip on a profile lands on a fallback model while the service's primary self._backend is something else -- currently nothing distinguishes "backend is librosa_mfcc so MFCC is expected" from "backend is speechbrain but this one clip silently degraded to MFCC" (section 5c) at the enroll()/add_clip() call sites, and both need the fix, not just identify().

### Things the issue's framing gets wrong or oversimplifies
1. identify() never returns None today -- it returns [] in every "nothing to report" case, including extraction failure (223-224). Proposed fix (b) as literally worded ("return None") would be a type change with a small ripple (/api/voices/identify's JSON shape) that the issue does not call out.
2. The issue frames this as an identify()-only bug; section 5c shows it is at minimum a two-sided bug -- enroll()/add_clip() can silently persist a profile whose first clip landed on the fallback model, which is arguably a worse outcome (a permanently-orphaned profile) than a single failed identify() call, and is not mentioned in the issue at all.
3. The issue implies the model-mismatch continue in identify() (235-236) is itself the bug -- it is not; that guard is correct and well-tested (test_identify_skips_profile_with_different_embedding_model, tests/test_voice_id.py:392-407) as a safety net against comparing incompatible vector spaces. The actual bug is the total absence of any signal that the skip happened for a "backend degraded" reason versus profiles genuinely being enrolled under a different (deliberately chosen) backend.
4. The issue's proposed fix (a) ("propagate to result_json") undersells the amount of plumbing needed -- result_json is not even wired into serialize_llm_job()/the frontend's t.voice_match_job object today (section 3), and the detail page has zero completed-state rendering for voice_match at all (section 4), so "the UI can show it" requires new UI code, not just a new field.
5. Dimensions cited (192/256/20) -- only 20 (MFCC) is verifiable in this repo; 192/256 are asserted about external pretrained models not encoded anywhere here, and no code in this repo hardcodes or asserts those numbers (section 5d) -- the guards that exist are generic length comparisons, which is good design and should not be replaced with hardcoded constants as part of any fix.
