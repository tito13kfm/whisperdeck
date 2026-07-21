# pyannote.audio Voice-ID Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up pyannote.audio (`pyannote/wespeaker-voxceleb-resnet34-LM`) as a real third voice-ID embedding backend in `services/voice_id.py`, and fix two adjacent defects the change makes more likely to fire: cross-embedding-model averaging/comparison in `_recompute_profile_embedding`/`identify()`.

**Architecture:** `_extract_embedding` changes its return type from a bare `np.ndarray` to `(embedding, model_id)`, so every caller knows which embedder actually produced a vector rather than trusting `self.backend_name` (which reflects the detected backend, not what a fallback path actually used). A new `VoiceClip.embedding_model` column persists that per-clip. `add_clip` refuses to mix models within one profile; `identify` skips profiles whose model doesn't match the probe's.

**Tech Stack:** Python, FastAPI, SQLAlchemy, pyannote.audio 3.x (`Model`/`Inference`), pytest with `monkeypatch`/`sys.modules` fake-injection (no real ML deps installed in this dev environment — same as every existing backend test today).

**Spec:** `docs/superpowers/specs/2026-07-21-pyannote-voice-id-backend-design.md`

**Note on baseline test state:** `pytest tests/test_voice_id.py` has one pre-existing failure unrelated to this work — `test_embed_speechbrain_caches_classifier_across_calls` fails because `torch` isn't installed in this environment (`tests/test_voice_id.py:45` does a real `import torch`). This plan's new tests fully fake-inject `torch`/`soundfile`/`pyannote.audio` into `sys.modules` instead of doing real imports, specifically to avoid adding more instances of that same problem — they should pass cleanly. When running the suite, expect 1 pre-existing failure (that test) before and after this plan; don't chase it as a regression.

---

## File Structure

- **Modify:** `database/__init__.py` — new `VoiceClip.embedding_model` column + migration.
- **Modify:** `services/voice_id.py` — `_extract_embedding` return-type change, `_embed_pyannote`, `_detect_backend`, `enroll`/`add_clip`/`_persist_clip`/`_recompute_profile_embedding`/`identify` updates.
- **Modify:** `app.py` — `hf_token` threading into the 3 voice route handlers.
- **Modify:** `tests/test_voice_id.py` — updated existing tests (new tuple return shape), new tests for pyannote + mismatch guard + legacy-null handling.
- **Modify:** `requirements-diarization.txt`, `docs/ROADMAP.md` — doc updates.

---

### Task 1: `VoiceClip.embedding_model` column + migration

**Files:**
- Modify: `database/__init__.py:146-157` (VoiceClip class), `database/__init__.py:304-305` (migration calls)

- [ ] **Step 1: Add the column**

