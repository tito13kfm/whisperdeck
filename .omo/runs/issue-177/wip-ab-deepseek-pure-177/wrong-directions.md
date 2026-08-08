# Wrong Directions — Issue #177 deepseek-pure

## Issue body has unchecked tasks that are already done

The issue body lists both tasks as `[ ]` (unchecked):
- Add `export_directory` to DEFAULT_SETTINGS
- Add text input to settings UI

Both were implemented in earlier commits (709359f, 7767782) before the current master HEAD. The issue is stale — it should be marked as resolved or closed.

**Recommended fix:** Close issue #177 with a comment noting it was resolved by commits 709359f and 7767782.

## No other discrepancies

AGENTS.md agent-cap table check: did not need to dispatch any local agents, so the local/cloud labeling discrepancy was not exercised.
