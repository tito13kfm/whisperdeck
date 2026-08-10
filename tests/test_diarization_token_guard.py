"""Issue #119: a missing HuggingFace token raises a clear MissingTokenError
instead of pyannote's cryptic gated-model 401.

The guard lives at the top of _run_pyannote_sync, BEFORE the torch import —
so it fires on venvs without torch, which is also what lets this file run
in CI without the diarization extras. Every test here fails if the guard
body were replaced with `return` (no raise, or the model load would be
reached and blow up differently).
"""
import pytest

from services.diarization import (
    DiarizationService,
    HF_TOKEN_HELP,
    MissingTokenError,
    resolve_hf_token,
)


class TestResolveHfToken:
    def test_empty_string_raises(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        with pytest.raises(MissingTokenError, match="HuggingFace token required"):
            resolve_hf_token("")

    def test_none_raises_with_actionable_message(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        with pytest.raises(MissingTokenError, match="Settings"):
            resolve_hf_token(None)

    def test_whitespace_raises(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        with pytest.raises(MissingTokenError):
            resolve_hf_token("   ")

    def test_explicit_token_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_env")
        assert resolve_hf_token("hf_explicit") == "hf_explicit"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_env")
        assert resolve_hf_token("") == "hf_env"

    def test_is_a_value_error(self):
        # /api/diarize catches MissingTokenError specifically; keeping it a
        # ValueError subclass preserves any broader except-ValueError sites.
        assert issubclass(MissingTokenError, ValueError)


class TestGuardPlacement:
    def test_run_pyannote_sync_raises_before_model_load(self, monkeypatch):
        """A dummy (non-tensor) waveform proves the guard fires before the
        torch import and any pyannote work."""
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        svc = DiarizationService()
        with pytest.raises(MissingTokenError, match="HuggingFace token required"):
            svc._run_pyannote_sync(object(), 16000, None, "")

    def test_voice_id_pyannote_inference_raises(self, monkeypatch):
        """services/voice_id.py shares the guard for its gated embedding
        model — same clear error instead of a 401 traceback recorded as
        the backend error."""
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        from services.voice_id import VoiceIdentificationService
        svc = VoiceIdentificationService.__new__(VoiceIdentificationService)
        import threading
        svc._model_lock = threading.Lock()
        svc._pyannote_inference = None
        with pytest.raises(MissingTokenError):
            svc._get_pyannote_inference("")

    def test_help_text_names_both_setup_paths(self):
        assert "Service Panel" in HF_TOKEN_HELP
        assert "HUGGINGFACE_TOKEN" in HF_TOKEN_HELP


class TestApiDiarize:
    def test_api_diarize_missing_token_returns_400(self, client, monkeypatch, tmp_path):
        """Explicit pyannote opt-in without a token is a 400 with the
        settings-pointing message, not a 500. Removing the
        except-MissingTokenError clause in /api/diarize turns this into a
        500 (mutation check)."""
        import app as app_module
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        monkeypatch.setattr(
            app_module.diarization_service, "_check_pyannote", lambda: True
        )
        monkeypatch.setattr(
            app_module.diarization_service, "pyannote_available", True
        )

        async def fake_pyannote(*a, **k):
            from services.diarization import HF_TOKEN_HELP, MissingTokenError
            raise MissingTokenError(HF_TOKEN_HELP)

        monkeypatch.setattr(
            app_module.diarization_service, "diarize_pyannote", fake_pyannote
        )
        import io
        resp = client.post(
            "/api/diarize",
            files={"file": ("t.wav", io.BytesIO(b"RIFF0000WAVE"), "audio/wav")},
            data={"method": "pyannote"},
        )
        assert resp.status_code == 400
        assert "HuggingFace token" in resp.json()["detail"]
