# Voice Clip Roster & Roster-Based Re-Diarization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `VoiceProfile` into an editable roster of named voice clips (not one overwritten embedding), manageable from the Voice roster page and the transcript screen, and use that roster to bulk-fix wrong diarization labels and to run an on-demand "match against roster" background job.

**Architecture:** New `VoiceClip` child table under `VoiceProfile`; `VoiceIdentificationService` gains `add_clip`/`remove_clip` and `VoiceProfile.embedding` becomes the mean of its clips, recomputed on change. New segment-index-scoped retag endpoint alongside the existing whole-label rename. New `"voice_match"` `LlmJob` kind that embeds each segment and relabels confident matches against the roster. Frontend: roster page clip management, transcript-screen enroll/retag pickers decoupled from rename, and a job-progress button mirroring the existing correction/summary UI.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), vanilla JS (`static/rack.js`), `librosa` MFCC embeddings (already installed), pytest.

## Global Constraints

- Match threshold for roster identification: `0.65` (existing `identify()` default — do not change).
- `VoiceProfile.user_id` scoping is unchanged — every new query filters by `current_user.id` / `user_id`, same as every existing route in `app.py`.
- No changes to `/api/transcripts/{id}/speakers/rename` behavior (whole-label rename stays as-is).
- `corrected_text` is left untouched by the new retag endpoint (spec: no reliable line-to-segment-index mapping after LLM correction).
- Every new DB-writing function takes `db: Session` as its first parameter (existing project convention — see `voice_id.py`, `llm_jobs.py`).

---

## File Structure

- **Modify** `database/__init__.py` — add `VoiceClip` model, add to `__all__`.
- **Modify** `services/voice_id.py` — add `add_clip`, `remove_clip`, `_recompute_profile_embedding`; refactor `enroll()` to use `add_clip`; guard `identify()` against `embedding is None`.
- **Modify** `services/llm_jobs.py` — add `"voice_match"` to `VALID_KINDS`; add a `run_llm_job` branch that embeds each segment and relabels confident matches.
- **Modify** `app.py` — clip CRUD routes under `/api/voices/{profile_id}/clips...`; refactor `/enroll-speaker` to call `add_clip`; new `/api/transcripts/{id}/segments/retag`; new `/api/transcripts/{id}/voice-match` job-enqueue route; `_serialize_transcript` gains a `voice_match_job` field; `GET /api/voices` includes each profile's `clips`.
- **Modify** `static/rack.js` — Roster page clip list/add/remove UI; transcript screen "Enroll marked clips" button (replacing the confirm-after-rename flow); bulk-select + "Re-tag selected" UI; "Match against voice roster" button and progress rendering.
- **Test**: `tests/test_voice_id.py` (extend), `tests/test_speaker_naming.py` (extend), new `tests/test_voice_match_job.py`.

---

### Task 1: `VoiceClip` model

**Files:**
- Modify: `database/__init__.py`

**Interfaces:**
- Produces: `VoiceClip` class with columns `id, voice_profile_id, audio_path, embedding, source_transcript_id, created_at`, exported in `__all__`.

- [ ] **Step 1: Add the model**

In `database/__init__.py`, immediately after the `VoiceProfile` class (currently ends at line 128, before `class ProviderConfig`), add:

```python
class VoiceClip(Base):
    """One enrolled audio clip backing a VoiceProfile. A profile's match
    embedding is the mean of its clips' embeddings, recomputed whenever a
    clip is added or removed (see services/voice_id.py)."""
    __tablename__ = "voice_clips"

    id = Column(Integer, primary_key=True)
    voice_profile_id = Column(Integer, ForeignKey("voice_profiles.id", ondelete="CASCADE"), nullable=False)
    audio_path = Column(String(512), nullable=False)
    embedding = Column(JSON, nullable=False)  # this clip's own embedding, list of floats
    source_transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
```

- [ ] **Step 2: Export it**

Change the `__all__` list at the bottom of `database/__init__.py`:

```python
__all__ = [
    "Base", "User", "Transcript", "Summary", "VoiceProfile", "VoiceClip", "ProviderConfig", "TranscriptionJob", "LlmJob", "HotwordEntry",
    "init_db", "migrate_schema", "backfill_user_id", "ensure_columns",
]
```

- [ ] **Step 3: Verify the table is created**

Run:
```bash
.venv/Scripts/python.exe -c "
from database import init_db, VoiceClip
engine, SessionLocal, _ = init_db(':memory:'.replace(':memory:', 'file::memory:?cache=shared'))
from sqlalchemy import inspect
print('voice_clips' in inspect(engine).get_table_names())
"
```
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add database/__init__.py
git commit -m "feat: add VoiceClip model for per-clip voice enrollment"
```

---

### Task 2: `add_clip` / `remove_clip` in `VoiceIdentificationService`

**Files:**
- Modify: `services/voice_id.py`
- Test: `tests/test_voice_id.py`

**Interfaces:**
- Consumes: `VoiceClip` (Task 1), existing `_extract_embedding(audio_path) -> Optional[np.ndarray]`.
- Produces:
  - `add_clip(db, profile_id: int, user_id: int, audio_path: str, source_transcript_id: int | None = None) -> VoiceClip` — raises `ValueError` if profile not found or embedding extraction fails (message includes `_last_backend_error` same as `enroll()`).
  - `remove_clip(db, profile_id: int, user_id: int, clip_id: int) -> bool` — `False` if not found/not owned.
  - `identify()` skips profiles with `embedding is None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_id.py`:

```python
from database import VoiceProfile, VoiceClip


def _profile(db_session, user_id, name="Alice"):
    p = VoiceProfile(user_id=user_id, name=name, embedding=None, sample_count=0)
    db_session.add(p)
    db_session.commit()
    return p


def test_add_clip_creates_row_and_sets_profile_embedding_to_its_value(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: np.array([1.0, 2.0, 3.0]))

    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    clip_file = tmp_path / "clip1.wav"
    clip_file.write_bytes(b"wav")

    clip = svc.add_clip(db_session, profile.id, user.id, str(clip_file))

    assert clip.id is not None
    assert clip.voice_profile_id == profile.id
    db_session.refresh(profile)
    assert profile.embedding == [1.0, 2.0, 3.0]
    assert profile.sample_count == 1


def test_add_clip_averages_embedding_across_multiple_clips(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    values = iter([np.array([0.0, 0.0]), np.array([2.0, 4.0])])
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: next(values))

    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    for i in range(2):
        clip_file = tmp_path / f"clip{i}.wav"
        clip_file.write_bytes(b"wav")
        svc.add_clip(db_session, profile.id, user.id, str(clip_file))

    db_session.refresh(profile)
    assert profile.embedding == [1.0, 2.0]
    assert profile.sample_count == 2


def test_add_clip_raises_when_extraction_fails(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: None)

    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    clip_file = tmp_path / "bad.wav"
    clip_file.write_bytes(b"wav")

    with pytest.raises(ValueError):
        svc.add_clip(db_session, profile.id, user.id, str(clip_file))


def test_remove_clip_recomputes_embedding_from_remaining(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    values = iter([np.array([0.0, 0.0]), np.array([2.0, 4.0])])
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: next(values))

    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    clips = []
    for i in range(2):
        clip_file = tmp_path / f"clip{i}.wav"
        clip_file.write_bytes(b"wav")
        clips.append(svc.add_clip(db_session, profile.id, user.id, str(clip_file)))

    ok = svc.remove_clip(db_session, profile.id, user.id, clips[0].id)
    assert ok is True

    db_session.refresh(profile)
    assert profile.embedding == [2.0, 4.0]
    assert profile.sample_count == 1


