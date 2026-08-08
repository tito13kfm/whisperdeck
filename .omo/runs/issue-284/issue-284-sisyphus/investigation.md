# Issue #284 Investigation: Voice dump backend LLM chain

**Target**: #284 (tracking issue #261, sub-issue)
**Worktree**: `C:\Claude\whisperdesk-issue-284-sisyphus` (branch `issue-284-sisyphus`, from `origin/master` @ f28e254)
**Main checkout**: `C:\Claude\whisperdesk` (branch `tooling-verify-gate`)

## What #283 already delivered (confirmed)

| What | Where | Status |
|------|-------|--------|
| `voice_dump` in VALID_KINDS | `services/llm_jobs.py:23` | Exists |
| `voice_dump` in IO_KINDS | `services/llm_jobs.py:42` | Exists |
| `voice_dump` NOT in AUTO_RETRY_KINDS | `services/llm_jobs.py:35` | Confirmed — intentional |
| `voice_dump` NOT in CPU_KINDS | `services/llm_jobs.py:43` | Confirmed — not CPU-bound |
| VoiceDumpItem model | `database/__init__.py:193-216` | Exists |
| Transcript.voice_dump_items relationship | `database/__init__.py:75` | Exists |
| Diarization force-off | `services/transcription.py:222-226` | Exists |
| Summary stub | `services/transcription.py:222-244` | Exists |
| `voice_dump_job: None` stubs (all 3 branches) | `app.py:_dictation_job_fields` lines 437, 446, 453 | Exists |
| Serializer contract test | `tests/test_serialize_transcript_contract.py` | Exists |
| Kind picker UI | `static/rack.js` | Exists |
| rebuilt rack.min.js | `static/rack.min.js` | Exists |

## What #284 must deliver

Three items, in order:

1. **Refactor `structure_voice_note` → extract `_structure_from_text(text, note_type, ...)`**
   - `structure_voice_note` (lines 170-202) currently does: get text, build prompt, call LLM, parse JSON, fallback
   - Extract the core into `_structure_from_text(text, note_type, api_key, provider_name, provider_config, model) -> dict`
   - `structure_voice_note` becomes: `text = _transcript_text(transcript); return await _structure_from_text(text, note_type, ...)`
   - `_structure_prompt` is already text-parameterized via `{text}` — no change needed
   - `_generate` handles all provider/model resolution — no change needed

2. **New `segment_voice_dump(transcript, api_key, provider_name, provider_config, model) -> list`**
   - One LLM call, prompt asks to split raw transcript into `[{span_text, tentative_type}]`
   - Types are the same as voice_note: `todo | idea | reminder | journal | general`
   - Returns list of `{span_text, tentative_type}` dicts only, not full bodies
   - Falls back to single-item list on parse error (the caller loops over it anyway)
   - `feature_name="VoiceDump"` so resolve_model picks up voice_dump settings

3. **New `run_voice_dump_job` dispatch in `run_llm_job`**
   - Insert new elif after `voice_note` (line 582), before `rediarize` (line 583)
   - Pipeline: segment → loop `_structure_from_text` per span → assemble `result_json = {"items": [...]}`
   - Each per-item call also requests `"clarifying_questions": [...]` in the prompt
   - `progress_total` = segment count + 1, incremented per completed call
   - Cancellation check between calls, same pattern as voice_note (db.refresh + status check)
   - `result_json` written to job, VoiceDumpItem rows NOT created here (that's #285's finalize endpoint)

## Refactor plan for `structure_voice_note` → `_structure_from_text`

Current (lines 170-202):
```python
async def structure_voice_note(
    transcript, note_type: str,
    api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> dict:
    if note_type not in NOTE_TYPES:
        note_type = "general"
    text = _transcript_text(transcript)
    prompt = _structure_prompt(note_type).replace("{text}", text)
    try:
        content = await _generate(prompt, api_key, provider_name, model, provider_config, json_mode=True)
        data = json.loads(content)
    except Exception:
        return {
            "type": note_type,
            "title": (text.strip().splitlines()[0] if text.strip() else "Voice note")[:80],
            "body": text,
            "structured": {},
        }
    return {
        "type": note_type,
        "title": (data.get("title") or "").strip()[:255],
        "body": (data.get("body") or "").strip(),
        "structured": data.get("structured") if isinstance(data.get("structured"), dict) else {},
    }
```

After refactor:
```python
async def _structure_from_text(
    text: str, note_type: str,
    api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> dict:
    """Run the per-type structure prompt against an already-extracted text
    string. Same contract as structure_voice_note but takes text directly
    so the voice-dump path can call it per-span without a transcript object."""
    if note_type not in NOTE_TYPES:
        note_type = "general"
    prompt = _structure_prompt(note_type).replace("{text}", text)
    try:
        content = await _generate(prompt, api_key, provider_name, model, provider_config, json_mode=True)
        data = json.loads(content)
    except Exception:
        return {
            "type": note_type,
            "title": (text.strip().splitlines()[0] if text.strip() else "Voice note")[:80],
            "body": text,
            "structured": {},
        }
    return {
        "type": note_type,
        "title": (data.get("title") or "").strip()[:255],
        "body": (data.get("body") or "").strip(),
        "structured": data.get("structured") if isinstance(data.get("structured"), dict) else {},
    }

async def structure_voice_note(
    transcript, note_type: str,
    api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> dict:
    text = _transcript_text(transcript)
    return await _structure_from_text(text, note_type, api_key, provider_name, provider_config, model)
```

