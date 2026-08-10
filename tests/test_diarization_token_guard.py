"""Issue #119: a pyannote model load that fails with no HuggingFace
credential anywhere reports a clear MissingTokenError instead of pyannote's
cryptic gated-model 401.

The diagnosis is deliberately AFTER the load attempt, not before it:
huggingface_hub resolves credentials of its own (a `huggingface-cli login`
token file, HF_TOKEN, HUGGING_FACE_HUB_TOKEN) and serves already-cached
models offline, so refusing to even try without a token of ours would break
every install authenticated the standard HuggingFace way. resolve_hf_token
therefore returns None rather than raising, and as_missing_token_error
decides whether a failure was really an auth problem.

Mutation check: make as_missing_token_error return `exc` unconditionally and
the translation tests fail; make resolve_hf_token raise on empty again and
test_no_token_still_attempts_the_load fails.
"""
import threading

import pytest

from services.diarization import (
    DiarizationService,
    HF_TOKEN_HELP,
    MissingTokenError,
    as_missing_token_error,
    resolve_hf_token,
)


@pytest.fixture()
def no_credentials(monkeypatch):
    """No token from this app and none from huggingface_hub either — the
    only state in which a failure is attributable to missing auth."""
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.setattr("services.diarization._hub_has_credentials", lambda: False)


class TestResolveHfToken:
    def test_empty_string_is_none_not_an_error(self, no_credentials):
        """None is the passthrough value huggingface_hub documents as "use
        whatever this machine is logged in with" — raising here is what broke
        CLI-authenticated and offline-cached installs."""
        assert resolve_hf_token("") is None

    def test_none_and_whitespace_are_none(self, no_credentials):
        assert resolve_hf_token(None) is None
        assert resolve_hf_token("   ") is None

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


class TestErrorTranslation:
    def test_no_credential_anywhere_becomes_the_actionable_error(self, no_credentials):
        translated = as_missing_token_error(RuntimeError("401 Client Error: gated repo"), None)
        assert isinstance(translated, MissingTokenError)
        assert "HuggingFace token required" in str(translated)
        assert "Service Panel" in str(translated)
        # the underlying reason is kept, not swallowed
        assert "401" in str(translated)

    def test_configured_token_keeps_the_original_error(self, no_credentials):
        """With a token set, a 401 means the token is wrong or the license
        was never accepted — telling the user to set a token would be a lie."""
        original = RuntimeError("401 Client Error: accept the license first")
        assert as_missing_token_error(original, "hf_configured") is original

    def test_hub_credentials_keep_the_original_error(self, monkeypatch):
        """A CLI login counts as configured even though our own setting is
        empty, so its failures are not missing-token failures."""
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        monkeypatch.setattr("services.diarization._hub_has_credentials", lambda: True)
        original = RuntimeError("connection reset")
        assert as_missing_token_error(original, None) is original

    def test_import_errors_are_never_reported_as_auth(self, no_credentials):
        """A machine without torch/pyannote has a dependency problem, and
        telling it to set a token would send the user down the wrong path."""
        original = ImportError("No module named 'torch'")
        assert as_missing_token_error(original, None) is original

    def test_help_text_names_both_setup_paths(self):
        assert "Service Panel" in HF_TOKEN_HELP
        assert "HUGGINGFACE_TOKEN" in HF_TOKEN_HELP

    def test_help_text_is_console_encodable(self):
        """It reaches print() on the queue worker, and a cp1252 console (the
        Windows default) raises UnicodeEncodeError on a "→"."""
        HF_TOKEN_HELP.encode("cp1252")


class TestLoadPaths:
    def test_no_token_still_attempts_the_load(self, no_credentials):
        """The load must be reached with token=None so a warm cache or a CLI
        login can satisfy it. Here there is no torch, so what comes back is
        the dependency error, NOT a missing-token error."""
        svc = DiarizationService()
        with pytest.raises(Exception) as exc_info:
            svc._run_pyannote_sync(object(), 16000, None, "")
        assert not isinstance(exc_info.value, MissingTokenError)

    def test_voice_id_translates_its_load_failure(self, no_credentials, monkeypatch):
        """services/voice_id.py shares the translation for its gated
        embedding model, so _embed_pyannote records the actionable message as
        the backend error instead of a 401 traceback."""
        import sys
        import types
        from services.voice_id import VoiceIdentificationService

        class FakeModel:
            @staticmethod
            def from_pretrained(name, token=None):
                assert token is None, "the hub passthrough must survive"
                raise RuntimeError("401 Client Error: gated repo")

        monkeypatch.setitem(
            sys.modules, "pyannote.audio",
            types.SimpleNamespace(Model=FakeModel, Inference=object),
        )
        svc = VoiceIdentificationService.__new__(VoiceIdentificationService)
        svc._model_lock = threading.Lock()
        svc._pyannote_inference = None
        with pytest.raises(MissingTokenError, match="HuggingFace token required"):
            svc._get_pyannote_inference("")


class TestApiDiarize:
    def test_api_diarize_missing_token_returns_400(self, client, monkeypatch, tmp_path):
        """Explicit pyannote opt-in with no credential is a 400 with the
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
