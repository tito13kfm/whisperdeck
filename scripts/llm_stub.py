"""Hermetic OpenAI-compatible LLM stub for the deep UX audit.

Stands in for a real correction/summary provider (Lemonade / Groq / etc.) so the
audit runs on a machine with no local LLM and no committed API keys. Deterministic,
offline, and slow-by-design so progress UI and the cancel-under-load race are observable.

Endpoints:
  GET  /v1/models           -> one model id (default: gpt-oss-20b-mxfp4-GGUF)
  POST /v1/chat/completions -> sleeps STUB_DELAY seconds, then:
        - if the prompt mentions "json" (summary/context extraction) -> returns the
          summary JSON schema the app expects ({short_summary, key_points,
          action_items, decisions}) so the summary pipeline completes instead of
          failing on a JSON parse error;
        - otherwise -> returns plain placeholder text (correction path).

Env:
  STUB_PORT   listen port (default 13305)
  STUB_MODEL  advertised model id (default gpt-oss-20b-mxfp4-GGUF)
  STUB_DELAY  seconds to sleep per completion (default 8) — long enough to see
              spinners/disabled states and to race the Queue cancel button.

Point the WhisperDeck "local"/"local_llm" provider at http://127.0.0.1:<STUB_PORT>/v1
and set the model to STUB_MODEL. See .claude/skills/e2e-ux-audit-deep/SKILL.md.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = float(os.environ.get("STUB_DELAY", "8"))
MODEL = os.environ.get("STUB_MODEL", "gpt-oss-20b-mxfp4-GGUF")
PORT = int(os.environ.get("STUB_PORT", "13305"))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "owned_by": "stub"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            req = {}
        if not self.path.startswith("/v1/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        time.sleep(DELAY)
        user_text = ""
        all_text = ""
        for m in req.get("messages", []):
            all_text += str(m.get("content", "")) + "\n"
            if m.get("role") == "user":
                user_text = str(m.get("content", ""))
        if "json" in all_text.lower():
            content = json.dumps({
                "short_summary": "[LLM-stub] Deterministic placeholder summary "
                                 f"of a {len(user_text)}-char transcript.",
                "key_points": ["Stub key point one", "Stub key point two"],
                "action_items": ["Stub action item"],
                "decisions": ["Stub decision"],
            })
        else:
            content = (
                "[LLM-stub output] Processed request of "
                f"{len(user_text)} chars. This is deterministic placeholder text "
                "produced by the UX-audit stub in place of a real model response."
            )
        self._send(200, {
            "id": "chatcmpl-stub", "object": "chat.completion",
            "created": int(time.time()), "model": req.get("model", MODEL),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2},
        })

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
