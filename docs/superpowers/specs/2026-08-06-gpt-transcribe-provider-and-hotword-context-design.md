# gpt-transcribe Provider + Hotword-as-Transcription-Context — Design Spec

**Goal:** Add OpenAI's `gpt-transcribe` model (released 2026-08-05, $0.0045/min, cheaper than `whisper-1`'s $0.006/min) as a selectable model for the existing `openai` and `openrouter` providers, and use its new structured context params (`keywords`, `languages`) — plus AssemblyAI's equivalent `keyterms_prompt` — to feed the user's existing hotword glossary into transcription itself, not just the post-hoc correction pass.

**Background:** `services/hotwords.py` currently states explicitly that the glossary "feeds the post-hoc correction pass, never the transcription-time prompt" — a deliberate prior design choice. `gpt-transcribe`'s new context API (free-form prompt + `keywords[]` array + `languages[]` array) and AssemblyAI's `keyterms_prompt[]` are dedicated hint mechanisms that didn't exist in the same form when that choice was made, so this spec proposes adding a second consumer of the glossary, not replacing the correction pass.

Diarization was evaluated separately (OpenAI's `gpt-4o-transcribe-diarize`) and rejected: capped at 4 known speakers via reference clips, redundant with the existing `services/voice_id.py` roster system which has no such cap and runs locally. Diarization is explicitly out of scope below.

**Confirmed limitation (live-tested against both OpenRouter and api.openai.com directly, 2026-08-06):** `gpt-transcribe` has no timestamp-bearing response mode, permanently. `response_format=verbose_json` is hard-rejected by OpenAI's own API for this model (`"response_format 'verbose_json' is not compatible with model 'gpt-transcribe-api-ev3'. Use 'json' or 'text' instead."`) — not an OpenRouter proxy gap. The plain `json` response is `{text, languages, usage}` only: no `segments`, no `words`. This is accepted as a known, permanent constraint of the model, not a bug to work around — `gpt-transcribe` is added purely as a flat-text, no-timeline model choice alongside the existing segment-producing providers, same tier as `builtin` (Whisper Tiny)'s "great for quick dictation, not full workflow quality" framing.

Also confirmed live: going through `openrouter.py` loses fidelity that `openai.py` (direct) has — OpenRouter's proxy strips the `languages` array from the response entirely and flattens `usage` to `{seconds, cost}` instead of OpenAI's native `{type: "duration", seconds}`. `backends/openai.py` (direct) is the full-fidelity path for this model; `openrouter.py` is a lesser-fidelity fallback.

## Scope

**In scope:**
- Add `gpt-transcribe` to `backends/openai.py`'s model list (`list_models()` currently filters to `"whisper" in id.lower()` — must widen to also match `"transcribe"`).
- Add `openai/gpt-transcribe` to `backends/openrouter.py`'s `_default_models()`.
- Fix pre-existing gap in `backends/openrouter.py`: it never forwards the `prompt` kwarg to the API at all (unlike `backends/openai.py`, which does). Fix while touching the same request-building code.
- Add `keywords: list[str]` and `languages: list[str]` to the `**kwargs` contract providers may read (no `BaseProvider` signature change needed, it's already `**kwargs`).
- `backends/openai.py` / `backends/openrouter.py`: when the configured model is `gpt-transcribe` (or the `gpt-4o-transcribe`/`gpt-live-transcribe` family), send `keywords` and `languages` in the multipart form in addition to (or, for `languages`, instead of — see note) the existing `language`/`prompt` fields.
- `backends/assemblyai.py`: send the glossary as `keyterms_prompt` (JSON array, up to 1000 terms) in the `_submit_transcript` body.
- Add `prompt` kwarg handling to `backends/groq.py`, `backends/replicate.py`, `backends/local.py`, `backends/moonshine.py` (currently none of these read it at all; `builtin.py` already does, via `initial_prompt`).
- `services/transcription.py`: before calling `provider.transcribe()`, fetch the caller's hotword glossary (`hotwords.list_hotwords(db, user_id)`) and pass it through as `keywords=[...]` (consumed differently per provider as above).
- `services/correction.py`'s existing post-hoc pass is unchanged and keeps running — glossary now has two consumers, not a replacement of one by the other.
- `backends/openai.py` duration handling: for `gpt-transcribe` there are no segments to scan for `max(s.end)`, so `duration_seconds` must fall back to `result.get("usage", {}).get("seconds", 0)` when `raw_segments` is empty.
- `services/diarization.py` / whatever calls `diarize_and_merge()`: must skip diarization (no-op, not an error) when the transcript has zero segments — there is nothing to merge speaker labels onto.
- Hotword glossary sanitization before sending as `keywords`: strip/reject terms containing `<`, `>`, carriage return, or line feed — OpenAI rejects the *entire* request if any keyword contains one of these. Same applies to the `prompt` field's length limit (exact limit not published; must be handled as a catchable 400, not pre-validated against a guessed number).

**Explicitly out of scope:**
- Any diarization change beyond the skip-on-empty-segments guard above (`gpt-4o-transcribe-diarize` rejected, see Background).
- UI changes to the hotword settings page — the glossary already exists and gets a second consumer, no new UI surface.
- Streaming transcription (`stream=true`, `transcript.text.delta` events) — not used anywhere in the app today, no current call site to attach it to.
- `gpt-live-transcribe` / realtime API — separate product surface, not the file-upload flow this app uses.
- Any UI/UX treatment of the resulting empty-timeline transcript beyond "don't crash" — `gpt-transcribe` transcripts render as a flat `full_text` blob with `segments = []`, same shape the UI must already tolerate for any provider that returns no segments. Confirming the UI actually handles this gracefully today is an implementation-time verification step, not assumed here.

## Resolved risks (confirmed via live test against tests/fixtures/O2C_CRP_1min.mp3, 2026-08-06)

1. **Multipart array encoding for `keywords`/`languages`: confirmed.** Repeated `key[]=` fields (`-F "keywords[]=term1" -F "keywords[]=term2" -F "languages[]=en"`), matching the convention already used by `known_speaker_names[]`, `include[]`, `timestamp_granularities[]` elsewhere in the API. Accepted with HTTP 200 by both OpenRouter and direct OpenAI.
2. **`languages` replaces `language` for `gpt-transcribe`, confirmed both in request (per OpenAI's own docs, "don't send both") and response** — direct OpenAI's `json` response includes `"languages":[{"code":"en"}]` in place of a `language` string. `backends/openai.py`'s language-parsing branch must special-case this: read `result.get("languages")` first for this model family, only falling back to `result.get("language")` for other models.
3. **AssemblyAI `keyterms_prompt` phrase limit (6 words/phrase) not re-verified against real glossary content** — still a minor open item, low risk given expected glossary term shapes (short product names/acronyms).

## Architecture / Data flow

1. `services/transcription.py:transcribe()` (around line 104, where `provider.transcribe(...)` is currently called) gains a step before that call: fetch `glossary = [h.term for h in list_hotwords(db, user_id)]`, and if non-empty, add `keywords=glossary` to the kwargs passed into `provider.transcribe()`.
2. Each provider's `transcribe()` interprets `kwargs.get("keywords")` per its own API shape:
   - `openai.py` / `openrouter.py` (gpt-transcribe family): `data["keywords"] = keywords`, `data["languages"] = languages_kwarg or [language]` in place of `data["language"]`.
   - `assemblyai.py`: `body["keyterms_prompt"] = keywords` in `_submit_transcript`.
   - `groq.py`, `replicate.py`, `local.py`, `moonshine.py`: join `keywords` into the existing free-text `prompt` kwarg path (comma-separated or sentence form, matching how `builtin.py` already uses `initial_prompt`).
3. No change to `services/correction.py` — it independently re-fetches the same glossary for its existing post-hoc pass, unchanged.

## Testing

No code or tests are written as part of this spec/session. Live curl tests against both OpenRouter and api.openai.com directly (2026-08-06, using `tests/fixtures/O2C_CRP_1min.mp3`) confirmed the response-shape and field-encoding facts stated above. Per project testing tiers (AGENTS.md), implementation still needs: unit coverage per touched backend (`tests/` already has per-backend test files for the existing providers to follow as a pattern), plus a real-key regression test asserting `duration_seconds` is correctly non-zero and diarization is skipped (not errored) for a `gpt-transcribe` transcript.
