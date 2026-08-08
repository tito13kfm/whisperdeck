# investigation.md — issue #112

**Issue:** #112 "voice_match: has_enrolled_voice check doesn't filter by current backend" (standalone, not a tracking issue).
**Worktree:** `C:\Claude\whisperdesk\.claude\worktrees\issue-112-voice-match-backend`, branch `worktree-issue-112-voice-match-backend`.
**Phase 1 agent:** Sonnet, `Explore` (read-only). Findings below cross-checked inline by the orchestrator (Opus) against the same files.

## 1. Current code location (issue's line numbers are stale)

Issue says `services/llm_jobs.py:368-373`. Actual location: **`services/llm_jobs.py:697-704`**, inside the `elif job.kind == "voice_match":` branch which spans **690-752**.

```python
690  elif job.kind == "voice_match":
691      if voice_id_service._backend == "none":
692          _finish(db, job, "failed", "No voice embedding backend available")
693          return
694      if not (transcript.audio_path and os.path.exists(transcript.audio_path)):
695          _finish(db, job, "failed", "No stored audio for this transcript")
696          return
697      has_enrolled_voice = (
698          db.query(VoiceProfile)
699          .filter(VoiceProfile.user_id == job.user_id, VoiceProfile.embedding.isnot(None))
700          .first()
701          is not None
702      )
703      if not has_enrolled_voice:
704          _finish(db, job, "failed", "No enrolled voices with clips — add a clip to a roster profile first")
705          return
```

Nothing between line 705 and the per-segment loop re-checks `embedding_model`. Each segment spawns `extract_clips_concat` (ffmpeg) at 717-720 before `identify()` is called at 727-731, so the wasted-CPU claim in the issue is accurate.

## 2. What `identify()` actually does (the predicate the guard must mirror)

`services/voice_id.py:221-249`. The per-profile skip conditions, verbatim:

```python
232      for profile in profiles:
233          if profile.embedding is None:
234              continue
235          if profile.embedding_model and profile.embedding_model != probe_model:
236              continue
237          stored = np.array(profile.embedding)
238          if len(stored) != len(probe_embedding):
239              continue
```

Three findings that constrain the fix:

1. **NULL `embedding_model` is a wildcard, not a mismatch.** Line 235 short-circuits on falsy `embedding_model`, so legacy/pre-migration rows (`database/__init__.py:267` comment: "NULL = pre-migration row") are matched, not skipped. Confirmed by `tests/test_voice_id.py:410` (`test_identify_still_matches_legacy_profile_with_no_recorded_model`). A guard that filters `embedding_model == backend_name` would wrongly reject these.

2. **`probe_model` is not `backend_name`.** `probe_model` comes from `_extract_embedding` (`services/voice_id.py:299-312`), which falls back to MFCC when the nominal backend is *installed but fails at runtime*:

```python
300      if self._backend == "speechbrain":
301          embedding = self._embed_speechbrain(audio_path)
302          if embedding is not None:
303              return embedding, "speechbrain/spkrec-ecapa-voxceleb"
304          return self._mfcc_fallback(audio_path)
305      elif self._backend == "pyannote":
306          embedding = self._embed_pyannote(audio_path, hf_token=hf_token)
307          if embedding is not None:
308              return embedding, "pyannote/wespeaker-voxceleb-resnet34-LM"
309          return self._mfcc_fallback(audio_path)
310      elif self._backend == "librosa_mfcc":
311          return self._mfcc_fallback(audio_path)
312      return None
314  def _mfcc_fallback(self, audio_path: str) -> Optional[tuple]:
315      embedding = self._embed_mfcc(audio_path)
316      return (embedding, "MFCC fingerprint (librosa)") if embedding is not None else None
```

So for `_backend == "speechbrain"` the set of model ids `identify()` may compare against is `{"speechbrain/spkrec-ecapa-voxceleb", "MFCC fingerprint (librosa)"}`, not a single value. **A guard filtering on `backend_name` alone would fail jobs that would in fact have matched** (user enrolled while speechbrain was broken, so their rows are tagged MFCC). That is a worse regression than the wasted CPU the issue reports: a false-negative hard failure instead of a slow no-op.

