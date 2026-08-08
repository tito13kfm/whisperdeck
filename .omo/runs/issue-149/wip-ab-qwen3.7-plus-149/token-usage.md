# Token Usage Analysis

## Worst Token Usage Areas

### 1. Explore agents for simple file reads (7m58s total)
- **Agent 1** (bg_69219ded): 3m10s to find and read rack.css
- **Agent 2** (bg_9cb1c4e8): 4m48s to find and read index.html
- **Why wasteful**: These were simple single-file reads. The agents had to search for the files first (glob failed, then ls, then read), which added overhead.
- **Better approach**: Use direct `read` tool with known paths. I knew from the issue body that the files were likely in `static/` or `templates/`. A quick `ls static/` would have found them in seconds.
- **Token savings**: ~80% reduction. Direct reads would take ~10s total vs 8 minutes.

### 2. Reading full fonts.css (234 lines)
- **Why wasteful**: I downloaded the full Google Fonts CSS and read all 234 lines, but only needed 8 specific URLs (the "latin" subset lines).
- **Better approach**: Use grep/sed to extract just the latin subset URLs directly from the curl output, without writing to file and reading.
- **Token savings**: ~90% reduction. Could have extracted URLs with one-liner: `curl ... | grep -A 6 "/* latin */" | grep "src:" | sed 's/.*url(\(.*\)).*/\1/'`

### 3. Multiple verification reads
- **What I did**: Read rack.css (70 lines), index.html (15 lines), then re-read sections to verify changes
- **Why wasteful**: I could have used `head`/`tail` or `grep` to verify specific sections without reading full file headers.
- **Better approach**: Use targeted grep/head commands for verification.
- **Token savings**: ~50% reduction on verification step.

## What Worked Well

### 1. Investigation.md written before implementation
- **Why good**: Forced me to enumerate all call sites and verify the issue's assumptions before coding.
- **Token impact**: Neutral. The time spent writing investigation.md was offset by not having to re-investigate during implementation.

### 2. Static verification before commit
- **Why good**: Used `file` command to verify woff2 validity, `grep` to confirm no Google Fonts references remained.
- **Token impact**: Low. These were fast bash commands, not agent calls.

### 3. Single commit with all changes
- **Why good**: Amended commit once to remove "Closes #149" (A/B test override), then pushed. No back-and-forth.
- **Token impact**: Minimal. One amend operation.

## Recommendations for Next Time

1. **Skip explore agents for known file paths**. If the issue mentions `index.html` and `rack.css`, just `ls static/` and read them directly. Save explore agents for "find all callers of X" or "trace the flow from Y to Z".

2. **Extract URLs with one-liners, not file reads**. When downloading fonts from Google Fonts CSS, pipe curl output through grep/sed to extract URLs directly, don't write to file and read.

3. **Use head/tail/grep for verification, not full file reads**. To verify changes, read only the affected sections (e.g., `head -60 rack.css | tail -50`), not the full file.

4. **Batch bash commands**. Instead of multiple separate bash calls for verification, combine them: `head -12 index.html && ls -1 static/fonts/ && grep -r "fonts.googleapis.com" static/ || echo "Clean"`.

## Estimated Token Savings

If I had followed these recommendations:
- Explore agents: 8 minutes → 10 seconds (save ~95% of agent time)
- Font URL extraction: 234-line read → 1-line grep (save ~90% of read tokens)
- Verification: 3 reads → 1 batched command (save ~60% of verification tokens)

**Overall**: This task could have been completed in ~2 minutes instead of ~10 minutes, with ~70% fewer tokens spent on exploration and verification.
