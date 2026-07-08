"""Test reasoning model behavior with different API parameters.

Tests:
1. Default behavior (content only, reasoning_content returned)
2. chat_template_kwargs.enable_thinking = false
3. reasoning_format = "none" 
4. Various models to find best fit for 16GB VRAM
"""
import json
import urllib.request
import urllib.error
import sys

API_BASE = "http://localhost:13305/v1"
TIMEOUT = 60

def chat_completion(model: str, extra_params: dict = None) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Reply with just the word 'hello' and nothing else."}
        ],
        "max_tokens": 100,
        "temperature": 0.01,
    }
    if extra_params:
        body.update(extra_params)
    
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8')[:200]}"}
    except Exception as e:
        return {"error": str(e)}


MODELS_TO_TEST = [
    # Reasoning models
    ("DeepSeek-Qwen3-8B-GGUF", "5.25 GB - reasoning, 131K ctx"),
    ("DeepSeek-R1-Distill-Qwen-1.5B-GGUF-Q4_K_M", "1.04 GB - reasoning, 131K ctx"),
    ("Qwen3.5-4B-MTP-GGUF", "3.66 GB - MTP/reasoning, 262K ctx"),
    ("Qwen3-0.6B-GGUF", "0.38 GB - reasoning, 40K ctx"),
    ("gpt-oss-20b-mxfp4-GGUF", "12.1 GB - reasoning, 131K ctx, on GPU (ROCM)"),
    # Non-reasoning models
    ("Bonsai-8B-gguf", "1.16 GB - non-reasoning, 65K ctx"),
]

# Test configurations
test_configs = [
    ("default (no extra params)", None),
    ('enable_thinking=false', {"chat_template_kwargs": {"enable_thinking": "false"}}),
    ('reasoning_format="none"', {"reasoning_format": "none"}),
]

print("=" * 100)
print(f"{'Model':<45} {'Config':<35} {'content':<50} {'reasoning_content':<50}")
print("=" * 100)

for model, desc in MODELS_TO_TEST:
    for config_name, extra_params in test_configs:
        result = chat_completion(model, extra_params)
        
        if "error" in result:
            content = f"ERROR: {result['error'][:60]}"
            reasoning = ""
        else:
            choices = result.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = (msg.get("content") or "(empty)")[:60]
                reasoning = (msg.get("reasoning_content") or "(none)")[:60]
            else:
                content = "no choices"
                reasoning = ""
        
        print(f"{model + ' (' + desc + ')':<45} {config_name:<35} {content:<50} {reasoning:<50}")

print("\n\nVRAM ANALYSIS:")
print("=" * 80)
print(f"Available VRAM: 16 GB")
print()
print("Models that fit comfortably in 16GB VRAM:")
print(f"  DeepSeek-Qwen3-8B-GGUF (5.25 GB) - YES, fits with headroom")
print(f"  DeepSeek-R1-Distill-Qwen-1.5B-GGUF-Q4_K_M (1.04 GB) - YES")
print(f"  Qwen3.5-4B-MTP-GGUF (3.66 GB) - YES")
print(f"  Qwen3-0.6B-GGUF (0.38 GB) - YES")
print(f"  Bonsai-8B-gguf (1.16 GB) - YES")
print(f"  gpt-oss-20b-mxfp4-GGUF (12.1 GB) - TIGHT, may cause OOM with other models loaded")
print()
print("RECOMMENDATIONS for LLM correction/summary with large context:")
print(f"  1. Qwen3.5-4B-MTP-GGUF (3.66 GB, 262K ctx) - BEST - huge context, fits with headroom")
print(f"     Use enable_thinking=false or reasoning_format=none to suppress thinking")
print(f"  2. DeepSeek-Qwen3-8B-GGUF (5.25 GB, 131K ctx) - GOOD - larger model, good context")
print(f"     Use enable_thinking=false to suppress thinking")
print(f"  3. Bonsai-8B-gguf (1.16 GB, 65K ctx) - OK but small context for long transcripts")