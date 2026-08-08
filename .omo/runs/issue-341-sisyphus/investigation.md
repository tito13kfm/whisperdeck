# Issue #341 Investigation: `transcript.speaker_count` Dual Definition

**Date:** 2026-08-04
**Worktree:** `C:\Claude\whisperdesk\.claude\worktrees\issue-341-sisyphus`

---

## 1. Summary

The issue is confirmed. `transcript.speaker_count` is written by **two families** that compute it differently:

- **Family A (pre-merge cluster count):** Uses `DiarizationResult.speaker_count`, which counts distinct speakers in raw diarization turns *before* `combine_with_transcript` maps them onto ASR segments.
- **Family B (post-merge label count):** Uses `count_distinct_speakers()` from `services/relabel.py`, which counts distinct speaker labels actually present in stored transcript segments.

They can disagree in two ways (both confirmed by code review):
1. A pyannote cluster whose turns lose every segment in `combine_with_transcript` (the overlapping-speaker vote) contributes to Family A's count but not Family B's.
2. The `"Unknown"` fallback stamped by `combine_with_transcript` (line 300 of `diarization.py`) is in stored segments but was never in Family A's count. (Mitigated by `NON_SPEAKER_LABELS = frozenset({"unknown"})` excluding it via casefold, so Family B cannot report a *higher* number than Family A — only equal or lower.)

---

## 2. Complete Writer Site Table

Every line that assigns to `transcript.speaker_count` (or creates the value that will be assigned):

| # | File | Line | Family | Code |
|---|---|---|---|---|
| 1 | `services/diarization.py` | 84 | **A** — origin | `return merged, result.speaker_count, result.method` |
| 2 | `services/diarization.py` | 103 | — creation | `speaker_count=0` (empty heuristic result) |
| 3 | `services/diarization.py` | 132 | — creation | `speaker_count=len(speaker_set)` (heuristic) |
| 4 | `services/diarization.py` | 255 | — creation | `speaker_count=len(speaker_set)` (pyannote) |
| 5 | `services/diarization.py` | 422 | — creation | `speaker_count=len(speaker_set)` (live_stereo) |
| 6 | `app.py` | 1392 | **A** — writer | `transcript.speaker_count = speaker_count` — inline diarize |
| 7 | `services/queue.py` | 566,606 | **A** — writer | `speaker_count` from `diarize_and_merge` return → `transcript.speaker_count = speaker_count` — chunked finalize |
| 8 | `services/llm_jobs.py` | 667,681 | **A** — writer | `speaker_count` from `diarize_and_merge` return → `transcript.speaker_count = speaker_count` — rediarize job |
| 9 | `app.py` | 2092 | **B** — writer | `t.speaker_count = count_distinct_speakers(t.segments)` — segments PATCH |
| 10 | `app.py` | 2315 | **B** — writer | `t.speaker_count = count_distinct_speakers(new_segments)` — speakers/rename |
| 11 | `app.py` | 2374 | **B** — writer | `t.speaker_count = count_distinct_speakers(new_segments)` — segments/retag |
| 12 | `app.py` | 2406 | **B** — writer | `t.speaker_count = count_distinct_speakers(segments)` — relabel-undo |
| 13 | `services/llm_jobs.py` | 802 | **B** — writer | `transcript.speaker_count = count_distinct_speakers(new_segments)` — voice_match job |

### Read-only sites (not writers):

| # | File | Line | Context |
|---|---|---|---|
| R1 | `app.py` | 380 | `_serialize_transcript` reads `t.speaker_count` for API response |
| R2 | `app.py` | 651 | `_serialize_transcript_summary` reads `t.speaker_count` for list view |
| R3 | `app.py` | 2537 | `/api/diarize` standalone endpoint returns `result.speaker_count` (raw diarization, no transcript) |
| R4 | `database/__init__.py` | 55 | Column default: `speaker_count = Column(Integer, default=0)` |

