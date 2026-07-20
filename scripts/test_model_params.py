"""Test reasoning model behavior with different API parameters."""
import json
import urllib.request
import urllib.error

API = "http://localhost:13305/v1"

def test(model, label, extra_params=None):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hello in one word."}],
        "max_tokens": 100,
        "temperature": 0.01,
    }
    if extra_params:
        body.update(extra_params)
    
    req = urllib.request.Request(
        f"{API}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        msg = r["choices"][0]["message"]
        finish = r["choices"][0].get("finish_reason", "")
        content = msg.get("content", "")
        rc = msg.get("reasoning_content", "NOT_PRESENT")
        print(f"[{label}] finish={finish}")
        print(f"  content:          {repr(content[:100])}")
        print(f"  reasoning_content: {repr(rc[:100])}")
        print()
    except Exception as e:
        print(f"[{label}] ERROR: {e}")
        print()

# Test configurations for each reasoning model
models = [
    "DeepSeek-Qwen3-8B-GGUF",
    "Qwen3.5-4B-MTP-GGUF",
    "Qwen3-0.6B-GGUF",
    "DeepSeek-R1-Distill-Qwen-1.5B-GGUF-Q4_K_M",
    "gpt-oss-20b-mxfp4-GGUF",
    "Bonsai-8B-gguf",
]

configs = [
    ("default", None),
    ("reasoning_format=none", {"reasoning_format": "none"}),
    ("reasoning_format=deepseek", {"reasoning_format": "deepseek"}),
]

for model in models:
    print(f"=== {model} ===")
    for cfg_name, params in configs:
        test(model, cfg_name, params)
    print()