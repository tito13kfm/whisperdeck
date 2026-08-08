# Issue #149 - wrong-directions.md (variant: deepseek-pro)

## Issue body assessment

**Accurate:** The issue correctly identifies the CDN dependency, the font families/weights needed, and the general approach (download woff2, add @font-face, remove links).

**Incomplete:** The issue's example @font-face snippet only shows one family/weight. A full implementation needs 8 declarations. This isn't wrong, just incomplete - the issue clearly says `/* ... repeat for each family/weight */`.

## Prompt/AGENTS.md discrepancies found this run

None. This was a straightforward issue with no branching, no multi-file call-site enumeration, and no document discrepancies encountered.

## Suggested prompt improvement

The Phase 3 instruction says "Check AGENTS.md's testing tiers for what this change requires." For a static asset change (fonts, CSS, HTML with no backend logic changes), AGENTS.md's tier 1 ("unit/integration test for the touched path") doesn't really apply - there are no backend units to test, and frontend integration tests would need a full browser setup. The static check (grep for remaining CDN references, verify @font-face count, verify woff2 file existence) was the appropriate verification. Consider adding a note that static-asset-only changes can use a static verification gate instead of a test suite run.
