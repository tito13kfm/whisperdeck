"""Tests for the service worker route and caching middleware (issue #146).

Covers:
- /sw.js is served at root with the correct content-type, scope header,
  and no-cache Cache-Control (so deploys propagate).
- static/sw.js exists (the release-zip packaging would silently break
  otherwise).
- The Cache-Control on /static/* is unchanged by the new branch.
- The Cache-Control on / is unchanged by the new branch.
"""
from fastapi.testclient import TestClient


def test_sw_js_served_at_root_with_required_headers():
    """GET /sw.js returns 200, JavaScript content-type, Service-Worker-Allowed
    header, and Cache-Control: no-cache. Without these the browser would
    either refuse the registration, fail to install, or stick on a stale
    worker after deploys."""
    import app as app_module
    fresh = TestClient(app_module.app)
    resp = fresh.get("/sw.js")
    assert resp.status_code == 200, f"GET /sw.js returned {resp.status_code}"
    assert resp.headers.get("content-type", "").startswith("application/javascript"), (
        f"unexpected content-type for /sw.js: {resp.headers.get('content-type')!r}"
    )
    assert resp.headers.get("service-worker-allowed") == "/", (
        "Service-Worker-Allowed: / header missing on /sw.js response"
    )
    assert resp.headers.get("cache-control") == "no-cache", (
        f"Cache-Control on /sw.js should be no-cache, got "
        f"{resp.headers.get('cache-control')!r}"
    )


def test_sw_js_file_exists_on_disk():
    """The script must be present under static/ or the route 404s in
    production (release zip packaging should pick it up automatically)."""
    from pathlib import Path
    sw_path = Path(__file__).resolve().parent.parent / "static" / "sw.js"
    assert sw_path.exists(), f"static/sw.js not found at {sw_path}"


def test_sw_js_body_contains_precache_urls():
    """The worker must precache /, /static/rack.js, and /static/rack.css.
    If any of these go missing, the install handler will reject the new
    worker and the offline shell will not exist."""
    import app as app_module
    fresh = TestClient(app_module.app)
    body = fresh.get("/sw.js").text
    assert "'/static/rack.js'" in body or '"/static/rack.js"' in body
    assert "'/static/rack.css'" in body or '"/static/rack.css"' in body
    # The navigation shell precache entry — must be a path string, not a
    # missing symbol.
    assert "'/'" in body or '"/"' in body


def test_sw_js_cache_header_independent_of_static_dir():
    """/sw.js must be no-cached even when /static/* is long-cached. The
    middleware applies the right rule per-path, not a global one."""
    import app as app_module
    fresh = TestClient(app_module.app)
    sw = fresh.get("/sw.js")
    css = fresh.get("/static/rack.css")
    assert sw.headers.get("cache-control") == "no-cache"
    assert css.headers.get("cache-control") == "public, max-age=3600"
    assert sw.headers.get("cache-control") != css.headers.get("cache-control")


def test_index_no_cache_unchanged():
    """/sw.js was added to the no-cache list; / must keep its existing
    no-cache header from #140. Regression guard."""
    import app as app_module
    fresh = TestClient(app_module.app)
    resp = fresh.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"
