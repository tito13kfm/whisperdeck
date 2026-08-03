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
    """The worker must precache /, /static/rack.min.js, and /static/rack.min.css.
    If any of these go missing, the install handler will reject the new
    worker and the offline shell will not exist."""
    import app as app_module
    fresh = TestClient(app_module.app)
    body = fresh.get("/sw.js").text
    assert "'/static/rack.min.js'" in body or '"/static/rack.min.js"' in body
    assert "'/static/rack.min.css'" in body or '"/static/rack.min.css"' in body
    # The navigation shell precache entry — must be a path string, not a
    # missing symbol.
    assert "'/'" in body or '"/"' in body


def test_sw_js_cache_header_independent_of_static_dir():
    """/sw.js must be no-cached even when /static/* is long-cached. The
    middleware applies the right rule per-path, not a global one."""
    import app as app_module
    fresh = TestClient(app_module.app)
    sw = fresh.get("/sw.js")
    css = fresh.get("/static/rack.min.css")
    assert sw.headers.get("cache-control") == "no-cache"
    assert css.headers.get("cache-control") == "public, max-age=3600"
    assert sw.headers.get("cache-control") != css.headers.get("cache-control")


def _served_cache_version(client):
    """The CACHE_VERSION literal as actually served by GET /sw.js."""
    import re
    body = client.get("/sw.js").text
    m = re.search(r"const CACHE_VERSION = '([^']*)'", body)
    assert m, f"no CACHE_VERSION declaration in served /sw.js:\n{body[:400]}"
    return m.group(1)


def _fake_static_tree(tmp_path, bundle_js: bytes):
    """A minimal BASE_DIR whose static/ holds the real sw.js plus stand-in
    fingerprinted assets, so a test can change 'the bundle' without touching
    the repo's own files."""
    from pathlib import Path
    real_sw = (Path(__file__).resolve().parent.parent / "static" / "sw.js").read_text(encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir(parents=True, exist_ok=True)
    (static / "sw.js").write_text(real_sw, encoding="utf-8")
    (static / "rack.min.js").write_bytes(bundle_js)
    (static / "rack.min.css").write_bytes(b".a{color:red}")
    (static / "index.html").write_bytes(b"<html></html>")
    return static


def test_sw_cache_version_includes_build_fingerprint():
    """The served worker's CACHE_VERSION must carry a content fingerprint on
    top of the hand-maintained literal.

    The worker is cache-first for static assets and its activate step only
    deletes caches whose name differs from the current CACHE_NAME, so a
    bundle change that leaves CACHE_VERSION alone pins existing clients to
    the old bundle forever (17 commits changed rack.min.js between the last
    two manual bumps). Serving a derived version removes the manual step.
    """
    import re
    import app as app_module
    fresh = TestClient(app_module.app)
    version = _served_cache_version(fresh)
    assert re.fullmatch(r"v\d+-[0-9a-f]{12}", version), (
        f"served CACHE_VERSION is {version!r}; expected the on-disk literal "
        f"plus a 12-hex build fingerprint, e.g. 'v3-1a2b3c4d5e6f'. The /sw.js "
        f"route's fingerprint substitution is missing or did not match."
    )
    on_disk = (app_module.BASE_DIR / "static" / "sw.js").read_text(encoding="utf-8")
    assert f"const CACHE_VERSION = '{version}'" not in on_disk, (
        "the fingerprint must be injected when serving, not committed into "
        "static/sw.js"
    )


def test_sw_fingerprint_changes_when_the_bundle_changes(tmp_path, monkeypatch):
    """Same worker source, different bundle bytes → different cache name.

    This is the invariant the whole mechanism exists for: without it a
    deployed bundle is invisible to any browser that already installed the
    previous worker.
    """
    import app as app_module
    fresh = TestClient(app_module.app)

    _fake_static_tree(tmp_path, b"console.log('bundle one')")
    monkeypatch.setattr(app_module, "BASE_DIR", tmp_path)
    before = _served_cache_version(fresh)

    _fake_static_tree(tmp_path, b"console.log('bundle two -- new feature')")
    after = _served_cache_version(fresh)

    assert before != after, (
        f"CACHE_VERSION stayed {before!r} across a bundle change — existing "
        f"clients would keep serving the old cached bundle"
    )
    # The human-readable half must survive; only the fingerprint moves.
    assert before.split("-")[0] == after.split("-")[0] != "", (
        f"expected the same literal prefix in {before!r} and {after!r}"
    )


def test_sw_fingerprint_is_stable_for_unchanged_assets(tmp_path, monkeypatch):
    """Same bundle bytes → same cache name, across separate requests.

    Guards the opposite failure: a version derived from a timestamp or a
    random value would satisfy the test above while busting the cache on
    every single request, which defeats the point of precaching entirely.
    """
    import app as app_module
    fresh = TestClient(app_module.app)

    _fake_static_tree(tmp_path, b"console.log('stable bundle')")
    monkeypatch.setattr(app_module, "BASE_DIR", tmp_path)

    first = _served_cache_version(fresh)
    second = _served_cache_version(fresh)
    assert first == second, (
        f"CACHE_VERSION changed between two requests with identical assets "
        f"({first!r} then {second!r}) — the worker would reinstall and "
        f"re-precache on every page load"
    )

    # And it must be reproducible from the asset bytes alone.
    assert app_module.sw_build_fingerprint() == first.split("-")[-1]


def test_index_no_cache_unchanged():
    """/sw.js was added to the no-cache list; / must keep its existing
    no-cache header from #140. Regression guard."""
    import app as app_module
    fresh = TestClient(app_module.app)
    resp = fresh.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"
