# Investigation: Issue #111 -- voice_match doesn't update transcript.speaker_count

All line numbers below are from the worktree checkout
`C:\Claude\WhisperDeck\.claude\worktrees\issue-111-speaker-count`
at commit `9169f16` ("chore: ignore skill-observations/ wholesale (#334)").

## 1. Current line numbers for voice_match and rediarize handlers

Both live in `services/llm_jobs.py`, inside `run_llm_job()` (the dispatch function that
executes one claimed job), as `elif job.kind == ...:` branches -- there are no separate
top-level functions per kind.

- **rediarize branch**: `services/llm_jobs.py:655-689`
  - Post-relabel writes: `services/llm_jobs.py:678-686`
    ```python
    from services.relabel import clear_relabel_history
    clear_relabel_history(db, transcript.id)
    transcript.segments = merged                          # 680
    transcript.speaker_count = speaker_count               # 681
    transcript.diarization_method = diarization_method     # 682
    transcript.updated_at = utcnow_naive()                 # 683
    job.progress_done = 1
    job.result_json = {"segments": merged}
    db.commit()
    ```
- **voice_match branch**: `services/llm_jobs.py:690-752`
  - Post-relabel writes: `services/llm_jobs.py:744-750`
    ```python
    if changed:
        from services.relabel import record_relabel
        record_relabel(db, transcript, "voice_match", changed,
                       description=f"voice match relabeled {len(changed)} lines")
    transcript.segments = new_segments              # 748
    transcript.updated_at = utcnow_naive()           # 749
    db.commit()                                      # 750
    ```
  - **`transcript.speaker_count` is never touched.** Confirmed stale -- matches the issue.

