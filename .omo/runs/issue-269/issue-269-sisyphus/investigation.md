# Issue #269 — Studio Front Door: Auto Default

**Target**: Issue #269 (resolved from tracking issue #264, child issues #266-#268
complete, #269 is first open child with no merged PR closing it).

**Worktree**: `C:/Claude/whisperdesk-269-sisyphus` (branch `issue-269-sisyphus`, base `d6e8092`)
**Main checkout**: `C:/Claude/whisperdesk` (branch `tooling-verify-gate`)
**Reports**: `C:/Claude/whisperdesk/.omo/runs/issue-269/issue-269-sisyphus/`

## Design decisions (from `docs/superpowers/specs/2026-08-01-studio-classification-design.md`)

Relevant decisions:
- **Decision 5**: Mode picker stays visible, defaults to Auto. User can still pick explicitly.
- **Decision 6**: Persisted state model: `kind`, `classification_status`, `classification_confidence`, `classification_provenance`.
- **Decision 8**: Pending/uncertain/failed classification treats `effective_kind()` as None, safe fallback.

## What backend already does (from #266-#268)

- `_run_transcription_pipeline` (app.py:1073-1075): `kind="auto"` → stores placeholder `"meeting"` with `classification_status="pending"`.
- Upload (app.py:1373): accepts `"auto"` as valid kind.
- Bulk import (app.py:1450, 1466): accepts `"auto"` in both global and per-file settings.
- PATCH (app.py:2008-2011): setting kind explicitly records `classification_status="override"`.
- `effective_kind()` (services/classification.py:30-32): returns None while pending/uncertain/failed.
- Classification pipeline job exists (services/classification.py:46-85): runs as async LlmJob.

## What #269 actually needs to implement

Issue plan items mapped to current code:

### 1. Default mode to Auto (not Meeting)

**`S.mode` initialization** — `static/rack.js:810`:
```
S.mode = 'meeting';
```
→ Change to `S.mode = 'auto'`.

**Mode VFD wheel options** — `static/rack.js:1736`:
```
opts: ['Meeting', 'Dictation', 'Voice Note'], idx: S.mode === 'dictation' ? 1 : S.mode === 'voice_note' ? 2 : 0
```
→ Add "Auto" as first option:
```
opts: ['Auto', 'Meeting', 'Dictation', 'Voice Note'], idx: S.mode === 'meeting' ? 1 : S.mode === 'dictation' ? 2 : S.mode === 'voice_note' ? 3 : 0
```

**Mode VFD change handler** — `static/rack.js:1819`:
```
S.mode = ['meeting', 'dictation', 'voice_note'][newIdx];
```
→ Add Auto at index 0:
```
S.mode = ['auto', 'meeting', 'dictation', 'voice_note'][newIdx];
```

**`mfdSingleSpeaker()`** — `static/rack.js:1722`:
```
function mfdSingleSpeaker() { return S.mode === 'dictation' || S.mode === 'voice_note'; }
```
→ No change needed. `'auto'` is not single-speaker (speakers control stays available, diarization decided by pre-pass).

### 2. Bulk import defaults

**`DEFAULT_BULK_DEFAULTS.kind`** — `static/rack.js:2693`:
```
kind: 'meeting',
```
→ Change to `kind: 'auto'`.

**Bulk import kind selector** — `static/rack.js:2759-2762`:
```
<option value="meeting" ...>Meeting</option>
<option value="dictation" ...>Dictation</option>
<option value="voice_note" ...>Voice Note</option>
```
→ Add Auto as first option:
```
<option value="auto" ...>Auto</option>
<option value="meeting" ...>Meeting</option>
```

**Per-file bulk kind selector** — `static/rack.js:2820-2822`: same change, add Auto option.

### 3. Detail page classification status display

**`kindLabel`** — `static/rack.js:4678-4682`:
```
const kind = t.kind || 'meeting';
const kindLabel = kind === 'voice_note' ? 'Voice note' : (kind.charAt(0).toUpperCase() + kind.slice(1));
```
→ Show classification status:
- `classification_status === 'pending'`: "Classifying..." or kind if already has a placeholder
- `classification_status === 'failed'`: kind label + " (unconfirmed)"
- `classification_status === 'uncertain'`: kind label + " (unconfirmed)"
- `classification_status === 'success'`: kind label + confidence%
- `classification_status === 'override'`: kind label + " (manual)"

