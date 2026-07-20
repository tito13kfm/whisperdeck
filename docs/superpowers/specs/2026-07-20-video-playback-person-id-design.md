# Play Source Video During Person Identification — Design Spec

**Goal:** On the transcript detail screen, pressing a segment's play button plays the *original source video* (not just audio) starting at that segment's timestamp, so the user can visually verify who's speaking, not just aurally. Falls back to today's audio-only playback when no video was uploaded (or the upload had no video track).

**Background:** The reference implementation (`C:\claude\whisperx-cpu`, a Tkinter desktop app) does this by shelling out to ffmpeg on every click to cut a padded clip and hand it to the OS's default media player. That approach doesn't transplant: no persisted video path (lives only on an in-memory `Job` object for the session), no real seek (re-cuts+re-encodes per click, blocking the UI thread), and it isn't a web app in the first place. WhisperDeck is a FastAPI + vanilla-JS web app, so this needs a real `<video>` element with HTTP range-seek, built fresh.

The bigger gap is upstream of playback: WhisperDeck already **discards the video track at ingestion**. `_run_transcription_pipeline` (`app.py:426`) calls `transcode_for_upload` (`services/audio_prep.py:44`), which runs `ffmpeg -vn ...` — strips video, downsamples to 16kHz mono mp3 — and reassigns `save_path` to that audio-only result. The original raw upload is never deleted, but its path is dropped and it becomes an untracked orphan in `UPLOAD_DIR`; nothing in the schema (`Transcript`, `TranscriptionJob`, anywhere) has a `video_path`-equivalent column today.

Existing per-line audio playback (`segPlay`, `rack.js:1808`) already has the exact seek pattern this feature needs: a JS `Audio()` object pointed at a full-file streaming route, `currentTime = start`, and a `timeupdate` listener that pauses at `end`. Video playback reuses the same technique against a visible `<video>` element instead of a headless `Audio()` object.

## Scope