(The issue's cited line numbers, 419-421, are stale -- the file has grown; the real
site is 744-752, and rediarize's comparison point is 674-687, not 353.)

## 2. Every write path for `transcript.segments` / `transcript.speaker_count` -- full enumeration

Searched the whole repo (excluding `docs/superpowers/plans/*.md`, which are historical
planning docs, not source) for `speaker_count` and `\.segments\s*=`. Table below is every
site that assigns `transcript.segments` (or the local `t`/`result` alias for a `Transcript`
row), whether it writes `speaker_count`, and a verdict.

| # | Site (file:line) | What it does | Writes speaker_count? | Verdict |
|---|---|---|---|---|
| 1 | `services/transcription.py:114` -- `Transcript.transcribe()` | Initial ASR result to `transcript.segments` (raw provider segments, speaker field usually None, no diarization yet) | No | **Not in scope** -- this is segment creation, not relabeling. speaker_count defaults to 0 and is set later by whichever diarization path runs. Not a regression. |
| 2 | `app.py:1372-1379` -- inline upload pipeline, post-hoc hallucination filter | `transcript.segments = filter_hallucinations(...)` -- drops hallucinated lines, runs before diarization | No | **Not in scope** -- runs pre-diarization, segments have no real speaker labels yet. |
| 3 | `app.py:1382-1394` -- inline upload pipeline, initial diarization | `transcript.segments = merged` (1391) then `transcript.speaker_count = speaker_count` (1392), diarization_method (1393) | **Yes** | Correct -- first-diarization path, symmetric writes. |
| 4 | `services/queue.py:601-608` -- chunked-transcription finalize | `transcript.segments = segments` (601), then guarded `if speaker_count is not None: transcript.speaker_count = speaker_count; transcript.diarization_method = diarization_method` (605-607) | **Yes** (conditionally, matches whether diarization ran at all) | Correct -- first-diarization path for the chunked pipeline, symmetric writes. |
| 5 | `services/llm_jobs.py:680-683` -- **rediarize** job handler | `transcript.segments = merged` + `transcript.speaker_count = speaker_count` + diarization_method + updated_at | **Yes** | Correct (the issue's own reference implementation). |
| 6 | `services/llm_jobs.py:748-749` -- **voice_match** job handler | `transcript.segments = new_segments` + updated_at only | **No** | **BUG -- this is the issue.** |
| 7 | `app.py:2078-2123` -- `PATCH /api/transcripts/{id}` (`update_transcript`) | `t.segments = data["segments"]` (2089) -- raw client-supplied segment array, arbitrary edit (also clears relabel history at 2088) | No | **BUG, same class, not named in the issue.** A client can PATCH an arbitrary segments array (including changed/merged speaker labels) and speaker_count is left untouched. |
| 8 | `app.py:2270-2327` -- `POST /api/transcripts/{id}/speakers/rename` (`rename_transcript_speaker`) | Renames every segment labeled old to new; `t.segments = new_segments` (2309) | No | **BUG, same class, not named in the issue.** If new already exists as a distinct label elsewhere in the transcript, this is exactly the "merge multiple labels into one" case the issue describes for voice_match -- count should shrink by 1 but doesn't move. |
| 9 | `app.py:2330-2368` -- `POST /api/transcripts/{id}/segments/retag` (`retag_transcript_segments`) | Retags an arbitrary index set to one speaker value; `t.segments = new_segments` (2365) | No | **BUG, same class, not named in the issue.** Can both merge labels (decrease count) or introduce a brand new label not previously present (increase count) -- pure by-index override. |
| 10 | `app.py:2371-2409` -- `POST /api/transcripts/{id}/relabel-undo` (`undo_last_relabel`) | Reverts the latest RelabelHistory inverse patch; `t.segments = segments` (2394) | No | **BUG, same class, not named in the issue.** Undoing a rename/retag/voice_match restores old per-index labels -- the label set (and hence the count) can change back, and nothing recomputes it. |

**Total in-scope write paths needing the fix: 5** -- voice_match (#6, the named issue) plus
four siblings the issue never mentions: `update_transcript` PATCH (#7),
`speakers/rename` (#8), `segments/retag` (#9), `relabel-undo` (#10). Sites #1-#5 are
either not relabeling (#1, #2) or already correct (#3, #4, #5).

`app.py:2412` (`enroll-speaker`) and `app.py:2491` (`/api/diarize`, a stateless
diarize-only-no-persist endpoint) do **not** write `transcript.segments` at all -- checked
and excluded.

No migration/backfill script touches `segments` or `speaker_count` -- the only
`*migrat*`/`*backfill*` files in the repo (`tests/test_classification_migration.py`,
`tests/test_llm_job_history_backfill.py`) are for unrelated columns (classification,
LlmJob history), confirmed by reading. `scripts/run_diarize_check.py` only prints a
computed speaker set for a manual CLI check -- it never writes to the DB.

## 3. Sibling sweep -- does rediarize update any other derived field voice_match omits?

Diffing the two handlers' post-relabel blocks field by field:

| Field | rediarize (655-689) | voice_match (690-752) |
|---|---|---|
| transcript.segments | yes (680) | yes (748) |
| transcript.speaker_count | yes (681) | **no** |
| transcript.diarization_method | yes (682), set to the method string ("pyannote"/"heuristic"/"live_stereo") | **no** -- not written, and arguably shouldn't be: voice_match doesn't re-run diarization, it relabels the existing clusters, so the diarization method is still whatever produced the underlying SPEAKER_XX clusters. Leaving it alone is correct, not an asymmetry. |
| transcript.updated_at | yes (683) | yes (749) -- already symmetric |
| RelabelHistory handling | `clear_relabel_history(db, transcript.id)` (679) -- wholesale regeneration invalidates all old inverse patches | `record_relabel(db, transcript, "voice_match", changed, ...)` (745-747) -- records a new invertible patch (correct: voice_match is index-preserving, not a wholesale regeneration, so recording an inverse instead of clearing is the right shape, already symmetric with intent) |
| job.result_json | `{"segments": merged}` (685) | not set (voice_match never sets job.result_json) | Minor asymmetry, but no consumer was found reading voice_match's job.result_json (see section 5) -- cosmetic only, not a bug worth folding into this fix. |

**Sweep result: found exactly one real asymmetry -- speaker_count (item #6 above), which is
the issue). Nothing else rediarize writes is missing from voice_match**, aside from the
cosmetic, harmless job.result_json omission noted above (not a functional gap; no reader
depends on it). diarization_method is intentionally not touched by voice_match and should
stay that way.

## 4. How speaker_count is computed elsewhere

Three call sites in `services/diarization.py`, all structurally identical, all operating on
`DiarizationSegment` **dataclass** objects (not the dict shape stored in
`transcript.segments`):

- `diarize_heuristic` -- `services/diarization.py:129`: `speaker_set = set(s.speaker for s in speakers)` -> `speaker_count=len(speaker_set)` (132)
- `diarize_pyannote` -- `services/diarization.py:252`: `speaker_set = set(s.speaker for s in result_segments)` -> `speaker_count=len(speaker_set)` (255)
- `diarize_live_stereo` -- `services/diarization.py:420`: `speaker_set = set(s.speaker for s in segments)` -> `speaker_count=len(speaker_set)` (422)

None of these three filter out any sentinel -- they don't need to, because
`DiarizationSegment.speaker` is always populated with a real cluster/channel label at that
point (never empty, never "Unknown").

**Important asymmetry to flag for the fix:** these three counts are computed **before**
`combine_with_transcript()` (`services/diarization.py:259-304`) merges diarization turns
onto the transcript's own ASR segments. That merge step has a fallback:
```python
merged.append({
    **seg,
    "speaker": best_speaker or seg.get("speaker", "Unknown"),   # line 300
    "speaker_confidence": confidence,
})
```
So a transcript segment with zero time-overlap against any diarization turn (possible for
pyannote/live_stereo, not for heuristic -- see the comment at diarization.py:285-287,
heuristic segments are always 1:1 with the transcript's own segments so overlap is always
1.0) can end up with the literal string "Unknown" as its "speaker" value in the persisted
transcript.segments, **even though transcript.speaker_count (computed pre-merge) never
counted "Unknown" as a speaker.**

Consequence for the fix: **the issue's proposed filter (`if s.get("speaker")`, i.e. only
excluding falsy/empty/None) is not consistent with what speaker_count means elsewhere in
this codebase.** If a transcript already has "Unknown"-labeled segments (from an earlier
pyannote/live_stereo rediarize) and voice_match runs, a naive recompute using only the
falsy-filter would count "Unknown" as a real distinct speaker and could report a higher
number than rediarize's own canonical definition would for the same segment set. There is
no sentinel constant defined anywhere for this ("Unknown" appears as a bare string literal
at `services/diarization.py:300` and `services/assistant.py:118`, no shared constant to
import). This is a real edge case, not just theoretical -- it will trigger whenever
pyannote/live_stereo diarization leaves an unmatched gap and voice_match is later run on
that transcript.

**No shared helper exists.** Grepped for `speaker_set`, `def.*speaker_count`,
`distinct.*speaker` -- the three diarization.py sites are the only computations, and they
operate on dataclasses, not the dict shape voice_match/rename/retag/undo/PATCH all use. **A
dict-based `len({s.get("speaker") for s in segments if s.get("speaker")})`-style helper does
not exist anywhere in the codebase today** -- writing the fix inline (as the issue proposes)
would indeed be introducing this dict-based expression for the first time, not duplicating
an existing one. Given there are now 4-5 call sites needing this exact same recompute (see
section 2), a shared helper (e.g. `services/relabel.py` or a new small function) is
warranted rather than inlining it 4-5 times independently -- that would reduce the risk of
the "Unknown" question above being answered differently at different call sites.

**Variable name check:** `new_segments` **is** the correct, currently-existing identifier in
voice_match -- confirmed at `services/llm_jobs.py:714` (`new_segments = list(segments)`),
`:739` (mutated per-match), and `:748` (`transcript.segments = new_segments`). The issue's
proposed snippet's variable name is correct for the voice_match site specifically. It is
**not** the right variable name for the other 4 sibling sites (`app.py`'s
`update_transcript` uses `data["segments"]` directly with no local var; `rename_transcript_speaker`
and `retag_transcript_segments` both use `new_segments` too, coincidentally same name;
`undo_last_relabel` uses `segments`).

## 5. Consumers of speaker_count

**Model definition** -- `database/__init__.py:55`:
```python
speaker_count = Column(Integer, default=0)
```
Plain Integer, nullable (no `nullable=False`), Python-level `default=0` (applies on insert
only if unset, not an app-level invariant). No CHECK constraint, no relationship to
num_speakers (a separate user-requested-count column, `database/__init__.py:61`) beyond
convention.

**API serializer** -- `app.py:380` inside `_serialize_transcript()`:
```python
"speaker_count": t.speaker_count,
```
Also present in the summary serializer at `app.py:651` and inside the batch-diarize route's
per-item response at `app.py:2524`.

**Contract test** -- `tests/test_serialize_transcript_contract.py:31` asserts speaker_count
is one of the keys `_serialize_transcript` must always return (key presence only, no value
assertion) -- a fix here must not remove or rename the key.

**Frontend (`static/rack.js`)** -- read-only display sites, no monotonicity assumption found:
- `static/rack.js:1132` -- dashboard card meta line: `if (sv.word === 'done' && t.speaker_count) parts.push(t.speaker_count + ' speakers');` (falsy-hidden, not compared against any other field)
- `static/rack.js:2585` -- bank/list expanded-row detail: `['Speakers', String(t.speaker_count || '-')]`
- `static/rack.js:5022` -- transcript detail page "Speakers" stat tile (the one the issue's "Impact" section describes): renders `${t.speaker_count || '-'}` plus diarization_method and an uncertain-lines badge -- this is the literal UI surface the bug report is about.

None of these three sites compare speaker_count against segments.length or assume it only
ever increases -- all three just render the raw number (or an em-dash placeholder if
0/null). **speaker_count is effectively nullable in practice (falsy -> placeholder), so
None is a safe value if a fix chooses not to write anything when there's nothing to
compute** -- though existing sites always write an int, so the fix should match that and
always write an int (0 is fine, matches the column default).

**Tests** -- see section 7 below; no test currently asserts speaker_count for voice_match,
rename, retag, or undo.

## 6. Does voice_match actually reduce the label set? Can it increase it?

Confirmed from `services/llm_jobs.py:712-743`:
```python
skipped = 0
changed = []
new_segments = list(segments)
for i, seg in enumerate(segments):
    ...
    matches = await loop.run_in_executor(None, _identify)
    ...
    if matches:
        changed.append((i, seg.get("speaker") or ""))
        new_segments[i] = {**seg, "speaker": matches[0]["name"]}   # 739
    ...
```
Each segment is judged **independently** (its own extracted clip -> its own `identify()`
call against the enrolled roster, threshold 0.65). This is **not** a 1:1 relabel:

- **Can decrease the count**: if segments originally labeled SPEAKER_00, SPEAKER_01,
  SPEAKER_02 (3 distinct raw labels) all confidently match the same enrolled voice name, the
  result is 1 name replacing 3 labels -- this is the scenario the issue describes.
- **Can leave the count unchanged**: 1:1 mapping, or nothing confidently matches (no
  matches, segment's original label untouched, per the "untouched, no confident match" test
  case in `tests/test_voice_match_job.py:81`).
- **Can increase the count**: because matching is per-segment/per-clip rather than
  per-cluster, it is entirely possible for some segments originally sharing one raw label
  (e.g. all SPEAKER_00) to split -- a subset confidently matches "Alice" while the rest stay
  unmatched at SPEAKER_00, or (less likely but not precluded by the code) different segments
  of the same original cluster match different enrolled voices due to per-clip embedding
  noise on short clips. Either way this **increases** the distinct-label count relative to
  before the run.

**Conclusion: a full recompute (as the issue proposes) is the right approach, not a
decrement** -- voice_match's per-segment matching can move the count in either direction,
so decrementing by "labels merged" would be wrong even if it were simpler to write. The
issue's own proposed expression is a recompute, not a decrement, so it gets this part right
despite the "count becomes stale" framing suggesting only shrinkage.

## 7. Existing test coverage

- **`tests/test_voice_match_job.py`** (282 lines) -- covers the voice_match job handler
  end-to-end via `run_llm_job()`. Relevant tests: `test_voice_match_relabels_confident_segments_only`
  (line 49), `test_voice_match_runs_real_identify_through_executor` (86),
  `test_voice_match_fails_fast_with_no_backend` (114),
  `test_voice_match_fails_fast_with_empty_roster` (131),
  `test_voice_match_fails_when_audio_missing` (156),
  `test_voice_match_skips_segment_on_extraction_failure_without_failing_job` (175),
  `test_voice_match_route_enqueues_job` (208), `test_voice_match_route_400_without_stored_audio` (223),
  `test_transcript_serialization_includes_voice_match_job` (237),
  `test_voice_match_passes_hf_token_from_user_settings` (251). **None assert on
  speaker_count.**
- **`tests/test_posthoc_reprocess.py`** -- has the rediarize job test:
  `test_run_llm_job_rediarize_merges_in_place_without_key` (line 345) asserts
  `t.segments == merged` (375) and `t.speaker_count == 2` (376) -- this is the reference
  pattern a new voice_match regression test should mirror.
- **`tests/test_relabel_undo.py`** -- covers rename/retag/undo/voice_match-history recording:
  `test_rename_then_undo_restores_segments_and_corrected_text` (51), `test_retag_then_undo`
  (67), `test_two_undos_walk_back_two_actions` (130), `test_voice_match_records_relabel_history`
  (181), `test_rediarize_clears_relabel_history` (221). **None assert on speaker_count** --
  this file is also where regression tests for the rename/retag/undo siblings (section 2,
  items 8-10) should be added.
- `app.py`'s `PATCH /api/transcripts/{id}` (`update_transcript`, item 7 in section 2) test
  coverage: no dedicated segments-PATCH test found asserting speaker_count after a raw
  segments PATCH -- treat as uncovered.

**Fixture/factory pattern** (from `tests/test_voice_match_job.py:12-46`):
- `_user(db_session, name=...)` -- creates + commits a bare User.
- `_enrolled_profile(db_session, user, name=...)` -- creates a VoiceProfile with a literal
  `embedding=[0.1, 0.2, 0.3]` (bypasses real embedding extraction).
- `_transcript_with_segments(db_session, user, tmp_path, segments)` -- writes a fake audio
  file to tmp_path, then creates+commits a `Transcript(status="completed", segments=segments,
  audio_path=str(audio))`.
- `job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")` then manually
  flips `job.status = "running"; db_session.commit()` (bypassing the worker-tick claim loop)
  before calling `asyncio.run(run_llm_job(factory, job.id, transcription_service=None))`,
  where `factory = lambda: _NoCloseSession(db_session)` wraps the shared test session so
  `db.close()` inside run_llm_job is a no-op and `db_session.refresh(...)` afterward sees the
  same rows.
- `db_session` fixture (`tests/conftest.py:72`) builds a fresh sqlite file per test via
  `init_db(str(db_path))` and returns the SessionLocal() instance directly -- this is the
  "DB session" a new regression test should reuse verbatim.
- External calls (`services.llm_jobs.extract_clips_concat`,
  `services.llm_jobs.voice_id_service.identify`) are patched with `unittest.mock.patch`, not
  real audio/embedding extraction.

## 8. Completion-race pattern check

Voice_match's handler (`services/llm_jobs.py:690-752`) has **no post-completion side
effect** -- its last two statements are the segments/updated_at write (748-750) followed
directly by `_finish(db, job, "completed", error)` (752). There is no analog to the
correction branch's pattern of finishing, then re-checking `job.status == "completed"`
before triggering a downstream enqueue. For contrast, that pattern **does** exist elsewhere
in this same file and is the shape a "later phase" checking for this class of bug should
know about:

```python
# services/llm_jobs.py:378-397 (correction branch)
if result == "ok":
    job.result_json = {"corrected_text": transcript.corrected_text}
    db.commit()
    _finish(db, job, "completed")
    # A cancel can race in between correct_transcript() returning
    # and _finish() running -- _finish() detects that and leaves
    # the job 'cancelled' instead of 'completed'. Only trigger
    # classification when correction actually completed...
    if job.status == "completed":
        from services.settings import get_user_settings
        enqueue_pipeline_classify(db, transcript, get_user_settings(db, job.user_id))
```
and the mirrored classify_pipeline branch at `services/llm_jobs.py:510-515` (guards `if
accepted and result["kind"] == "voice_note"` / `"voice_dump"` after `_finish`). Both
correctly check `job.status == "completed"` (not just absence of "cancelled") before firing
the downstream trigger. **voice_match has no such downstream trigger to guard, so this exact
pattern doesn't apply to it** -- reported per the instructions even though the answer is
"not present here."

**A related, more concrete race gap actually found in voice_match** (worth flagging even
though it's not exactly the requested pattern): unlike the tagging branch
(`services/llm_jobs.py:531-533`: `db.refresh(job); if job.status == "cancelled": return`
**before** writing TranscriptTag rows) and the voice_note branch (`:568-570`, same guard
before writing the VoiceNote row), **voice_match never re-checks job.status between the
per-segment loop and its unconditional `transcript.segments = new_segments; db.commit()`
(748-750)**. If `cancel_llm_job` (`services/llm_jobs.py:295-307`) flips the job to
"cancelled" while the loop is mid-flight, the loop itself has no cancellation check at all
(no `db.refresh(job)` / `if job.status == "cancelled"` anywhere in the `for i, seg in
enumerate(segments):` block, `:715-743`) and will run to completion, then unconditionally
persist whatever partial relabeling happened -- `_finish()`'s cancel-check (`:319-330`)
only protects the job row's status from being overwritten to "completed", it does nothing
to stop the transcript.segments write that already landed one line above it. This is a
pre-existing gap independent of the speaker_count bug, surfaced here because item 8 asked
about it specifically.

## 9. Issue's proposed snippet -- what it gets right and wrong

```python
transcript.speaker_count = len({s.get("speaker") for s in new_segments if s.get("speaker")})
```

**Right:**
- Variable name `new_segments` is correct for the voice_match site (confirmed, section 4).
- It's a **recompute**, not a decrement -- correct given section 6 (count can move in either
  direction).
- Filtering falsy values (`if s.get("speaker")`) correctly excludes None/"" so untouched
  segments that somehow have no speaker at all don't count as a phantom label.
- Placement (after the segments variable is finalized, before the commit at line 750) is
  approximately right -- needs to sit alongside `transcript.segments = new_segments` (748),
  before `db.commit()` (750), same as rediarize's placement pattern.

**Wrong / missing:**
1. **Only covers 1 of 5 in-scope write paths.** Voice_match is fixed but `update_transcript`
   PATCH (`app.py:2089`), `rename_transcript_speaker` (`app.py:2309`),
   `retag_transcript_segments` (`app.py:2365`), and `undo_last_relabel` (`app.py:2394`) are
   left equally stale -- all four are the same class of bug and none were named in the
   issue (section 2).
2. **Doesn't address the "Unknown" sentinel asymmetry** (section 4): `if s.get("speaker")`
   treats the literal string "Unknown" (a real value combine_with_transcript can write into
   segments, `services/diarization.py:300`) as a legitimate distinct speaker, whereas the
   canonical speaker_count computed by every diarization path explicitly excludes it (those
   sites compute the set before the "Unknown" fallback is ever applied). A transcript that
   already has "Unknown"-labeled gaps from an earlier pyannote/live_stereo rediarize would
   get an inflated count from this snippet relative to what rediarize itself would report
   for the same segments.
3. **No shared helper.** This would be the first dict-based speaker-count computation in the
   codebase (the three existing ones in services/diarization.py operate on
   DiarizationSegment dataclasses, not dicts) -- and it's needed at (up to) 5 call sites,
   not 1, per section 2/#4. Inlining it 5 times independently risks each site answering the
   "Unknown" question differently. A shared helper (plausible home: services/relabel.py,
   which already centralizes the shared cross-cutting logic for these exact 5 call sites'
   record/clear-history behavior) is warranted.
4. **Line numbers in the issue are stale** -- cites `services/llm_jobs.py:419-421` for
   voice_match and "line 353" for rediarize; the real, current lines are 744-752 and 674-687
   respectively (section 1).