In `database/__init__.py`, `VoiceClip` class (currently lines 146-157):

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
    embedding_model = Column(String(64), nullable=True)  # backend that produced THIS clip's vector; NULL = pre-migration row
    source_transcript_id = Column(Integer, ForeignKey("transcripts.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
```

- [ ] **Step 2: Add the migration**

In `database/__init__.py`, immediately after the existing line 305 (`ensure_columns(engine, "users", {"is_admin": ...})`), add:

```python
    ensure_columns(engine, "voice_clips", {"embedding_model": "TEXT"})
```

- [ ] **Step 3: Verify the migration runs cleanly**

Run: `cd C:\Claude\WhisperDeck && python -c "from database import init_db; init_db()"`
Expected: no errors (creates/updates `data/whisperdeck.db` or the configured DB path with the new column). If a test DB already exists at that path, this proves `ensure_columns` is idempotent (its existing pattern already handles "column already exists" — verify no exception either way).

- [ ] **Step 4: Commit**

```bash
git add database/__init__.py
git commit -m "feat: add VoiceClip.embedding_model column"
```

---

### Task 2: `_extract_embedding` returns `(embedding, model_id)`, update all callers of the old shape

**Files:**
- Modify: `services/voice_id.py:265-276` (`_extract_embedding`)
- Test: `tests/test_voice_id.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_voice_id.py`, replace `test_extract_embedding_falls_back_to_mfcc_when_speechbrain_fails` (currently lines 55-64) with:

```python
def test_extract_embedding_falls_back_to_mfcc_when_speechbrain_fails(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_embed_speechbrain", lambda path: None)
    fallback = np.array([4.0, 5.0, 6.0])
    monkeypatch.setattr(svc, "_embed_mfcc", lambda path: fallback)

    result = svc._extract_embedding("fake.wav")

    assert result is not None
    embedding, model_id = result
    assert np.array_equal(embedding, fallback)
    assert model_id == "MFCC fingerprint (librosa)"


def test_extract_embedding_speechbrain_success_reports_speechbrain_model(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    ok = np.array([1.0, 2.0, 3.0])
    monkeypatch.setattr(svc, "_embed_speechbrain", lambda path: ok)

    result = svc._extract_embedding("fake.wav")

    embedding, model_id = result
    assert np.array_equal(embedding, ok)
    assert model_id == "speechbrain/spkrec-ecapa-voxceleb"


def test_extract_embedding_librosa_backend_reports_mfcc_model(tmp_path, monkeypatch):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    svc._backend = "librosa_mfcc"
    ok = np.array([1.0, 2.0])
    monkeypatch.setattr(svc, "_embed_mfcc", lambda path: ok)

    result = svc._extract_embedding("fake.wav")

    embedding, model_id = result
    assert np.array_equal(embedding, ok)
    assert model_id == "MFCC fingerprint (librosa)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "extract_embedding" -v`
Expected: FAIL — `_extract_embedding` still returns a bare array, so `embedding, model_id = result` raises `TypeError: cannot unpack non-iterable numpy.ndarray object` (or similar).

- [ ] **Step 3: Implement the new `_extract_embedding`**

In `services/voice_id.py`, replace `_extract_embedding` (currently lines 265-276):

```python
    def _extract_embedding(self, audio_path: str, hf_token: Optional[str] = None) -> Optional[tuple]:
        """Extract a speaker embedding vector from an audio file, tagged with
        the model that actually produced it (not just the detected backend —
        a fallback to MFCC must be labeled as MFCC, not as whatever backend
        was guessed at startup, or _recompute_profile_embedding ends up
        averaging incompatible vectors). Falls back to MFCC if the primary
        backend fails on this call."""
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

    def _mfcc_fallback(self, audio_path: str) -> Optional[tuple]:
        embedding = self._embed_mfcc(audio_path)
        return (embedding, "MFCC fingerprint (librosa)") if embedding is not None else None
```

Note: this task does NOT implement `_embed_pyannote` yet (that's Task 3) — the `elif self._backend == "pyannote"` branch is added now but `_backend` can never actually be `"pyannote"` yet (Task 4 wires up detection), so this compiles and the two new/updated tests above pass without needing `_embed_pyannote` to exist.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "extract_embedding" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/voice_id.py tests/test_voice_id.py
git commit -m "feat: _extract_embedding returns (embedding, model_id) tuple"
```

---

### Task 3: `_embed_pyannote` implementation

**Files:**
- Modify: `services/voice_id.py:26-31` (`__init__`), add new methods near `_embed_speechbrain`
- Test: `tests/test_voice_id.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_voice_id.py`:

```python
def test_embed_pyannote_caches_inference_across_calls(tmp_path, monkeypatch):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    svc._backend = "pyannote"

    calls = {"instantiated": 0}

    class FakeInference:
        def __init__(self, model, window):
            calls["instantiated"] += 1
            self.model = model
            self.window = window

        def __call__(self, audio_dict):
            return np.array([1.0, 2.0, 3.0])

    class FakeModel:
        @staticmethod
        def from_pretrained(name, token=None):
            return FakeModel()

    fake_pyannote_audio = types.SimpleNamespace(Model=FakeModel, Inference=FakeInference)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_pyannote_audio)

    fake_torch = types.SimpleNamespace(from_numpy=lambda arr: arr)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    fake_data = np.array([[0.1], [0.2]])  # (frames, channels), always_2d=True shape
    fake_soundfile = types.SimpleNamespace(read=lambda path, dtype, always_2d: (fake_data, 16000))
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)

    svc._embed_pyannote("fake1.wav")
    svc._embed_pyannote("fake2.wav")

    assert calls["instantiated"] == 1


def test_embed_pyannote_returns_flat_vector(tmp_path, monkeypatch):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    svc._backend = "pyannote"

    class FakeInference:
        def __init__(self, model, window):
            pass

        def __call__(self, audio_dict):
            return np.array([[1.0, 2.0, 3.0]])  # deliberately not pre-flattened

    class FakeModel:
        @staticmethod
        def from_pretrained(name, token=None):
            return FakeModel()

    monkeypatch.setitem(sys.modules, "pyannote.audio", types.SimpleNamespace(Model=FakeModel, Inference=FakeInference))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(from_numpy=lambda arr: arr))
    fake_data = np.array([[0.1], [0.2]])
    monkeypatch.setitem(sys.modules, "soundfile", types.SimpleNamespace(read=lambda path, dtype, always_2d: (fake_data, 16000)))

    result = svc._embed_pyannote("fake.wav")

    assert result.shape == (3,)
    assert np.array_equal(result, np.array([1.0, 2.0, 3.0]))


def test_embed_pyannote_sets_last_backend_error_on_failure(tmp_path, monkeypatch):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    svc._backend = "pyannote"

    class FakeModel:
        @staticmethod
        def from_pretrained(name, token=None):
            raise RuntimeError("401 Client Error: gated repo, accept license first")

    monkeypatch.setitem(sys.modules, "pyannote.audio", types.SimpleNamespace(Model=FakeModel, Inference=object))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(from_numpy=lambda arr: arr))
    monkeypatch.setitem(sys.modules, "soundfile", types.SimpleNamespace(read=lambda path, dtype, always_2d: (np.array([[0.1]]), 16000)))

    result = svc._embed_pyannote("fake.wav", hf_token="bad-token")

    assert result is None
    assert "pyannote" in svc._last_backend_error
    assert "gated repo" in svc._last_backend_error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "embed_pyannote" -v`
Expected: FAIL with `AttributeError: 'VoiceIdentificationService' object has no attribute '_embed_pyannote'`

- [ ] **Step 3: Implement `_embed_pyannote` and its cache**

In `services/voice_id.py`, `__init__` (currently lines 26-31), add the new cache attribute:

```python
    def __init__(self, voices_dir: str = _DEFAULT_VOICES_DIR):
        self.voices_dir = voices_dir
        os.makedirs(voices_dir, exist_ok=True)
        self._backend = self._detect_backend()
        self._classifier = None  # cached speechbrain EncoderClassifier
        self._pyannote_inference = None  # cached pyannote Inference wrapper
        self._last_backend_error = None
```

Then add these two new methods right after `_embed_speechbrain` (which ends around line 308):

```python
    def _get_pyannote_inference(self, hf_token: Optional[str] = None):
        """Build the pyannote Inference wrapper once and cache it — mirrors
        _get_classifier. Only cached on success: a failed from_pretrained
        (bad/missing token, license not accepted) leaves it None so a later
        call with a valid token/accepted license can retry."""
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
        speaker-diarization model diarize_pyannote() uses in
        services/diarization.py — a token that works for diarization may
        still 401 here until this model's own license is accepted."""
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "embed_pyannote" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/voice_id.py tests/test_voice_id.py
git commit -m "feat: implement _embed_pyannote using pyannote.audio wespeaker model"
```

---

### Task 4: Wire pyannote into `_detect_backend` and `backend_name`

**Files:**
- Modify: `services/voice_id.py:33-58`
- Test: `tests/test_voice_id.py`

- [ ] **Step 1: Update the failing/changing test**

In `tests/test_voice_id.py`, replace `test_detect_backend_skips_unimplemented_pyannote` (currently lines 67-79) with:

```python
def test_detect_backend_picks_pyannote_when_speechbrain_absent(tmp_path, monkeypatch):
    # Historically, pyannote.audio importing successfully made _detect_backend
    # pick "pyannote" even though _extract_embedding had no pyannote branch,
    # so every enroll/identify call silently returned None. That's now fixed
    # (see Task 2/3) — pyannote should be picked and actually work.
    monkeypatch.setitem(sys.modules, "speechbrain", None)
    monkeypatch.setitem(sys.modules, "librosa", None)
    monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
    monkeypatch.setitem(sys.modules, "pyannote.audio", types.ModuleType("pyannote.audio"))

    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))

    assert svc._backend == "pyannote"
    assert svc.backend_name == "pyannote/wespeaker-voxceleb-resnet34-LM"