3. `_backend` is probed once at import (`services/voice_id.py:29`, singleton at 403), so it never changes within a process. `backend_name` maps `_backend` to the same strings `_extract_embedding` returns (`services/voice_id.py:59-67`).

**Conclusion: the pre-flight predicate must be the permissive superset of what `identify()` can accept — `embedding IS NOT NULL AND (embedding_model IS NULL OR embedding_model IN <models this backend can produce>)`.** A pre-flight guard must never block a job that could match; it may only block ones that provably cannot. The dimension check at line 238 cannot be pre-flighted (probe length is unknown before extraction) and is deliberately out of the guard.

## 3. Complement sweep — every "does this user have enrolled voices" site

| Location | What it does | Verdict |
|---|---|---|
| `services/llm_jobs.py:697-704` | Job early-exit gate before per-segment ffmpeg | **In scope — this is the bug.** Only functional gate on enrollment anywhere. |
| `app.py:572` `voice_count = db.query(VoiceProfile)...count()` | Dashboard "N voice profiles" stat (`static/rack.js:428`) | Out of scope. Informational count of rows that exist, not a precondition. Filtering it would misreport how many profiles the user has. |
| `app.py:3321-3324` `list_voices` → `voice_id_service.list_profiles` (`services/voice_id.py:252-280`) | Roster listing; already returns each row's own `embedding_model` (line 264) | Out of scope. A listing, not a gate, and it already surfaces per-row model for the user. |
| `app.py:3381` `"total_profiles": len(...)` in `/api/voices/identify` | Diagnostic metadata next to `"backend": voice_id_service._backend` (3382) | Out of scope. Diagnostic response field, gates nothing. |
| `app.py:2731-2757` `POST /api/transcripts/{id}/voice-match` | Enqueue route; checks stored audio only, does **not** duplicate the enrollment check | Out of scope, nothing to mirror. The job-level guard is the single authoritative gate (Complement Rule item 2: enforced server-side). |
| `static/rack.js:5074-5079` | "N enrolled voice(s) might match unlabeled speakers here" nudge + "Match now" CTA, gated on `voices.length` only | Same blind spot in kind, but **excluded from this change** — see decision note below. Spawns no ffmpeg; the server guard still refuses correctly. |

## 4. Sibling sweep the issue never named

Checked every `_finish(db, job, "failed", ...)` early-exit in `services/llm_jobs.py` for the same "resource exists but may be unusable by the current backend/config" shape:

- `voice_match`: `_backend == "none"` (691) and stored-audio existence (694) — service/file availability, no persisted per-row backend tag to go stale. Not the same class.
- `rediarize` (656-663): `diarization_service is None` + stored-audio existence — same shape as above, no persisted model tag. Not applicable.
- Other kinds: api-key presence (359), transcript existence (352), `user_request` presence (782) — no backend/model staleness concept.

**Result: nothing else found.** `has_enrolled_voice` is the only precondition in the file that queries a resource carrying a persisted `embedding_model` without filtering on it.

## 5. What the frontend reads from a voice_match job

- `_finish` (`services/llm_jobs.py:319-330`) writes `job.status` + `job.error`. `serialize_llm_job` (48-57) exposes `"error": job.error`. No `result_json` is exposed for this kind.
- `static/rack.js:3472-3473, 3501-3502` render `humanizeJobError(j.error)` **regardless of status**, and color the row red when `j.error` is truthy (3479, 3509). This branch already relies on that: line 751-752 sets a non-null `error` on a `completed` job for the `skipped` count.
- `result_json` is read only for `voice_note_job` / `voice_dump_job` (rack.js:4603-4606, 4683-4689) and the `/runs/{kind}` picker, whose allow-list (`app.py:2771`) does **not** include `"voice_match"`.

