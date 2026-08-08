# Token usage — issue #149 run, variant minimax-m3

Quick retro on where tokens went and what would cut it next time. This is a
rough, qualitative split — exact counts live in OpenCode's usage panel for
this session, not anything I can read from inside the runner.

## Where the budget went

1. **Issue + investigation reading.** One `gh issue view`, three file reads
   via `Read`, one `cat` for rack.css, one `cat` for sw.js, four `grep` runs
   for "googleapis / gstatic / font-family / preconnect / @font-face". All
   necessary; nothing to cut.
2. **Google Fonts CSS download.** One `curl` (~10 KB, 234 lines, 26 @font-face
   blocks). Read the full file because the relevant URLs only show up in the
   `/* latin */` subset blocks and naming them needed the actual hash. Could
   be cut to a `grep -A 6 'latin \*/'` next time, but the one-shot full read
   was small enough that the grep + targeted read pipeline would save maybe
   200 tokens at most. Not worth optimizing.
3. **Implementation.** 3 file edits (rack.css, index.html, sw.js), 8
   `curl -o` for the font files. Mechanical. No agents.
4. **Static checks.** Three `python -c` scripts. Two of them crashed once
   (cp1252) and worked the second time with `encoding='utf-8'`. Cost: a few
   hundred tokens on the failed runs and their re-runs.
5. **Smoke test.** One `python -c` that mounted `/static` via FastAPI on a
   throwaway port (18791), curled 7 paths, killed the daemon. Caught the
   real concern: woff2 files are actually served with valid signatures via
   the same StaticFiles mount app.py uses. Worth keeping — but the next run
   can collapse the "200 + size + first 4 bytes" check into one assertion
   and skip printing per-row.
6. **Agent dispatches.** Zero. The change was small (1 comment + 8
   @font-face + 1 array append + 8 file downloads + 3-line removal) and
   fully specified by the investigation, so per the runner prompt's
   delegation-exception rule, this was transcription of a plan, not a
   "deep / ultrabrain" problem. Skipping agents saved the most tokens of
   anything in this run.

## What would cut it next time

1. **Default to `encoding='utf-8'`** in any one-off `python -c` over repo
   files. The cp1252 default crashed twice; the fix is one argument.
2. **Skip the per-row smoke test printout.** A single summary line ("8/8
   woff2 served, 4/4 static files served, 0 Google refs") is enough. The
   per-row table was nice for this run while I was learning the path, not
   for a repeat.
3. **Grep for `/* latin */` blocks** in the Google Fonts CSS instead of
   reading all 234 lines, but only if the file were bigger — at <10 KB the
   full read is fine.
4. **Pre-check issue cross-references with `gh issue view <N>`** before
   quoting them in implementation, instead of trusting the body. #149
   referenced #140 and #138 in ways that didn't match the live issues. A
   5-second `gh issue view 140 --json title` and `gh issue view 138
   --json title` would have caught that without the second-guessing.

## Local-cap notes

No local agents dispatched this run, so the 2-cap rule didn't apply. If a
follow-up run needs a local agent, the 2-cap is the binding limit per
AGENTS.md and the runner prompt.