def test_detect_backend_prefers_speechbrain_over_pyannote(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "speechbrain", types.ModuleType("speechbrain"))
    monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
    monkeypatch.setitem(sys.modules, "pyannote.audio", types.ModuleType("pyannote.audio"))

    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))

    assert svc._backend == "speechbrain"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "detect_backend" -v`
Expected: FAIL — `svc._backend == "pyannote"` is False (still skipped) and `backend_name` lookup for `"pyannote"` key still returns the old `"pyannote/embedding"` label.

- [ ] **Step 3: Implement**

In `services/voice_id.py`, replace `_detect_backend` and `backend_name` (currently lines 33-58):

```python
    def _detect_backend(self) -> str:
        """Detect which embedding backend is available."""
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

    @property
    def backend_name(self) -> str:
        names = {
            "speechbrain": "speechbrain/spkrec-ecapa-voxceleb",
            "pyannote": "pyannote/wespeaker-voxceleb-resnet34-LM",
            "librosa_mfcc": "MFCC fingerprint (librosa)",
            "none": "No backend available — install speechbrain",
        }
        return names.get(self._backend, "unknown")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "detect_backend" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/voice_id.py tests/test_voice_id.py
git commit -m "feat: detect pyannote backend, update backend_name label"
```

---

### Task 5: `enroll`/`add_clip`/`_persist_clip` — hf_token + per-clip model tracking + cross-model rejection

**Files:**
- Modify: `services/voice_id.py:60-176` (`enroll`, `add_clip`, `_persist_clip`)
- Test: `tests/test_voice_id.py`

This task changes the shape every caller of `_extract_embedding` relies on, so every existing test that monkeypatches `_extract_embedding` to return a bare array must be updated to return a `(array, model_id)` tuple instead.

- [ ] **Step 1: Update existing tests to the new tuple-return mocking shape, write new failing tests**

In `tests/test_voice_id.py`, update every `monkeypatch.setattr(svc, "_extract_embedding", lambda path: np.array(...))` call to return a tuple. Specifically:

`test_add_clip_creates_row_and_sets_profile_embedding_to_its_value` (currently lines 101-117): change
```python
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: np.array([1.0, 2.0, 3.0]))
```
to
```python
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0, 3.0]), "speechbrain/spkrec-ecapa-voxceleb"))
```
and add after the existing `assert profile.sample_count == 1`:
```python
    clip_row = db_session.query(VoiceClip).filter(VoiceClip.id == clip.id).first()
    assert clip_row.embedding_model == "speechbrain/spkrec-ecapa-voxceleb"
