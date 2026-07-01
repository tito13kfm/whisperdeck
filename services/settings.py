"""Per-user tunables for audio prep and the chunking queue.

Stored as a single JSON blob on User.settings (see database/__init__.py) —
no separate table, since this is a small fixed set of scalar values, not
a growing per-item collection like provider configs.
"""
from database import User

DEFAULT_SETTINGS = {
    "bitrate_kbps": 128,
    "chunk_threshold_mb": 20,
    "max_concurrent_chunks": 4,
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
    ignored so a stray frontend field can't pollute the stored JSON."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    current = user.settings or {}
    for key, value in updates.items():
        if key in DEFAULT_SETTINGS:
            current[key] = value
    user.settings = current
    db.commit()
    return get_user_settings(db, user_id)
