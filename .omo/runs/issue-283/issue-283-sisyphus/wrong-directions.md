# wrong-directions.md — issue #283

## 1. Issue says "services/settings.py — add voice_dump to bulk_defaults.kind allowed values"

The bulk_defaults is a default-value dict (`"kind": "meeting"`), not a validation list. The actual kind validation lives in app.py's four validation tuples. No change needed in settings.py. The issue's phrasing is misleading — it conflates "default value" with "allowed values."

Recommendation: Rephrase as "app.py — add voice_dump to all kind validation tuples (single upload, bulk global, per-file override, retranscribe)."

## 2. Issue says "services/transcription.py — diarization default branch for voice_dump"

There is no diarization logic in transcription.py. The diarization force-off for voice_note/dictation lives in app.py:1150. The issue's "diarization default branch" actually refers to the summarize method's kind branching — voice_note gets a stub instead of a real summary. We added the same stub for voice_dump. The real diarization force-off was updated separately in app.py.

Recommendation: Clarify that this is two separate changes: (a) transcription.py summarize: add voice_dump stub branch, (b) app.py: add voice_dump to diarization force-off tuple.

## 3. Issue misses the retranscribe validation site

The four kind validation tuples — single upload (line 1444), bulk global (line 1540), and two others — all use the same tuple pattern. The issue names "bulk import" but not retranscribe (line 2074/2077). Complement Rule sweep found this.

## 4. Static/rack.js first edit shifted indentation

Lines 2763-2764 have 12 spaces while lines 2760-2762 have 14 spaces. The edit tool's oldString selector matched a different indentation level. Still valid HTML — no rendering impact.

## 5. verify_self_audit.py build check fails in worktree

Fresh worktrees have no `node_modules` (gitignored). The build check (`esbuild static/rack.js`, `esbuild static/rack.css`) fails with "esbuild not recognized." Both builds succeed from the main checkout using `npx esbuild`. The rack.min.js in the diff was rebuilt manually. This is a pre-existing infra issue, not caused by this change.

Recommendation: Have verify_self_audit.py detect whether esbuild is available and fall back gracefully with a note rather than a blocking error.
