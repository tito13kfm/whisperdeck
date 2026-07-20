# Lemonade Server Reference

A local OpenAI-compatible inference server running on this machine. This
note captures the API surface, model list, and gotchas discovered while
integrating WhisperDeck with it for screenshot/demo data generation.
This information is also kept in the Serena memory `lemonade-server` if
that tool is available; this file is a redundant, plain-text copy.

## Connection

- **Base URL**: `http://localhost:13305`
- **API style**: OpenAI-compatible at `/v1/*`
- **Health**: `GET /api/v1/health` returns 200 with per-model status
- **Model list**: `GET /v1/models` or `GET /api/v1/models`

## Models

### TTS — `kokoro-v1` (CPU, Kokoro ONNX)

- Endpoint: `POST /v1/audio/speech`
- Request: `{"model": "kokoro-v1", "input": "text", "voice": "VOICE_NAME"}`
- Response: `audio/mpeg` (MP3) or `audio/wav` (with `response_format: "wav"`)
- 24 kHz mono MP3
- **CRITICAL GOTCHA**: voice names MUST include the suffix
  (e.g. `af_bella`, not `af`). Bare prefixes (`af`, `am`, `bf`, `bm`)
  return HTTP 200 with 0 bytes — a silent failure that looks like success.

  **Working voices** (10 total):

  | Region    | Female         | Male           |
  |-----------|----------------|----------------|
  | American  | `af_bella` `af_sky` `af_nicole` `af_sarah` | `am_adam` `am_michael` |
  | British   | `bf_emma` `bf_isabella`                    | `bm_george` `bm_lewis` |

### LLMs — `POST /v1/chat/completions`

| Model ID                                | Size  | Reasoning | Notes                              |
|-----------------------------------------|-------|-----------|------------------------------------|
| `Qwen3-0.6B-GGUF`                       | 0.4GB | yes       | Fastest, default tool-calling      |
| `Bonsai-8B-gguf`                        | 1.2GB | **no**    | Good for JSON output (non-reasoning) |
| `DeepSeek-R1-Distill-Qwen-1.5B-GGUF-Q4_K_M` | 1.0GB | yes | Distilled reasoning                 |
| `Qwen3.5-4B-MTP-GGUF`                   | 3.7GB | yes       | Vision capable                     |
| `DeepSeek-Qwen3-8B-GGUF`                | 5.3GB | yes       | Larger reasoning                   |
| `gpt-oss-20b-mxfp4-GGUF`                | 12GB  | yes       | ROCm llama.cpp backend             |
| `Qwen3-Coder-30B-A3B-Instruct-GGUF`     | 19GB  | yes       | Coding specialist                  |

### Transcription (Whisper)

- `Whisper-Tiny` (0.08GB), `Whisper-Large-v3` (3.1GB), `Whisper-Large-v3-Turbo` (1.6GB)
- Endpoint: `POST /v1/audio/transcriptions` (OpenAI format)

### Image

- `SD-Turbo` (5.2GB), 512x512, 4 steps
- Endpoint: `POST /v1/images/generations`

### Omni

- `LMX-Omni-5.5B-Lite` (9.3GB) — collection combining LLM + SD + Whisper + Kokoro

## WhisperDeck Integration

- Use the `Local / Custom` provider in WhisperDeck settings.
- `api_url`: `http://localhost:13305/v1`
- `api_key`: any **non-empty** string. WhisperDeck sends
  `Authorization: Bearer <key>` unconditionally; an empty key produces
  `Illegal header value b'Bearer '` errors before the request is sent.
  Lemonade ignores the header, so any non-empty placeholder works
  (e.g. `"not-needed"`).

### Transcription

- Pick any Whisper model. Transcription does not require JSON output,
  so reasoning models work fine here.

### Correction / summarization (JSON-mode)

- `services/correction.py` defines `_JSON_MODE_PROVIDERS = {"groq", "openai", "openrouter"}`.
  The `local` provider is NOT in that set, so it does not request JSON
  mode from the model.
- Reasoning models (Qwen3, DeepSeek-R1, gpt-oss) emit their thinking
  trace before any JSON, and `local` does not strip it. Result: the
  parser sees `"Okay, let's start by..."` and fails with
  `Model did not return valid JSON`.
- Workaround: use a **non-reasoning** LLM, e.g. `Bonsai-8B-gguf`. Or
  point `correction_provider` at a hosted provider with JSON mode.
- Summary jobs in particular need JSON output; the correction text
  passes through a more forgiving parser.

## Test Audio Generation

- `scripts/generate_test_audio.py` (in this repo) wraps Kokoro TTS
  into a 3-speaker meeting MP3 using `af_bella`/`am_adam`/`bf_emma`.
  It hard-codes the gotcha (suffixes required) and ffmpeg-concatenates
  the segments. Used to populate the README screenshots.

## Recipes

```bash
# Quick TTS check (1-second clip from a known-working voice)
curl -X POST http://localhost:13305/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro-v1","input":"test","voice":"af_bella"}' \
  -o /tmp/test.mp3

# LLM chat
curl -X POST http://localhost:13305/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-0.6B-GGUF","messages":[{"role":"user","content":"hi"}],"max_tokens":20}'

# Whisper transcription
curl -X POST http://localhost:13305/v1/audio/transcriptions \
  -H 'Content-Type: multipart/form-data' \
  -F 'model=Whisper-Large-v3-Turbo' \
  -F 'file=@some.wav'
```
</content>
</invoke>