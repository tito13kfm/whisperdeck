"""Tests for Cache-Control headers on static assets and index.html (issue #140)."""
import pytest
from fastapi.testclient import TestClient


def test_static_asset_cache_control(client):
    """GET /static/rack.css returns Cache-Control: public, max-age=3600."""
    resp = client.get("/static/rack.css")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "public, max-age=3600"


def test_index_no_cache(client):
    """GET / returns Cache-Control: no-cache (revalidate every time)."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-cache"


def test_other_route_no_static_cache(client):
    """GET /api/health does NOT receive the static asset cache header."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    cc = resp.headers.get("Cache-Control")
    assert cc != "public, max-age=3600", f"Unexpected static Cache-Control on /api/health: {cc}"


def test_static_cache_middleware_ordering():
    """Verify middleware order: static files get cached even through full stack.

    Creates a fresh TestClient without auth to avoid session/csrf noise and
    confirms the cache header is present on a deep path under /static/.
    """
    import app as app_module
    fresh = TestClient(app_module.app)
    resp = fresh.get("/static/rack.js")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "public, max-age=3600"