def test_remove_last_clip_zeroes_profile(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: np.array([1.0, 1.0]))

    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    clip_file = tmp_path / "only.wav"
    clip_file.write_bytes(b"wav")
    clip = svc.add_clip(db_session, profile.id, user.id, str(clip_file))

    svc.remove_clip(db_session, profile.id, user.id, clip.id)

    db_session.refresh(profile)
    assert profile.embedding is None
    assert profile.sample_count == 0


def test_identify_skips_profiles_with_no_embedding(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)
    _profile(db_session, user.id, name="Empty")  # embedding=None, no clips
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: np.array([1.0, 0.0]))

    probe = tmp_path / "probe.wav"
    probe.write_bytes(b"wav")
    results = svc.identify(db_session, user.id, str(probe))

    assert results == []  # no crash, no match — the empty profile is skipped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_voice_id.py -k "add_clip or remove_clip or identify_skips" -v`
Expected: `AttributeError: 'VoiceIdentificationService' object has no attribute 'add_clip'` (and similar) for each new test.

- [ ] **Step 3: Implement `add_clip`, `remove_clip`, embedding recompute, and the `identify()` guard**

In `services/voice_id.py`, add the import at the top:

```python
from database import VoiceProfile, VoiceClip
```

Add these methods to `VoiceIdentificationService` (after `enroll`, before `identify`):

```python
    def add_clip(
        self,
        db,
        profile_id: int,
        user_id: int,
        audio_path: str,
        source_transcript_id: Optional[int] = None,
    ) -> VoiceClip:
        """Add one clip to an existing profile and recompute the profile's
        match embedding as the mean of all its clips."""
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.id == profile_id, VoiceProfile.user_id == user_id
        ).first()
        if not profile:
            raise ValueError(f"Voice profile {profile_id} not found")

        embedding = self._extract_embedding(audio_path)
        if embedding is None:
            reason = f" ({self._last_backend_error})" if self._last_backend_error else ""
            raise ValueError(
                f"Voice embedding extraction failed using the {self.backend_name} "
                f"backend.{reason}"
            )

        clip = VoiceClip(
            voice_profile_id=profile.id,
            audio_path=audio_path,
            embedding=embedding.tolist() if isinstance(embedding, np.ndarray) else embedding,
            source_transcript_id=source_transcript_id,
        )
        db.add(clip)
        db.commit()
        self._recompute_profile_embedding(db, profile)
        return clip

    def remove_clip(self, db, profile_id: int, user_id: int, clip_id: int) -> bool:
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.id == profile_id, VoiceProfile.user_id == user_id
        ).first()
        if not profile:
            return False
        clip = db.query(VoiceClip).filter(
            VoiceClip.id == clip_id, VoiceClip.voice_profile_id == profile.id
        ).first()
        if not clip:
            return False
        try:
            os.remove(clip.audio_path)
        except OSError:
            pass
        db.delete(clip)
        db.commit()
        self._recompute_profile_embedding(db, profile)
        return True

    def _recompute_profile_embedding(self, db, profile: VoiceProfile) -> None:
        clips = db.query(VoiceClip).filter(VoiceClip.voice_profile_id == profile.id).all()
        if not clips:
            profile.embedding = None
            profile.sample_count = 0
        else:
            stacked = np.array([c.embedding for c in clips])
            profile.embedding = np.mean(stacked, axis=0).tolist()
            profile.sample_count = len(clips)
        profile.embedding_model = self.backend_name
        profile.updated_at = datetime.datetime.utcnow()
        db.commit()
```

Refactor `enroll()` to delegate to `add_clip` for the profile-creation case (keeps the direct-file-upload roster flow working):

```python
    def enroll(
        self,
        db,
        user_id: int,
        name: str,
        audio_path: str,
        notes: str = "",
    ) -> VoiceProfile:
        """Enroll a speaker by name from an audio sample — creates the
        profile if it doesn't exist yet, then adds this sample as its
        first clip."""
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == user_id, VoiceProfile.name == name
        ).first()
        if not profile:
            profile = VoiceProfile(
                user_id=user_id, name=name, embedding=None,
                embedding_model=self.backend_name, sample_count=0, notes=notes,
            )
            db.add(profile)
            db.commit()
        elif notes:
            profile.notes = notes
            db.commit()

        self.add_clip(db, profile.id, user_id, audio_path)
        db.refresh(profile)
        return profile
```

Guard `identify()` against clip-less profiles — change:

```python
        for profile in profiles:
            stored = np.array(profile.embedding)
```

to:

```python
        for profile in profiles:
            if profile.embedding is None:
                continue
            stored = np.array(profile.embedding)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_voice_id.py tests/test_speaker_naming.py -v`
Expected: all pass (existing `test_enroll_speaker_happy_path` and `test_enroll_speaker_cleans_up_when_enroll_fails` in `tests/test_speaker_naming.py` must still pass unchanged — they patch `app.voice_id_service.enroll` directly, so the refactor is transparent to them).

- [ ] **Step 5: Commit**

```bash
git add services/voice_id.py tests/test_voice_id.py
git commit -m "feat: per-clip voice enrollment with averaged profile embedding"
```

---

### Task 3: Clip CRUD routes + `GET /api/voices` clip list

**Files:**
- Modify: `app.py`
- Test: `tests/test_voice_id.py`

**Interfaces:**
- Consumes: `voice_id_service.add_clip`, `voice_id_service.remove_clip` (Task 2).
- Produces: `POST /api/voices/{profile_id}/clips`, `DELETE /api/voices/{profile_id}/clips/{clip_id}`, `GET /api/voices/{profile_id}/clips/{clip_id}/audio`. `GET /api/voices` response items gain `"clips": [{"id", "created_at", "source_transcript_id"}]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_id.py`:

```python
def test_list_voices_includes_clips(client, db_session, tmp_path, monkeypatch):
    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    from services import voice_id as voice_id_module
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path: np.array([1.0, 2.0]))
    clip_file = tmp_path / "c.wav"
    clip_file.write_bytes(b"wav")
    import app as app_module
    app_module.voice_id_service.add_clip(db_session, profile.id, user.id, str(clip_file))

    r = client.get("/api/voices")
    assert r.status_code == 200
    body = next(v for v in r.json() if v["id"] == profile.id)
    assert len(body["clips"]) == 1
    assert "id" in body["clips"][0] and "created_at" in body["clips"][0]


def test_add_clip_route_happy_path(client, db_session, tmp_path, monkeypatch):
    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path: np.array([1.0, 2.0]))

    r = client.post(
        f"/api/voices/{profile.id}/clips",
        files={"file": ("clip.wav", io.BytesIO(b"wav bytes"), "audio/wav")},
    )
    assert r.status_code == 200
    assert r.json()["voice_profile_id"] == profile.id


def test_add_clip_route_404_for_missing_profile(client, db_session):
    r = client.post(
        "/api/voices/999999/clips",
        files={"file": ("clip.wav", io.BytesIO(b"wav bytes"), "audio/wav")},
    )
    assert r.status_code == 400  # add_clip raises ValueError("...not found")


def test_delete_clip_route(client, db_session, tmp_path, monkeypatch):
    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path: np.array([1.0, 2.0]))
    import app as app_module
    clip_file = tmp_path / "c.wav"
    clip_file.write_bytes(b"wav")
    clip = app_module.voice_id_service.add_clip(db_session, profile.id, user.id, str(clip_file))

    r = client.delete(f"/api/voices/{profile.id}/clips/{clip.id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.delete(f"/api/voices/{profile.id}/clips/{clip.id}")
    assert r2.status_code == 404


def test_clip_audio_route_serves_file(client, db_session, tmp_path, monkeypatch):
    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path: np.array([1.0, 2.0]))
    import app as app_module
    clip_file = tmp_path / "c.wav"
    clip_file.write_bytes(b"real wav bytes")
    clip = app_module.voice_id_service.add_clip(db_session, profile.id, user.id, str(clip_file))

    r = client.get(f"/api/voices/{profile.id}/clips/{clip.id}/audio")
    assert r.status_code == 200
    assert r.content == b"real wav bytes"
```

