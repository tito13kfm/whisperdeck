# Wrong directions — issue #110

Discrepancies found while executing the issue text, the issue-runner prompt, AGENTS.md, or a skill.
Written as they were discovered, not backfilled at the end.

## 1. Issue #110's `app.py:1538` citation is stale

The issue says the enrollment route is at `app.py:1538`. That line in current `master` is unrelated
per-file-override validation inside `update_transcript_settings` (`file_settings[{idx}] must be an
object`). The real route is `enroll_speaker_from_transcript` at `app.py:2412-2413`, with its
`add_clip()` call at `app.py:2460`.

**Recommended fix:** none needed for the code; this is the expected staleness the issue-runner
prompt already warns about, and Phase 1's "don't trust the issue's line numbers" rule caught it.
Worth noting only as one more data point that these issue bodies must be re-derived from source.

## 2. Issue #110 names only one of three racing routes

The issue frames the race as "the enrollment route (`enroll_speaker_from_transcript`) ... can race
with a running voice_match job." Three event-loop routes are equally exposed, not one:
`enroll_voice` (`app.py:3350`), `enroll_speaker_from_transcript` (`app.py:2460`), and
`add_voice_clip` (`app.py:3417`). `identify_speaker` (`app.py:3381`) also *writes* the field as a
side effect even though `identify()` never reads it back.

**Recommended fix:** a fix that special-cased the one named route would have been a regression per
the Complement Rule. This was avoided by fixing the state inside `services/voice_id.py` instead of
at any call site, which covers all four routes plus the executor path by construction.

## 3. Issue #110's proposed fix option 3 (a lock) does not fix the reported symptom

The issue offers "Use a lock around reads/writes" as one of three interchangeable options. It is
not interchangeable: the reported impact is "Wrong error message shown to user." A lock makes the
read *consistent*, not *correct* — thread A would still read thread B's error text, just without
tearing. Only scoping the value (per-thread, or returned per-call) fixes cross-request
contamination.

**Recommended fix:** when an issue lists several fixes as equivalent, check each against the stated
*impact*, not just against the stated mechanism. Two of #110's three options fix the symptom; one
does not.

## 4. Issue #110's stated mitigation is narrower than claimed

The issue says the race is "Currently mitigated by `_MAX_CONCURRENT_CPU_JOBS = 1`."
`_MAX_CONCURRENT_CPU_JOBS` (`services/llm_jobs.py:45`) caps concurrency only among CPU-kind jobs
pulled through the LLM job queue. The three `app.py` routes never go through that queue, so there
was no mitigation at all for the job-vs-route race that is the actual subject of the issue.

## 5. Issue #110 misses two adjacent bugs that the mandated sibling sweep found

Neither is mentioned in the issue body:

- **Same-thread staleness.** `_embed_mfcc` guards its write with `if not self._last_backend_error`
  so it won't clobber a more specific primary-backend error. Nothing ever reset the field between
  calls, so on a `librosa_mfcc`-only backend the first failure's text stuck permanently and every
  later failure reported the first call's reason. `threading.local()` alone would NOT have fixed
  this; it needed the per-call reset at the top of `_extract_embedding`.
- **Two unguarded lazy-init caches.** `_get_classifier` and `_get_pyannote_inference` were plain
  check-then-act on `self._classifier` / `self._pyannote_inference`, reachable from the same
  event-loop-vs-executor call graph, so two threads could both load the model into the same on-disk
  `savedir`.

**Recommended fix:** both are fixed in this PR and disclosed as scope decisions in `self-audit.md`.

## 6. Serena MCP's project root is pinned to the main checkout, so its edit tools cannot target a worktree

Serena activated the project at `C:\Claude\WhisperDeck` (the main checkout). Its `relative_path`
arguments resolve against that root, so `replace_symbol_body` / `replace_content` /
`insert_after_symbol` on `services/voice_id.py` would have written the fix into the **main
checkout**, not the worktree — the exact path-crossing the issue-runner prompt forbids. Discovered
before any edit was issued; all Phase 2 edits used `Edit` with absolute worktree paths instead.

