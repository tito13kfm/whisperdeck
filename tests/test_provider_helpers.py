"""Tests for services.settings get_provider_config / require_provider_key (issue #404).

Mutation check: replacing get_provider_config with `return None` must fail
the 'found' tests; replacing require_provider_key with `return ""` must fail
the guard tests.
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ProviderConfig, User
from services.settings import get_provider_config, require_provider_key, KEYLESS_PROVIDERS


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    u = User(username="alice", password_hash="x", password_salt="y")
    s.add(u)
    s.commit()
    s.refresh(u)
    yield s
    s.close()
    engine.dispose()


def test_get_provider_config_found(db):
    u = db.query(User).filter(User.username == "alice").first()
    db.add(ProviderConfig(user_id=u.id, name="groq", api_key="gsk_test", api_url="https://api.groq.com"))
    db.commit()
    cfg = get_provider_config(db, u.id, "groq")
    assert cfg is not None
    assert cfg.api_key == "gsk_test"
    assert cfg.api_url == "https://api.groq.com"


def test_get_provider_config_missing_returns_none(db):
    u = db.query(User).filter(User.username == "alice").first()
    assert get_provider_config(db, u.id, "groq") is None


def test_get_provider_config_scoped_to_user(db):
    u = db.query(User).filter(User.username == "alice").first()
    db.add(User(username="bob", password_hash="x", password_salt="y"))
    db.commit()
    bob = db.query(User).filter(User.username == "bob").first()
    db.add(ProviderConfig(user_id=bob.id, name="groq", api_key="gsk_bob"))
    db.commit()
    assert get_provider_config(db, u.id, "groq") is None
    assert get_provider_config(db, bob.id, "groq") is not None


def test_require_provider_key_raises_400_when_keyed_provider_missing(db):
    u = db.query(User).filter(User.username == "alice").first()
    with pytest.raises(HTTPException) as ei:
        require_provider_key(db, u.id, "groq")
    assert ei.value.status_code == 400
    assert "add one in the service panel" in ei.value.detail
    assert "groq" in ei.value.detail


def test_require_provider_key_passes_for_keyless_provider_missing(db):
    u = db.query(User).filter(User.username == "alice").first()
    for provider in KEYLESS_PROVIDERS:
        out = require_provider_key(db, u.id, provider)
        assert out == ""


def test_require_provider_key_passes_when_key_present(db):
    u = db.query(User).filter(User.username == "alice").first()
    db.add(ProviderConfig(user_id=u.id, name="groq", api_key="gsk_present"))
    db.commit()
    out = require_provider_key(db, u.id, "groq")
    assert out == "gsk_present"


def test_require_provider_key_message_is_canonical(db):
    """Regression for #404 drift: every 400 must carry the UI hint."""
    u = db.query(User).filter(User.username == "alice").first()
    try:
        require_provider_key(db, u.id, "openai")
    except HTTPException as e:
        assert e.detail == "No openai API key saved \u2014 add one in the service panel"
    else:
        pytest.fail("expected HTTPException")
