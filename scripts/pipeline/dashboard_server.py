#!/usr/bin/env python3
"""Localhost dashboard for the opencode issue pipeline.

Serves dashboard.html plus a small read-only JSON API over two sources:

  1. The pipeline's own state, written by run-pipeline.ps1 into
     <repo>/.omo/pipeline/  (state.json, events.jsonl, logs/).
  2. The opencode server's REST API, for the live session feed: which sessions
     are running, which agent and model each uses, and every tool call with its
     input and output.

Stdlib only, binds to 127.0.0.1, and every handler is a GET. It never writes
anything and never touches a live session other than by HTTP read, which is what
makes it safe to point at a run that is in progress.

  python dashboard_server.py --port 4748 --repo-root C:\\Claude\\WhisperDeck \\
      --opencode-url http://127.0.0.1:4747
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

ARGS = None  # set in main()


# ------------------------------------------------------------------ opencode API

def opencode_get(path: str, params: dict | None = None, timeout: float = 20.0):
    """GET against the attached opencode server. Returns parsed JSON or raises."""
    if not ARGS.opencode_url:
        raise RuntimeError("no --opencode-url configured")
    query = dict(params or {})
    # Every session endpoint needs the project directory; without it the server
    # hangs instead of erroring.
    query.setdefault("directory", ARGS.repo_root)
    url = f"{ARGS.opencode_url.rstrip('/')}{path}?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_sessions():
    sessions = opencode_get("/session")
    out = []
    for s in sessions:
        model = s.get("model") or {}
        tokens = s.get("tokens") or {}
        time = s.get("time") or {}
        out.append(
            {
                "id": s.get("id"),
                "parentID": s.get("parentID"),
                "title": s.get("title"),
                "agent": s.get("agent"),
                "model": model.get("id"),
                "provider": model.get("providerID"),
                "cost": s.get("cost"),
                "tokens": {
                    "input": tokens.get("input"),
                    "output": tokens.get("output"),
                    "reasoning": tokens.get("reasoning"),
                },
                "created": time.get("created"),
                "updated": time.get("updated"),
            }
        )
    out.sort(key=lambda x: x.get("updated") or 0, reverse=True)
    return out


def _clip(value, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value = str(value)
    value = value.strip()
    if len(value) > limit:
        return value[:limit] + " …"
    return value


def _tool_headline(tool: str, args: dict | None) -> str:
    """One short line naming what a tool call actually did."""
    args = args or {}
    for key in ("filePath", "file_path", "path", "pattern", "command", "url",
                "query", "description", "prompt", "name"):
        if key in args and args[key]:
            return f"{tool}  {_clip(args[key], 160)}"
    if args:
        return f"{tool}  {_clip(args, 160)}"
    return tool


def session_feed(session_id: str, limit: int = 400):
    """Flatten a session's messages into a compact chronological feed."""
    messages = opencode_get(f"/session/{urllib.parse.quote(session_id)}/message")
    items = []
    for msg in messages:
        info = msg.get("info") if isinstance(msg, dict) else None
        if not isinstance(info, dict):
            info = msg if isinstance(msg, dict) else {}
        role = info.get("role") or msg.get("role") or "?"
        created = ((info.get("time") or {}) or {}).get("created")

        for part in msg.get("parts") or []:
            ptype = part.get("type")
            if ptype == "text":
                text = (part.get("text") or "").strip()
                if not text:
                    continue
                items.append(
                    {
                        "kind": "text",
                        "role": role,
                        "at": created,
                        "title": _clip(text, 600),
                        "detail": None,
                        "status": None,
                    }
                )
            elif ptype == "tool":
                state = part.get("state") or {}
                items.append(
                    {
                        "kind": "tool",
                        "role": role,
                        "at": created,
                        "tool": part.get("tool"),
                        "title": _tool_headline(part.get("tool") or "tool", state.get("input")),
                        "detail": _clip(state.get("output"), 800),
                        "status": state.get("status"),
                    }
                )
            elif ptype == "reasoning":
                text = (part.get("text") or "").strip()
                if text:
                    items.append(
                        {
                            "kind": "reasoning",
                            "role": role,
                            "at": created,
                            "title": _clip(text, 300),
                            "detail": None,
                            "status": None,
                        }
                    )
            elif ptype in ("step-start", "step-finish", "snapshot", "patch"):
                continue
    return items[-limit:]


