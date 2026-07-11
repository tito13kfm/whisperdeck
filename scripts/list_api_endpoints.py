"""Cross-check the API endpoints the SPA calls against the routes app.py defines.

The SPA (static/rack.js) makes every request through its `api(path, opts)`
wrapper, building paths by string concatenation, e.g.:

    api('/api/transcripts/' + t.id + '/summarize', {...})

so this script extracts the first argument of every api() call, collapses
each non-literal fragment to a `{p}` placeholder, and matches the result
against the literal paths in app.py's @app.get/post/put/delete/patch
decorators (path params likewise collapsed to `{p}`). Template literals are
handled the same way: `${...}` interpolations collapse to `{p}`.

A call whose path is built entirely from a variable (e.g.
`api('/api/' + S.authMode)`) can't be resolved statically; annotate it at
the call site with the paths it can take and the script treats them as
called literals:

    api('/api/' + S.authMode /* api-paths: /api/login /api/register */, ...)

Output: every server route with its methods, marked [SPA] if the frontend
calls it. Exits 1 if extraction comes up empty (a broken regex should fail
loudly, not print nothing) or if the SPA calls a path no route serves.
Call arguments that mention /api/ but can't be resolved to a checkable
path are printed as warnings so parser blind spots are visible.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ROUTE_RE = re.compile(r'@app\.(get|post|put|delete|patch)\(\s*"([^"]+)"')
LITERAL_RE = re.compile(r"""^\s*('[^']*'|"[^"]*")\s*$""")
TEMPLATE_RE = re.compile(r"^\s*`([^`]*)`\s*$")
ANNOTATION_RE = re.compile(r"/\*\s*api-paths:\s*([^*]+)\*/")


def _first_arg(src: str, start: int) -> str:
    """Return api()'s first argument: scan from just past `api(` to the
    comma or closing paren at depth 0, respecting quotes and nesting."""
    depth, i, quote = 0, start, None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            if depth == 0:
                return src[start:i]
            depth -= 1
        elif ch == "," and depth == 0:
            return src[start:i]
        i += 1
    return src[start:i]


def _split_operands(arg: str) -> list[str]:
    """Split a JS expression on `+` at depth 0, outside quotes."""
    parts, buf, depth, quote = [], [], 0, None
    skip = False
    for ch in arg:
        if skip:
            buf.append(ch)
            skip = False
            continue
        if quote:
            buf.append(ch)
            if ch == "\\":
                skip = True
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif ch == "+" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def normalize(path: str) -> str:
    """Collapse every path parameter to {p} and drop any query string."""
    path = path.split("?")[0]
    path = re.sub(r"\{[^}]+\}", "{p}", path)
    return path.rstrip("/")


def server_routes() -> dict[str, set[str]]:
    """Map normalized route path -> set of HTTP methods, from app.py."""
    src = (REPO / "app.py").read_text(encoding="utf-8")
    routes: dict[str, set[str]] = {}
    for method, path in ROUTE_RE.findall(src):
        if "/api/" not in path:
            continue
        routes.setdefault(normalize(path), set()).add(method.upper())
    return routes


def spa_paths(src: str | None = None) -> tuple[set[str], list[str]]:
    """Normalized paths the SPA passes to api(), concat fragments -> {p}.

    Returns (paths, suspicious): suspicious holds the collapsed form of any
    argument that mentions /api/ but doesn't resolve to a checkable /api/...
    path (e.g. a variable prefix like BASE + '/api/x' collapsing to
    '{p}/api/x'), so parser blind spots warn instead of vanishing."""
    if src is None:
        src = (REPO / "static" / "rack.js").read_text(encoding="utf-8")
    paths: set[str] = set()
    suspicious: list[str] = []
    # api() covers JSON calls; new Audio(...) streams audio endpoints.
    for m in re.finditer(r"\bapi\(|\bnew Audio\(", src):
        arg = _first_arg(src, m.end())
        # An /* api-paths: ... */ annotation names the concrete paths a
        # dynamically-built argument can take; count them as called literals
        # and skip operand parsing (the annotation is the full path set).
        note = ANNOTATION_RE.search(arg)
        if note:
            for token in note.group(1).split():
                if token.startswith("/api/"):
                    paths.add(normalize(token))
            continue
        parts = []
        for op in _split_operands(arg):
            tpl = TEMPLATE_RE.match(op)
            if tpl:
                # `${...}` -> {p}; nested braces inside ${} are not handled,
                # which is fine for the flat expressions rack.js uses.
                parts.append(re.sub(r"\$\{[^}]*\}", "{p}", tpl.group(1)))
                continue
            lit = LITERAL_RE.match(op)
            if lit:
                parts.append(lit.group(1)[1:-1])  # strip the quotes
            elif op.strip():
                parts.append("{p}")  # variable/expression fragment
        # Collapse adjacent placeholders ('' + a + b + '' patterns).
        joined = re.sub(r"(\{p\})+", "{p}", "".join(parts))
        if joined.startswith("/api/"):
            paths.add(normalize(joined))
        elif "/api/" in joined:
            suspicious.append(joined)
    return paths, suspicious


def _compatible(route: str, called: str) -> bool:
    """Segment-wise match: a `{p}` on either side matches any one segment
    (the SPA may pass a literal like `correction` where the server declares
    `{kind}`, or a variable where the route is literal)."""
    a, b = route.split("/"), called.split("/")
    if len(a) != len(b):
        return False
    return all(x == y or x == "{p}" or y == "{p}" for x, y in zip(a, b))


def main() -> int:
    routes = server_routes()
    called, suspicious = spa_paths()
    if not routes:
        print("ERROR: no routes extracted from app.py — extractor broken?")
        return 1
    if not called:
        print("ERROR: no api() calls extracted from rack.js — extractor broken?")
        return 1

    # A path like /api/{p} has no literal segment left to anchor on; it's
    # built entirely from a variable and can't be checked statically.
    dynamic = {p for p in called if set(p.split("/")[2:]) <= {"{p}"}}
    checkable = called - dynamic

    used = {r for r in routes if any(_compatible(r, c) for c in checkable)}
    unmatched = sorted(
        c for c in checkable if not any(_compatible(r, c) for r in routes)
    )

    for path in sorted(routes):
        mark = "[SPA]" if path in used else "     "
        methods = ",".join(sorted(routes[path]))
        print(f"{mark} {methods:<22} {path}")

    print(f"\n{len(routes)} server routes, {len(called)} distinct SPA call paths")
    for path in sorted(dynamic):
        print(f"skipped (fully dynamic, can't check statically): {path}")
    for form in suspicious:
        print(f"WARNING: unresolvable call argument mentions /api/: {form}")
    if unmatched:
        print("\nSPA calls with NO matching server route:")
        for path in unmatched:
            print(f"  {path}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