**Recommended fix:** add a line to `.claude/issue-runner-prompt.md`, in the "Two path roots exist"
block: "If a symbolic-editing MCP server (e.g. Serena) is active, note that its project root is the
main checkout, not your worktree. Do not use its editing tools during this run; use `Edit`/`Write`
with absolute worktree paths." This belongs in the prompt because the conflict is invisible until
you notice the server's activation message names the main repo path.

## 6b. `verify_self_audit.py`'s build check cannot run on this machine, in either root

`scripts/verify_self_audit.py` auto-detected the worktree correctly (the branch-name matching added
in PR #332 works), and its citation check passes clean there. Its build check cannot run at all:

```
BUILD [build:js]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
BUILD [build:css]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
```

This is not the "fresh worktree has no `node_modules`" case the issue-runner prompt already warns
about: the same failure occurs with `--repo-root` pointed at the main checkout, so `esbuild` is
absent from `node_modules/.bin` there too. Pre-existing environmental condition, unrelated to this
change, and provably harmless for it: `git diff --stat` shows this PR touches exactly two files,
`services/voice_id.py` and `tests/test_voice_id.py`, so no `esbuild`-declared bundle can be stale
because of it.

**Recommended fix:** two small ones. (1) The prompt tells you to run
`python scripts/verify_self_audit.py <path>` "from the main repo checkout" as one command; it should
add that a Python-only change can pass `--skip-build-check` after confirming via `git diff --stat`
that no `esbuild` source was touched, so an unrelated missing toolchain doesn't read as a blocking
finding. (2) `verify_self_audit.py` should distinguish "rebuild failed because esbuild is missing"
from "rebuild succeeded and the bytes differ" -- the first is an environment problem and should be
advisory, the second is the stale-bundle bug the check exists to catch, and today both are reported
as blocking.

## 7. Coordination risk: a parallel worktree exists for the neighbouring issue #109

A local branch `worktree-issue-109-voiceid-fallback` exists (0 commits ahead of `master` at the
time of checking, so nothing to conflict with yet). Issue #109 is the silent-MFCC-fallback bug in
`_extract_embedding` — the same function this PR edits. No open PR for it.

**Recommended fix:** add a Phase 0 step to `.claude/issue-runner-prompt.md`: after resolving the
target issue, run `git branch -a` and `gh pr list --state open` and report any branch or PR whose
name references a neighbouring issue in the same file, so the user can sequence them. Detecting
this after the fact is a merge conflict; detecting it in Phase 0 is a scheduling decision.

## 8. The self-report artifacts are invisible to the independent reviewer they exist for

Found after this PR was opened and audited by an independent model. The audit's honesty section
reported:

```
self-audit.md [x] lines verified: 0/0. No self-report artifacts found.
```

The four required files live at `.omo/runs/issue-110/<branch>/` in the **main checkout**, and
`.omo/` is gitignored, so none of them are on the PR branch. A reviewer reading the PR cannot see
`self-audit.md` and cannot check a single `[x]` against the diff. Phase 3.5 states that a false
`[x]` is "a serious self-report violation, worse than an honest `[ ]`" -- yet no PR reviewer is
positioned to find one. The failure mode is worse than absence: the reviewer printed `0/0 verified`
next to `0 false claims`, which reads as a clean result rather than as "this check did not run."
The 25 citations in this run were verified, but by the author only, via
`scripts/verify_self_audit.py` against the worktree.

**Recommended fix:** in Phase 4, after opening the PR, post `self-audit.md` as a PR comment
(`gh pr comment <N> --body-file <path-to-self-audit.md>`). It then travels with the PR for any
reviewer to check line by line, without committing a gitignored run artifact. Failing that, put the
absolute run-directory path in the PR body and have `/audit-pr` read it from there. Separately, the
auditing side should treat "self-audit not found" as its own outcome, `honesty check NOT RUN`,
rather than folding it into a `0/0 verified` pass.
