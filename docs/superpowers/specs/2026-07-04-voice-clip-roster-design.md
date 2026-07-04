# Voice Clip Roster & Roster-Based Re-Diarization — Design Spec

**Goal:** Turn `VoiceProfile` from a single-overwritten-embedding record into an editable roster of named voice clips per person, manageable from both the Voice roster page and the transcript detail screen. Use that roster to (a) let a user correct a diarization run's speaker labels after the fact by matching known voices, and (b) fix a diarization run that mislabeled a chunk of lines, without disturbing correctly-labeled segments elsewhere in the same transcript.

**Background:** Today `VoiceProfile` stores exactly one embedding vector; re-enrolling a name overwrites it (`services/voice_id.py:enroll`, `existing.embedding = ...`). `sample_count` increments but nothing is averaged, so there is no real notion of "the voice profile built from these 5 clips" — just whichever clip was enrolled most recently. There is also no way to bulk-fix a diarization run against known voices after the fact; today's only correction tool is `/api/transcripts/{id}/speakers/rename`, which relabels *every* segment carrying an old label — it can't fix a *chunk* of misattributed lines without also renaming the correctly-labeled ones sharing that same original label.

Prerequisite fixed separately (already landed): `services/voice_id.py`'s backend detection previously could silently select the unimplemented `"pyannote"` embedding path, causing every enroll/identify call to fail with a misleading generic error. Fixed, and `librosa` (MFCC fallback backend) is now installed in `.venv` so embeddings actually work end-to-end for testing this feature.

## Scope