Add `import io` and `import numpy as np` at the top of `tests/test_voice_id.py` if not already present (they are — `numpy as np` is already imported; add `import io`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_voice_id.py -k "clip_route or list_voices_includes or add_clip_route or delete_clip_route or clip_audio_route" -v`
Expected: 404s (routes don't exist) or `KeyError: 'clips'`.

- [ ] **Step 3: Implement the routes**

In `app.py`, modify `GET /api/voices` (around line 1123-1126) — no change needed to the route itself since `list_profiles` will be updated in this step. Update `services/voice_id.py`'s `list_profiles` (in the same file touched by Task 2, but this is the step where it's needed) to include clips:

```python
    def list_profiles(self, db, user_id: int) -> list[dict]:
        profiles = (
            db.query(VoiceProfile)
            .filter(VoiceProfile.user_id == user_id)
            .order_by(VoiceProfile.name)
            .all()
        )
        return [
            {
                "id": p.id,
                "name": p.name,
                "sample_count": p.sample_count,
                "embedding_model": p.embedding_model,
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "clips": [
                    {
                        "id": c.id,
                        "created_at": c.created_at.isoformat() if c.created_at else None,
                        "source_transcript_id": c.source_transcript_id,
                    }
                    for c in db.query(VoiceClip)
                    .filter(VoiceClip.voice_profile_id == p.id)
                    .order_by(VoiceClip.created_at)
                    .all()
                ],
            }
            for p in profiles
        ]
```

In `app.py`, add these routes right after `delete_voice_profile` (currently ending around line 1191):

```python
@app.post("/api/voices/{profile_id}/clips")
async def add_voice_clip(
    profile_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add one clip to an existing roster profile — recomputes the
    profile's match embedding as the mean of all its clips."""
    file_ext = os.path.splitext(file.filename or "clip.wav")[1] or ".wav"
    safe_name = f"clip_{profile_id}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S%f')}{file_ext}"
    save_path = VOICES_DIR / safe_name
    with open(save_path, "wb") as f:
        f.write(await file.read())

    try:
        clip = voice_id_service.add_clip(db, profile_id, current_user.id, str(save_path))
        return {"id": clip.id, "voice_profile_id": clip.voice_profile_id,
                "created_at": clip.created_at.isoformat() if clip.created_at else None}
    except ValueError as e:
        try:
            os.remove(save_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/voices/{profile_id}/clips/{clip_id}")
async def delete_voice_clip(
    profile_id: int, clip_id: int,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    ok = voice_id_service.remove_clip(db, profile_id, current_user.id, clip_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Clip not found")
    return {"ok": True}


@app.get("/api/voices/{profile_id}/clips/{clip_id}/audio")
async def get_voice_clip_audio(
    profile_id: int, clip_id: int,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    profile = db.query(VoiceProfile).filter(
        VoiceProfile.id == profile_id, VoiceProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    clip = db.query(VoiceClip).filter(
        VoiceClip.id == clip_id, VoiceClip.voice_profile_id == profile.id
    ).first()
    if not clip or not os.path.exists(clip.audio_path):
        raise HTTPException(status_code=404, detail="Clip audio not found")
    ext = os.path.splitext(clip.audio_path)[1].lower()
    return FileResponse(clip.audio_path, media_type=_AUDIO_MIME.get(ext, "audio/wav"))
```

Add `VoiceClip` to the `database` import line near the top of `app.py`:

```python
from database import init_db, backfill_user_id, Transcript, Summary, VoiceProfile, VoiceClip, ProviderConfig, User, LlmJob
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_voice_id.py tests/test_speaker_naming.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app.py services/voice_id.py tests/test_voice_id.py
git commit -m "feat: voice clip CRUD routes and clip list on GET /api/voices"
```

---

### Task 4: Decouple transcript-screen enrollment from rename (`/enroll-speaker` uses `add_clip`)

**Files:**
- Modify: `app.py`
- Test: `tests/test_speaker_naming.py`

**Interfaces:**
- Consumes: `voice_id_service.add_clip` (Task 2), a "find or create profile by name" helper.
- Produces: `/api/transcripts/{id}/enroll-speaker` now creates/finds a profile by `name` and calls `add_clip` for the extracted seed-clip sample, instead of calling `enroll()` (which always overwrote). Existing response shape (`name`, plus new `clip_id`) preserved for `p.name` used by the frontend today.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_speaker_naming.py`, in the `enroll-speaker` section:

```python
def test_enroll_speaker_appends_clip_to_existing_profile_without_overwriting(client, db_session, tmp_path):
    t = _transcript(db_session, tmp_path)
    user = _test_user(db_session)
    from database import VoiceProfile
    profile = VoiceProfile(user_id=user.id, name="Alice", embedding=[9.0, 9.0],
                           embedding_model="MFCC fingerprint (librosa)", sample_count=1)
    db_session.add(profile)
    db_session.commit()

    sample = tmp_path / "seed.wav"
    sample.write_bytes(b"wav")
    fake_extract = AsyncMock(return_value=str(sample))

    with patch("app.extract_clips_concat", fake_extract), \
         patch("app.voice_id_service._extract_embedding", return_value=__import__("numpy").array([1.0, 3.0])):
        r = client.post(f"/api/transcripts/{t.id}/enroll-speaker",
                        json={"name": "Alice", "clips": [{"start": 0.0, "end": 2.0}]})
    assert r.status_code == 200
    db_session.expire_all()
    refreshed = db_session.query(VoiceProfile).filter(VoiceProfile.id == profile.id).first()
    # averaged with the existing [9.0, 9.0] embedding, not overwritten to [1.0, 3.0]
    assert refreshed.embedding == [5.0, 6.0]
    assert refreshed.sample_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_speaker_naming.py::test_enroll_speaker_appends_clip_to_existing_profile_without_overwriting -v`
Expected: FAIL — `refreshed.embedding == [1.0, 3.0]` (old `enroll()` overwrite), not `[5.0, 6.0]`.

- [ ] **Step 3: Update the route**

In `app.py`, find `enroll_speaker_from_transcript` (currently calls `voice_id_service.enroll(...)`). Replace the body from the `try:` block onward:

```python
    try:
        sample_path = await extract_clips_concat(t.audio_path, clips, str(UPLOAD_DIR))
    except (AudioPrepError, KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Could not extract seed clips: {e}")
    try:
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == current_user.id, VoiceProfile.name == name
        ).first()
        if not profile:
            profile = VoiceProfile(
                user_id=current_user.id, name=name, embedding=None,
                embedding_model=voice_id_service.backend_name, sample_count=0,
                notes=f"Seeded from transcript {t.id}",
            )
            db.add(profile)
            db.commit()
        clip = voice_id_service.add_clip(db, profile.id, current_user.id, sample_path,
                                          source_transcript_id=t.id)
        db.refresh(profile)
        return {
            "id": profile.id,
            "name": profile.name,
            "sample_count": profile.sample_count,
            "embedding_model": profile.embedding_model,
            "notes": profile.notes,
            "clip_id": clip.id,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            os.remove(sample_path)
        except OSError:
            pass
```

Note: `add_clip` stores `audio_path=sample_path`, but the `finally` block removes `sample_path` right after — this matches the existing test `test_enroll_speaker_happy_path`'s assertion `assert not sample.exists()`. Since the seed sample is a temporary concatenation (not the clip's permanent storage), copy it into `VOICES_DIR` before calling `add_clip` so the stored `audio_path` survives the cleanup:

```python
    try:
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == current_user.id, VoiceProfile.name == name
        ).first()
        if not profile:
            profile = VoiceProfile(
                user_id=current_user.id, name=name, embedding=None,
                embedding_model=voice_id_service.backend_name, sample_count=0,
                notes=f"Seeded from transcript {t.id}",
            )
            db.add(profile)
            db.commit()
        permanent_path = VOICES_DIR / f"clip_{profile.id}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S%f')}.wav"
        shutil.copyfile(sample_path, permanent_path)
        clip = voice_id_service.add_clip(db, profile.id, current_user.id, str(permanent_path),
                                          source_transcript_id=t.id)
        db.refresh(profile)
        return {
            "id": profile.id,
            "name": profile.name,
            "sample_count": profile.sample_count,
            "embedding_model": profile.embedding_model,
            "notes": profile.notes,
            "clip_id": clip.id,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            os.remove(sample_path)
        except OSError:
            pass
```

(`shutil` is already imported at the top of `app.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_speaker_naming.py -v`
Expected: all pass, including the pre-existing `test_enroll_speaker_happy_path` and `test_enroll_speaker_cleans_up_when_enroll_fails` (the latter patches `app.voice_id_service.enroll` — check this still applies; if `enroll()` is no longer called by this route, update that test to patch `app.voice_id_service.add_clip` with `side_effect=ValueError("no backend")` instead, keeping the same assertions).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_speaker_naming.py
git commit -m "feat: enroll-speaker route appends a clip instead of overwriting the profile"
```

---

### Task 5: Segment-index-scoped retag endpoint

**Files:**
- Modify: `app.py`
- Test: `tests/test_speaker_naming.py`

**Interfaces:**
- Produces: `POST /api/transcripts/{id}/segments/retag` — body `{"indices": [int, ...], "speaker": str}`, returns `{"retagged": int, "transcript": {...}}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_speaker_naming.py`:

```python
def test_retag_only_changes_selected_indices(client, db_session):
    t = _transcript(db_session)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0], "speaker": "Bob"})
    assert r.status_code == 200
    assert r.json()["retagged"] == 1
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    speakers = [s["speaker"] for s in t2.segments]
    # index 0 retagged; index 2 (also originally SPEAKER_00) untouched
    assert speakers == ["Bob", "SPEAKER_01", "SPEAKER_00"]


def test_retag_leaves_corrected_text_untouched(client, db_session):
    corrected = "SPEAKER_00: hello there\n\nSPEAKER_01: general kenobi"
    t = _transcript(db_session, corrected_text=corrected)
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [0], "speaker": "Bob"})
    assert r.status_code == 200
    db_session.expire_all()
    t2 = db_session.query(Transcript).filter(Transcript.id == t.id).first()
    assert t2.corrected_text == corrected


def test_retag_validation(client, db_session):
    t = _transcript(db_session)
    assert client.post(f"/api/transcripts/{t.id}/segments/retag",
                       json={"indices": [], "speaker": "Bob"}).status_code == 400
    assert client.post(f"/api/transcripts/{t.id}/segments/retag",
                       json={"indices": [0], "speaker": "  "}).status_code == 400
    r = client.post(f"/api/transcripts/{t.id}/segments/retag",
                    json={"indices": [99], "speaker": "Bob"})
    assert r.status_code == 400
    assert "out of range" in r.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_speaker_naming.py -k retag -v`
Expected: 404 Not Found for all three (route doesn't exist).

- [ ] **Step 3: Implement the route**

In `app.py`, add immediately after `rename_transcript_speaker` (after its closing, currently ending around line 803):

```python
@app.post("/api/transcripts/{transcript_id}/segments/retag")
async def retag_transcript_segments(
    transcript_id: int,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fix a chunk of mis-diarized lines by index, without touching other
    segments that happen to share the same (correct) original label.
    corrected_text is intentionally left untouched — there is no reliable
    line-to-segment-index mapping once the LLM has reworded/merged lines."""
    indices = data.get("indices") or []
    speaker = (data.get("speaker") or "").strip()
    if not indices or not speaker:
        raise HTTPException(status_code=400, detail="'indices' (non-empty) and 'speaker' are required")

    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")

    segments = t.segments or []
    for i in indices:
        if not isinstance(i, int) or i < 0 or i >= len(segments):
            raise HTTPException(status_code=400, detail=f"Segment index {i} is out of range")

    index_set = set(indices)
    new_segments = [
        {**seg, "speaker": speaker} if i in index_set else seg
        for i, seg in enumerate(segments)
    ]
    t.segments = new_segments
    t.updated_at = datetime.datetime.utcnow()
    db.commit()
    return {"retagged": len(index_set), "transcript": _serialize_transcript(db, t)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_speaker_naming.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_speaker_naming.py
git commit -m "feat: segment-index-scoped retag endpoint for partial diarization fixes"
```

---

### Task 6: `voice_match` background job

**Files:**
- Modify: `services/llm_jobs.py`
- Test: `tests/test_voice_match_job.py` (new)

**Interfaces:**
- Consumes: `voice_id_service.identify(db, user_id, audio_path, threshold) -> list[dict]` (existing, from Task 2's guard), `extract_clips_concat(audio_path, clips, output_dir) -> str` (existing, `services/audio_prep.py`).
- Produces: `run_llm_job` handles `job.kind == "voice_match"`; `VALID_KINDS` includes `"voice_match"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_match_job.py`:

```python
"""voice_match background job: relabels segments against the roster,
leaves low-confidence segments untouched, tolerates per-segment failures."""
import asyncio
from unittest.mock import patch

from database import Transcript, User
from services.llm_jobs import enqueue_llm_job, run_llm_job


class _NoCloseSession:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def _user(db_session, name="matcher"):
    user = User(username=name, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def _transcript_with_segments(db_session, user, tmp_path, segments):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                   full_text="x", segments=segments, audio_path=str(audio))
    db_session.add(t)
    db_session.commit()
    return t


def test_voice_match_relabels_confident_segments_only(db_session, tmp_path):
    user = _user(db_session)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    def fake_identify(db, user_id, audio_path, threshold=0.65):
        # first call (segment 0) matches confidently, second doesn't
        fake_identify.calls += 1
        if fake_identify.calls == 1:
            return [{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 2}]
        return []
    fake_identify.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify", fake_identify):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "Alice"
    assert t.segments[1]["speaker"] == "SPEAKER_01"  # untouched, no confident match
    assert job.progress_done == 2
    assert job.progress_total == 2


def test_voice_match_fails_fast_with_no_backend(db_session, tmp_path):
    user = _user(db_session)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.voice_id_service._backend", "none"):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert "backend" in job.error.lower()


def test_voice_match_fails_when_audio_missing(db_session):
    user = _user(db_session)
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                   full_text="x", segments=[{"start": 0, "end": 1, "text": "hi", "speaker": "S"}],
                   audio_path="nope/missing.mp3")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert "No stored audio" in job.error


def test_voice_match_skips_segment_on_extraction_failure_without_failing_job(db_session, tmp_path):
    user = _user(db_session)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def flaky_extract(audio_path, clips, output_dir):
        flaky_extract.calls += 1
        if flaky_extract.calls == 1:
            raise ValueError("boom")
        return str(tmp_path / "clip.wav")
    flaky_extract.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", flaky_extract), \
         patch("services.llm_jobs.voice_id_service.identify",
               lambda db, user_id, audio_path, threshold=0.65: [{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}]):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "SPEAKER_00"  # extraction failed, left alone
    assert t.segments[1]["speaker"] == "Alice"
    assert "1 segment" in job.error  # skip count surfaced even though status is completed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_voice_match_job.py -v`
Expected: `ValueError: Unknown LLM job kind: voice_match` from `enqueue_llm_job`.

- [ ] **Step 3: Implement**

In `services/llm_jobs.py`:

Change `VALID_KINDS`:
```python
VALID_KINDS = ("correction", "summary", "rediarize", "voice_match")
```

Add imports near the top of the file (module level, since `run_llm_job` already does local imports for `os` and `services.correction` — follow that pattern and import inside `run_llm_job` instead, to avoid a hard import-time dependency on `services.voice_id`/`services.audio_prep` for callers that never touch this job kind):

Inside `run_llm_job`, at the top where `correct_transcript` is imported (currently):
```python
    from services.correction import correct_transcript
    from services.settings import resolve_provider_key
```
add:
```python
    from services.audio_prep import extract_clips_concat
    from services.voice_id import voice_id_service
```

Wait — `voice_id_service` in `app.py` is an *instance* constructed once at app startup, not a module-level singleton in `services/voice_id.py`. To let both `app.py` and `services/llm_jobs.py` share the same instance (so roster data enrolled via `app.py`'s routes is visible to the job), add a module-level singleton to `services/voice_id.py`. At the bottom of `services/voice_id.py`, after the class definition, add:

```python
voice_id_service = VoiceIdentificationService()
```

Then in `app.py`, find where `voice_id_service` is currently constructed (search for `VoiceIdentificationService(`) and replace that instantiation with an import of the shared singleton instead — change:
```python
voice_id_service = VoiceIdentificationService(voices_dir=str(VOICES_DIR))
```
to:
```python
from services.voice_id import voice_id_service
```
(placed alongside the existing `from services.voice_id import VoiceIdentificationService` import — keep both, `VoiceIdentificationService` is only needed if referenced elsewhere; if this replacement removes its last use in `app.py`, delete that import line instead of leaving it unused.)

Note: `voice_id_service` is constructed with `voices_dir="data/voices"` (the class default), which matches `str(VOICES_DIR)` (`data/voices`) already used by the rest of `app.py` — no behavior change.

Now add the `run_llm_job` branch. In the `if/elif` chain (after the `"rediarize"` branch, before the final `else`):

```python
        elif job.kind == "voice_match":
            if voice_id_service._backend == "none":
                _finish(db, job, "failed", "No voice embedding backend available")
                return
            if not (transcript.audio_path and os.path.exists(transcript.audio_path)):
                _finish(db, job, "failed", "No stored audio for this transcript")
                return
            segments = transcript.segments or []
            job.progress_total = len(segments)
            job.progress_done = 0
            db.commit()
            skipped = 0
            new_segments = list(segments)
            for i, seg in enumerate(segments):
                try:
                    clip_path = await extract_clips_concat(
                        transcript.audio_path, [{"start": seg["start"], "end": seg["end"]}],
                        str(os.path.dirname(transcript.audio_path)),
                    )
                    try:
                        matches = voice_id_service.identify(db, job.user_id, clip_path, threshold=0.65)
                    finally:
                        try:
                            os.remove(clip_path)
                        except OSError:
                            pass
                    if matches:
                        new_segments[i] = {**seg, "speaker": matches[0]["name"]}
                except Exception:
                    skipped += 1
                job.progress_done = i + 1
                db.commit()
            transcript.segments = new_segments
            transcript.updated_at = datetime.datetime.utcnow()
            db.commit()
            error = f"{skipped} segment(s) skipped (extraction/embedding failed)" if skipped else None
            _finish(db, job, "completed", error)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_voice_match_job.py tests/test_posthoc_reprocess.py tests/test_speaker_naming.py tests/test_voice_id.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/llm_jobs.py services/voice_id.py app.py tests/test_voice_match_job.py
git commit -m "feat: voice_match background job relabels segments against the voice roster"
```

---

### Task 7: Enqueue route + transcript serialization

**Files:**
- Modify: `app.py`
- Test: `tests/test_voice_match_job.py`

**Interfaces:**
- Produces: `POST /api/transcripts/{id}/voice-match` (mirrors `/rediarize`'s shape), `_serialize_transcript` gains `"voice_match_job"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_match_job.py`:

```python
def test_voice_match_route_enqueues_job(client, db_session, tmp_path):
    from database import User as _User
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _transcript_with_segments(db_session, user, tmp_path,
                                   [{"start": 0, "end": 1, "text": "hi", "speaker": "S"}])
    r = client.post(f"/api/transcripts/{t.id}/voice-match")
    assert r.status_code == 200
    assert r.json()["job"]["kind"] == "voice_match"
    assert r.json()["job"]["status"] == "pending"


def test_voice_match_route_400_without_stored_audio(client, db_session):
    from database import User as _User, Transcript as _Transcript
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _Transcript(user_id=user.id, title="n", filename="n.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/voice-match")
    assert r.status_code == 400


def test_transcript_serialization_includes_voice_match_job(client, db_session, tmp_path):
    from database import User as _User
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _transcript_with_segments(db_session, user, tmp_path,
                                   [{"start": 0, "end": 1, "text": "hi", "speaker": "S"}])
    client.post(f"/api/transcripts/{t.id}/voice-match")
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["voice_match_job"]["kind"] == "voice_match"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_voice_match_job.py -k route_or_serialization -v` (adjust `-k` to match the three new test names if the substring filter doesn't hit all three, or just run the whole file).
Expected: 404 for the route tests, `KeyError: 'voice_match_job'` for the serialization test.

- [ ] **Step 3: Implement**

In `app.py`, add the route right after `rediarize_transcript` (after its `return {"job": serialize_llm_job(job)}` line):

```python
@app.post("/api/transcripts/{transcript_id}/voice-match")
async def voice_match_transcript(
    transcript_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue a background pass that relabels segments using the voice
    roster — no re-clustering, just matching against enrolled voices."""
    t = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not (t.audio_path and os.path.exists(t.audio_path)):
        raise HTTPException(status_code=400, detail="No stored audio for this transcript")
    job = enqueue_llm_job(db, current_user.id, transcript_id, "voice_match", "", "")
    return {"job": serialize_llm_job(job)}
```

In `_serialize_transcript`, add alongside `summary_job`:

```python
        "summary_job": serialize_llm_job(sj) if (sj := latest_job(db, t.id, "summary")) else None,
        "voice_match_job": serialize_llm_job(vj) if (vj := latest_job(db, t.id, "voice_match")) else None,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: all pass (full suite — this is the last backend task, good checkpoint before frontend work).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_voice_match_job.py
git commit -m "feat: voice-match enqueue route and transcript serialization field"
```

---

### Task 8: Roster page — clip list, add clip, remove clip

**Files:**
- Modify: `static/rack.js`

**Interfaces:**
- Consumes: `GET /api/voices` (now returns `clips`), `POST /api/voices/{id}/clips`, `DELETE /api/voices/{id}/clips/{clip_id}`, `GET /api/voices/{id}/clips/{clip_id}/audio` (Task 3).

- [ ] **Step 1: Add clip list rendering to each profile card**

In `static/rack.js`, in `loadVoices()` (around line 2103), the `cards` template currently renders one row per profile with no expansion. Change the per-card template to include a collapsible clip list. Replace the `cards` mapping:

```javascript
  let expandedVoice = null; // profile id currently showing its clip list

  const cards = voices.map(v => {
    const initials = (v.name || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
    const meta = (v.sample_count || 0) + ' clip' + ((v.sample_count || 0) !== 1 ? 's' : '') + ' · ' + (v.embedding_model || '—');
    const open = expandedVoice === v.id;
    const clipRows = (v.clips || []).map(c => `
      <div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--seg-edge)">
        <button data-clip-play="${c.id}" data-vid="${v.id}" style="background:none;border:1px solid var(--inset-edge);border-radius:3px;width:24px;height:22px;cursor:pointer;font-size:10px;color:var(--label-dim)">▶</button>
        <span style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim)">${c.created_at ? new Date(c.created_at).toLocaleString() : ''}</span>
        <button data-clip-del="${c.id}" data-vid="${v.id}" style="margin-left:auto;background:none;border:none;color:var(--red);cursor:pointer;font-size:11px">Remove</button>
      </div>`).join('') || '<div style="font-size:11.5px;color:var(--label-dim);padding:6px 0">No clips yet</div>';
    return `
    <div class="unit" style="padding:11px 34px">
      <div style="display:grid;grid-template-columns:auto 1fr auto auto;align-items:center;gap:16px;cursor:pointer" data-voice-toggle="${v.id}">
        <div style="width:38px;height:38px;border-radius:50%;background:linear-gradient(155deg,#D4D6D8,#A9ACAF 70%);display:flex;align-items:center;justify-content:center;box-shadow:0 2px 4px rgba(0,0,0,0.5),inset 0 -2px 3px rgba(0,0,0,0.2);font-family:var(--f-cond);font-weight:700;font-size:14px;color:var(--key-ink)">${escapeHtml(initials)}</div>
        <div style="min-width:0">
          <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(v.name)}</div>
          <div style="font-family:var(--f-mono);font-size:10.5px;color:var(--label-dim);margin-top:2px">${escapeHtml(meta)}</div>
        </div>
        <div style="font-size:12px;color:var(--label-dim)">${escapeHtml(v.notes || '')}</div>
        <button class="btn btn--red" data-vdel="${v.id}" style="font-size:11px;padding:5px 12px;background:none">Remove</button>
      </div>
      ${open ? `
      <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--seg-edge)">
        ${clipRows}
        <button data-add-clip="${v.id}" style="margin-top:8px;font-family:var(--f-mono);font-size:11px;background:none;border:1px dashed var(--dash);color:var(--label-dim);padding:6px 10px;border-radius:2px;cursor:pointer">+ Add clip</button>
      </div>` : ''}
    </div>`;
  }).join('');
```

- [ ] **Step 2: Wire up the new event handlers**

In `loadVoices()`, after the existing `root.querySelectorAll('[data-vdel]')...` block, add:

```javascript
  root.querySelectorAll('[data-voice-toggle]').forEach(el => el.addEventListener('click', () => {
    const id = Number(el.dataset.voiceToggle);
    expandedVoice = expandedVoice === id ? null : id;
    loadVoices();
  }));
  let clipAudio = null;
  root.querySelectorAll('[data-clip-play]').forEach(btn => btn.addEventListener('click', () => {
    if (clipAudio) clipAudio.pause();
    clipAudio = new Audio('/api/voices/' + btn.dataset.vid + '/clips/' + btn.dataset.clipPlay + '/audio');
    clipAudio.play().catch(err => toast(err.message, 'error'));
  }));
  root.querySelectorAll('[data-clip-del]').forEach(btn => btn.addEventListener('click', async () => {
    if (!window.confirm('Remove this clip?')) return;
    try {
      await api('/api/voices/' + btn.dataset.vid + '/clips/' + btn.dataset.clipDel, { method: 'DELETE' });
      toast('Clip removed');
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
  }));
  root.querySelectorAll('[data-add-clip]').forEach(btn => btn.addEventListener('click', () => openAddClipModal(Number(btn.dataset.addClip))));
```

- [ ] **Step 3: Add the "add clip" modal**

After `openEnrollModal()`'s closing brace (around line 2197), add:

```javascript
let addClipFile = null;
function openAddClipModal(profileId) {
  addClipFile = null;
  openModal(`
    <div style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:12px">Add a clip</div>
    <div class="field" style="gap:4px;margin-bottom:16px">
      <label class="t-label" style="font-size:12px">Voice sample</label>
      <button id="add-clip-file-btn" style="font-family:var(--f-mono);font-size:11px;background:var(--panel-lo);border:1px dashed var(--dash);color:var(--label-dim);padding:12px;border-radius:2px;cursor:pointer">Choose an audio file…</button>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="btn" id="add-clip-cancel" style="font-size:12px;border-color:var(--inset-edge)">Cancel</button>
      <button id="add-clip-go" style="font-family:var(--f-mono);font-size:11px;font-weight:700;background:${AMBER};color:var(--amber-ink);border:none;padding:8px 14px;border-radius:2px;cursor:pointer">Add clip</button>
    </div>`);
  $('add-clip-cancel').addEventListener('click', closeModal);
  $('add-clip-file-btn').addEventListener('click', () => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'audio/*,.mp3,.wav,.m4a,.flac,.ogg';
    inp.addEventListener('change', () => {
      addClipFile = inp.files[0] || null;
      if (addClipFile) $('add-clip-file-btn').textContent = addClipFile.name;
    });
    inp.click();
  });
  $('add-clip-go').addEventListener('click', async () => {
    if (!addClipFile) { toast('Choose a voice sample first', 'error'); return; }
    const fd = new FormData();
    fd.append('file', addClipFile);
    try {
      await api('/api/voices/' + profileId + '/clips', { method: 'POST', body: fd });
      toast('Clip added');
      closeModal();
      loadVoices();
    } catch (e) { toast(e.message, 'error'); }
  });
}
```

- [ ] **Step 4: Manual verification**

Run: `run.bat` (or however the app is started per `INSTALL.md`), open the Voice roster page in a browser, click a profile card to expand it, add a clip via "+ Add clip", confirm it appears with a working play button, remove it, confirm it disappears and `sample_count` in the card meta line updates on next `loadVoices()`.

- [ ] **Step 5: Commit**

```bash
git add static/rack.js
git commit -m "feat: roster page clip list, add-clip, and remove-clip UI"
```

---

### Task 9: Transcript screen — "Enroll marked clips" decoupled from rename

**Files:**
- Modify: `static/rack.js`

**Interfaces:**
- Consumes: `GET /api/voices` (for the name picker), `POST /api/transcripts/{id}/enroll-speaker` (Task 4, now appends a clip).

- [ ] **Step 1: Remove the enroll-after-rename side effect from `renameSpeaker`**

In `static/rack.js`, `renameSpeaker` (around line 1674) currently does the rename, then if `clips.length`, prompts `window.confirm` to enroll. Simplify it to only handle renaming:

```javascript
async function renameSpeaker(speaker) {
  const t = detailData;
  if (!t) return;
  const name = (window.prompt('Rename "' + speaker + '" to:', speaker) || '').trim();
  if (!name || name === speaker) return;
  try {
    const r = await api('/api/transcripts/' + t.id + '/speakers/rename', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: speaker, to: name }),
    });
    toast('Renamed ' + r.renamed + ' line' + (r.renamed !== 1 ? 's' : '') + ' to ' + name, 'info');
    if (seedClips[speaker]) { seedClips[name] = seedClips[speaker]; delete seedClips[speaker]; }
  } catch (e) { toast(e.message, 'error'); return; }
  await loadTranscriptDetail(t.id, { preserveQuery: true });
}
```

- [ ] **Step 2: Add an "Enroll marked clips" button and picker**

Find where the segment list toolbar/controls are rendered — the detail screen's tab area near `detailTabsHtml()` (around line 1578). Add a new function for the button and picker, placed after `renameSpeaker`:

```javascript
function markedSpeakers() {
  return Object.keys(seedClips).filter(sp => (seedClips[sp] || []).length);
}

