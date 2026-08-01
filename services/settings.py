"""Per-user tunables for audio prep and the chunking queue.

Stored as a single JSON blob on User.settings (see database/__init__.py) —
no separate table, since this is a small fixed set of scalar values, not
a growing per-item collection like provider configs.
"""
import json

from sqlalchemy import text

from database import User

DEFAULT_SETTINGS = {
    "bitrate_kbps": 128,
    "chunk_threshold_mb": 20,
    "max_concurrent_chunks": 4,
    "hf_token": "",
    "auto_correct": True,
    # Defaults for LLM-backed passes. Sensible even with no keys saved —
    # runs that need a missing key skip with a recorded reason instead of
    # failing. Keys themselves always come from the ProviderConfig pool.
    # gpt-oss-20b-mxfp4-GGUF is the fastest local LLM (GPU via ROCM,
    # 12.1 GB, 131K ctx). Falls back to Qwen3.5-4B-MTP-GGUF (3.66 GB,
    # 262K ctx) if VRAM is tight.
    "correction_provider": "local_llm",
    "correction_model": "gpt-oss-20b-mxfp4-GGUF",
    "summary_provider": "local_llm",
    "summary_model": "gpt-oss-20b-mxfp4-GGUF",
    "format_provider": "local_llm",
    "format_model": "gpt-oss-20b-mxfp4-GGUF",
    "classification_provider": "local_llm",
    "classification_model": "gpt-oss-20b-mxfp4-GGUF",
    # Conservative on purpose (design decision 3): a low-confidence guess is
    # rejected (stored as 'uncertain', safe fallback) rather than accepted —
    # a wrong-but-confident-looking auto-kind is worse than staying safe.
    "classification_confidence_threshold": 0.75,
    # Audio cleanup stage (issue #270): per-step opt-in with safe fallback to
    # original audio on failure. All thresholds are provisional — benchmark
    # before adjusting (the referenced whisperhallu-review.md is missing).
    "cleanup_loudnorm_enabled": False,      # loudnorm + highpass + afftdn chain
    "cleanup_loudnorm_target": -23.0,       # LUFS target (EBU R128)
    "cleanup_highpass_enabled": False,      # rumble/handling-noise filter (80Hz)
    "cleanup_denoise_enabled": False,       # ffmpeg afftdn denoiser
    "cleanup_vad_enabled": True,            # Silero VAD (builtin-only, default-on)
    "cleanup_vad_min_silence_ms": 100,      # ms
    "cleanup_vad_threshold": 0.5,           # speech probability 0-1
    "cleanup_hallu_enabled": False,         # post-hoc repetition+low-confidence filter
    "cleanup_hallu_rep_window": 3,          # n-gram window for repetition detection
    "cleanup_hallu_logprob_cutoff": -2.0,   # avg_logprob below this = suspect
    "cleanup_hallu_no_speech_cutoff": 0.6,  # no_speech_prob above this = suspect
    "cleanup_demucs_enabled": False,        # Demucs vocal isolation (local-only, expensive)
    "export_directory": "",  # empty = feature disabled (Save as .md button hidden in detail toolbar)
    "bulk_defaults": {
        "provider": "moonshine",
        "model": "",
        "language": "auto",
        "diarize": False,
        "auto_correct": True,
        "kind": "meeting",
        "num_speakers": None,
    },
}

# Providers that work without an API key (local inference / user-hosted URL).
# "local" is the transcription (STT) endpoint; "local_llm" is a separate,
# independently-configured endpoint for correction/summary/hotword-extraction
# — the two often run on different local servers (e.g. a Whisper server on
# 8080 alongside Ollama on 11434), so they can't share one saved URL.
KEYLESS_PROVIDERS = ("local", "local_llm", "builtin", "moonshine")


def _decrypt_key_if_needed(encrypted: str, session_secret: str = "") -> str:
    """Decrypt an API key if it looks encrypted (base64-encoded salt+token).
    Falls back to returning the value as-is for plaintext keys that predate
    encryption support."""
    if not encrypted or not session_secret:
        return encrypted
    # Encrypted keys are always longer than 64 chars and start with a
    # base64 character. Plaintext keys are short (gsk_..., sk-..., r8_...).
    if len(encrypted) < 64:
        return encrypted
    try:
        from services.security import decrypt_api_key
        return decrypt_api_key(encrypted, session_secret)
    except Exception:
        # Not encrypted or wrong secret — return as-is
        return encrypted


def resolve_provider_key(db, user_id: int, provider: str) -> tuple[str, dict]:
    """The one place API keys are drawn from: the user's ProviderConfig pool.
    Returns (api_key, provider_config); both empty when nothing is saved."""
    from database import ProviderConfig  # local import avoids a module-load cycle
    cfg = (
        db.query(ProviderConfig)
        .filter(ProviderConfig.user_id == user_id, ProviderConfig.name == provider)
        .first()
    )
    if not cfg:
        return "", {}
    # Import SESSION_SECRET at call time to avoid circular imports
    import os
    from pathlib import Path
    _base = Path(__file__).parent.parent.resolve()
    _data = Path(os.environ.get("WHISPERDECK_DATA_DIR") or os.environ.get("WHISPERDESK_DATA_DIR") or str(_base / "data"))
    _secret_path = _data / ".session_secret"
    _secret = _secret_path.read_text().strip() if _secret_path.exists() else ""
    raw_key = cfg.api_key or ""
    decrypted = _decrypt_key_if_needed(raw_key, _secret)
    return decrypted, {
        "api_key": decrypted,
        "api_url": cfg.api_url or "",
        "default_model": cfg.default_model or "",
    }


def get_user_settings(db, user_id: int) -> dict:
    """Return this user's settings merged over the defaults — any key the
    user hasn't set yet falls back to DEFAULT_SETTINGS rather than being
    absent, so callers never need their own fallback logic."""
    user = db.query(User).filter(User.id == user_id).first()
    stored = (user.settings or {}) if user else {}
    return {**DEFAULT_SETTINGS, **stored}


def update_user_settings(db, user_id: int, updates: dict) -> dict:
    """Merge updates into the user's stored settings and return the full
    merged-with-defaults settings dict. Unknown keys in updates are
    ignored so a stray frontend field can't pollute the stored JSON.

    Uses SQLite's json_patch() to do the read-merge-write atomically inside
    one UPDATE statement, rather than reading user.settings in Python and
    writing it back — that read-then-write pattern lost data under real
    usage: two settings saves fired close together (e.g. saving the audio
    card and the HuggingFace token card within the same second) could
    interleave so the second request read the settings before the first
    request's write landed, silently overwriting the first save's field
    when it committed. json_patch merges inside the database in one atomic
    step, so there's no window for a second request to read a stale copy."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    patch = {key: value for key, value in updates.items() if key in DEFAULT_SETTINGS}
    if patch:
        db.execute(
            text("UPDATE users SET settings = json_patch(coalesce(settings, '{}'), :patch) WHERE id = :uid"),
            {"patch": json.dumps(patch), "uid": user_id},
        )
        db.commit()
    return get_user_settings(db, user_id)
