# Issue #176 wrong-directions.md — deepseek-pure

## AGENTS.md doc error: `explore-hard` agent key doesn't exist

AGENTS.md's model table references `explore-hard` as an agent. The live config only defines `explore` — attempting `subagent_type="explore-hard"` returned "Unknown agent." Used `explore` instead.

**Recommended fix:** Remove `explore-hard` from AGENTS.md or add it to oh-my-openagent.json.

## Investigate: `_SERIALIZED_JOB_KINDS` missing "assistant"

Issue body doesn't mention this. Sibling sweep found `_SERIALIZED_JOB_KINDS` in app.py L268-272 needed the new kind for transcript detail serialization. Added.

## Test: wrong-user access returns 404 not 403

Plan says "different user's job → 403." Endpoint scopes by `user_id == current_user.id` in the query filter, so a different user's job simply isn't found → 404. This is correct security practice (doesn't leak existence). Test adjusted.

## Test: unauthenticated POST returns 403 not 401

CSRF middleware runs before auth dependency injection on POST mutations. Unauthenticated POST returns 403 (CSRF blocks first), which is the existing behavior across all endpoints. Test adjusted.