```

`test_add_clip_averages_embedding_across_multiple_clips` (currently lines 120-135): change
```python
    values = iter([np.array([0.0, 0.0]), np.array([2.0, 4.0])])
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: next(values))
```
to
```python
    values = iter([np.array([0.0, 0.0]), np.array([2.0, 4.0])])
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (next(values), "speechbrain/spkrec-ecapa-voxceleb"))
```

`test_add_clip_raises_when_extraction_fails` (currently lines 138-149): change
```python
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: None)
```
to
```python
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: None)
```
(same pattern applies to `test_enroll_error_includes_underlying_reason_when_all_backends_fail`, `test_remove_clip_recomputes_embedding_from_remaining`, `test_remove_last_clip_zeroes_profile`, `test_delete_profile_removes_clip_files_and_rows`, `test_identify_skips_profiles_with_no_embedding` — every one of these needs its `_extract_embedding` lambda changed from `lambda path: <value>` to `lambda path, hf_token=None: (<value>, "speechbrain/spkrec-ecapa-voxceleb")` when `<value>` is an array, or `lambda path, hf_token=None: None` when `<value>` is `None`).

Also update the three `monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path: np.array([1.0, 2.0]))` calls in `test_list_voices_includes_clips`, `test_add_clip_route_happy_path`, `test_delete_clip_route`, `test_clip_audio_route_serves_file` to:
```python
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0]), "speechbrain/spkrec-ecapa-voxceleb"))
```

Add two new tests for the cross-model rejection:

```python
def test_add_clip_raises_when_embedding_model_differs_from_existing_clips(tmp_path, monkeypatch, db_session):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)
    profile = _profile(db_session, user.id)

    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0]), "speechbrain/spkrec-ecapa-voxceleb"))
    clip_file_1 = tmp_path / "clip1.wav"
    clip_file_1.write_bytes(b"wav")
    svc.add_clip(db_session, profile.id, user.id, str(clip_file_1))

    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([3.0, 4.0, 5.0]), "pyannote/wespeaker-voxceleb-resnet34-LM"))
    clip_file_2 = tmp_path / "clip2.wav"
    clip_file_2.write_bytes(b"wav")

    with pytest.raises(ValueError) as exc_info:
        svc.add_clip(db_session, profile.id, user.id, str(clip_file_2))

    assert "speechbrain/spkrec-ecapa-voxceleb" in str(exc_info.value)
    assert "pyannote/wespeaker-voxceleb-resnet34-LM" in str(exc_info.value)