**In scope:**
- Detect whether an upload's raw file has a video stream (ffprobe), independent of file extension.
- Persist the original file as `Transcript.video_path` (new column) when it does and its container is browser-playable, instead of losing track of it after the audio-only transcode reassigns `save_path`.
- New `GET /api/transcripts/{id}/video` route mirroring the existing audio route — range/seek support is free via Starlette's `FileResponse` (already confirmed to implement Range/If-Range parsing in the installed version).
- `has_video` flag in `_serialize_transcript`, same pattern as existing `has_audio`.
- Frontend: a `<video>` element on the detail page, shown when `has_video`; segment play buttons seek+play it (reusing `segPlay`'s pattern) instead of the audio-only player when video is available.
- Retranscribe/re-version: a new transcript row created via `source_transcript_id` (retranscribe) inherits the parent's `video_path` — the source video doesn't change between transcription runs of the same recording.
- **File inventory & manual cleanup page**: since retaining original videos makes the pre-existing "nothing ever deletes these files from disk" gap materially worse, this plan now includes a small storage-management surface — see its own section below — rather than deferring it. Requested directly by the user once the disk-usage tradeoff was flagged.

**Explicitly out of scope:**
- Re-encoding/compressing the retained original video (kept as-is, whatever container the user uploaded).
- Any *automatic* retention policy (age-based expiry, size caps, auto-delete-on-view) — cleanup stays manual/user-initiated (see File inventory section).
- Live-capture (`startLiveCapture`, `rack.js:1275`) gaining video — it explicitly stops video tracks today; unchanged.
- Voice-seed extraction / enrollment flow — stays audio-only, uses `audio_path` as it does today.
- Orphaned voice-clip files (`VoiceClip.audio_path`) — the file inventory below scopes to `UPLOAD_DIR` (transcript audio/video + transcription-job chunks) only, not `VOICES_DIR`; voice clips already have their own delete route (`DELETE /api/voices/{profile_id}/clips/{clip_id}`, which does clean up its file — `services/voice_id.py:remove_clip`) and aren't part of the gap this feature is closing.

## Data model

```python
# database/__init__.py, Transcript
video_path = Column(String(512), nullable=True)  # original upload, kept only if it had a video stream
```

`ensure_columns(engine, "transcripts", {..., "video_path": "TEXT"})` added to the existing migration call (`database/__init__.py:298`).

## Ingestion changes

`services/audio_prep.py` gains:
```python
def has_video_stream(path: str) -> bool:
    """ffprobe check for at least one video stream — extension-independent,
    so a .mp4 that's actually audio-only (or a misnamed file) doesn't
    falsely trigger video retention."""
```
implemented via `ffprobe -select_streams v -show_entries stream=codec_type -of csv=p=0`, non-empty output → True. Mirrors the existing `_ffprobe_bin()`/`get_audio_duration` pattern in the same file.

In `_run_transcription_pipeline` (`app.py:426`), capture the raw path *before* the `needs_transcode` branch reassigns `save_path` (line 474):
```python
raw_path = save_path
video_path = str(raw_path) if has_video_stream(str(raw_path)) else None
```
This runs unconditionally for every call to `_run_transcription_pipeline`, including retranscribe (`app.py:761`) — retranscribe passes the *stored, already audio-only* `t.audio_path` as input, so `has_video_stream` naturally returns `False` there. To avoid silently losing the video on a retranscribed version, when `source_transcript_id` is set, look up the parent transcript and carry its `video_path` forward instead of re-detecting:
```python
if source_transcript_id is not None:
    parent = db.query(Transcript).get(source_transcript_id)
    video_path = parent.video_path if parent else video_path
```
`video_path` is then passed through to both transcript-creation branches (`create_transcript_stub`, `transcribe`) as a new optional kwarg, set directly on the `Transcript(...)` constructor call in each (`services/transcription.py:40`, `services/transcription.py:77`).

## Playback route

```python
@app.get("/api/transcripts/{transcript_id}/video")
async def get_transcript_video(transcript_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    t = db.query(Transcript).filter(Transcript.id == transcript_id, Transcript.user_id == current_user.id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not (t.video_path and os.path.exists(t.video_path)):
        raise HTTPException(status_code=404, detail="No stored video for this transcript")
    ext = os.path.splitext(t.video_path)[1].lower()
    return FileResponse(t.video_path, media_type=_VIDEO_MIME.get(ext, "video/mp4"))
```
Placed right after `get_transcript_audio` (`app.py:781-793`), same auth/ownership scoping. `_VIDEO_MIME` table alongside the existing `_AUDIO_MIME`, deliberately restricted to `.mp4`/`.webm` — see "Container restriction" below.

### Container restriction

`has_video_stream` alone isn't sufficient to decide whether to retain a file as playable video: a browser `<video>` tag can't play most `.mkv`, `.avi`, or non-H.264 `.mov` files. Retaining those anyway would reproduce the exact "sort of works" failure mode this feature exists to fix — the route serves a file, the element has a `src`, but the browser shows a black player with no error. `video_path` is only set when the raw upload's extension is in `{".mp4", ".webm"}` AND it has a video stream. Anything else (`.mkv`, `.avi`, most `.mov`) still transcribes normally (audio extraction is unaffected) — it just doesn't get the video-playback control, falling back to today's audio-only segment playback rather than a broken control. A wider-format follow-up (transcode-to-mp4 on ingest for playback purposes) is a real option later, not something to solve by silently widening the allowlist.

`_serialize_transcript` (`app.py:146-186`) gains:
```python
"has_video": bool(t.video_path and os.path.exists(t.video_path)),
```
right next to the existing `has_audio` line (177).

## Frontend changes

`static/rack.js`:
- New module state alongside `segAudio`/`segAudioTid`/`segPlayingBtn` (`rack.js:1682`): `segVideoTid` tracks which transcript the `<video>` element was last seeked for.
- Detail page template gains a `<video>` element (controls, fixed max-height, e.g. 260px) shown only when `t.has_video`, placed above the tab bar / segment list, with `src` written directly into the template attribute (`src="/api/transcripts/${t.id}/video"`) rather than set imperatively after the fact.
- **Re-render lifecycle**: unlike `segAudio` (a detached `Audio()` object that survives `renderDetail()` re-renders because it isn't a DOM node), the `<video>` element lives in the template and is destroyed/rebuilt on every `renderDetail()` call — which happens on rename, seed-toggle, and job-poll ticks (`scheduleDetailPoll`, `rack.js:1717-1732`). During person-ID, re-renders while a segment is mid-playback are the expected case. Consequences: `src` must come from the template (not a one-time imperative set), and the play/stop-at-end listeners must be (re-)attached on every play-button click rather than gated behind a "seen this transcript before" guard — that guard is exactly what would silently no-op after a re-render swaps in a fresh, listener-less node.
- `segmentsHtml` gating (`rack.js:1762`, currently `!t.has_audio ? '' : ...`) changes to `!(t.has_audio || t.has_video) ? '' : ...` so the play button still renders for video-only transcripts (edge case: local providers always populate `audio_path` today, so in practice `has_audio` is already true whenever `has_video` is — this guard is defensive, not load-bearing).
- `segPlay` (`rack.js:1808`) branches at the top: if `t.has_video`, resolve the shared `<video>` element, set `src` to `/api/transcripts/{id}/video` once per transcript switch (mirroring the `segAudioTid` guard), then apply the identical `currentTime = start` / `_stopAt = end` / `timeupdate`-pause / `pause`-resets-button-glyph logic already proven for `segAudio` — against the video element instead. If `!t.has_video`, fall through to the existing `segAudio` path unchanged.
- Video's own audio track is authoritative when playing video — `segAudio` must not also start (avoid dual audio). Since the branch is exclusive (video path vs audio path, not both), this falls out naturally rather than needing an explicit mute.
- `resetSegAudio()` (`rack.js:1690`) extended to also pause the `<video>` element and clear `segVideoTid` when switching transcripts, matching its existing audio-reset responsibility.

## File inventory & manual cleanup

Retaining original videos makes a pre-existing gap materially worse: nothing in WhisperDeck today deletes `audio_path`/`video_path` files from disk — not `delete_transcript` (`app.py:668-677`, just `db.delete(t)`), not retranscribe (the old version's files stay even though a new transcript row now exists), not the chunked-upload path (`TranscriptionJob.audio_path` chunk files are never swept after processing). Audio-only orphans were small enough to ignore; multi-hundred-MB video orphans are not. Rather than defer this, the plan adds a small storage-management surface.

**Backend — file inventory:**
`GET /api/files` scans `UPLOAD_DIR` (not `VOICES_DIR` — see Explicitly out of scope) and classifies every file against two reference sets: (a) `Transcript.audio_path`/`Transcript.video_path` for transcripts owned by `current_user`, and (b) `TranscriptionJob.audio_path` for jobs in `pending`/`running` status (any user — an in-flight chunk file must never be offered for deletion regardless of whose transcript it belongs to). Response:
```json
{
  "linked": [{"transcript_id": 1, "transcript_title": "...", "field": "audio_path", "path": "...", "size_bytes": N, "modified_at": "..."}],
  "orphaned": [{"path": "...", "size_bytes": N, "modified_at": "..."}],
  "total_linked_bytes": N, "total_orphaned_bytes": N
}
```
"Linked" only includes the current user's own transcripts — a file linked to another user's transcript is neither shown nor deletable here (excluded from both lists, not surfaced as orphaned). "Orphaned" (not referenced by any transcript of any user, and not an in-flight job chunk) has no owner by construction and is visible/deletable by any authenticated user — acceptable for this project's self-hosted, effectively-single-operator deployment model; call out explicitly as a multi-tenant caveat rather than silently assuming it.

**Backend — delete:**
`POST /api/files/delete`, body `{"paths": [...]}` — handles both individual (one path) and bulk (the frontend sends the full list for a group) through one endpoint. Per path: resolve to an absolute real path and reject (400) anything outside `UPLOAD_DIR` (path-traversal guard — this endpoint takes attacker-shaped input, a client-supplied string, so this check is not optional). Then:
- If it matches a `pending`/`running` `TranscriptionJob.audio_path` → skip, reported as `"in_use"`.
- If it matches a transcript's `audio_path`/`video_path` owned by another user → skip, reported as `"forbidden"`.
- If it matches a transcript's `audio_path`/`video_path` owned by `current_user` → delete the file, **null only that column** on the transcript (confirmed behavior: deleting a file never deletes the transcript — text/segments/speakers stay fully intact and browsable, only playback controls disappear, matching how `has_audio`/`has_video` already degrade gracefully for pre-existing transcripts with missing files).
- Otherwise (orphaned) → delete the file outright.
Response: `{"deleted": [...], "skipped": [{"path", "reason"}], "freed_bytes": N}`.

**`delete_transcript` itself also gets fixed** (small, adjacent, same underlying gap): deleting a transcript now removes its `audio_path`/`video_path` files too, so the normal delete flow stops creating new orphans that would otherwise require a trip to the Files page.

**Frontend:** new page (e.g. a "Files" or "Storage" entry in the nav) with two sections — Linked (grouped by transcript, links back to the transcript) and Orphaned — each with per-row checkboxes, "select all", a delete-selected action, and a running total of bytes selected. This is the "either/or ... delete all or individual" surface: the either/or is which section (linked vs orphaned) the user is acting on; delete-all-in-a-section and individual-row delete both go through the same selection + delete-selected mechanism rather than being two different code paths.

## Open tradeoffs (flagging, not solving here)

- **Disk usage roughly doubles for video-sourced transcripts** while both files exist: original video + the 16kHz mono audio extract used for transcription. The File inventory page above gives the user a way to see and reclaim this, but there's still no *automatic* policy — a user who never visits that page accumulates the full doubled cost indefinitely. A size-cap or "only keep video under N minutes/MB" auto-rule would be a natural follow-up if manual cleanup proves insufficient in practice.

## Testing / verification

- `has_video_stream`: unit tests against a real short video fixture (silence + color test pattern, generated via ffmpeg in the test, not committed as a binary) and a real audio-only fixture — both already-available generation patterns exist in `tests/` for ffmpeg-dependent tests (chunk/duration tests already spin up short real media with ffmpeg).
- `_run_transcription_pipeline`: fresh video upload sets `video_path` to the original file and leaves `audio_path` as the transcoded mp3; fresh audio-only upload leaves `video_path` null; retranscribe of a video-sourced transcript carries `video_path` forward from the parent without re-probing.
- `GET /api/transcripts/{id}/video`: 404 when no `video_path`, 404 when the file is missing from disk, 200 + correct bytes when present, ownership-scoped (other user's transcript → 404).
- `_serialize_transcript`: `has_video` true/false matrix (path unset, path set but missing file, path set and present).
- Frontend: manual check (no existing e2e harness covers per-segment audio playback either, per the existing `segPlay` code having no test file) — play a video-sourced transcript's segment, confirm the `<video>` element seeks to `start` and stops at `end`; confirm switching to an audio-only transcript still uses the plain audio path unchanged; confirm playback survives a rename/seed-triggered re-render mid-play instead of going silently dead.
- `GET /api/files`: linked vs orphaned classification correct; another user's linked file excluded from both lists; an in-flight `TranscriptionJob` chunk file excluded from orphaned.
- `POST /api/files/delete`: path-traversal payloads (`../`, absolute paths outside `UPLOAD_DIR`) rejected with 400; deleting a linked file nulls only the matching column and leaves the transcript's text/segments untouched; deleting another user's linked file is skipped as forbidden, not silently deleted; deleting an in-flight job's chunk file is skipped as in_use; deleting an orphan removes it outright.
- `delete_transcript`: now also removes `audio_path`/`video_path` files from disk (regression test — this is new behavior, not just documentation, so it needs its own test where none existed before).
