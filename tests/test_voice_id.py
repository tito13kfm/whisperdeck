"""VoiceIdentificationService backend robustness: classifier caching,
fallback chain when the primary backend fails, and surfaced error detail."""
import sys
import types
import numpy as np
import pytest

from services.voice_id import VoiceIdentificationService


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
    assert np.array_equal(result, fallback)


def test_detect_backend_skips_unimplemented_pyannote(tmp_path, monkeypatch):
    # pyannote.audio importing successfully used to make _detect_backend pick
    # "pyannote" — but _extract_embedding has no pyannote branch, so every
    # enroll/identify call silently returned None forever, surfacing as the
    # generic "torch, torchaudio" error regardless of what's actually broken.
    monkeypatch.setitem(sys.modules, "speechbrain", None)
    monkeypatch.setitem(sys.modules, "librosa", None)
    monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
    monkeypatch.setitem(sys.modules, "pyannote.audio", types.ModuleType("pyannote.audio"))

    svc = VoiceIdentificationService(voices_dir=str(tmp_path / "voices"))

    assert svc._backend != "pyannote"


def test_enroll_error_includes_underlying_reason_when_all_backends_fail(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_embed_speechbrain", lambda path: None)
    monkeypatch.setattr(svc, "_embed_mfcc", lambda path: None)
    svc._last_backend_error = "torchaudio.load: no audio backend available (torchcodec incompatibility)"

    with pytest.raises(ValueError) as exc_info:
        svc.enroll(db=None, user_id=1, name="Alice", audio_path="fake.wav")

    assert "torchcodec incompatibility" in str(exc_info.value)