---

## 3. Definition of Each Family

### Family A: Pre-merge cluster count

All three diarization methods compute `speaker_count = len(speaker_set)` over their own `DiarizationSegment` objects:

- `diarize_heuristic` (line 129-132): counts distinct speaker labels after pause-gap clustering
- `diarize_pyannote` (line 252-256): counts distinct speakers from pyannote turns
- `diarize_live_stereo` (line 420-422): counts distinct speakers from mic "You" + pyannote remote turns

`diarize_and_merge` (line 51-84) routes to one of the three, then calls `combine_with_transcript(result, segments)` to merge diarization turns onto ASR segments. It returns `(merged, result.speaker_count, result.method)` — the **pre-merge** count.

Three callers write this value to `transcript.speaker_count`:
- `app.py:1392` — inline diarize path in `_run_transcription_pipeline`
- `services/queue.py:606` — chunked finalize in `_finalize_if_done`
- `services/llm_jobs.py:681` — rediarize job in `run_llm_job`

### Family B: Post-merge label count

`count_distinct_speakers(segments)` from `services/relabel.py:20-43` counts distinct `speaker` values in the stored segment list, excluding labels in `NON_SPEAKER_LABELS = frozenset({"unknown"})` via casefold comparison. Five writers use it:

- `app.py:2092` — segments PATCH (client-supplied replacement)
- `app.py:2315` — speakers/rename
- `app.py:2374` — segments/retag
- `app.py:2406` — relabel-undo
- `services/llm_jobs.py:802` — voice_match job

These paths no longer hold diarization turns (they were replaced by `combine_with_transcript` or never had them), so they must recount from stored labels. Family B is the *honest* number — it reflects what the user actually sees.

---

## 4. Sibling Sweep: Every Segment-Modifying Path

Below is every path that modifies `transcript.segments` and whether it recomputes `speaker_count`. Paths that **do not** recompute are the gap the issue is about.

| Path | File:Line | Modifies segments? | Recomputes speaker_count? | Family |
|---|---|---|---|---|
| Inline diarize | `app.py:1382-1394` | Yes (merged) | Yes (line 1392) | A |
| Chunked finalize diarize | `services/queue.py:543-607` | Yes (merged) | Yes (line 606) | A |
| Rediarize job | `services/llm_jobs.py:667-686` | Yes (merged) | Yes (line 681) | A |
| Voice match job | `services/llm_jobs.py:797-802` | Yes (new_segments) | Yes (line 802) | B |
| Segments PATCH | `app.py:2087-2092` | Yes (replacement) | Yes (line 2092) | B |
| Speakers rename | `app.py:2312-2315` | Yes (new_segments) | Yes (line 2315) | B |
| Segments retag | `app.py:2371-2374` | Yes (new_segments) | Yes (line 2374) | B |
| Relabel undo | `app.py:2403-2406` | Yes (restored) | Yes (line 2406) | B |
| Dictation speaker stamp | `app.py:~1160` (inline) | Yes (stamps "You") | No — sets `speaker_count = 1` inline | A-like |
| Hallucination filter | `app.py:1372-1378` | Yes (filters) | **No** | — |
| Retranscribe | `app.py:2166-2224` | Creates NEW row | Yes (new pipeline, Family A) | A |

### Gap found: Hallucination filter (line 1372-1378)

`filter_hallucinations` removes segments by index. If it removes all segments belonging to a speaker, the post-merge label count would drop, but `speaker_count` was already set earlier (or will be set by the diarize block at line 1392, which runs *after* the hallucination filter). The diarize block does set `speaker_count` after the filter, so this is only a gap if diarize=False. In that case, `speaker_count` stays at default `0` — arguably wrong but not a Family A vs B issue.

**Verdict: No other paths missed by the issue.** Every path that meaningfully modifies segments recomputes `speaker_count` — the problem is solely that Family A paths compute it differently than Family B paths.