async function openEnrollMarkedModal() {
  const speakers = markedSpeakers();
  if (!speakers.length) { toast('No clips flagged — use the ◈ button on a line first', 'error'); return; }
  let voices = [];
  try { voices = await api('/api/voices'); } catch { /* picker still works with just "new name" */ }
  const options = voices.map(v => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)}</option>`).join('');
  const speakerOptions = speakers.map(sp => `<option value="${escapeHtml(sp)}">${escapeHtml(sp)} (${seedClips[sp].length} clip${seedClips[sp].length !== 1 ? 's' : ''})</option>`).join('');
  openModal(`
    <div style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:12px">Enroll marked clips</div>
    <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px">
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px">Flagged speaker</label>
        <select class="inp" id="enroll-marked-speaker" style="font-size:12px;padding:7px 9px">${speakerOptions}</select>
      </div>
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px">Roster name</label>
        <select class="inp" id="enroll-marked-existing" style="font-size:12px;padding:7px 9px">
          <option value="">— New name —</option>${options}
        </select>
        <input class="inp" id="enroll-marked-new" type="text" placeholder="New speaker name" style="font-size:12px;padding:7px 9px;margin-top:6px">
      </div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="btn" id="enroll-marked-cancel" style="font-size:12px;border-color:var(--inset-edge)">Cancel</button>
      <button id="enroll-marked-go" style="font-family:var(--f-mono);font-size:11px;font-weight:700;background:${AMBER};color:var(--amber-ink);border:none;padding:8px 14px;border-radius:2px;cursor:pointer">Enroll</button>
    </div>`);
  $('enroll-marked-cancel').addEventListener('click', closeModal);
  $('enroll-marked-go').addEventListener('click', async () => {
    const sp = $('enroll-marked-speaker').value;
    const existing = $('enroll-marked-existing').value;
    const newName = $('enroll-marked-new').value.trim();
    const name = existing || newName;
    if (!name) { toast('Pick an existing name or type a new one', 'error'); return; }
    const clips = seedClips[sp] || [];
    try {
      const p = await api('/api/transcripts/' + detailData.id + '/enroll-speaker', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, clips }),
      });
      toast('Voice profile "' + p.name + '" now has ' + p.sample_count + ' clip' + (p.sample_count !== 1 ? 's' : ''), 'info');
      delete seedClips[sp];
      closeModal();
      renderDetailBody();
    } catch (e) { toast(e.message, 'error'); }
  });
}
```