def test_add_clip_allows_legacy_null_embedding_model_to_mix(tmp_path, monkeypatch, db_session):
    # Pre-migration clips have embedding_model=None — leniently allowed to
    # coexist with a real model going forward, not treated as a mismatch.
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)
    profile = _profile(db_session, user.id)

    legacy_clip = VoiceClip(voice_profile_id=profile.id, audio_path="legacy.wav",
                             embedding=[1.0, 2.0], embedding_model=None)
    db_session.add(legacy_clip)
    db_session.commit()

    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([3.0, 4.0]), "speechbrain/spkrec-ecapa-voxceleb"))
    clip_file = tmp_path / "new.wav"
    clip_file.write_bytes(b"wav")

    clip = svc.add_clip(db_session, profile.id, user.id, str(clip_file))

    assert clip.embedding_model == "speechbrain/spkrec-ecapa-voxceleb"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -v`
Expected: FAIL — every test using the old `lambda path: <value>` mocking shape breaks (`TypeError` on the extra `hf_token` kwarg not being accepted by `enroll`/`add_clip`, or on tuple-unpacking), plus the two new cross-model tests fail with `AttributeError`/no exception raised since the guard doesn't exist yet.

- [ ] **Step 3: Implement `enroll`, `add_clip`, `_persist_clip`**

In `services/voice_id.py`, replace `enroll` through `_persist_clip` (currently lines 60-156):

```python
    def enroll(
        self,
        db,
        user_id: int,
        name: str,
        audio_path: str,
        notes: str = "",
        hf_token: Optional[str] = None,
    ) -> VoiceProfile:
        """Enroll a speaker by name from an audio sample — creates the
        profile if it doesn't exist yet, then adds this sample as its
        first clip.

        Embedding extraction is validated before any db access so a failed
        extraction never mutates state (and never requires a real db when
        extraction fails outright — see
        test_enroll_error_includes_underlying_reason_when_all_backends_fail)."""
        result = self._extract_embedding(audio_path, hf_token=hf_token)
        if result is None:
            if self._backend == "none":
                raise ValueError(
                    "No voice embedding backend available. "
                    "Install speechbrain (pip install speechbrain) or librosa "
                    "(pip install librosa) to enable voice enrollment."
                )
            reason = f" ({self._last_backend_error})" if self._last_backend_error else ""
            raise ValueError(
                f"Voice embedding extraction failed using the {self.backend_name} "
                f"backend. Check that the audio file is valid and the backend's "
                f"dependencies (e.g. torch, torchaudio) are working correctly.{reason}"
            )
        embedding, model_id = result

        profile = db.query(VoiceProfile).filter(
            VoiceProfile.user_id == user_id, VoiceProfile.name == name
        ).first()
        if not profile:
            profile = VoiceProfile(
                user_id=user_id, name=name, embedding=None,
                embedding_model=model_id, sample_count=0, notes=notes,
            )
            db.add(profile)
            db.commit()
        elif notes:
            profile.notes = notes
            db.commit()

        self._persist_clip(db, profile, audio_path, embedding, model_id)
        db.refresh(profile)
        return profile

    def add_clip(
        self,
        db,
        profile_id: int,
        user_id: int,
        audio_path: str,
        source_transcript_id: Optional[int] = None,
        hf_token: Optional[str] = None,
    ) -> VoiceClip:
        """Add one clip to an existing profile and recompute the profile's
        match embedding as the mean of all its clips. Refuses to mix
        embedding models within one profile — averaging or comparing
        vectors from different embedding spaces is meaningless."""
        profile = db.query(VoiceProfile).filter(
            VoiceProfile.id == profile_id, VoiceProfile.user_id == user_id
        ).first()
        if not profile:
            raise ValueError(f"Voice profile {profile_id} not found")

        result = self._extract_embedding(audio_path, hf_token=hf_token)
        if result is None:
            reason = f" ({self._last_backend_error})" if self._last_backend_error else ""
            raise ValueError(
                f"Voice embedding extraction failed using the {self.backend_name} "
                f"backend.{reason}"
            )
        embedding, model_id = result

        existing_clips = db.query(VoiceClip).filter(VoiceClip.voice_profile_id == profile.id).all()
        mismatch = next((c for c in existing_clips if c.embedding_model and c.embedding_model != model_id), None)
        if mismatch:
            raise ValueError(
                f"This clip was extracted using {model_id}, but profile '{profile.name}' "
                f"already has clips extracted using {mismatch.embedding_model}. Mixing "
                f"embedding models within one profile isn't supported — switch backends "
                f"back, or enroll this speaker as a separate profile."
            )

        return self._persist_clip(db, profile, audio_path, embedding, model_id, source_transcript_id)

    def _persist_clip(
        self,
        db,
        profile: VoiceProfile,
        audio_path: str,
        embedding,
        model_id: str,
        source_transcript_id: Optional[int] = None,
    ) -> VoiceClip:
        """Shared by enroll() and add_clip(): write the VoiceClip row for an
        already-extracted embedding and recompute the profile's mean
        embedding. Kept separate from add_clip so enroll() doesn't have to
        run embedding extraction twice."""
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -v`
Expected: PASS for every test except the one already-known pre-existing failure (`test_embed_speechbrain_caches_classifier_across_calls`, missing `torch` — see plan header note).

- [ ] **Step 5: Commit**

```bash
git add services/voice_id.py tests/test_voice_id.py
git commit -m "feat: thread hf_token through enroll/add_clip, reject cross-model clips"
```

---

### Task 6: `_recompute_profile_embedding` derives `embedding_model` from actual clips

**Files:**
- Modify: `services/voice_id.py:178-189`
- Test: `tests/test_voice_id.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_voice_id.py`:

```python
def test_recompute_profile_embedding_derives_model_from_latest_clip(tmp_path, monkeypatch, db_session):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)
    profile = _profile(db_session, user.id)

    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0]), "speechbrain/spkrec-ecapa-voxceleb"))
    clip_file = tmp_path / "c.wav"
    clip_file.write_bytes(b"wav")
    svc.add_clip(db_session, profile.id, user.id, str(clip_file))

    db_session.refresh(profile)
    assert profile.embedding_model == "speechbrain/spkrec-ecapa-voxceleb"


