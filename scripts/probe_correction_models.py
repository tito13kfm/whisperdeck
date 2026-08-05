"""Test which models work well for transcript correction."""
import json
import urllib.request
import urllib.error
import time

API = "http://localhost:13305/v1"

def test(model, label, max_tokens=4096):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You output only what is requested, no commentary."},
            {"role": "user", "content": "Fix this transcript:\nSpeaker A: hello how are you\nSpeaker B: I am doing good thanks"}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    t0 = time.time()
    try:
        req = urllib.request.Request(
            f"{API}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        r = json.loads(urllib.request.urlopen(req, timeout=120).read())
        elapsed = time.time() - t0
        msg = r["choices"][0]["message"]
        finish = r["choices"][0].get("finish_reason")
        content = msg.get("content", "")
        rc = msg.get("reasoning_content", "")
        print(f"=== {label} ({elapsed:.1f}s) ===")
        print(f"  finish: {finish}")
        print(f"  content: {repr(content[:300])}")
        print(f"  reasoning_content len: {len(rc)}")
        print(f"  total_tokens: {r['usage']['total_tokens']}")
        print()
    except Exception as e:
        print(f"=== {label} === ERROR: {e}")
        print()


# Test all available LLM models for correction
print("=" * 60)
print("CORRECTION MODEL TEST")
print("=" * 60)
print()

test("Qwen3.5-4B-MTP-GGUF", "Qwen3.5-4B-MTP (3.66 GB, 262K ctx)", 8192)
test("Qwen3-0.6B-GGUF", "Qwen3-0.6B (0.38 GB, 40K ctx)", 4096)
test("gpt-oss-20b-mxfp4-GGUF", "gpt-oss-20b-mxfp4 (12.1 GB, 131K ctx, GPU)", 8192)
test("Bonsai-8B-gguf", "Bonsai-8B (1.16 GB, 65K ctx)", 4096)