- [ ] **Step 3: Add the toolbar button**

Locate the transcript detail toolbar rendering (search `detailTabsHtml()` usage in the page render function, likely `renderDetail()`). Add a button that calls `openEnrollMarkedModal()` when clicked, conditioned on `markedSpeakers().length` — for example, next to wherever the existing search/query input for segments is rendered:

```javascript
`<button id="enroll-marked-btn" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Enroll marked clips</button>`
```

with the corresponding listener added where other detail-screen buttons are bound:

```javascript
const enrollMarkedBtn = $('enroll-marked-btn');
if (enrollMarkedBtn) enrollMarkedBtn.addEventListener('click', openEnrollMarkedModal);
```

(Exact insertion point depends on the current toolbar markup around the query/search box in the transcript detail render function — place it there, following the existing button styling conventions already used by `voice-enroll-btn` on the roster page.)

- [ ] **Step 4: Manual verification**

Start the app, open a transcript with audio, flag 1-2 lines with `◈`, click "Enroll marked clips", pick "New name", type a name, submit. Confirm a toast shows the new sample count, and the roster page (Task 8) shows the new profile with that clip playable.

- [ ] **Step 5: Commit**

```bash
git add static/rack.js
git commit -m "feat: decouple clip enrollment from rename on the transcript screen"
```

