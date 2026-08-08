# Issue #269 — Wrong Directions

## app.py comment about error message

The PATCH error message at app.py:2001 was updated to include 'auto', but the new message re-uses the pre-existing wording pattern. No issue.

## No e2e selector changes needed

Grepped `tests/e2e/*.py` for "Meeting", "Dictation", "Voice Note" — zero matches. No e2e change needed.

## No local agents used

All agents in this config are cloud (`explore` → `openrouter/inclusionai/ling-3.0-flash:free`, `deep`/`ultrabrain` → `openrouter/deepseek/deepseek-v4-pro`). Only `atlas` and `writing` map to Lemonade. No local agents used in this run.

## verify_self_audit.py BUILD: esbuild not found

The mechanical checker reports two blocking findings: esbuild rebuild failed for `build:js` and `build:css`. This is a pre-existing infrastructure condition — the worktree has no `node_modules` (gitignored, not copied by `git worktree add`). The bundle was built manually using the main checkout's esbuild (`npx --prefix C:/Claude/whisperdesk esbuild ...`) and verified to contain the new strings ("Classifying", "Manual override", "auto"). Not a regression introduced by this change.