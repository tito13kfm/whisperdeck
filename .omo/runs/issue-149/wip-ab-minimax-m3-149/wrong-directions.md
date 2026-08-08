# Wrong directions found in this run

Things this run hit that disagreed with the inputs the runner was given. For future runs, not backfill material.

## 1. Issue body file path is wrong (high confidence)

Issue #149 says "index.html:7-9" with the two `<link rel="preconnect">` and the
Google Fonts `<link rel="stylesheet">`. The actual file is at
`static/index.html:7-9`, not at the repo root. The line numbers happened to
match anyway because the head is small and the linker tags are still at the
top, but a different change in the future might cite lines that have shifted
if the file moves. The investigation should always `ls` to confirm the path
before editing.

## 2. Issue's cross-references to #140 and #138 are stale/wrong (high confidence)

The issue body says:

- "No `font-display: swap` (see #140)" — `#140` is actually "Add Cache-Control
  headers for static assets" (CLOSED, merged). It has nothing to do with
  `font-display`. The actual `font-display: swap` is implicit in the Google
  Fonts URL the issue itself includes (`&display=swap`), so the claim "no
  font-display swap" is also factually wrong about the live page: the served
  CSS from Google already has `font-display: swap` on every @font-face block.
  The only gap was the absence of an explicit local `@font-face` block, which
  the fix adds.
- "Combined with #138 (cache headers)" — `#138` is "docs: add exploratory
  planning doc for mobile capture and intent routing" (MERGED, unrelated).
  The cache-headers work referenced is actually `#140`.

Fix recommendation: any future body of #149 (or sibling issues that copy this
template) should drop the "#140 / #138" cross-references or update them to
match what those issues actually are. Future issue-authoring template should
require cross-references to be live-checked against `gh issue view <N>`.

## 3. Tool quirk: Windows default codec is cp1252 (medium confidence)

Two `python -c` static checks crashed with `UnicodeDecodeError: 'charmap'
codec can't decode byte 0x90` when reading static files that contain UTF-8
em-dashes (rack.css has them, index.html title has one). The fix is to pass
`encoding='utf-8'` to `open()`. Wasted about 30 seconds on the first crash,
zero on the second. Next run: default to `encoding='utf-8'` for any `python
-c "open(...)"` over the repo's text files.

## 4. AGENTS.md known doc errors (not investigated this run)

Per the runner prompt, AGENTS.md has two known errors that this run did not
need to verify because no agents were dispatched (the change was small enough
for direct edits). Logging here for the next run that does need them:

- Line ~127 area names `scout` and `plan` as distinct agents. Current config
  only has `explore` and `explore-hard`. If a run tries to invoke `scout` or
  `plan`, fall back to `explore` / `explore-hard`.
- Line ~127 area also lists `atlas`, `quick`, `writing`, `unspecified-low` as
  OpenRouter-only (no local cap). Last check found all four actually mapped
  to local Lemonade models, so they DO count toward the 2-agent local cap.