---

### Task 10: Transcript screen — bulk select + re-tag

**Files:**
- Modify: `static/rack.js`

**Interfaces:**
- Consumes: `POST /api/transcripts/{id}/segments/retag` (Task 5).

- [ ] **Step 1: Add select-mode state and checkboxes to segment rows**

In `static/rack.js`, near the other detail-screen state (`let detailData = null;` area, around line 1522), add:

```javascript
let selectMode = false;
let selectedSegments = new Set();
```

In `segmentsHtml(t)` (around line 1589), add a checkbox to each row when `selectMode` is on. Modify the row template's opening `<div style="display:flex;gap:16px;...">` to prepend a checkbox and carry the segment's real index (not the filtered-list index, since search can filter):

```javascript
function segmentsHtml(t) {
  const q = (S.query || '').trim().toLowerCase();
  const allSegs = t.segments || [];
  const segs = allSegs
    .map((sg, i) => ({ sg, i }))
    .filter(({ sg }) => !q || (sg.text || '').toLowerCase().includes(q) || (sg.speaker || '').toLowerCase().includes(q));
  if (!segs.length) {
    return '<div style="padding:30px;text-align:center;font-family:var(--f-mono);font-size:11px;color:var(--label-dim)">' +
      (q ? 'NO SEGMENTS MATCH — CLEAR THE SEARCH OR CHECK JOB STATUS' : 'NO SEGMENTS YET — CHECK JOB STATUS') + '</div>';
  }
  const segBtn = 'background:none;border:1px solid var(--inset-edge);border-radius:3px;width:24px;height:22px;cursor:pointer;font-size:10px;padding:0;flex-shrink:0';
  return segs.map(({ sg, i }) => {
    const dot = hashColor(sg.speaker || '');
    const seeded = sg.speaker && (seedClips[sg.speaker] || []).some(c => c.start === sg.start && c.end === sg.end);
    const checkbox = selectMode
      ? `<input type="checkbox" data-seg-select="${i}" ${selectedSegments.has(i) ? 'checked' : ''} style="margin-top:4px;flex-shrink:0">`
      : '';
    const controls = !t.has_audio ? '' : `
      <div style="display:flex;flex-direction:column;gap:4px;flex-shrink:0">
        <button data-seg-play data-start="${sg.start}" data-end="${sg.end}" title="Play this line from the recording" style="${segBtn};color:var(--label-dim)">▶</button>
        ${sg.speaker ? `<button data-seg-seed data-speaker="${escapeHtml(sg.speaker)}" data-start="${sg.start}" data-end="${sg.end}" title="${seeded ? 'Flagged as a voice seed — click to unflag' : 'Flag this line as a voice seed for enrollment'}" style="${segBtn};color:${seeded ? 'var(--nixie)' : 'var(--label-dim)'};${seeded ? 'border-color:var(--nixie);text-shadow:0 0 5px rgba(255,138,61,0.6)' : ''}">◈</button>` : ''}
      </div>`;
    const speakerLabel = sg.speaker
      ? `<span data-seg-rename="${escapeHtml(sg.speaker)}" title="Rename this speaker everywhere" style="font-family:var(--f-cond);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:0.05em;cursor:pointer;border-bottom:1px dotted var(--label-dim)">${escapeHtml(sg.speaker)}</span>`
      : `<span style="font-family:var(--f-cond);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:0.05em">Speaker</span>`;
    return `
    <div style="display:flex;gap:16px;padding:12px 0;border-bottom:1px solid var(--seg-edge)">
      ${checkbox}
      ${controls}
      <div style="font-family:var(--f-mono);font-size:11px;color:var(--nixie);text-shadow:0 0 4px rgba(255,138,61,0.4);width:44px;flex-shrink:0;padding-top:2px">${formatTime(sg.start)}</div>
      <div style="min-width:0">
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">
          <span style="width:7px;height:7px;border-radius:50%;background:${dot};box-shadow:0 0 4px ${dot}"></span>
          ${speakerLabel}
        </div>
        <div style="font-size:13.5px;line-height:1.55;color:var(--body)">${escapeHtml(sg.text || '')}</div>
      </div>
    </div>`;
  }).join('');
}
```

