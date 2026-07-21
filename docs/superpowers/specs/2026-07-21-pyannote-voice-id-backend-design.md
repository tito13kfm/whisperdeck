# Wire Up pyannote.audio as a Voice-ID Embedding Backend — Design Spec

**Goal:** Implement `_embed_pyannote()` in `services/voice_id.py` and wire it into `_detect_backend()`, so pyannote.audio becomes a real third voice-ID embedding backend (priority: speechbrain > pyannote > librosa, per README). Closes [issue #38](https://github.com/tito13kfm/whisperdeck/issues/38).

**Background:** `_detect_backend()` currently has an explicit comment skipping pyannote detection because no embedding extractor exists — confirmed via git history (`19e2a58`) this was deliberately removed, not a regression. `backend_name` already has an unreachable `"pyannote": "pyannote/embedding"` label. `services/diarization.py` already depends on pyannote.audio (>=3.3.0, pinned in `requirements-diarization.txt` alongside `torch>=2.2.0`) for diarization via `diarize_pyannote()`, which establishes the hf_token-threading and soundfile-based-audio-loading patterns this spec reuses.

While scoping this, an independent design review (advisor pass) surfaced two adjacent defects in the existing embedding-storage logic that this change makes materially more likely to actually trigger, both now bundled into this spec (with user sign-off):

1. `identify()` compares a probe embedding against every enrolled profile's stored embedding by raw cosine similarity, with no check that they came from the same embedding model. Two different embedding spaces (or just different vector lengths) produce a meaningless similarity score, or a crash.
2. When embedding extraction falls back to MFCC (e.g. speechbrain or pyannote raises but librosa is available), the stored `embedding_model` is set from the *detected* backend (`self.backend_name`), not the embedder that actually produced the vector. A profile whose clips end up mixing real-embedder and fallback-MFCC vectors averages incompatible vectors in `_recompute_profile_embedding` — a ragged array whose `np.mean` either crashes or silently produces garbage.

Both existed before this change (present for speechbrain today) but pyannote adds a new failure mode — HF auth/gating failure, not just a missing dependency — that raises the odds of the fallback path actually firing in practice.

**Out of scope (explicitly deferred, not part of this change):** making `enroll`/`add_clip`/`identify` async / offloading to `run_in_executor`. These are fully synchronous today (true for speechbrain already; already tracked separately as an observed gap). Pyannote's first-call model download adds to the amount of event-loop time this blocks for, but fixing that is a broader refactor (touches every call site and every test in `test_voice_id.py`) that stands on its own rather than being folded into "wire up a new backend." Left as a known, accepted limitation.

## Scope

**In scope:**
- `_embed_pyannote()` using `pyannote/wespeaker-voxceleb-resnet34-LM` (the pyannote.audio 3.x-native embedding model — not the legacy `pyannote/embedding` label currently hardcoded, whose compatibility with the pinned 3.3.0+ is unverified).
- `_detect_backend()` picks up pyannote between speechbrain and librosa.
- `hf_token` threaded through `enroll()`/`add_clip()`/`identify()` and their three `app.py` route call sites, mirroring `diarize_pyannote`'s existing `get_user_settings(...).get("hf_token")` pattern.
- Per-clip `embedding_model` tracking (new `VoiceClip.embedding_model` column) reflecting the embedder that *actually* produced that clip's vector, not the detected backend.
- `add_clip()` refuses to mix embedding models within one profile (raises `ValueError` telling the user to switch backends back or enroll as a separate profile) instead of silently averaging incompatible vectors.
- `identify()` skips profiles whose `embedding_model` doesn't match the probe's, before computing similarity (length check kept as a cheap secondary guard).
- Docs: `requirements-diarization.txt` comment note, `ROADMAP.md` moved to Done.
- Tests: existing backend-detection test flipped, new tests for pyannote embedding + mismatch-guard + cross-model-rejection, following the existing `monkeypatch`/fake-module-injection style (nothing in this dev environment has torch/pyannote.audio/speechbrain/librosa installed — same as today).

**Explicitly out of scope:**
- Async/executor-offload of embedding extraction (see Background).
- Any UI change — `embedding_model` is already surfaced in `list_profiles()`; no new frontend work needed.
- Re-backfilling `embedding_model` on pre-migration `VoiceClip` rows (see Migration below — treated leniently, not rewritten).

## Data model

```python
# database/__init__.py, VoiceClip
embedding_model = Column(String(64), nullable=True)  # backend that produced THIS clip's vector; NULL = pre-migration row
```

`ensure_columns(engine, "voice_clips", {"embedding_model": "TEXT"})` added alongside the existing migration calls. Nullable and lenient by design: pre-migration rows have no recorded value, and both the mismatch-guard and `identify()`'s skip-check treat `None` as "unknown, don't block" rather than "mismatch" — so existing installs don't lose match capability for profiles enrolled before this change ships.

`VoiceProfile.embedding_model` (existing column) keeps its current meaning (the model backing the profile's current mean embedding) but is now derived from clips' actual `embedding_model` values in `_recompute_profile_embedding`, not from `self.backend_name` at recompute time.

## `services/voice_id.py` changes

### `_extract_embedding` returns `(embedding, model_id)` instead of a bare array

```python
def _extract_embedding(self, audio_path: str, hf_token: Optional[str] = None) -> Optional[tuple[np.ndarray, str]]:
    if self._backend == "speechbrain":
        embedding = self._embed_speechbrain(audio_path)
        if embedding is not None:
            return embedding, "speechbrain/spkrec-ecapa-voxceleb"
        return self._mfcc_fallback(audio_path)
    elif self._backend == "pyannote":
        embedding = self._embed_pyannote(audio_path, hf_token=hf_token)
        if embedding is not None:
            return embedding, "pyannote/wespeaker-voxceleb-resnet34-LM"
        return self._mfcc_fallback(audio_path)
    elif self._backend == "librosa_mfcc":
        return self._mfcc_fallback(audio_path)
    return None

def _mfcc_fallback(self, audio_path: str) -> Optional[tuple[np.ndarray, str]]:
    embedding = self._embed_mfcc(audio_path)
    return (embedding, "MFCC fingerprint (librosa)") if embedding is not None else None
```

Model-id strings reuse the exact labels already in `backend_name`'s dict, so `embedding_model` stays consistent with what `list_profiles()` already displays — no new vocabulary introduced.

### `_embed_pyannote(self, audio_path, hf_token=None)`

```python
def _get_pyannote_inference(self, hf_token: Optional[str] = None):
    """Build the pyannote Inference wrapper once and cache it — mirrors
    _get_classifier. Only cached on success: a failed from_pretrained
    (bad/missing token) leaves it None so a later call with a valid
    token can retry."""
    if self._pyannote_inference is None:
        from pyannote.audio import Model, Inference
        model = Model.from_pretrained(
            "pyannote/wespeaker-voxceleb-resnet34-LM",
            token=hf_token or os.environ.get("HUGGINGFACE_TOKEN", None),
        )
        self._pyannote_inference = Inference(model, window="whole")
    return self._pyannote_inference

def _embed_pyannote(self, audio_path: str, hf_token: Optional[str] = None) -> Optional[np.ndarray]:
    """Extract embedding using pyannote.audio's wespeaker model.

    wespeaker-voxceleb-resnet34-LM is a separately-gated HF repo from the
    speaker-diarization model diarize_pyannote() uses — a token that works
    for diarization may still 401 here until this model's own license is
    accepted."""
    try:
        import torch
        import soundfile as sf

        inference = self._get_pyannote_inference(hf_token)
        data, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        waveform = torch.from_numpy(data.T)  # (channel, time)
        if waveform.shape[1] > sample_rate * 30:
            waveform = waveform[:, :sample_rate * 30]
        embedding = inference({"waveform": waveform, "sample_rate": sample_rate})
        return np.asarray(embedding).reshape(-1)
    except Exception as e:
        self._last_backend_error = f"pyannote: {e}"
        return None
```

Notes carried over from the advisor pass:
- Loads audio via `soundfile` + `torch.from_numpy`, not pyannote's built-in decoder — same torchcodec/FFmpeg-DLL dodge already documented in `diarization.py:204-208`.
- Caps input to 30s, matching `_embed_speechbrain`'s and `_embed_mfcc`'s existing truncation (native `window="whole"` has no built-in cap).
- `np.asarray(...).reshape(-1)` flattens whatever shape `Inference` returns to a flat vector, matching `_embed_speechbrain`'s `.squeeze()` before storing as a JSON list.
- **Implementation-time check** (can't be verified in this dev environment — nothing here has pyannote.audio installed): confirm `token=` is still the correct kwarg name on `Model.from_pretrained` for the pinned `pyannote.audio>=3.3.0` (not the pre-3.1 `use_auth_token`). Verify against the installed package wherever this actually runs with `requirements-diarization.txt` present.

### `_detect_backend()`

```python
def _detect_backend(self) -> str:
    try:
        import speechbrain  # noqa
        return "speechbrain"
    except ImportError:
        pass
    try:
        import pyannote.audio  # noqa
        return "pyannote"
    except ImportError:
        pass
    try:
        import librosa  # noqa
        return "librosa_mfcc"
    except ImportError:
        pass
    return "none"
```

`backend_name`'s `"pyannote"` label updates to `"pyannote/wespeaker-voxceleb-resnet34-LM"`.

### `__init__`

Add `self._pyannote_inference = None` alongside the existing `self._classifier = None`.

### `enroll()` / `add_clip()` / `_persist_clip()` — hf_token + per-clip model tracking

```python
def enroll(self, db, user_id, name, audio_path, notes="", hf_token=None) -> VoiceProfile:
    result = self._extract_embedding(audio_path, hf_token=hf_token)
    if result is None:
        # ... same "no backend" / "extraction failed" ValueError branches as today
    embedding, model_id = result

    profile = db.query(VoiceProfile).filter(
        VoiceProfile.user_id == user_id, VoiceProfile.name == name
    ).first()
    if not profile:
        profile = VoiceProfile(user_id=user_id, name=name, embedding=None,
                                embedding_model=model_id, sample_count=0, notes=notes)
        db.add(profile)
        db.commit()
    elif notes:
        profile.notes = notes
        db.commit()

    self._persist_clip(db, profile, audio_path, embedding, model_id)
    db.refresh(profile)
    return profile

def add_clip(self, db, profile_id, user_id, audio_path, source_transcript_id=None, hf_token=None) -> VoiceClip:
    profile = db.query(VoiceProfile).filter(
        VoiceProfile.id == profile_id, VoiceProfile.user_id == user_id
    ).first()
    if not profile:
        raise ValueError(f"Voice profile {profile_id} not found")

    result = self._extract_embedding(audio_path, hf_token=hf_token)
    if result is None:
        # ... same ValueError as today
    embedding, model_id = result

    existing = db.query(VoiceClip).filter(VoiceClip.voice_profile_id == profile.id).all()
    mismatch = next((c for c in existing if c.embedding_model and c.embedding_model != model_id), None)
    if mismatch:
        raise ValueError(
            f"This clip was extracted using {model_id}, but profile '{profile.name}' "
            f"already has clips extracted using {mismatch.embedding_model}. Mixing "
            f"embedding models within one profile isn't supported — switch backends "
            f"back, or enroll this speaker as a separate profile."
        )

    return self._persist_clip(db, profile, audio_path, embedding, model_id, source_transcript_id)

def _persist_clip(self, db, profile, audio_path, embedding, model_id, source_transcript_id=None) -> VoiceClip:
    clip = VoiceClip(
        voice_profile_id=profile.id,
        audio_path=audio_path,
        embedding=embedding.tolist() if isinstance(embedding, np.ndarray) else embedding,
        embedding_model=model_id,
        source_transcript_id=source_transcript_id,
    )
    db.add(clip)
    db.commit()
    self._recompute_profile_embedding(db, profile)
    return clip
```

`remove_clip()` is unaffected (no embedding extraction happens there).

### `_recompute_profile_embedding()`

```python
def _recompute_profile_embedding(self, db, profile) -> None:
    clips = db.query(VoiceClip).filter(VoiceClip.voice_profile_id == profile.id).all()
    if not clips:
        profile.embedding = None
        profile.sample_count = 0
    else:
        stacked = np.array([c.embedding for c in clips])
        profile.embedding = np.mean(stacked, axis=0).tolist()
        profile.sample_count = len(clips)
        latest_model = next((c.embedding_model for c in reversed(clips) if c.embedding_model), None)
        if latest_model:
            profile.embedding_model = latest_model
    profile.updated_at = utcnow_naive()
    db.commit()
```

`add_clip`'s mismatch guard means all of a profile's clips share one non-null `embedding_model` going forward (or are legacy nulls) — `stacked = np.array([...])` stays a clean rectangular array, no ragged-array crash. `latest_model` walks from the newest clip backward so a profile whose only clips are pre-migration (all `None`) keeps whatever `embedding_model` it already had.

### `identify()`

```python
def identify(self, db, user_id, audio_path, threshold=0.65, hf_token=None) -> list[dict]:
    result = self._extract_embedding(audio_path, hf_token=hf_token)
    if result is None:
        return []
    probe_embedding, probe_model = result

    profiles = db.query(VoiceProfile).filter(VoiceProfile.user_id == user_id).all()
    if not profiles:
        return []

    results = []
    for profile in profiles:
        if profile.embedding is None:
            continue
        if profile.embedding_model and profile.embedding_model != probe_model:
            continue  # different embedding space — comparing is meaningless
        stored = np.array(profile.embedding)
        if len(stored) != len(probe_embedding):
            continue  # secondary guard: same-label-different-dim edge case
        similarity = self._cosine_similarity(probe_embedding, stored)
        if similarity >= threshold:
            results.append({
                "id": profile.id, "name": profile.name,
                "similarity": round(float(similarity), 4),
                "sample_count": profile.sample_count,
            })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results
```

A profile with `embedding_model=None` (fully pre-migration, never touched since) is still compared — leniency for existing installs — guarded only by the length check.

## `app.py` changes

Three route handlers gain the same `get_user_settings` + `hf_token` pass-through already used by the diarization route:

```python
# POST /api/voices/enroll
user_settings = get_user_settings(db, current_user.id)
profile = voice_id_service.enroll(db, current_user.id, name=name, audio_path=str(save_path),
                                   notes=notes, hf_token=user_settings.get("hf_token"))

# POST /api/voices/identify
user_settings = get_user_settings(db, current_user.id)
matches = voice_id_service.identify(db, current_user.id, str(save_path), threshold=threshold,
                                     hf_token=user_settings.get("hf_token"))

# POST /api/voices/{profile_id}/clips
user_settings = get_user_settings(db, current_user.id)
clip = voice_id_service.add_clip(db, profile_id, current_user.id, str(save_path),
                                  hf_token=user_settings.get("hf_token"))
```

No other route or response-shape changes.

## Docs

- `requirements-diarization.txt`: add a comment line noting it also unlocks the pyannote voice-ID backend (no new pip package — reuses the pinned `torch` + `pyannote.audio`), and that `wespeaker-voxceleb-resnet34-LM` is a separately-gated HF model from the diarization pipeline (accepting the diarization model's license doesn't automatically grant access to this one).
- `ROADMAP.md`: move "pyannote.audio voice-ID embedding backend" from **In Progress** to **Done**.

## Testing

All mocked via `monkeypatch`/`sys.modules` fake-injection, same style as every existing test in `test_voice_id.py` (nothing in this dev environment has torch/pyannote.audio/speechbrain/librosa installed regardless of backend — this isn't a new gap).

- `test_detect_backend_skips_unimplemented_pyannote` → flip to assert pyannote **is** now selected when speechbrain is absent and `pyannote.audio` imports successfully (rename to reflect the new behavior).
- New: `_embed_pyannote` extracts and caches `_pyannote_inference` across calls (mirrors `test_embed_speechbrain_caches_classifier_across_calls`).
- New: `_extract_embedding` falls back to MFCC when pyannote embedding raises (mirrors the existing speechbrain-fallback test).
- New: `add_clip` raises `ValueError` when a clip's extracted `model_id` doesn't match an existing clip's `embedding_model` on the same profile.
- New: `identify` skips a profile whose `embedding_model` doesn't match the probe's model, even when `threshold` would otherwise pass on raw cosine similarity of mismatched vectors (construct same-length-different-space vectors to prove the model-check fires, not just the length-check).
- New: `_recompute_profile_embedding` doesn't crash and correctly sets `profile.embedding_model` when clips include legacy `embedding_model=None` rows mixed with real ones.
- Existing enroll/add_clip/identify tests updated for the new `(embedding, model_id)` tuple return from mocked `_extract_embedding`.
