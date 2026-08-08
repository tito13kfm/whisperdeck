# Wrong Directions — Issue #169 (deepseek-pro-r2)

## During execution

### 1. No migrations framework exists
**Expected**: The prompt says "check migrations/ directory" and references a migration pattern.
**Actual**: No `migrations/` directory exists. Tables are created via `Base.metadata.create_all(engine)` in database/__init__.py line 343.
**Impact**: Adding a new SQLAlchemy model that inherits from `Base` is sufficient — it auto-creates on next app start. No separate migration file needed.
**Recommended fix**: Update the canonical issue-runner prompt to note "check for migrations/ or create_all pattern" rather than assuming migrations/.

### 2. Issue has no explicit acceptance criteria
**Expected**: Phase 3 instructions say "Walk the issue's own acceptance criteria one by one."
**Actual**: Issue #169 body has no checklist, Definition of Done, or Requirements section. Criteria must be inferred from the body prose.
**Impact**: Self-audit checklist must synthesize criteria from the body rather than checking against an explicit list.
**No action needed** — handled in investigation.md's section 6.

### 3. glob tool for .opencode/ — already noted in CLAUDE.md
The /issueAB command instructions reference this as a "known doc error." The CLAUDE.md already documents this as a tool quirk. No new discrepancy.