**In scope:**
- New `VoiceClip` table: one row per enrolled audio clip, owned by a `VoiceProfile`.
- `VoiceProfile.embedding` becomes derived (mean of its clips' embeddings), recomputed whenever a clip is added or removed.
- Voice roster page: expand each profile to list/play/remove its clips; add a clip to an existing profile directly (outside the transcript flow).
- Transcript detail screen: decouple enrollment from rename — "Enroll marked clips" button, with a picker (existing roster name, append; or new name, create).
- Transcript detail screen: bulk re-tag — multi-select segment rows, retag the selection only (by segment index, not by old-label match) to a roster name (existing or new).
- New background job kind `"voice_match"`: embeds each segment's audio span, compares against the user's roster via `identify()`, reassigns `speaker` on confident match (>=0.65, matching `identify()`'s existing default), leaves the rest untouched.

**Explicitly out of scope:**
- Anti-match / negative examples per profile (considered, deferred — bulk re-tag covers the reported case of "large portion mislabeled").
- Changing the existing `/speakers/rename` whole-transcript rename endpoint — stays as-is for the "just rename this whole label" case.
- Cross-user roster sharing — `VoiceProfile.user_id` scoping is unchanged.
- Re-running pyannote's clustering itself — the existing `"rediarize"` job kind already does that (in-place pyannote re-run); `"voice_match"` is a distinct kind that only relabels using the roster, no re-clustering.
- Real-time/live-capture speaker matching — this only operates on completed transcripts with stored audio.

## Data model

```python
class VoiceClip(Base):
    __tablename__ = "voice_clips"
    id = Column(Integer, primary_key=True)
    voice_profile_id = Column(Integer, ForeignKey("voice_profiles.id"), nullable=False)
    audio_path = Column(String(512), nullable=False)
    embedding = Column(JSON, nullable=False)  # list of floats, this clip's own embedding
    source_transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
```

`VoiceProfile.embedding` stops being written directly by `enroll()`. Instead:
- `add_clip(db, profile_id, audio_path, source_transcript_id=None) -> VoiceClip`: extracts the embedding for this one clip, inserts the `VoiceClip` row, recomputes `profile.embedding` as the elementwise mean of all the profile's clip embeddings, sets `sample_count = len(clips)`.
- `remove_clip(db, clip_id) -> bool`: deletes the audio file and row, recomputes `profile.embedding` from the remaining clips (or leaves the profile with `embedding=None` and `sample_count=0` if the last clip is removed — such a profile is excluded from `identify()` matching until it has a clip again). `identify()` must skip profiles with `embedding is None` rather than passing `None` to `np.array()`, which is a real gap introduced by allowing an empty profile to exist — add that guard as part of this change.
- `enroll()` (existing method, used by the direct file-upload roster flow) becomes a thin wrapper: create-or-get the profile, then call `add_clip`.

## Roster page changes

Each profile card expands (click to toggle) to show its clip list: a small player per clip (reuses the existing per-line audio-play pattern from the transcript screen) and a remove button. Add-clip on an existing profile reuses the existing "Enroll speaker" file-upload modal, retargeted: if a profile is already selected, it calls `add_clip` instead of creating a new profile.

`GET /api/voices` response gains a `clips` array per profile: `[{id, created_at, source_transcript_id}]` (no embedding payload to the client — kept server-side only, same as today).

New routes:
- `POST /api/voices/{profile_id}/clips` (multipart file) — add a clip to an existing profile.
- `DELETE /api/voices/{profile_id}/clips/{clip_id}` — remove one clip.
- `GET /api/voices/{profile_id}/clips/{clip_id}/audio` — stream the clip for playback (mirrors `GET /api/transcripts/{id}/audio`'s pattern).

## Transcript screen changes

**Enroll marked clips (decoupled from rename):** The existing `◈` seed-flag mechanic is unchanged. A new "Enroll marked clips" button (visible when `seedClips` has entries) opens a small picker: dropdown of existing roster names (fetched from `GET /api/voices`) plus a "+ New name" option. Submitting calls `POST /api/transcripts/{id}/enroll-speaker` (existing endpoint), which internally now calls `add_clip` per flagged clip against the resolved profile (existing or newly created) instead of the current single-overwrite `enroll()` call. Rename (`renameSpeaker`) no longer triggers enrollment as a side effect — the confirm-dialog-after-rename flow is removed; enrollment is only triggered from this explicit button.

**Bulk re-tag:** Segment rows gain a checkbox (visible in a "select" mode toggled by a new toolbar button). Selecting one or more rows and choosing "Re-tag selected" opens the same existing/new-name picker as above, then calls a new endpoint:

`POST /api/transcripts/{id}/segments/retag` — body `{"indices": [2, 3, 4], "speaker": "Sarah Chen"}`. Segments are addressed by their position in the `segments` list (stable for a given transcript — the list is never reordered, only replaced wholesale on rename/correction). Route validates all indices are in range, applies the new speaker only to those positions (immutable-list-rebuild pattern, matching the existing rename route), leaves every other segment — including ones that shared the old mislabeled speaker tag outside the selection — untouched. Also line-anchored-rewrites `corrected_text` is *not* attempted here (unlike `/speakers/rename`): a partial retag has no reliable way to know which corrected-text lines correspond to which original segment indices once the LLM has reworded/merged lines, so `corrected_text` is left as-is and the user re-runs correction if they want it to reflect the retag.

## Voice-match background job

New job kind `"voice_match"` (added to `VALID_KINDS` in `services/llm_jobs.py`, alongside existing `correction`/`summary`/`rediarize` — distinct from `rediarize`, which re-runs pyannote clustering; `voice_match` only relabels using the roster, no clustering). Enqueued via a new "Match against voice roster" button on the transcript screen (parallel to the existing re-diarize action), guarded by `has_audio` same as other audio-dependent actions.

Execution (`run_llm_job`, new branch): for each segment, extract its audio span (reuses `extract_clips_concat`-style single-clip extraction), embed it, call `voice_id_service.identify(db, user_id, clip_path, threshold=0.65)`, and if the top match clears the threshold, set that segment's `speaker` to the matched profile's name. Segments with no confident match keep their original label. Progress reported as `progress_done`/`progress_total` = segments processed / total segments, same shape the transcript detail screen already polls and renders for correction/summary jobs (`jobRunningUnit`).

## Error handling

- No roster profiles with embeddings → job fails fast with "No enrolled voices with clips — add a clip to a roster profile first" (checked before iterating segments).
- Embedding backend unavailable (`voice_id_service._backend == "none"`) → job fails immediately with the same actionable message `enroll()` already raises.
- Per-segment extraction/embedding failure (e.g. a zero-length segment) → that segment is skipped (left at its original label), not a job-ending failure; count of skipped segments included in the job's final state for visibility (`error` field set to a summary if any were skipped, even though status is `"completed"`).

## Testing / verification

TDD per segment as usual. Key cases:
- `VoiceClip` add/remove correctly recomputes `VoiceProfile.embedding` as the mean of remaining clips (unit test against `voice_id.py`, real MFCC embeddings now that `librosa` is installed — no mocking needed for the embedding math itself).
- Removing a profile's last clip zeroes `sample_count` and excludes it from `identify()` results.
- `/segments/retag` only mutates the given indices, leaves other segments sharing the old label untouched, leaves `corrected_text` untouched, validates out-of-range indices with a 400.
- `voice_match` job: segments below threshold keep their original speaker; segments above threshold get relabeled; job completes even when some segments fail extraction (skipped, not fatal).
- Existing `/speakers/rename` and `/enroll-speaker` tests in `tests/test_speaker_naming.py` continue to pass with `enroll()` refactored to call `add_clip` internally (behavior-preserving for the single-clip case).

## Documentation

No `INSTALL.md` changes needed (librosa already added to `requirements.txt` as part of the prerequisite fix). Roster page's existing description line ("Profiles on this roster are matched against every diarized transcript to auto-name speakers") stays accurate — the voice-match job is an explicit, on-demand instance of that same matching, not a new automatic behavior.