---

## 5. Analysis of Suggested Fix

### What the issue suggests

> After `combine_with_transcript` returns `merged`, have the diarize paths set `speaker_count = count_distinct_speakers(merged)` instead of the pre-merge `DiarizationResult.speaker_count`, so all six writers share one expression.

### Does it work for all three diarize paths?

**Yes.** All three diarize paths flow through `diarize_and_merge`, which always calls `combine_with_transcript` before returning. The `merged` value is already available at all three writer sites:

1. **Inline diarize** (`app.py:1384-1392`): `merged` is the first return value, available on the next line.
2. **Chunked finalize** (`services/queue.py:566-606`): `merged` is the first return value, available.
3. **Rediarize** (`services/llm_jobs.py:667-681`): `merged` is the first return value, available.

The fix would be:

```python
# Before (Family A — pre-merge):
transcript.speaker_count = speaker_count

# After (Family B — post-merge, same as relabel paths):
transcript.speaker_count = count_distinct_speakers(merged)
```

This is a one-line change at each of the three sites, plus an import of `count_distinct_speakers` in files that don't already import it (`app.py` already imports it at line 61; `services/queue.py` does not; `services/llm_jobs.py` imports it at line 796 for the voice_match branch).

### What the issue gets right

- Correctly identifies the two families and their definitions.
- Correctly identifies the two disagreement mechanisms (cluster losing all segments, "Unknown" fallback).
- Correctly identifies all three Family A writer sites.
- Correctly identifies all Family B writer sites (the voice_match path counts as Family B, which the issue groups under "relabel paths").

### What the issue overlooks

1. **The `/api/diarize` standalone endpoint** (`app.py:2537`) returns `result.speaker_count` directly as JSON — it does NOT write to a transcript. This endpoint has no segments to merge onto (it's raw diarization), so `count_distinct_speakers` would be semantically wrong there. The pre-merge cluster count is the correct answer for this endpoint. `DiarizationResult.speaker_count` cannot be dropped entirely while this endpoint exists.

2. **`diarize_and_merge` return value contract**: If the three Family A callers stop using `speaker_count` from the return, `diarize_and_merge` still returns it. That's harmless but a stale contract that future callers might accidentally rely on. Consider whether to keep returning it (backward compatible) or change the return to drop it (cleaner contract, breaks nothing but `/api/diarize` callers — which never call `diarize_and_merge`, they call `diarize_pyannote`/`diarize_heuristic` directly).

---

## 6. Open Questions Answered

### Q1: Does `DiarizationResult.speaker_count` still have consumers besides `transcript.speaker_count`?

**Yes.** Two categories:

**A) Transcript writers (the three Family A sites):** These are the ones the issue proposes changing.

**B) Standalone diarization consumers:**
- `app.py:2537` — `/api/diarize` endpoint returns `result.speaker_count` directly
- `tests/test_diarize_heuristic_no_segments.py:35,49` — asserts `result.speaker_count >= 1` and `result.speaker_count == 0`
- `tests/test_diarization_confidence.py:8` — test helper uses it
- `tests/test_diarization_metadata.py:58,79,110,138` — test fixtures use it

The standalone endpoint and its tests are not affected by changing the three transcript writers. `DiarizationResult.speaker_count` must stay on the dataclass for this consumer.

### Q2: Will post-merge `count_distinct_speakers` give the same answer as pre-merge for the test fixture?

**No.** `tests/test_posthoc_reprocess.py:364-376`:

```python
merged = [{"start": 0, "end": 1, "text": "a b", "speaker": "SPEAKER_01"}]
fake_diar.diarize_and_merge = AsyncMock(return_value=(merged, 2, "pyannote"))
...
assert t.speaker_count == 2  # line 376
```

The mock returns `speaker_count=2` (pre-merge) but `merged` has only **one** distinct speaker label (`"SPEAKER_01"`). `count_distinct_speakers(merged)` would return `1`, not `2`.

