# Issue #149 - token-usage.md (variant: deepseek-pro)

## Summary

Straightforward single-task issue. No agent dispatches needed - all work done directly with bash, curl, edit, and write tools. Low token cost.

## Breakdown

| Step | Tool | Tokens (est.) | Notes |
|------|------|---------------|-------|
| Phase 0: gh issue view | bash | low | Single JSON response |
| Setup: git fetch, worktree | bash | low | Standard setup |
| Phase 1: Read index.html | bash (cat) | moderate | Full HTML, ~180 lines |
| Phase 1: Read rack.css | bash (head) | low | First 100 lines only |
| Phase 1: Fetch Google Fonts CSS | webfetch | moderate | Full CSS response with all subsets |
| Phase 1: Write investigation.md | write | low | Single file write |
| Phase 2: Download 8 woff2 files | bash (8x curl) | low | Binary files, no text output |
| Phase 2: Edit rack.css | edit | low | Single insertion |
| Phase 2: Edit index.html | edit | low | Single deletion |
| Phase 3: grep verification | bash | low | Quick confirmation |
| Phase 4: Commit + push | bash | low | Standard git ops |
| Phase 5: Write reports | write | low | Two small files |

## What worked well

1. No agent dispatches at all - the task was simple enough for direct tools. This is the ideal case.
2. Parallel curl downloads saved time - all 8 fonts downloaded in one batch.
3. `from_end=true` not needed since no background agents were used.
4. Static verification (grep) was sufficient; no server start/browser cycle needed.

## What could cut tokens next time

1. For similar self-hosting tasks, pre-compute the woff2 filenames from the Google Fonts API response and use a single curl loop instead of 8 parallel calls. Not a big difference here since bash output is minimal.
2. The Google Fonts CSS response includes all unicode subsets (vietnamese, latin-ext, cyrillic, etc.) for every weight. If this were a larger font family, filtering to latin-only would cut the response significantly. For 8 declarations the overhead was negligible.