def test_recompute_profile_embedding_keeps_existing_model_when_only_legacy_clips_remain(tmp_path, db_session):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)
    profile = VoiceProfile(user_id=user.id, name="LegacyOnly", embedding=None,
                            embedding_model="speechbrain/spkrec-ecapa-voxceleb", sample_count=0)
    db_session.add(profile)
    db_session.commit()

    legacy_clip = VoiceClip(voice_profile_id=profile.id, audio_path="legacy.wav",
                             embedding=[1.0, 2.0], embedding_model=None)
    db_session.add(legacy_clip)
    db_session.commit()

    svc._recompute_profile_embedding(db_session, profile)

    db_session.refresh(profile)
    assert profile.embedding_model == "speechbrain/spkrec-ecapa-voxceleb"  # unchanged, no clip had a real value to derive from
    assert profile.embedding == [1.0, 2.0]
```

- [ ] **Step 2: Run tests to verify they fail (or pass by coincidence — confirm the second one specifically)**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "recompute_profile_embedding" -v`
Expected: first test should currently PASS already (existing code sets `embedding_model = self.backend_name`, which happens to equal `"speechbrain/spkrec-ecapa-voxceleb"` here since `svc._backend` defaults from real detection — but this is coincidental, not derived from the clip). Confirm by temporarily checking: the point of Task 6 is to make this derivation explicit and correct even when `self.backend_name` and the clips' actual models diverge (e.g. backend detection reports "pyannote" mid-session while all existing clips are speechbrain-labeled). Proceed to Step 3 regardless — the behavior must be explicit, not incidental.

- [ ] **Step 3: Implement**

In `services/voice_id.py`, replace `_recompute_profile_embedding` (currently lines 178-189):

```python
    def _recompute_profile_embedding(self, db, profile: VoiceProfile) -> None:
        clips = db.query(VoiceClip).filter(VoiceClip.voice_profile_id == profile.id).all()
        if not clips:
            profile.embedding = None
            profile.sample_count = 0
        else:
            stacked = np.array([c.embedding for c in clips])
            profile.embedding = np.mean(stacked, axis=0).tolist()
            profile.sample_count = len(clips)
            # add_clip's mismatch guard (Task 5) means all of a profile's
            # clips share one non-null embedding_model going forward, or are
            # legacy nulls — walk newest-first so a profile with only legacy
            # (None) clips keeps whatever embedding_model it already had.
            latest_model = next((c.embedding_model for c in reversed(clips) if c.embedding_model), None)
            if latest_model:
                profile.embedding_model = latest_model
        profile.updated_at = utcnow_naive()
        db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "recompute_profile_embedding" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full voice_id test file**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -v`
Expected: PASS except the 1 known pre-existing `torch`-missing failure.

- [ ] **Step 6: Commit**

```bash
git add services/voice_id.py tests/test_voice_id.py
git commit -m "feat: derive profile embedding_model from actual clip models"
```

---

### Task 7: `identify()` skips mismatched-model profiles

**Files:**
- Modify: `services/voice_id.py:191-216`
- Test: `tests/test_voice_id.py`

- [ ] **Step 1: Write the failing tests**

Update `test_identify_skips_profiles_with_no_embedding` (currently lines 218-229) — the mock shape must change:
```python
    monkeypatch.setattr(svc, "_extract_embedding", lambda path: np.array([1.0, 0.0]))
```
to
```python
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 0.0]), "speechbrain/spkrec-ecapa-voxceleb"))
```

Add new tests:

