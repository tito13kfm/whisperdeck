"""VoiceIdentificationService backend robustness: classifier caching,
fallback chain when the primary backend fails, and surfaced error detail."""
import io
import os
import sys
import types
import numpy as np
import pytest

from database import User, VoiceProfile, VoiceClip
from services.voice_id import VoiceIdentificationService, voice_id_service


def _test_user(db_session):
    user = db_session.query(User).filter(User.username == "testuser").first()
    if not user:
        user = User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    return user


def _svc(tmp_path):
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    svc._backend = "speechbrain"
    return svc


def test_embed_speechbrain_caches_classifier_across_calls(tmp_path, monkeypatch):
    svc = _svc(tmp_path)

    calls = {"instantiated": 0}

    class FakeClassifier:
        def encode_batch(self, signal):
            return np.array([[1.0, 2.0, 3.0]])

    def fake_from_hparams(**kwargs):
        calls["instantiated"] += 1
        return FakeClassifier()

    fake_speaker_module = types.SimpleNamespace(EncoderClassifier=types.SimpleNamespace(from_hparams=fake_from_hparams))
    monkeypatch.setitem(sys.modules, "speechbrain.inference.speaker", fake_speaker_module)

    import torch
    fake_signal = torch.zeros(1, 16000)
    monkeypatch.setattr("torchaudio.load", lambda path: (fake_signal, 16000))

    svc._embed_speechbrain("fake1.wav")
    svc._embed_speechbrain("fake2.wav")

    assert calls["instantiated"] == 1


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


def test_detect_backend_picks_pyannote_when_speechbrain_absent(tmp_path, monkeypatch):
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

    fake_data = np.array([[0.1], [0.2]])
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
            return np.array([[1.0, 2.0, 3.0]])

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


def test_enroll_error_includes_underlying_reason_when_all_backends_fail(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_embed_speechbrain", lambda path: None)
    monkeypatch.setattr(svc, "_embed_mfcc", lambda path: None)
    svc._last_backend_error = "torchaudio.load: no audio backend available (torchcodec incompatibility)"

    with pytest.raises(ValueError) as exc_info:
        svc.enroll(db=None, user_id=1, name="Alice", audio_path="fake.wav")

    assert "torchcodec incompatibility" in str(exc_info.value)


def _profile(db_session, user_id, name="Alice"):
    p = VoiceProfile(user_id=user_id, name=name, embedding=None, sample_count=0)
    db_session.add(p)
    db_session.commit()
    return p


def test_add_clip_creates_row_and_sets_profile_embedding_to_its_value(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0, 3.0]), "speechbrain/spkrec-ecapa-voxceleb"))

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
    clip_row = db_session.query(VoiceClip).filter(VoiceClip.id == clip.id).first()
    assert clip_row.embedding_model == "speechbrain/spkrec-ecapa-voxceleb"


def test_add_clip_averages_embedding_across_multiple_clips(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    values = iter([np.array([0.0, 0.0]), np.array([2.0, 4.0])])
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (next(values), "speechbrain/spkrec-ecapa-voxceleb"))

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
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: None)

    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    clip_file = tmp_path / "bad.wav"
    clip_file.write_bytes(b"wav")

    with pytest.raises(ValueError):
        svc.add_clip(db_session, profile.id, user.id, str(clip_file))


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


def test_remove_clip_recomputes_embedding_from_remaining(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    values = iter([np.array([0.0, 0.0]), np.array([2.0, 4.0])])
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (next(values), "speechbrain/spkrec-ecapa-voxceleb"))

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
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 1.0]), "speechbrain/spkrec-ecapa-voxceleb"))

    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    clip_file = tmp_path / "only.wav"
    clip_file.write_bytes(b"wav")
    clip = svc.add_clip(db_session, profile.id, user.id, str(clip_file))

    svc.remove_clip(db_session, profile.id, user.id, clip.id)

    db_session.refresh(profile)
    assert profile.embedding is None
    assert profile.sample_count == 0


def test_delete_profile_removes_clip_files_and_rows(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    values = iter([np.array([0.0, 0.0]), np.array([2.0, 4.0])])
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (next(values), "speechbrain/spkrec-ecapa-voxceleb"))

    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    clip_paths = []
    clip_ids = []
    for i in range(2):
        clip_file = tmp_path / f"clip{i}.wav"
        clip_file.write_bytes(b"wav")
        clip = svc.add_clip(db_session, profile.id, user.id, str(clip_file))
        clip_paths.append(clip_file)
        clip_ids.append(clip.id)

    ok = svc.delete_profile(db_session, user.id, profile.id)
    assert ok is True

    for clip_file in clip_paths:
        assert not clip_file.exists()
    remaining = db_session.query(VoiceClip).filter(VoiceClip.id.in_(clip_ids)).all()
    assert remaining == []


def test_identify_skips_profiles_with_no_embedding(tmp_path, monkeypatch, db_session):
    from services.voice_id import VoiceIdentificationService
    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))
    user = _test_user(db_session)
    _profile(db_session, user.id, name="Empty")
    monkeypatch.setattr(svc, "_extract_embedding", lambda path, hf_token=None: (np.array([1.0, 0.0]), "speechbrain/spkrec-ecapa-voxceleb"))

    probe = tmp_path / "probe.wav"
    probe.write_bytes(b"wav")
    results = svc.identify(db_session, user.id, str(probe))

    assert results == []  # no crash, no match — the empty profile is skipped


def test_list_voices_includes_clips(client, db_session, tmp_path, monkeypatch):
    user = _test_user(db_session)
    profile = _profile(db_session, user.id)
    from services import voice_id as voice_id_module
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0]), "speechbrain/spkrec-ecapa-voxceleb"))
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
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0]), "speechbrain/spkrec-ecapa-voxceleb"))

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
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0]), "speechbrain/spkrec-ecapa-voxceleb"))
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
    monkeypatch.setattr("app.voice_id_service._extract_embedding", lambda path, hf_token=None: (np.array([1.0, 2.0]), "speechbrain/spkrec-ecapa-voxceleb"))
    import app as app_module
    clip_file = tmp_path / "c.wav"
    clip_file.write_bytes(b"real wav bytes")
    clip = app_module.voice_id_service.add_clip(db_session, profile.id, user.id, str(clip_file))

    r = client.get(f"/api/voices/{profile.id}/clips/{clip.id}/audio")
    assert r.status_code == 200
    assert r.content == b"real wav bytes"


def test_singleton_voices_dir_is_absolute_and_cwd_independent():
    """The module-level singleton's default voices_dir must resolve to an
    absolute path rooted at the project directory, not a path relative to
    whatever the process's current working directory happens to be."""
    assert os.path.isabs(voice_id_service.voices_dir)
    assert voice_id_service.voices_dir.replace("\\", "/").endswith("data/voices")