## `segment_voice_dump` design

```python
async def segment_voice_dump(
    transcript,
    api_key: str = "", provider_name: str = "groq",
    provider_config: dict | None = None, model: str = "",
) -> list[dict]:
    """Split a long multi-topic voice-dump transcript into ordered spans.
    Returns [{span_text, tentative_type}] — spans + labels only, no full
    bodies (avoid truncation). Falls back to single-item list on error."""
    text = _transcript_text(transcript)
    prompt = (
        "The following is a raw speech-to-text transcript of a continuous "
        "voice capture session where the speaker dictated multiple separate "
        "items one after another (bugs, ideas, todos, reminders, journal "
        "entries, or general notes).\n\n"
        "Split this transcript into individual items. For each item, include "
        "the exact span of text belonging to that item and classify its type "
        "into one of:\n"
        "- \"todo\": a list of things to do, a task, a plan of action\n"
        "- \"idea\": a concept, an observation, something to think about\n"
        "- \"reminder\": something the speaker wants to be reminded of later\n"
        "- \"journal\": a personal reflection, a moment being recorded\n"
        "- \"general\": none of the above fit well\n\n"
        "Respond with a JSON array: "
        "[{\"span_text\": \"the exact transcript text for this item\", "
        "\"tentative_type\": \"todo\"}, ...]\n\n"
        f"TRANSCRIPT:\n{text}"
    )
    try:
        content = await _generate(
            prompt, api_key, provider_name, model,
            provider_config, json_mode=True,
        )
        items = json.loads(content)
        if isinstance(items, list) and len(items) > 0:
            # Validate each item has required keys
            valid = [
                {"span_text": item.get("span_text", "").strip(),
                 "tentative_type": item.get("tentative_type", "general")}
                for item in items
                if isinstance(item, dict) and item.get("span_text", "").strip()
            ]
            if valid:
                return valid
    except Exception:
        pass
    # Fallback: entire transcript as one general item
    return [{"span_text": text.strip(), "tentative_type": "general"}]
```

## `run_voice_dump_job` dispatch design

Inserted in `run_llm_job` as new elif after `voice_note` (after line 582):

```python
elif job.kind == "voice_dump":
    # Segment → structure per span → assemble items array.
    # progress_total = N spans + 1; one tick per structure call.
    from services.voice_notes import segment_voice_dump, _structure_from_text
    segments = await segment_voice_dump(
        transcript, api_key=api_key, provider_name=job.provider,
        provider_config=provider_config, model=job.model,
    )
    job.progress_total = len(segments) + 1
    db.commit()
    items = []
    for i, seg in enumerate(segments):
        db.refresh(job)
        if job.status == "cancelled":
            return
        result = await _structure_from_text(
            seg["span_text"], seg.get("tentative_type", "general"),
            api_key=api_key, provider_name=job.provider,
            provider_config=provider_config, model=job.model,
        )
        items.append({
            "index": i,
            "type": result.get("type", "general"),
            "title": result.get("title", ""),
            "body": result.get("body", ""),
            "structured": result.get("structured", {}),
            "clarifying_questions": [],
        })
        job.progress_done = i + 1
        db.commit()
    job.result_json = {"items": items}
    job.progress_done = len(segments)
    db.commit()
    _finish(db, job, "completed")
```

## Sibling sweep

- `voice_dump` already in VALID_KINDS and IO_KINDS — no new kind-wiring needed
- `voice_dump` already in `_dictation_job_fields` stubs (app.py lines 437, 446, 453) — no new serialization wiring needed for this phase
- `voice_dump` NOT in AUTO_RETRY_KINDS — this job kind must survive a browser reload (draft in result_json), retry would clobber
- No other LLM chain has a text-taking refactor pattern — `_structure_from_text` is the only extraction
- `_generate` takes `feature_name` param — need to pass `"VoiceDump"` so resolve_model routes correctly
- `classify_pipeline` case (line 446-486) has its own retroactive voice_note enqueue — `voice_dump` doesn't need auto-enqueue from classify
- Existing tests (`test_voice_note_chain.py`) must still pass — refactor must be behavior-preserving

## What the issue's suggested approach gets right
- Extracting `_structure_from_text` is the right refactor
- Segmentation + per-span structure is the right two-phase design
- `progress_total = segment count + 1` is correct
- Clarifying questions folded into structure prompt is correct (no extra call)

## What the issue's suggested approach is missing/sketchy on
- Doesn't specify the `feature_name` for `_generate` calls (should be `"VoiceDump"`)
- Doesn't specify that `_structure_from_text` should handle its own JSON parse + fallback (currently inside `structure_voice_note`, extraction moves it)
- Doesn't mention the `_generate` wrapper already has `feature_name` param and `resolve_model` call — the voice_dump calls just need a different feature_name value
- The clarifying_questions key: the structure prompt needs to be extended to request these, OR we add them as a post-processing note. Per the design doc (plan): "Extend the structure prompt's requested JSON keys with an optional `clarifying_questions` key". However, this changes the existing voice_note prompt behavior. The safer approach is to add a separate clarifying_questions key in the voice_dump path only, by having `_structure_from_text` optionally accept an `include_clarifying` param that extends the prompt.