**Conclusion:** The test assertion must be updated if the definition changes. The mock's return value should be made self-consistent: either return `(merged, 1, "pyannote")` (pre-merge count made to match) or expand `merged` to have 2 distinct speakers with the pre-merge count of 2.

Alternatively, keep `speaker_count=2` in the mock (representing two clusters that both won segments in the real diarizer) and change `merged` to:
```python
merged = [
    {"start": 0, "end": 0.5, "text": "a", "speaker": "SPEAKER_01"},
    {"start": 0.5, "end": 1, "text": "b", "speaker": "SPEAKER_02"},
]
```
This makes the fixture self-consistent regardless of which definition is used.

### Q3: Backfill for existing rows?

The issue asks whether existing rows should be backfilled or left to converge naturally. A backfill SQL query would be:

```sql
-- Recompute speaker_count from stored segments, excluding 'Unknown'
-- This is a no-op for rows where the two definitions already agree.
UPDATE transcript
SET speaker_count = (
    SELECT COUNT(DISTINCT LOWER(json_extract(value, '$.speaker')))
    FROM json_each(segments)
    WHERE json_extract(value, '$.speaker') IS NOT NULL
      AND LOWER(json_extract(value, '$.speaker')) != 'unknown'
)
WHERE segments IS NOT NULL;
```

However, `json_each` + `json_extract` per-row on a large table is expensive. A backfill is straightforward but not necessary for correctness — the value converges the next time any writer touches the transcript. **Recommendation:** documentation-only, no forced backfill.

---

## 7. Risks and Caveats

1. **Test breakage:** `test_posthoc_reprocess.py:376` will fail (see Q2). Fix is trivial — make the mock self-consistent.

2. **Test breakage:** `tests/test_diarization_failure.py:103` asserts `transcript.speaker_count == 1` after chunked finalize with a mock that returns `(segments, 1, "heuristic")`. The mock's segments have one speaker (`"SPEAKER_01"`), so `count_distinct_speakers` would also return 1. This test is already self-consistent and would not break.

3. **Test breakage:** `tests/test_relabel_undo.py` — these tests explicitly set `t.speaker_count` to known values before running Family B endpoints. They are not affected by the Family A change.

4. **Performance:** `count_distinct_speakers` iterates over all segments. For a large transcript (thousands of segments), this is O(n) but sub-millisecond — negligible compared to the diarization itself (seconds to minutes).

5. **Import cycles:** `services/relabel.py` is already imported in `app.py` (line 61). `services/queue.py` does not currently import from `relabel` and would need a new import. `services/llm_jobs.py` imports `count_distinct_speakers` at line 796 for the voice_match branch, so it's already available at the rediarize branch (line 681).

6. **`diarize_and_merge` return contract:** After the fix, the `speaker_count` return value (line 84) is unused by all three callers. It could be dropped from the return tuple, but the standalone `/api/diarize` endpoint doesn't call `diarize_and_merge` (it calls the individual diarize methods), so this is a dead value at this level. Safe to drop or keep — no functional difference.

---

## 8. Recommended Implementation Plan

1. Add `from services.relabel import count_distinct_speakers` to `services/queue.py`.
2. Change `services/queue.py:606`: `transcript.speaker_count = count_distinct_speakers(merged)`
3. Change `app.py:1392`: `transcript.speaker_count = count_distinct_speakers(merged)` (import already exists at line 61)
4. Change `services/llm_jobs.py:681`: `transcript.speaker_count = count_distinct_speakers(merged)` (import already exists at line 796)
5. Update `tests/test_posthoc_reprocess.py:364-366`: make mock return self-consistent (either 1 speaker in merged OR 2 distinct speakers in merged).
6. Run full test suite; verify all diarization and relabel tests pass.
7. Optionally: update `diarize_and_merge` docstring to note that `speaker_count` return value is no longer used by callers (or drop it from the return).
