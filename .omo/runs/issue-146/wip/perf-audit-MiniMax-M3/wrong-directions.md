# Wrong directions encountered during issue #146

## Issue #146's cross-reference to "#138" is stale

Issue #146's "Current Behavior" section says: *"Static assets rely
entirely on browser HTTP cache (which has no cache headers — see #138)"*.

`#138` is actually the MERGED exploratory planning doc
(*"docs: add exploratory planning doc for mobile capture and intent
routing"*) — a docs-only PR with no code paths touched. The actual
Cache-Control-headers work that the issue's "no cache headers" claim
is referring to is **#140** (`Add Cache-Control headers for static
assets`, CLOSED, already merged). The /static/* responses have carried
`Cache-Control: public, max-age=3600` for some time, and `/` carries
`Cache-Control: no-cache` (see `app.py:192-205`).

The "no cache headers" claim in #146 is therefore **stale** and does
not match current code. #146 is still valid (a service worker adds
*offline* and *cross-session-persistent* caching on top of the existing
1-hour HTTP cache, plus real install-time precache), but the rationale
in the body is wrong.

Recommended fix to the issue body: replace "see #138" with "see #140"
and reword "no cache headers" to "browser HTTP cache only lasts 1
hour and is lost on restart" — the *real* value the SW adds.

## Issue #146's suggested registration snippet has a scope bug

The issue's body shows:

```js
navigator.serviceWorker.register('/static/sw.js');
```

…which gives the worker scope `/static/` (the directory containing the
script, per the Service Worker spec). A SW with scope `/static/`
**cannot intercept** `GET /` (navigation) or `GET /api/*` traffic, so
the install precache of `'/'` and the `/api/*` fetch handling in the
issue's own fetch handler would silently never fire for out-of-scope
requests. The browser ignores `respondWith()` calls outside the
worker's scope.

The fix: serve the script at the root path `/sw.js` (with
`Service-Worker-Allowed: /` as defensive insurance) and pass
`{ scope: '/' }` at registration. The file still lives under
`static/` on disk so the release-zip packaging picks it up. This is
what this PR does.

Recommended fix to the issue body: replace
`register('/static/sw.js')` with `register('/sw.js', { scope: '/' })`
and note that `/sw.js` is served by a dedicated route, not by the
`/static/*` mount.

## `add_middleware` ordering comment is fragile

`app.py:209-212` carries a 4-line comment explaining the
add_middleware() prepend-to-stack rule and the order dependency
between `enforce_csrf` and `SessionMiddleware`. The same rule
applies to the new `static_cache_headers` branch — if anyone reorders
the middleware registration lines without re-reading this comment,
Cache-Control on /sw.js could be lost (or appear on responses that
shouldn't have it). The new comment on the `/sw.js` route mentions
the dependency but does not call out the ordering rule itself.

Not changing the existing comment, just flagging it as the kind of
invariant that is easy to break under refactor and hard to spot from
a single-route edit.
