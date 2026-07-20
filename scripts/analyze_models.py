"""Analyze available models and recommend best options for 16GB VRAM."""
import json
import urllib.request

API = "http://localhost:13305/v1"

req = urllib.request.Request(f"{API}/models")
r = json.loads(urllib.request.urlopen(req).read())
models = r["data"]

print("=" * 80)
print("ALL AVAILABLE MODELS")
print("=" * 80)
for m in sorted(models, key=lambda x: x.get("size", 0) or 0):
    ctx = m.get("max_context_window", "N/A")
    labels = m.get("labels", [])
    size = m.get("size", 0)
    print(f"  {m['id']:<45} {size:>5.1f} GB  ctx={ctx:<8} labels={labels}")

print()
print("=" * 80)
print("RECOMMENDATIONS FOR 16GB VRAM")
print("=" * 80)
print()
print("BEST OPTIONS FOR LLM CORRECTION/SUMMARY:")
print()

# Qwen3.5-4B-MTP-GGUF test results
print("1. Qwen3.5-4B-MTP-GGUF (3.66 GB, 262K ctx)")
print("   - Already downloaded")
print("   - Huge 262K context window - handles full transcripts easily")
print("   - With max_tokens=16384+ it finishes thinking and produces correct output")
print("   - Our reasoning_content fallback already handles this model")
print("   - Speed: ~40s for short correction, but will be slower for long transcripts")
print("   - VRAM: 3.66 GB - plenty of headroom")
print()

print("2. gpt-oss-20b-mxfp4-GGUF (12.1 GB, 131K ctx, GPU via ROCM)")
print("   - Already downloaded")
print("   - Fastest: 9s for correction (GPU accelerated)")
print("   - 131K context window - good for most transcripts")
print("   - Outputs in BOTH content AND reasoning_content (redundant but works)")
print("   - VRAM: 12.1 GB - tight but fits in 16GB")
print("   - RISK: May OOM if Whisper model is also loaded on GPU")
print()

print("3. Bonsai-8B-gguf (1.16 GB, 65K ctx)")
print("   - Already downloaded")
print("   - Non-reasoning model - outputs directly in content")
print("   - Fast: 13s for correction")
print("   - 65K context - may truncate very long transcripts")
print("   - VRAM: 1.16 GB - minimal footprint")
print()

print("4. DeepSeek-Qwen3-8B-GGUF (5.25 GB, 131K ctx)")
print("   - Already downloaded")
print("   - 131K context - good for long transcripts")
print("   - CPU-only (no GPU label) - VERY SLOW (timed out at 30s)")
print("   - Not practical for real-time use")
print()

print("=" * 80)
print("RECOMMENDED DEFAULT CONFIGURATION")
print("=" * 80)
print()
print("For best balance of speed, quality, and context size:")
print("  correction_model: gpt-oss-20b-mxfp4-GGUF")
print("  summary_model:    gpt-oss-20b-mxfp4-GGUF")
print("  (fastest, GPU accelerated, 131K context)")
print()
print("If gpt-oss-20b causes OOM issues:")
print("  correction_model: Qwen3.5-4B-MTP-GGUF")
print("  summary_model:    Qwen3.5-4B-MTP-GGUF")
print("  (262K context, 3.66 GB, but slower)")
print()
print("For minimal VRAM usage:")
print("  correction_model: Bonsai-8B-gguf")
print("  summary_model:    Bonsai-8B-gguf")
print("  (1.16 GB, 65K context, non-reasoning)")
print()
print("NOTE: The reasoning_content fallback in correction.py and")
print("transcription.py already handles all reasoning models correctly.")
print("No code changes needed - just configure the model name in settings.")