# --------------------------------------------------------------- pipeline state

def read_state():
    p = Path(ARGS.repo_root) / ".omo" / "pipeline" / "state.json"
    if not p.exists():
        return {"status": "no-state", "note": f"no state file yet at {p}", "issues": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # A read that lands mid-write; the writer is atomic, so just retry next poll.
        return {"status": "state-unreadable", "note": str(exc), "issues": {}}


def read_events(after: int = 0, limit: int = 500):
    p = Path(ARGS.repo_root) / ".omo" / "pipeline" / "events.jsonl"
    if not p.exists():
        return {"nextIndex": 0, "events": []}
    events = []
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for idx, line in enumerate(fh):
            if idx < after:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec["_i"] = idx
            events.append(rec)
    total = after + len(events)
    return {"nextIndex": total, "events": events[-limit:]}


LOG_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def read_log(name: str, tail: int = 300):
    logdir = Path(ARGS.repo_root) / ".omo" / "pipeline" / "logs"
    if not LOG_NAME.match(name or ""):
        raise ValueError("bad log name")
    p = (logdir / name).resolve()
    if logdir.resolve() not in p.parents:
        raise ValueError("log outside log directory")
    if not p.exists():
        return {"name": name, "lines": [], "note": "no such log yet"}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"name": name, "lines": lines[-tail:], "total": len(lines)}


def list_logs():
    logdir = Path(ARGS.repo_root) / ".omo" / "pipeline" / "logs"
    if not logdir.exists():
        return []
    out = []
    for p in sorted(logdir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True):
        out.append({"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime})
    return out


# ----------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "pipeline-dashboard"

    def log_message(self, fmt, *args):  # quiet; the pipeline console is the log
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        def one(key, default=None):
            v = q.get(key)
            return v[0] if v else default

        try:
            if path in ("/", "/index.html"):
                html = (HERE / "dashboard.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
            elif path == "/api/config":
                self._json({"repoRoot": ARGS.repo_root, "opencodeUrl": ARGS.opencode_url})
            elif path == "/api/state":
                self._json(read_state())
            elif path == "/api/events":
                self._json(read_events(after=int(one("after", "0")),
                                       limit=int(one("limit", "500"))))
            elif path == "/api/logs":
                self._json(list_logs())
            elif path == "/api/log":
                self._json(read_log(one("file", ""), tail=int(one("tail", "300"))))
            elif path == "/api/sessions":
                self._json(list_sessions())
            elif path == "/api/feed":
                sid = one("session")
                if not sid:
                    self._json({"error": "session parameter required"}, 400)
                else:
                    self._json({"session": sid, "items": session_feed(sid)})
            else:
                self._json({"error": "not found"}, 404)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            self._json({"error": f"opencode server unreachable: {exc}"}, 502)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # keep the dashboard alive on any single bad poll
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4748)
    ap.add_argument("--repo-root", default=os.getcwd())
    ap.add_argument("--opencode-url", default=None,
                    help="base URL of an opencode server, e.g. http://127.0.0.1:4747")
    ARGS = ap.parse_args()
    ARGS.repo_root = str(Path(ARGS.repo_root).resolve())

    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print(f"dashboard: http://127.0.0.1:{ARGS.port}")
    print(f"  repo root:    {ARGS.repo_root}")
    print(f"  opencode url: {ARGS.opencode_url or '(none: session feed disabled)'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
