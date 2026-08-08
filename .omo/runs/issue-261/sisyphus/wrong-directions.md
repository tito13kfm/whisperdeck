# Wrong directions (issue #261 investigation run)

## Settings file discrepancy

**Claim**: Plan doc line 97 says `services/settings.py: bulk_defaults.kind allowed values`.

**Reality**: `services/settings.py` has `DEFAULT_SETTINGS` dict with no `bulk_defaults` key. The `bulk_defaults` concept likely lives elsewhere (possibly only in the frontend or in bulk import validation within `app.py`).

**Fix**: Sub-issue #283 should grep for the actual bulk import kind validation site and update it there. The plan doc's file list is a conceptual mapping, not a literal file path.

## No label to add

The `gh issue create --label "voice-dump"` call failed because no such label exists on the repo. Skipped the label for all 5 sub-issues. Minor — labels are cosmetic only.
