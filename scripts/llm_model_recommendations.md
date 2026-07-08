# LLM Model Recommendations for 16GB VRAM

## The core problem
Reasoning models (DeepSeek, Qwen-mtp) output their chain-of-thought in `reasoning_content`, not `content`. When `max_tokens` is exhausted by the thinking, `content` comes back empty.

## What I tested

Models that **work** for correction (output correct `content`):
| Model | Size | Context | Speed | Notes |
|-------|------|---------|-------|-------|
| **gpt-oss-20b-mxfp4-GGUF** | 12.1 GB | 131K | **9s** (GPU) | Fastest, outputs in both content+reasoning |
| **Qwen3.5-4B-MTP-GGUF** | 3.66 GB | **262K** | 4-40s | With max_tokens=16384 it finishes thinking fine |
| **Bonsai-8B-gguf** | 1.16 GB | 65K | 13s | Non-reasoning, but limited context |

Models that **don't work well** for correction:
| Model | Problem |
|-------|---------|
| DeepSeek-Qwen3-8B-GGUF (5.25 GB) | CPU-bound, times out at 30s+ |
| DeepSeek-R1-Distill-Qwen-1.5B (1.04 GB) | Too small, poor quality |

## What I tried to suppress thinking

- **`chat_template_kwargs.enable_thinking=false`** → HTTP 400 error (Lemonade version doesn't support this)
- **`reasoning_format=none`** → Makes things WORSE (thinking goes into `content` wrapped in `<think>` tags)
- **`reasoning_format=deepseek`** → Same as default behavior

## The real fix: just increase max_tokens

With max_tokens=8192, Qwen3.5 consumed all tokens on thinking (finish_reason=length, content="").
With max_tokens=16384, it finished thinking (finish_reason=stop) and produced correct output in 4.4s.

Our existing `reasoning_content` fallback in correction.py and transcription.py already handles this correctly.

## Recommendations

### Option A: Use gpt-oss-20b-mxfp4-GGUF (BEST)
- Already downloaded, GPU-accelerated (9s), 131K context
- Outputs correct `content` with `reasoning_content` also populated
- 12.1 GB fits in 16GB VRAM (but tight if Whisper also on GPU)
- Set in settings: `correction_model: gpt-oss-20b-mxfp4-GGUF`

### Option B: Use Qwen3.5-4B-MTP-GGUF (BEST for large transcripts)
- Already downloaded, 3.66 GB, massive 262K context
- Need to ensure max_tokens >= 16384 (already 8192 in code, needs doubling)
- 4.4s for short corrections, slower for long transcripts
- Our `reasoning_content` fallback already handles it

### Option C: Use Bonsai-8B-gguf (BEST for low VRAM)
- Already downloaded, only 1.16 GB, non-reasoning
- 65K context - good for most transcripts
- Fast (13s), outputs directly in `content`
- No reasoning overhead at all

### Recommended configuration
```
correction_model: gpt-oss-20b-mxfp4-GGUF
summary_model:    gpt-oss-20b-mxfp4-GGUF
```
With fallback to Qwen3.5-4B-MTP if VRAM is tight.

## The reasoning_content fallback was the right fix
The code change to use `msg.get("reasoning_content") or msg.get("content") or ""` was correct and sufficient. No additional API hacks needed.