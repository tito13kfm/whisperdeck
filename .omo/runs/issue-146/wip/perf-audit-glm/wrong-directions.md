# Wrong directions — issue #146

## 1. File size in issue body is stale
Issue says "~155KB of JS+CSS". Actual: rack.js is 225KB alone, rack.css 25KB. Total static ~250KB.
Recommended fix: update issue body, or just ignore the byte count in the rationale.

## 2. AGENTS.md local/cloud labeling is wrong (confirmed)
AGENTS.md line ~127 listed `atlas`, `quick`, `writing`, `unspecified-low` as OpenRouter-only, not subject to the local cap.
Actual config `~/.config/opencode/oh-my-openagent.json`:
- `atlas` -> lemonade/Qwen3.5-4B-MTP-GGUF (local)
- `quick` -> lemonade/Qwen3-0.6B-GGUF (local)
- `writing` -> lemonade/Bonsai-8B-gguf (local)
- `unspecified-low` -> lemonade/Qwen3-0.6B-GGUF (local)
All four ARE local and DO share the 2-agent VRAM cap.
**Resolved upstream**: commit 172a689 ("docs: fix stale agent names and local/cloud split in AGENTS.md (#161)") merged this fix into master before this run branched. Worktree on origin/master tip already has the corrected AGENTS.md. Stub entered above was wrong on re-read; corrected here. Recommended AGENTS.md fix: already applied.

## 3. AGENTS.md names scout/plan as distinct agents
AGENTS.md model table lists `scout` and `plan`. Current config only has `explore` and `explore-hard`.
Not encountered this run because Phase 1 used `explore` directly. Logged for the record.

## 4. Issue's suggested snippet is structurally broken (issue body, not AGENTS.md)
Recorded for the issue-runner's "don't trust the snippet" instruction. The issue body's sw.js snippet registers `/static/sw.js` which defaults to scope `/static/` only — it cannot intercept navigations to `/` (which the snippet explicitly tries to cache via `STATIC_ASSETS = ['/', ...]`). Either the SW must move to `/sw.js` (chosen) or be served with `Service-Worker-Allowed: /` plus `scope:'/'` on register. Also the snippet omits `clients.claim()` in activate and has no `activate` handler at all (no old-cache cleanup, no first-install takeover). Investigation.md (Phase 1) captures the full list of seven snippet defects. Recommended issue-body fix: rewrite the snippet, or remove it and let the implementer read investigation.md.

## 5. Issue body file size fact wrong
Issue says "~155KB of JS+CSS". Actual (verified): rack.js 225818 B, rack.css 25846 B. Total ~250KB. Cosmetic; rationale still holds (the cache cuts bytes either way) but the number is ~1.6x low.