```python
def test_identify_skips_profile_with_different_embedding_model(tmp_path, monkeypatch, db_session):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)

    profile = VoiceProfile(user_id=user.id, name="Bob", embedding=[1.0, 0.0],
                            embedding_model="pyannote/wespeaker-voxceleb-resnet34-LM", sample_count=1)
    db_session.add(profile)
    db_session.commit()

    # Same length as the stored embedding, would otherwise score a perfect
    # match on raw cosine similarity — but it's a different embedding space.
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 0.0]), "speechbrain/spkrec-ecapa-voxceleb"))

    probe = tmp_path / "probe.wav"
    probe.write_bytes(b"wav")
    results = svc.identify(db_session, user.id, str(probe))

    assert results == []  # model mismatch, not a real match


def test_identify_still_matches_legacy_profile_with_no_recorded_model(tmp_path, monkeypatch, db_session):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)

    profile = VoiceProfile(user_id=user.id, name="LegacyBob", embedding=[1.0, 0.0],
                            embedding_model=None, sample_count=1)
    db_session.add(profile)
    db_session.commit()

    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 0.0]), "speechbrain/spkrec-ecapa-voxceleb"))

    probe = tmp_path / "probe.wav"
    probe.write_bytes(b"wav")
    results = svc.identify(db_session, user.id, str(probe))

    assert len(results) == 1
    assert results[0]["name"] == "LegacyBob"


def test_identify_dim_mismatch_guard_still_skips_same_label_different_length(tmp_path, monkeypatch, db_session):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)

    profile = VoiceProfile(user_id=user.id, name="Weird", embedding=[1.0, 0.0, 0.0],
                            embedding_model="speechbrain/spkrec-ecapa-voxceleb", sample_count=1)
    db_session.add(profile)
    db_session.commit()

    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 0.0]), "speechbrain/spkrec-ecapa-voxceleb"))

    probe = tmp_path / "probe.wav"
    probe.write_bytes(b"wav")
    results = svc.identify(db_session, user.id, str(probe))

    assert results == []  # same label, different length — secondary guard catches it
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "identify" -v`
Expected: FAIL — `test_identify_skips_profile_with_different_embedding_model` currently returns a match (no model check exists yet); the dim-mismatch test currently crashes with a `ValueError`/shape error from `np.dot` on mismatched lengths inside `_cosine_similarity` rather than skipping cleanly.

- [ ] **Step 3: Implement**

In `services/voice_id.py`, replace `identify` (currently lines 191-216):

```python
    def identify(self, db, user_id: int, audio_path: str, threshold: float = 0.65, hf_token: Optional[str] = None) -> list[dict]:
        """Identify a speaker from an audio sample. Returns ranked candidates."""
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
                    "id": profile.id,
                    "name": profile.name,
                    "similarity": round(float(similarity), 4),
                    "sample_count": profile.sample_count,
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "identify" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add services/voice_id.py tests/test_voice_id.py
git commit -m "feat: identify() skips profiles with mismatched embedding_model"
```

---

### Task 8: Thread `hf_token` through `app.py` route handlers

**Files:**
- Modify: `app.py:1768-1857` (`enroll_voice`, `identify_speaker`, `add_voice_clip`)
- Test: `tests/test_voice_id.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_voice_id.py`:

```python
def test_enroll_route_passes_hf_token_from_user_settings(client, db_session, tmp_path, monkeypatch):
    from services.settings import get_user_settings
    user = _test_user(db_session)

    captured = {}

    def fake_enroll(db, user_id, name, audio_path, notes="", hf_token=None):
        # Setting this on the instance (not the class) shadows the bound
        # method, so pytest calls it directly without an implicit `self`.
        captured["hf_token"] = hf_token
        return VoiceProfile(id=1, user_id=user_id, name=name, embedding=[1.0], sample_count=1, notes=notes)

    monkeypatch.setattr("app.voice_id_service.enroll", fake_enroll)
    monkeypatch.setattr("app.get_user_settings", lambda db, uid: {"hf_token": "test-token-123"})

    r = client.post(
        "/api/voices/enroll",
        data={"name": "Carol"},
        files={"file": ("voice.wav", io.BytesIO(b"wav bytes"), "audio/wav")},
    )
    assert r.status_code == 200
    assert captured["hf_token"] == "test-token-123"
```

Note: check `app.py` imports `get_user_settings` directly (`from services.settings import get_user_settings`) before writing this test — if it's imported under a different alias, adjust the `monkeypatch.setattr("app.get_user_settings", ...)` target string to match (verify with `grep -n "get_user_settings" app.py` first).

- [ ] **Step 2: Verify the import and run the test to confirm it fails**

