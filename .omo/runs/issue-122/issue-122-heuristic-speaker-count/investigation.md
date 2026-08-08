# Investigation — Issue #122: heuristic speaker_count mismatch

**Target**: Issue #122, standalone
**Worktree**: `C:/Claude/whisperdesk/.claude/worktrees/issue-122-heuristic-speaker-count`
**Main**: `C:/Claude/whisperdesk` (master, 325ead7)
**Branch**: `issue-122-heuristic-speaker-count`

## Root cause

`diarize_heuristic()` at `services/diarization.py:86-134` generates N speaker labels
(line 114: `speaker_labels = [f"Speaker {i+1}" for i in range(max(num_speakers, 2))]`)
but only advances to the next label on gaps > 1.5s (line 118). If all gaps are
shorter than 1.5s, `speaker_idx` stays at 0, every segment gets "Speaker 1", and
`speaker_count` returns 1 regardless of `num_speakers`.

## Scope

- `diarize_heuristic()` at `services/diarization.py:86` — the only function needing change
- Callers: `diarize_and_merge()` at line 80, and `app.py` (API endpoint)
- Existing test: `tests/test_diarize_heuristic_no_segments.py`
- No sibling functions — this is the only heuristic diarizer

## Fix

After the gap-based alternation pass, if fewer than `num_speakers` distinct labels
were assigned, distribute the unused labels across remaining segments via simple
round-robin. This guarantees `speaker_count >= num_speakers` when the user
explicitly requested a speaker count, while preserving the gap-based alternation
for natural speaker changes.

The change is additive: after the existing loop (line 127), add a second pass:

```python
# If fewer labels were used than requested, distribute remaining labels
used = set(s.speaker for s in speakers)
if len(used) < len(speaker_labels):
    unused = [l for l in speaker_labels if l not in used]
    ui = 0
    for seg in speakers:
        if seg.speaker not in unused and unused:
            seg.speaker = unused[ui % len(unused)]
            ui = (ui + 1) % len(unused)
```

This only activates when the gap heuristic under-assigns; single-speaker
recordings with default `num_speakers=2` still get speaker_count=2 (the
default was always the minimum, the heuristic just couldn't deliver it).

## Phase 1.5 check

Not applicable — no job/state completion paths touched.

## Existing test coverage

- `tests/test_diarize_heuristic_no_segments.py` — tests empty segments case
- No test covers the num_speakers vs actual count mismatch