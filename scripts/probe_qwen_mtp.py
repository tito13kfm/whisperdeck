"""Test Qwen3.5-4B-MTP with sufficient max_tokens for correction."""
import json
import urllib.request
import time

API = "http://localhost:13305/v1"

body = {
    "model": "Qwen3.5-4B-MTP-GGUF",
    "messages": [
        {"role": "system", "content": "You output only what is requested, no commentary."},
        {"role": "user", "content": "Fix this transcript, correct errors and improve readability. Return only corrected lines:\nSpeaker A: hello how are you doing today\nSpeaker B: I am doing good thanks for asking\nSpeaker A: great did you finish the project report\nSpeaker B: yes I finished it last night its ready for review"}
    ],
    "max_tokens": 16384,
    "temperature": 0.2,
}

t0 = time.time()
req = urllib.request.Request(
    f"{API}/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
)
r = json.loads(urllib.request.urlopen(req, timeout=180).read())
elapsed = time.time() - t0

msg = r["choices"][0]["message"]
finish = r["choices"][0]["finish_reason"]
content = msg.get("content", "")
rc = msg.get("reasoning_content", "")
tokens = r["usage"]["total_tokens"]

print(f"Qwen3.5-4B-MTP with 16K max_tokens:")
print(f"  Time: {elapsed:.1f}s")
print(f"  finish: {finish}")
print(f"  content: {repr(content[:300])}")
print(f"  rc_len: {len(rc)}")
print(f"  tokens: {tokens}")