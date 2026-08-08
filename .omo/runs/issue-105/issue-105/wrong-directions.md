# Wrong Directions — Issue #105

## Issue line numbers are stale

Issue says `app.py:1250-1251` for the PATCH endpoint. Actual PATCH endpoint is at `app.py:1535-1546`. Lines 1250-1251 are actually part of the POST /api/transcribe endpoint signature, not the PATCH handler. The issue's content is correct (the bug is real) but the line numbers are from an older version of app.py.

**Fix:** Update the issue body with correct line numbers (1544-1545) once this fix lands.

## Issue says queue.py:550-551

Current code has `clear_relabel_history` at queue.py:550-551 but the actual assignment is at line 552. Minor off-by-one in the issue text; the call is there.

## oracle agent unreachable

`meta/muse-spark-1.1` (the oracle model in oh-my-openagent.json) returned `invalid_api_key` from OpenRouter. Unable to run Phase 3.75 Oracle regression pass. Fallback: manual review (see self-audit.md).

**Fix:** Either configure a valid API key for the muse-spark model, or remap Oracle to a different model that works.

## explore-hard agent not available

The workflow says to use `explore-hard` for reasoning-heavy investigation, but this agent key does not exist in the current `oh-my-openagent.json` config. The available agents are: explore, scout, plan, general, etc. Used `explore` instead, which worked fine for this investigation.

**Fix:** Either add `explore-hard` back to the agent config (it's mentioned in AGENTS.md as recently remapped to cloud) or update the workflow to stop referencing it. Check the live config first — AGENTS.md says both explore and explore-hard are currently mapped to the same cloud model (ling-3.0-flash:free), so having two separate agent keys pointing at the same model may be redundant.
