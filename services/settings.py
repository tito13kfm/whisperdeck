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
    "correction_provider": "groq",
    "correction_model": "llama-3.3-70b-versatile",
    "summary_provider": "groq",
    "summary_model": "llama-3.3-70b-versatile",
}

# Providers that work without an API key (local inference / user-hosted URL).
KEYLESS_PROVIDERS = ("local", "builtin", "moonshine")


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
    return cfg.api_key or "", {
        "api_key": cfg.api_key or "",
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