Run: `cd C:\Claude\WhisperDeck && grep -n "get_user_settings" app.py`
Confirm the import line, adjust the monkeypatch target in Step 1 if needed.

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -k "hf_token" -v`
Expected: FAIL — `captured["hf_token"]` is never set to `"test-token-123"` because the route doesn't fetch/pass it yet (it'll be `None` or the test will fail on the `fake_enroll` signature not matching how the route currently calls `enroll`).

- [ ] **Step 3: Implement**

In `app.py`, update the three route handlers. `enroll_voice` (currently lines 1768-1795):

```python
@app.post("/api/voices/enroll")
async def enroll_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enroll a new speaker from an audio sample."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"enroll_{name}_{utcnow_naive().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        user_settings = get_user_settings(db, current_user.id)
        profile = voice_id_service.enroll(db, current_user.id, name=name, audio_path=str(save_path),
                                           notes=notes, hf_token=user_settings.get("hf_token"))
        return {
            "id": profile.id,
            "name": profile.name,
            "sample_count": profile.sample_count,
            "embedding_model": profile.embedding_model,
            "notes": profile.notes,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

`identify_speaker` (currently lines 1798-1822):

```python
@app.post("/api/voices/identify")
async def identify_speaker(
    file: UploadFile = File(...),
    threshold: float = Form(0.65),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Identify a speaker from an audio sample against enrolled profiles."""
    file_ext = os.path.splitext(file.filename or "voice.wav")[1] or ".wav"
    safe_name = f"ident_{utcnow_naive().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    save_path = VOICES_DIR / safe_name

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        user_settings = get_user_settings(db, current_user.id)
        matches = voice_id_service.identify(db, current_user.id, str(save_path), threshold=threshold,
                                             hf_token=user_settings.get("hf_token"))
        return {
            "matches": matches,
            "total_profiles": len(voice_id_service.list_profiles(db, current_user.id)),
            "backend": voice_id_service._backend,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

`add_voice_clip` (currently lines 1833-1857):

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
    safe_name = f"clip_{profile_id}_{utcnow_naive().strftime('%Y%m%d_%H%M%S%f')}{file_ext}"
    save_path = VOICES_DIR / safe_name
    with open(save_path, "wb") as f:
        f.write(await file.read())

    try:
        user_settings = get_user_settings(db, current_user.id)
        clip = voice_id_service.add_clip(db, profile_id, current_user.id, str(save_path),
                                          hf_token=user_settings.get("hf_token"))
        return {"id": clip.id, "voice_profile_id": clip.voice_profile_id,
                "created_at": clip.created_at.isoformat() if clip.created_at else None}
    except ValueError as e:
        try:
            os.remove(save_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/test_voice_id.py -v`
Expected: PASS except the 1 known pre-existing `torch`-missing failure.

- [ ] **Step 5: Run the full test suite to check for regressions elsewhere**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/ -v`
Expected: same pass/fail counts as the documented baseline (1 pre-existing failure), no new failures outside `test_voice_id.py`.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_voice_id.py
git commit -m "feat: thread hf_token from user settings into voice-ID routes"
```

---

### Task 9: Docs — requirements comment + ROADMAP update

**Files:**
- Modify: `requirements-diarization.txt`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Update `requirements-diarization.txt`**

Add after the existing header comment block (before `torch>=2.2.0`):

```
# This also unlocks the pyannote voice-ID embedding backend
# (services/voice_id.py) — no separate pip package needed, it reuses
# torch + pyannote.audio pinned below. Note: the voice-ID model
# (pyannote/wespeaker-voxceleb-resnet34-LM) is a SEPARATELY gated HF repo
# from the diarization model (pyannote/speaker-diarization-3.1) — accepting
# one model's license on huggingface.co does not grant access to the other.
```

- [ ] **Step 2: Update `docs/ROADMAP.md`**

Move the line from **In Progress** to **Done**:

Remove from "In Progress":
```
- **pyannote.audio voice-ID embedding backend** (priority: needed asap — [issue #38](https://github.com/tito13kfm/whisperdeck/issues/38)) — named in `services/voice_id.py`'s backend-label mapping but `_detect_backend()` never returns it; the code comment says to skip it until an embedding extractor is wired up. speechbrain and librosa MFCC are the only real backends today.
```

Add to "Done" (at the end of the list):
```
- pyannote.audio voice-ID embedding backend (`docs/superpowers/plans/2026-07-21-pyannote-voice-id-backend.md`)
```

If the "In Progress" section becomes empty, leave the `## In Progress` header in place (matches the file's existing convention of keeping section headers even when momentarily empty — confirm by checking if any other section is currently empty; if uncertain, just leave the header with no bullets under it).

- [ ] **Step 3: Commit**

```bash
git add requirements-diarization.txt docs/ROADMAP.md
git commit -m "docs: note pyannote voice-ID unlock in requirements, move ROADMAP item to Done"
```

---

### Task 10: Close out

- [ ] **Step 1: Run the full test suite one more time**

Run: `cd C:\Claude\WhisperDeck && python -m pytest tests/ -v 2>&1 | tail -40`
Expected: same baseline as Task 8 Step 5 (1 known pre-existing failure, everything else passing).

- [ ] **Step 2: Close issue #38 with a reference to the merge**

Once this branch is merged (not before — don't close an issue for unmerged work):
```bash
gh issue close 38 --repo tito13kfm/WhisperDeck --comment "Implemented in <merge-commit-sha> — pyannote.audio wired up as a third voice-ID embedding backend (pyannote/wespeaker-voxceleb-resnet34-LM), plus per-clip embedding_model tracking to prevent cross-backend averaging/comparison bugs."
```