**Mode display cell** — `static/rack.js:4750`:
Currently just shows kindLabel button. Add classification provenance text beneath:
```
<div>Mode</div>
<button ... data-dact="toggle-kind">${kindLabel}</button>
${classificationStatusText}  <!-- e.g., "Classifying...", "85% confidence", "Manual override" -->
```

**Toggle button title** — `static/rack.js:4750`:
Change from `"Switch between meeting, dictation, and voice-note modes"` to `"Switch mode — explicit selection overrides auto-classification"`.

### 4. `toggle-kind` action — keep 3-state cycle

`static/rack.js:4854-4878`: The existing cycle (meeting→dictation→voice_note→meeting) is fine.
Explicitly picking a kind sets it as override. No Auto in the cycle — once you pick, you're
out of Auto. The user can switch back by a separate mechanism (or just pick any kind).

Note: This means there's no direct "back to Auto" button. The design says "explicit override
remains available where specified." The user who manually picks a kind has to stick with the
3-state cycle. Acceptable per decision 5 — "A user who already knows what they're recording
can still pick a kind explicitly."

### 5. [Computed from design — no issue explicit ask, but required for consistency]

**Detail page: show classification provenance** — Add a line beneath Mode showing where the
classification came from: provider + model for auto-classified, "(manual)" for override,
"(legacy)" for pre-classification transcripts.

### Sibling sweep

Checked every site that reads `S.mode` or displays kind:

| Site | Uses kind/S.mode | Change needed? |
|---|---|---|
| rack.js:1722 `mfdSingleSpeaker()` | `S.mode` for speakers control | No — auto not single-speaker |
| rack.js:1736 VFD mode opts | `S.mode` for display | Yes — add Auto |
| rack.js:1819 VFD change | Sets `S.mode` | Yes — add Auto |
| rack.js:2268 `startJob()` | Sends `S.mode` as kind | No — backend handles "auto" |
| rack.js:2759-2762 bulk kind | Bulk import defaults | Yes — add Auto |
| rack.js:2820-2822 per-file kind | Per-file overrides | Yes — add Auto |
| rack.js:4682 `kindLabel` | Detail page | Yes — show status |
| rack.js:4750 Mode cell | Detail page metadata | Yes — provenance |
| rack.js:4854 `toggle-kind` | Cycle handler | No — keeps 3-state |
| rack.js:1738 speakers idx | `single` guard | No — auto != single |
| rack.js:4799 voice-match | `t.kind !== 'dictation'` | **Check**: needs to use effective_kind too, but this is #268 territory. Existing behavior already uses raw `t.kind`. For #269 scope, when a transcript is auto-classified as 'dictation', this already works correctly because the backend stores the resolved kind after classification. Pending auto transcripts have `classification_status='pending'` and `kind='meeting'` (placeholder), so `t.kind !== 'dictation'` is true — voice-match would show for pending, but that's a server-side guard point, not a frontend one. Server-side rediarize already blocks pending (app.py:2511-2514 per design decision 11). Accept existing behavior for now. |

### E2E selectors

Checked `tests/e2e/` for hardcoded mode labels: no matches for "Meeting", "Dictation", "Voice Note" in e2e test files. No e2e selector updates needed.

### Test files to update/add

- `tests/test_transcript_kind_patch.py`: Add test for auto → explicit override path, test that patching to a kind sets override status.
- `tests/test_bulk_import.py`: Add test for `kind: "auto"` in global and per-file settings.
- `tests/test_serialize_transcript_contract.py`: Add test for auto-kind transcript with pending status serialization.

### Open question: PATCH rejects "auto" kind

Currently app.py:2000 rejects `"auto"` in PATCH:
```
if data["kind"] not in ("meeting", "dictation", "voice_note"):
```
Should PATCH accept "auto" to let the user reset a manual override back to auto-classification? The issue doesn't explicitly address this. Decision: Yes, PATCH should accept "auto" to let users revert to auto-classification. This is not in the issue text but follows from the design — if the mode picker offers Auto, the detail page's kind toggle should too. Without it, a user who accidentally picks a kind can't go back.