- [ ] **Step 2: Wire up checkbox change and add toolbar controls**

In `detailBodyClick(e)` (around line 1626), add handling for the checkbox (note: checkboxes fire `change`, not `click`, on most browsers reliably, but a delegated `click` listener still works for `<input type=checkbox>` — add it alongside the existing branches):

```javascript
function detailBodyClick(e) {
  const play = e.target.closest('[data-seg-play]');
  if (play) { segPlay(play); return; }
  const seed = e.target.closest('[data-seg-seed]');
  if (seed) { toggleSeed(seed); return; }
  const sel = e.target.closest('[data-seg-select]');
  if (sel) {
    const i = Number(sel.dataset.segSelect);
    if (sel.checked) selectedSegments.add(i); else selectedSegments.delete(i);
    return;
  }
  const ren = e.target.closest('[data-seg-rename]');
  if (ren && !selectMode) { renameSpeaker(ren.dataset.segRename); }
}
```

(Rename is disabled while `selectMode` is on, so clicking a label doesn't accidentally trigger the whole-transcript rename while the user is mid-selection.)

Add the toolbar toggle button and "Re-tag selected" action next to the "Enroll marked clips" button added in Task 9's Step 3:

```javascript
`<button id="select-mode-btn" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">${selectMode ? 'Cancel select' : 'Select lines…'}</button>
${selectMode ? `<button id="retag-selected-btn" class="btn" style="font-size:11px;padding:6px 12px;border-color:var(--inset-edge)">Re-tag selected (${selectedSegments.size})</button>` : ''}`
```

with listeners bound alongside the other detail-screen buttons:

```javascript
const selectModeBtn = $('select-mode-btn');
if (selectModeBtn) selectModeBtn.addEventListener('click', () => {
  selectMode = !selectMode;
  if (!selectMode) selectedSegments.clear();
  renderDetailBody();
});
const retagBtn = $('retag-selected-btn');
if (retagBtn) retagBtn.addEventListener('click', openRetagModal);
```

- [ ] **Step 3: Add the retag modal**

After `openEnrollMarkedModal()` (Task 9), add:

```javascript
async function openRetagModal() {
  if (!selectedSegments.size) { toast('Select at least one line first', 'error'); return; }
  let voices = [];
  try { voices = await api('/api/voices'); } catch { /* picker still works with just "new name" */ }
  const options = voices.map(v => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)}</option>`).join('');
  openModal(`
    <div style="font-family:var(--f-cond);font-weight:700;font-size:16px;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:12px">Re-tag ${selectedSegments.size} line${selectedSegments.size !== 1 ? 's' : ''}</div>
    <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px">
      <div class="field" style="gap:4px">
        <label class="t-label" style="font-size:12px">Correct speaker</label>
        <select class="inp" id="retag-existing" style="font-size:12px;padding:7px 9px">
          <option value="">— New name —</option>${options}
        </select>
        <input class="inp" id="retag-new" type="text" placeholder="New speaker name" style="font-size:12px;padding:7px 9px;margin-top:6px">
      </div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="btn" id="retag-cancel" style="font-size:12px;border-color:var(--inset-edge)">Cancel</button>
      <button id="retag-go" style="font-family:var(--f-mono);font-size:11px;font-weight:700;background:${AMBER};color:var(--amber-ink);border:none;padding:8px 14px;border-radius:2px;cursor:pointer">Re-tag</button>
    </div>`);
  $('retag-cancel').addEventListener('click', closeModal);
  $('retag-go').addEventListener('click', async () => {
    const existing = $('retag-existing').value;
    const newName = $('retag-new').value.trim();
    const name = existing || newName;
    if (!name) { toast('Pick an existing name or type a new one', 'error'); return; }
    try {
      const r = await api('/api/transcripts/' + detailData.id + '/segments/retag', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ indices: Array.from(selectedSegments), speaker: name }),
      });
      toast('Re-tagged ' + r.retagged + ' line' + (r.retagged !== 1 ? 's' : ''), 'info');
      selectMode = false;
      selectedSegments = new Set();
      closeModal();
      await loadTranscriptDetail(detailData.id, { preserveQuery: true });
    } catch (e) { toast(e.message, 'error'); }
  });
}
```

- [ ] **Step 4: Manual verification**

Open a transcript, click "Select lines…", check 2-3 lines, click "Re-tag selected", pick or type a name, submit. Confirm only the checked lines change speaker, everything else stays as it was, `corrected_text` tab is unaffected.

- [ ] **Step 5: Commit**

```bash
git add static/rack.js
git commit -m "feat: bulk select and re-tag mis-diarized segments by index"
```

---

### Task 11: "Match against voice roster" button and job progress

**Files:**
- Modify: `static/rack.js`

**Interfaces:**
- Consumes: `POST /api/transcripts/{id}/voice-match` (Task 7), `voice_match_job` field on the transcript object (Task 7), existing `jobRunningUnit(job, label)` and `llmJobActive(job)` helpers.

- [ ] **Step 1: Include `voice_match_job` in the poll fingerprint**

In `_jobFingerprint(t)` (around line 1554), add the new job to the fingerprint so polling picks up its progress:

```javascript
function _jobFingerprint(t) {
  const f = (j) => j ? j.status + ':' + (j.progress ? j.progress.done : 0) : '-';
  return f(t.correction_job) + '|' + f(t.summary_job) + '|' + f(t.voice_match_job);
}
```

In `scheduleDetailPoll()` (around line 1561), include it in the active-job check:

```javascript
  if (!t || !(llmJobActive(t.correction_job) || llmJobActive(t.summary_job) || llmJobActive(t.voice_match_job))) return;
