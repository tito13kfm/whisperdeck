"""List all LLM models available on the Lemonade server."""
import json
import urllib.request

API = "http://localhost:13305/v1"

req = urllib.request.Request(f"{API}/models")
r = json.loads(urllib.request.urlopen(req).read())
models = r["data"]

print("CURRENT LLM MODELS:")
for m in models:
    mid = m["id"].lower()
    if "whisper" in mid or "kokoro" in mid or "sd-turbo" in mid:
        continue
    ctx = m.get("max_context_window", "?")
    size = m.get("size", 0)
    labels = m.get("labels", [])
    print(f"  {m['id']:<45} {size:>5.1f}GB  ctx={ctx:<8} labels={labels}")