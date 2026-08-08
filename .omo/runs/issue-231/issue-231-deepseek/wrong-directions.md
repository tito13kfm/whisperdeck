# wrong-directions.md — Issue #231 run (issue-231-deepseek)

## 1. Investigation agent bulk_defaults recommendations were off

**What the agent recommended:**
```python
"bulk_defaults": {
    "provider": "moonshine",
    "model": "base",     # ← wrong: issue says ""
    "language": "en",     # ← wrong: issue says "auto"
    "diarize": False,
    "kind": "meeting",
    # Missing: auto_correct, num_speakers
},
```

**What the issue spec says:**
```python
"bulk_defaults": {
    "provider": "moonshine",
    "model": "",
    "language": "auto",
    "diarize": False,
    "auto_correct": True,
    "kind": "meeting",
    "num_speakers": None,
},
```

The agent dropped `auto_correct` and `num_speakers` (both in the spec) and changed `model`/`language` defaults. Caught during manual review, corrected in investigation.md.

**Fix:** None needed — corrected in investigation.md before any code was written. The deep agent category (`openrouter/deepseek/deepseek-v4-pro`) made simple data-entry errors. A future improvement would be to pass the issue's exact DEFAULT_SETTINGS snippet to the agent as a quoted literal rather than relying on it to copy from the description.

## 2. Issue spec doesn't mention _serialize_transcript_summary

The issue says "The _serialize_transcript() function adds one field" — but _serialize_transcript_summary (used for list views) also needs batch_id for forward compatibility with issue #234 (frontend batch grouping). Decided to include it anyway. This is a design judgment, not a spec error — the child issue (#234) will need it and adding it now costs one field.

## 3. No discrepancies in AGENTS.md or the issue-runner-prompt

All instructions in the workflow were accurate for this run. No tool failures, no misconfigurations detected.