```

- [ ] **Step 2: Add the button and running-state unit**

Find wherever the existing "Re-diarize" trigger lives in the transcript detail screen (search for `/rediarize` in `static/rack.js`'s fetch/`api(` calls) and add a sibling button "Match against voice roster" with the same `has_audio` gating, calling:

```javascript
async function runVoiceMatch() {
  const t = detailData;
  if (!t) return;
  try {
    await api('/api/transcripts/' + t.id + '/voice-match', { method: 'POST' });
    toast('Matching against voice roster…', 'info');
    await loadTranscriptDetail(t.id, { preserveQuery: true });
  } catch (e) { toast(e.message, 'error'); }
}
```

Render its running/error state the same way correction/summary already do — wherever the transcript tab body checks `llmJobActive(t.correction_job)` to show `jobRunningUnit`, add an equivalent check for `t.voice_match_job`:

```javascript
if (llmJobActive(t.voice_match_job)) {
  // render alongside/instead of the segment list, matching how
  // correctedHtml() renders jobRunningUnit(t.correction_job, 'Correction')
}
```

using `jobRunningUnit(t.voice_match_job, 'Voice match')` (existing helper, already used by `correctedHtml()` for the correction job — same call shape).

- [ ] **Step 3: Manual verification**

With `librosa` installed (already done) and a roster profile with at least one clip (from Task 8/9), open a transcript with audio and multiple diarized segments, click "Match against voice roster", watch the progress line update ("running — section X of Y"), confirm on completion that segments matching the enrolled voice got relabeled and others didn't.

- [ ] **Step 4: Commit**

```bash
git add static/rack.js
git commit -m "feat: match-against-voice-roster button and job progress display"
```

---

## Self-Review Notes

- **Spec coverage:** Data model (Task 1-2), roster page (Task 8), transcript-screen enroll decoupling (Task 4, 9), bulk re-tag (Task 5, 10), voice-match job (Task 6-7, 11), `identify()` empty-profile guard (Task 2) — all spec sections have a task.
- **Type consistency:** `add_clip(db, profile_id, user_id, audio_path, source_transcript_id=None)` signature is identical everywhere it's called (Tasks 3, 4, 6 use it consistently — Task 6 doesn't call it directly, only Tasks 3-4 do). `voice_id_service` becomes a shared module-level singleton starting Task 6 — Tasks 3 and 4's routes were written against `app.voice_id_service` as a patchable attribute either way, which still works since `app.py` imports the same singleton object.
- **Placeholder scan:** no TBD/TODO; Task 9 Step 3 and Task 11 Step 2 note "exact insertion point depends on current markup" because the toolbar's precise line location shifts as earlier tasks land — this is a pointer to search for an anchor (`detailTabsHtml()` usage, `/rediarize` call site), not an unresolved requirement.