**Therefore: `job.error` is the correct and only channel. A new `result_json` key for voice_match would be silently ignored by the UI.** Using `job.error` needs no frontend change and no e2e selector updates.

## 6. Existing test coverage and the gap

`tests/test_voice_match_job.py` (281 lines, default/unit tier; `pytest.ini` defines only the `e2e` marker, excluded by default):

- `test_voice_match_fails_fast_with_no_backend` (114-128) — `_backend == "none"`.
- `test_voice_match_fails_fast_with_empty_roster` (131-153) — no rows at all; asserts the exact message at 153.
- `_enrolled_profile` helper (29-36) hardcodes `embedding_model="test"`.

`tests/test_voice_id.py` covers the unit-level skip: `test_identify_skips_profile_with_different_embedding_model` (392-407), `test_identify_still_matches_legacy_profile_with_no_recorded_model` (410+).

**Gap: no job-level test asserts the early-exit accounts for model mismatch.** That is the coverage this fix must add.

**Blocking complement finding (mine, not the issue's, not the agent's):** the test environment's backend is `pyannote` (verified: `.venv/Scripts/python.exe -c "from services.voice_id import voice_id_service as v; print(v._backend, v.backend_name)"` → `pyannote | pyannote/wespeaker-voxceleb-resnet34-LM`; `import speechbrain` fails, `librosa` imports fine). `_enrolled_profile`'s `embedding_model="test"` is therefore incompatible with the current backend, so adding the filter makes **four existing tests fail** (`test_voice_match_relabels_confident_segments_only`, `test_voice_match_runs_real_identify_through_executor`, `test_voice_match_skips_segment_on_extraction_failure_without_failing_job`, `test_voice_match_passes_hf_token_from_user_settings`). The helper (and the `_extract_embedding` monkeypatch at line 101 that returns `"test"` as probe_model) must be updated in the same change.

## 7. What the issue's own proposed fix gets wrong

1. "Filter by embedding_model" as written (`embedding_model == backend_name`) **drops legacy NULL rows** that `identify()` deliberately matches. Must be `embedding_model IS NULL OR embedding_model IN (...)`.
2. It treats "the current backend" as one value. It isn't — the runtime MFCC fallback means the accepted set has two members for speechbrain/pyannote. Filtering on one value creates false-negative job failures.
3. The existing failure message (`"No enrolled voices with clips — add a clip to a roster profile first"`) becomes wrong advice for the mismatch case: the profiles *do* have clips. The fix needs a distinct message, or it trades one confusing outcome for another.
4. The issue frames early-exit and warning as alternatives ("or"). Only the early-exit addresses the stated Impact (wasted ffmpeg per segment); a post-hoc warning fires after the CPU is already spent. Early-exit is the primary fix.
5. Neither the issue nor the fix can pre-flight the embedding-dimension check (`voice_id.py:238`); a compatible-model row with a wrong-length vector still no-ops. Out of scope, documented here.

## Chosen approach

1. Add a single source of truth on the service: `VoiceIdentificationService.compatible_embedding_models()` returning the set of model ids `_extract_embedding` can produce for the current `_backend` (primary + MFCC fallback; empty for `"none"`). Both paths derive the predicate from one place instead of two hand-maintained lists.
2. In `services/llm_jobs.py`, split the guard in two so the messages stay accurate:
   - no profile with an embedding at all → keep the existing message verbatim (existing test asserts it).
   - profiles exist but none is backend-compatible → new distinct message naming the enrolled model(s) and the current `backend_name`, via `job.error`.
3. Tests: job-level mismatch early-exit (asserts `extract_clips_concat` never called), legacy-NULL row still proceeds, and a unit test for `compatible_embedding_models()`. Update `_enrolled_profile` + the `_extract_embedding` monkeypatch so the four existing tests keep passing.

## Acceptance criteria walk

The issue states no numbered acceptance criteria. Derived from its Problem/Impact/Proposed Fix, walked in `self-audit.md`.
